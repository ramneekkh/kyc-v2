import json
import logging
import os

from google.genai import types

from ..ai_client import ADCGenerativeModel, DEFAULT_GEMINI_MODEL
from .prompts import KYC_DOCUMENT_ANALYSIS_PROMPT

logger = logging.getLogger(__name__)


def get_model() -> ADCGenerativeModel:
    """Returns a configured Vertex AI Gemini 3.8 Flash model instance using ADC."""
    return ADCGenerativeModel(model_name=DEFAULT_GEMINI_MODEL)


def analyze_document_authenticity(file_path: str, mime_type: str):
    """
    Analyzes a document for authenticity and extracts data using Gemini 3.8 Flash via Vertex AI ADC.
    Uses inline Part.from_bytes() compatible with Vertex AI Application Default Credentials.
    """
    model = get_model()
    if not model:
        return {"error": "AI Model not configured"}

    try:
        logger.info(f"Reading document {file_path} ({mime_type}) for Vertex AI Gemini analysis...")
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        doc_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        response = model.generate_content(
            [KYC_DOCUMENT_ANALYSIS_PROMPT, doc_part],
            generation_config={"response_mime_type": "application/json"},
        )

        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]

        return json.loads(text.strip())

    except Exception as e:
        logger.error(f"Document analysis failed: {e}")
        return {"error": f"Analysis failed: {str(e)}"}
