# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""
Google Cloud Secret Manager integration using Application Default Credentials (ADC).
Stores and retrieves application secrets (GOOGLE_CSE_ID, GOOGLE_SEARCH_API_KEY, SB_API_KEY)
from Google Secret Manager with in-memory caching and fast local environment fallback.
"""

import logging
import os
from functools import lru_cache
from typing import Dict, Optional

import google.auth
import google.auth.transport.requests
from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import secretmanager

logger = logging.getLogger(__name__)

# Bootstrap values used to seed Secret Manager on a fresh project.
#
# These are read from the environment, never embedded. A credential committed to
# a repository is a credential in every clone, fork, CI log and backup, and
# rotating it becomes a code change rather than a console action. Populate a
# git-ignored .env, or export the variables, before running scripts/sync_secrets.py.
_BOOTSTRAP_SECRET_ENV_VARS = (
    "GOOGLE_CSE_ID",
    "GOOGLE_SEARCH_API_KEY",
    "GOOGLE_API_KEY",
    "SB_API_KEY",
)

DEFAULT_SECRET_FALLBACKS: Dict[str, str] = {
    name: os.environ[name] for name in _BOOTSTRAP_SECRET_ENV_VARS if os.environ.get(name)
}


def get_project_id() -> Optional[str]:
    """Resolves the active GCP project ID from environment or ADC."""
    project_id = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or os.environ.get("PROJECT_ID")
    )
    if project_id:
        return project_id
    try:
        _, adc_project = google.auth.default()
        return adc_project
    except Exception as exc:
        logger.debug(f"Could not determine project ID from ADC: {exc}")
        return None


def _get_valid_adc_credentials():
    """Fast check for valid ADC credentials to avoid gRPC retry stalls on expired RAPT tokens."""
    creds, adc_project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not creds.valid:
        creds.refresh(google.auth.transport.requests.Request())
    return creds, adc_project


@lru_cache(maxsize=32)
def get_secret(
    secret_id: str,
    version_id: str = "latest",
    project_id: Optional[str] = None,
    default: Optional[str] = None,
) -> Optional[str]:
    """
    Retrieves a secret value from Google Cloud Secret Manager using ADC,
    falling back immediately to environment variables and configured defaults if unavailable.
    """
    # 1. Check if explicitly overridden in environment (e.g., mounted via Cloud Run --set-secrets)
    env_val = os.environ.get(secret_id)
    if env_val and env_val not in ("yourkey", "your-api-key", "your-cse-id", ""):
        return env_val

    # 2. Attempt to fetch from Google Cloud Secret Manager via ADC
    resolved_project = project_id or get_project_id()
    if resolved_project:
        try:
            creds, _ = _get_valid_adc_credentials()
            client = secretmanager.SecretManagerServiceClient(credentials=creds)
            name = f"projects/{resolved_project}/secrets/{secret_id}/versions/{version_id}"
            response = client.access_secret_version(
                request={"name": name},
                retry=None,
                timeout=3.0,
            )
            payload = response.payload.data.decode("UTF-8").strip()
            if payload:
                os.environ[secret_id] = payload
                logger.info(f"Loaded secret '{secret_id}' from Google Secret Manager ({resolved_project}).")
                return payload
        except Exception as exc:
            logger.warning(
                f"Could not fetch secret '{secret_id}' from Secret Manager in project "
                f"'{resolved_project}': {exc}. Falling back to local configuration."
            )

    # 3. Fallback to configured default or provided default
    fallback = default or DEFAULT_SECRET_FALLBACKS.get(secret_id)
    if fallback:
        os.environ[secret_id] = fallback
    return fallback


def upsert_secret_in_gsm(
    secret_id: str,
    secret_value: str,
    project_id: Optional[str] = None,
) -> str:
    """
    Creates or updates a secret in Google Cloud Secret Manager using ADC.
    Returns the resource name of the created secret version.
    """
    resolved_project = project_id or get_project_id()
    if not resolved_project:
        raise ValueError("GOOGLE_CLOUD_PROJECT must be set or resolvable via ADC to store secrets.")

    creds, _ = _get_valid_adc_credentials()
    client = secretmanager.SecretManagerServiceClient(credentials=creds)
    parent = f"projects/{resolved_project}"
    secret_path = f"{parent}/secrets/{secret_id}"

    try:
        client.get_secret(request={"name": secret_path}, retry=None, timeout=5.0)
        logger.info(f"Secret '{secret_id}' already exists in {resolved_project}.")
    except NotFound:
        logger.info(f"Creating secret '{secret_id}' in {resolved_project}...")
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            },
            retry=None,
            timeout=5.0,
        )
    except AlreadyExists:
        pass

    version = client.add_secret_version(
        request={
            "parent": secret_path,
            "payload": {"data": secret_value.encode("UTF-8")},
        },
        retry=None,
        timeout=5.0,
    )
    logger.info(f"Added new secret version: {version.name}")
    get_secret.cache_clear()
    return version.name
