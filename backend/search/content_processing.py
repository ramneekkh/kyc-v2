import os
import logging
import json
import time
import google.generativeai as genai
from .prompts import KYC_DOCUMENT_ANALYSIS_PROMPT

logger = logging.getLogger(__name__)

# Initialize Gemini (Global or Lazy)
API_KEY = os.getenv("GOOGLE_API_KEY")
if API_KEY:
    genai.configure(api_key=API_KEY)
    
def get_model():
    """Returns a configured Gemini model instance for multimodal tasks."""
    # Use gemini-1.5-flash for speed and vision capabilities
    return genai.GenerativeModel('gemini-1.5-flash')

def upload_file_to_gemini(file_path, mime_type):
    """
    Uploads a file to Gemini File API for processing.
    Returns the file object/URI.
    """
    try:
        logger.info(f"Uploading file {file_path} to Gemini...")
        file_ref = genai.upload_file(file_path, mime_type=mime_type)
        
        # Poll for active state
        while file_ref.state.name == "PROCESSING":
            time.sleep(1)
            file_ref = genai.get_file(file_ref.name)
            
        if file_ref.state.name != "ACTIVE":
            logger.error(f"File upload failed. State: {file_ref.state.name}")
            return None
            
        logger.info(f"File uploaded successfully: {file_ref.uri}")
        return file_ref
    except Exception as e:
        logger.error(f"Error uploading file to Gemini: {e}")
        return None

def analyze_document_authenticity(file_path, mime_type):
    """
    Analyzes a document for authenticity and extracts data.
    """
    model = get_model()
    if not model:
        return {"error": "AI Model not configured"}

    file_ref = upload_file_to_gemini(file_path, mime_type)
    if not file_ref:
        return {"error": "Failed to upload document for analysis"}

    try:
        response = model.generate_content(
            [KYC_DOCUMENT_ANALYSIS_PROMPT, file_ref],
            generation_config={"response_mime_type": "application/json"}
        )
        
        # Cleanup file after analysis? 
        # Ideally yes, but depends on if we need it later. For now, let it be auto-cleaned by retention policy or manual del.
        # genai.delete_file(file_ref.name)
        
        text = response.text.strip()
        # Clean markdown if present (though response_mime_type usually handles it)
        if text.startswith("```json"): text = text[7:]
        if text.endswith("```"): text = text[:-3]
        
        return json.loads(text)

    except Exception as e:
        logger.error(f"Document analysis failed: {e}")
        return {"error": f"Analysis failed: {str(e)}"}
