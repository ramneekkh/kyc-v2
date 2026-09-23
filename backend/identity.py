# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""
Subject identity derivation.

The original implementation keyed every subject on `uuid5(NAMESPACE_DNS, name)`.
That makes the customer's NAME the primary key, which is wrong in a way that is
specific and dangerous for KYC:

  * Two distinct customers called "Wei Ling Tan" collapse into one record. The
    findings of one are attributed to the other.
  * A customer who changes their legal name becomes a second, unlinked subject,
    and their screening history silently resets.
  * Because the key is a pure function of the name, there is no tenant boundary:
    every institution using the deployment shares one identity space.

The correct key is the institution's own customer identifier, which is stable
across name changes and unique across same-named individuals. Where no customer
identifier is supplied (ad-hoc research, prospect screening) we fall back to a
composite natural key built from the discriminating attributes actually collected,
and we record which strategy was used so the weaker keys are auditable.
"""

import hashlib
import logging
import os
import re
import unicodedata
import uuid
from typing import Any, Dict, Optional, Tuple

# Namespace for all subject identifiers produced by this application. Distinct from
# NAMESPACE_DNS so ids cannot collide with unrelated uuid5 values.
SUBJECT_NAMESPACE = uuid.UUID("6f6b4f2e-0f4e-5d6a-9c3b-2a1d8e7f4c50")

# Identifies the owning institution. Subject ids are scoped to it so two tenants
# screening the same person do not share a record.
DEFAULT_TENANT_ID = os.environ.get("KYC_TENANT_ID", "default")

KEY_STRATEGY_CUSTOMER_ID = "customer_id"
KEY_STRATEGY_COMPOSITE = "composite_attributes"
KEY_STRATEGY_NAME_ONLY = "name_only"

# Attributes that meaningfully discriminate between same-named individuals,
# in order of decreasing strength.
_DISCRIMINATORS = ("dob", "national_id", "company", "region", "profession")


def _normalize(value: Any) -> str:
    """Case-folds, strips accents and collapses whitespace for stable hashing."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def derive_subject_id(
    subject_name: str,
    customer_id: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
    tenant_id: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Returns (subject_id, key_strategy).

    `customer_id` is the institution's system-of-record identifier (CIF number,
    party id, account id). Supply it whenever one exists - it is the only key that
    survives a name change and separates same-named customers.

    `attributes` may carry dob, national_id, company, region, profession. These are
    used only when no customer_id is available.
    """
    attributes = attributes or {}
    tenant = _normalize(tenant_id or DEFAULT_TENANT_ID) or "default"

    if customer_id and str(customer_id).strip():
        key = f"tenant={tenant}|customer_id={_normalize(customer_id)}"
        return str(uuid.uuid5(SUBJECT_NAMESPACE, key)), KEY_STRATEGY_CUSTOMER_ID

    present = [
        f"{field}={_normalize(attributes.get(field))}"
        for field in _DISCRIMINATORS
        if _normalize(attributes.get(field))
    ]

    if present:
        key = f"tenant={tenant}|name={_normalize(subject_name)}|" + "|".join(present)
        logging.info(
            "No customer_id supplied for '%s'; using composite key over %s. "
            "Supply customer_id to guarantee a stable, unambiguous subject record.",
            subject_name,
            [p.split("=", 1)[0] for p in present],
        )
        return str(uuid.uuid5(SUBJECT_NAMESPACE, key)), KEY_STRATEGY_COMPOSITE

    key = f"tenant={tenant}|name={_normalize(subject_name)}"
    logging.warning(
        "Subject '%s' is keyed on NAME ALONE - no customer_id and no discriminating "
        "attributes (dob, national_id, company, region, profession). Distinct "
        "individuals sharing this name WILL be merged into one record. "
        "This record must not be relied upon for a customer-level KYC decision.",
        subject_name,
    )
    return str(uuid.uuid5(SUBJECT_NAMESPACE, key)), KEY_STRATEGY_NAME_ONLY


def extract_identity_attributes(source: Dict[str, Any]) -> Dict[str, Any]:
    """Pulls the discriminating attributes out of a request/filter dict.

    Only attributes that are actually present are returned. Emitting
    ``{"dob": None}`` would make an absent attribute look like a supplied one to
    every downstream reader -- including the persisted subject profile, where it
    reads as "we asked and there is no DOB" rather than "we never captured one".
    """
    attributes = {}
    for field in _DISCRIMINATORS:
        value = source.get(field)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        attributes[field] = value
    return attributes


def content_fingerprint(text: Optional[str]) -> Optional[str]:
    """
    SHA-256 of normalized article text, used to detect that a URL's content changed.

    An article that is silently edited after publication (a correction, a retraction,
    or an expanded charge sheet) keeps the same URL. URL-only deduplication therefore
    pins the original assessment forever; comparing this fingerprint catches it.
    """
    if not text:
        return None
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()
