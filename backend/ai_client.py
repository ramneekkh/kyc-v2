# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""
Vertex AI Gemini Client authenticated strictly via Application Default Credentials (ADC).
Configured for Gemini 3.8 Flash (`gemini-3.8-flash`) with automatic endpoint/model resilience.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import google.auth
from google import genai
from google.genai import types

from .secret_manager import get_project_id

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
DEFAULT_VERTEX_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

# Ordered fallback chain if a specific Vertex AI region does not yet serve `gemini-3.8-flash`
MODEL_FALLBACK_CHAIN = [
    DEFAULT_GEMINI_MODEL,
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]


@dataclass
class UsageMetadataCompat:
    prompt_token_count: int = 0
    candidates_token_count: int = 0


@dataclass
class ResponseCompat:
    text: str
    candidates: List[Any]
    usage_metadata: UsageMetadataCompat


class ADCGenerativeModel:
    """
    Drop-in Vertex AI Gemini model wrapper using Application Default Credentials (ADC).
    Eliminates reliance on AI Studio API keys (`GOOGLE_API_KEY`) for Gemini generation.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_GEMINI_MODEL,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
    ):
        self.model_name = model_name or DEFAULT_GEMINI_MODEL
        self.project_id = project_id or get_project_id() or "elevate-data-508005"
        self.location = location or DEFAULT_VERTEX_LOCATION
        self._client: Optional[genai.Client] = None

    def _get_client(self) -> genai.Client:
        if self._client is None:
            # Authenticate strictly via Application Default Credentials (ADC) on Vertex AI
            credentials, adc_project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            resolved_project = self.project_id or adc_project
            self._client = genai.Client(
                vertexai=True,
                project=resolved_project,
                location=self.location,
                credentials=credentials,
            )
            logger.info(
                f"Initialized Vertex AI Gemini client via ADC "
                f"(project={resolved_project}, location={self.location}, model={self.model_name})"
            )
        return self._client

    def generate_content(
        self,
        contents: Union[str, List[Any]],
        safety_settings: Optional[Dict[str, str]] = None,
        generation_config: Optional[Dict[str, Any]] = None,
        request_options: Optional[Dict[str, Any]] = None,
    ) -> ResponseCompat:
        """
        Generates content via Vertex AI using ADC.
        Automatically retries across MODEL_FALLBACK_CHAIN if a model ID returns 404 NOT_FOUND.
        """
        client = self._get_client()

        config_kwargs: Dict[str, Any] = {}
        if generation_config:
            if "response_mime_type" in generation_config:
                config_kwargs["response_mime_type"] = generation_config["response_mime_type"]
            if "temperature" in generation_config:
                config_kwargs["temperature"] = generation_config["temperature"]

        # Map legacy dict safety_settings to google-genai SafetySetting list
        if safety_settings:
            config_kwargs["safety_settings"] = [
                types.SafetySetting(
                    category=cat,
                    threshold=thresh,
                )
                for cat, thresh in safety_settings.items()
            ]

        gen_config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        # Build deduplicated candidate models list starting with requested model
        candidate_models = []
        for m in [self.model_name] + MODEL_FALLBACK_CHAIN:
            if m and m not in candidate_models:
                candidate_models.append(m)

        last_error: Optional[Exception] = None
        for candidate_model in candidate_models:
            try:
                resp = client.models.generate_content(
                    model=candidate_model,
                    contents=contents,
                    config=gen_config,
                )
                usage = getattr(resp, "usage_metadata", None)
                prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
                candidate_tokens = getattr(usage, "candidates_token_count", 0) or 0

                return ResponseCompat(
                    text=resp.text or "",
                    candidates=getattr(resp, "candidates", [True]) or [True],
                    usage_metadata=UsageMetadataCompat(
                        prompt_token_count=prompt_tokens,
                        candidates_token_count=candidate_tokens,
                    ),
                )
            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                if "404" in err_str or "NOT_FOUND" in err_str or "not found" in err_str.lower():
                    logger.warning(
                        f"Model '{candidate_model}' not found in Vertex AI ({self.location}); "
                        f"falling back to next available Gemini Flash model..."
                    )
                    continue
                raise

        raise RuntimeError(f"All Gemini models failed via Vertex AI ADC. Last error: {last_error}")
