# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# backend/search/utils.py
import os
import logging
import json
import uuid
import time
import re
import concurrent.futures
from urllib.parse import urlparse, quote
from googleapiclient.discovery import build
from dotenv import load_dotenv
from typing import List, Dict, Generator
import datetime
import requests
from bs4 import BeautifulSoup


import google.generativeai as genai
from .prompts import (
    KYC_RISK_CATEGORIES_PROMPT_TEXT,
    KYC_ANALYZE_SINGLE_RESULT_PROMPT,
    KYC_OVERALL_SUMMARY_PROMPT,
    KYC_RISK_CATEGORY_OPTIONS_LIST_STR,
    KYC_BULK_ENTITY_EXTRACTION_PROMPT,
    KYC_GRAPH_EXTRACTION_PROMPT
)

# Import Spanner Client
from ..database.spanner_client import spanner_client
from .deduplication import SyndicationFilter, compute_url_hash

load_dotenv()

try:
    genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
    model = genai.GenerativeModel('gemini-3-flash-preview')
    lite_model = genai.GenerativeModel('gemini-3-flash-preview')
except Exception as e:
    logging.error(f"Failed to configure Gemini API: {e}. Make sure GOOGLE_API_KEY is set.")
    model = None
    lite_model = None

DEFAULT_NUM_QUERIES = 100
DEFAULT_NUM_RESULTS = 10

safety_settings = {
    "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
    "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
    "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
}

def _extract_json_from_response(text: str) -> str:
    """Extracts a JSON object from a string, handling markdown."""
    match = re.search(r"```json\s*([\s\S]*?)\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)

    start_index = text.find('{')
    end_index = text.rfind('}')
    if start_index != -1 and end_index != -1 and end_index > start_index:
        return text[start_index : end_index + 1].strip()

    start_index_list = text.find('[')
    end_index_list = text.rfind(']')
    if start_index_list != -1 and end_index_list != -1 and end_index_list > start_index_list:
        return text[start_index_list : end_index_list + 1].strip()

    return text.strip()

def google_web_search(query: str, api_key: str, cse_id: str, num_results: int = 10, recency_days: int = 0, max_retries: int = 3) -> list:
    """Performs a Google search with retry logic."""
    for attempt in range(max_retries):
        try:
            service = build("customsearch", "v1", developerKey=api_key)
            search_params = {'q': query, 'cx': cse_id, 'num': num_results}
            if recency_days and isinstance(recency_days, int) and recency_days > 0:
                 search_params['dateRestrict'] = f'd{recency_days}'
            result = service.cse().list(**search_params).execute()
            return result.get('items', [])
        except Exception as e:
            logging.warning(f"Attempt {attempt + 1} for google_web_search '{query}' failed: {e}")
            if attempt + 1 == max_retries:
                logging.error(f"All {max_retries} attempts failed for google_web_search '{query}'.")
                return []
            time.sleep(1)
    return []

def perform_google_web_searches(queries: list, num_results: int, recency_days: int) -> list:
    """Executes search queries in parallel."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    cse_id = os.environ.get("GOOGLE_CSE_ID")
    if not api_key or not cse_id:
        logging.error("GOOGLE_API_KEY or GOOGLE_CSE_ID is not configured.")
        return []

    all_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_query = {executor.submit(google_web_search, query, api_key, cse_id, num_results, recency_days): query for query in queries}
        for future in concurrent.futures.as_completed(future_to_query):
            try:
                results = future.result()
                if results:
                    all_results.extend(results)
            except Exception as exc:
                logging.error(f"Query generated an exception: {exc}")
    # De-duplicate results based on link
    unique_results = {result['link']: result for result in all_results}.values()
    return list(unique_results)

def generate_entity_graph_with_gemini(findings, subject_name):
    """
    Generates a knowledge graph (nodes and edges) from the findings using Gemini.
    """
    if not findings:
        return {"nodes": [], "edges": []}

    try:
        # Prepare a lightweight version of findings to save context window
        # But prefer full content if available (as per user request for deeper graph)
        simplified_findings = []
        for f in findings:
            item = {
                "title": f.get("source_title"),
                "snippet": f.get("snippet"),
                "insight": f.get("gemini_insight"),
                "url": f.get("url")
            }
            # If full scraped content is passed (e.g. from generate_graph endpoint)
            if f.get("scraped_content") and len(f.get("scraped_content")) > 100:
                item["full_content"] = f.get("scraped_content")[:20000] # Cap content to avoid huge context usage
            
            simplified_findings.append(item)
        
        findings_json = json.dumps(simplified_findings, indent=2)
        
        prompt = KYC_GRAPH_EXTRACTION_PROMPT.format(
            subject_name=subject_name,
            findings_json=findings_json
        )
        
        # Use a model with larger context window if possible.
        # User requested "Gemini 3 Flash Preview".
        model = genai.GenerativeModel('gemini-3-flash-preview') 
        response = model.generate_content(prompt)
        
        if not response.text:
            return {"nodes": [], "edges": []}
            
        cleaned_text = response.text.strip()
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]
            
        graph_data = json.loads(cleaned_text)
        
        # Extract usage metadata if available
        if hasattr(response, 'usage_metadata'):
            graph_data["usage_metadata"] = {
                "input_tokens": response.usage_metadata.prompt_token_count,
                "output_tokens": response.usage_metadata.candidates_token_count
            }
            
        return graph_data

    except Exception as e:
        logging.error(f"Error generating entity graph: {e}")
        return {"nodes": [], "edges": []}


def scrape_url_content(url: str) -> str:
    """
    Makes a robust attempt to scrape content, first directly, then with ScrapingBee.
    """
    if not url:
        return "Scraping failed: No URL provided."

    # 1. First attempt: Direct scrape with bs4
    logging.info(f"Attempting direct scrape for {url}...")
    content = _scrape_with_bs4(url)
    if content:
        return content

    # 2. Fallback: ScrapingBee
    logging.info(f"Direct scrape failed for {url}. Falling back to ScrapingBee.")
    api_key = os.environ.get("SB_API_KEY")
    if not api_key:
        logging.error("ScrapingBee API key (SB_API_KEY) not found in environment variables.")
        return "Scraping failed: Fallback service is not configured."

    try:
        encoded_url = quote(url)
        # Increase timeout to 60s
        api_url = f"https://app.scrapingbee.com/api/v1/?api_key={api_key}&url={encoded_url}&render_js=false" 

        response = requests.get(api_url, timeout=60)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()

        main_content = soup.find('article') or soup.find('main') or soup.find('body')

        if main_content:
            paragraphs = main_content.find_all('p')
            content = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
            if content:
                logging.info(f"ScrapingBee scrape successful for {url}")
                return content

        return "Scraping failed with ScrapingBee: Could not isolate main content."

    except requests.RequestException as e:
        logging.error(f"ScrapingBee request failed for URL {url}: {e}")
        return f"Scraping failed with ScrapingBee. Reason: {e}"
    except Exception as e:
        logging.error(f"An unexpected error occurred during ScrapingBee scrape of {url}: {e}")
        return f"Scraping failed with ScrapingBee. Check logs."

def generate_search_queries_with_gemini(filters: Dict, prompt_template: str, max_retries: int = 3) -> List[str]:
    """Uses Gemini to brainstorm search queries."""
    if not lite_model:
        logging.error("Gemini lite_model not initialized.")
        return []

    custom_keywords_list = filters.get('custom_keywords', [])
    custom_keywords_prompt_str = ", ".join(f'"{k}"' for k in custom_keywords_list) if custom_keywords_list else "None"

    prompt = prompt_template.format(
        subject_name=filters.get('subject_name'),
        alias=filters.get('alias', 'N/A'),
        profession=filters.get('profession', 'N/A'),
        company=filters.get('company', 'N/A'),
        region=filters.get('region', 'N/A'),

        dob=filters.get('dob', 'N/A'),
        age=filters.get('age', 'N/A'),
        ownership=filters.get('ownership', 'N/A'),
        spouse=filters.get('spouse', 'N/A'),
        custom_keywords=custom_keywords_prompt_str,
        num_queries=filters.get('num_queries'),
        risk_categories=KYC_RISK_CATEGORIES_PROMPT_TEXT
    )

    for attempt in range(max_retries):
        try:
            response = lite_model.generate_content(prompt, safety_settings=safety_settings)
            if not response.candidates:
                logging.error(f"AI query generation response was blocked on attempt {attempt + 1}.")
                time.sleep(1)
                continue
            json_string = _extract_json_from_response(response.text)
            queries = json.loads(json_string)
            
            usage_info = {}
            if hasattr(response, 'usage_metadata'):
                usage_info = {
                    "input_tokens": response.usage_metadata.prompt_token_count,
                    "output_tokens": response.usage_metadata.candidates_token_count
                }
            
            return queries, usage_info
        except (json.JSONDecodeError, Exception) as e:
            logging.warning(f"Attempt {attempt + 1} failed for query generation: {e}")
            if attempt + 1 == max_retries:
                logging.error(f"All {max_retries} attempts failed for query generation.")
                break
            time.sleep(1)
    return [f'"{filters.get("subject_name")}" AND (fraud OR investigation OR sanctions)'], {}

def _scrape_and_hash_single_result(result: Dict, syndication_filter: SyndicationFilter = None) -> Dict:
    """Worker to scrape content and compute hash for deduplication."""
    url = result.get('link')
    content = scrape_url_content(url)
    
    # Compute hashes
    u_hash = compute_url_hash(url)
    c_hash_str = None
    minhash_obj = None
    
    if content and syndication_filter:
        minhash_obj = syndication_filter.compute_minhash(content)
        c_hash_str = syndication_filter.serialize_minhash(minhash_obj)
        
    return {
        "link": url,
        "full_content": content,
        "url_hash": u_hash,
        "content_hash": c_hash_str,
        "minhash_obj": minhash_obj
    }

def _analyze_single_kyc_result(result: Dict, filters: Dict, is_priority: bool, pre_scraped_content: str = None, max_retries: int = 3) -> Dict:
    """Worker function to analyze one search result with Gemini."""
    subject_name = filters.get('subject_name')
    prompt = KYC_ANALYZE_SINGLE_RESULT_PROMPT.format(
        subject_name=subject_name,
        alias=filters.get('alias', 'N/A'),
        profession=filters.get('profession', 'N/A'),
        company=filters.get('company', 'N/A'),
        region=filters.get('region', 'N/A'),

        dob=filters.get('dob', 'N/A'),
        age=filters.get('age', 'N/A'),
        ownership=filters.get('ownership', 'N/A'),
        spouse=filters.get('spouse', 'N/A'),
        title=result.get('title', 'N/A'),
        snippet=result.get('snippet', 'N/A'),
        link=result.get('link', 'N/A'),
        risk_categories=KYC_RISK_CATEGORIES_PROMPT_TEXT,
        risk_category_options=KYC_RISK_CATEGORY_OPTIONS_LIST_STR,
        current_date=datetime.date.today().isoformat()
    )
    for attempt in range(max_retries):
        try:
            response = lite_model.generate_content(prompt, safety_settings=safety_settings)
            if not response.candidates:
                raise ValueError("Response blocked.")
            json_string = _extract_json_from_response(response.text)
            analysis = json.loads(json_string)

            structured_date_raw = result.get('pagemap', {}).get('metatags', [{}])[0].get('article:published_time', 'N/A')
            structured_date = structured_date_raw.split('T')[0] if structured_date_raw != 'N/A' else 'N/A'
            llm_extracted_date = analysis.get("publication_date", "N/A")
            final_date = structured_date if structured_date != 'N/A' else llm_extracted_date

            # Extract usage metadata if available
            usage_info = {}
            if hasattr(response, 'usage_metadata'):
                usage_info = {
                    "input_tokens": response.usage_metadata.prompt_token_count,
                    "output_tokens": response.usage_metadata.candidates_token_count
                }
            
            # Use pre-scraped content if available, else scrape
            full_content_text = pre_scraped_content if pre_scraped_content is not None else scrape_url_content(result.get('link'))
            
            # Derive Media House from URL
            domain = urlparse(result.get('link', '')).netloc
            media_house = domain.replace('www.', '') if domain else "Unknown"

            # Derive Scraping Status
            scraping_status = "Success"
            if not full_content_text or full_content_text.startswith("Scraping failed") or len(full_content_text) < 50:
                scraping_status = "Failed"

            return {
                "source_title": result.get('title', 'N/A'),
                "url": result.get('link', 'N/A'),
                "media_house": media_house,
                "snippet": result.get('snippet', 'N/A'),
                "full_content": full_content_text, 
                "gemini_insight": analysis.get("gemini_insight", "Error in analysis."),
                "risk_level": analysis.get("risk_level", "Unknown"),
                "risk_category": analysis.get("risk_category", "Uncategorized"),
                "match_status": analysis.get("match_status", "Possible"),
                "media_house_reputation": analysis.get("media_house_reputation", "Unknown"),
                "scraping_status": scraping_status,
                "relevance_score": analysis.get("relevance_score", 0),
                "source_date": final_date,
                "source_type": "priority_search" if is_priority else "gemini_search", 
                "usage_metadata": usage_info
            }
        except (json.JSONDecodeError, Exception) as e:
            logging.warning(f"Failed to analyze result for '{subject_name}' on attempt {attempt+1}: {e}")
            if attempt + 1 < max_retries:
                time.sleep(1)

    # Use pre-scraped content even on failure
    full_content_text = pre_scraped_content if pre_scraped_content is not None else scrape_url_content(result.get('link'))
    
    # Fallback Logic on Error
    domain = urlparse(result.get('link', '')).netloc
    media_house = domain.replace('www.', '') if domain else "Unknown"
    scraping_status = "Success"
    if not full_content_text or full_content_text.startswith("Scraping failed") or len(full_content_text) < 50:
        scraping_status = "Failed"

    return {
        "source_title": result.get('title', 'N/A'),
        "url": result.get('link', 'N/A'),
        "media_house": media_house,
        "snippet": result.get('snippet', 'N/A'),
        "full_content": full_content_text,
        "gemini_insight": "AI analysis failed after multiple retries.",
        "risk_level": "Unknown",
        "risk_category": "Uncategorized",
        "match_status": "Unknown",
        "media_house_reputation": "Unknown",
        "scraping_status": scraping_status,
        "relevance_score": 0,
        "source_date": result.get('pagemap', {}).get('metatags', [{}])[0].get('article:published_time', 'N/A').split('T')[0],
        "source_type": "priority_search" if is_priority else "gemini_search" 
    }


# --- Pricing Constants (USD per 1M tokens) ---
# Estimates based on public pricing
PRICING = {
    'gemini-1.5-flash': {'input': 0.075, 'output': 0.30},
    'gemini-1.5-pro': {'input': 3.50, 'output': 10.50}
}

def calculate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Calculates estimated cost for a request."""
    # Simple mapping to handle version suffixes
    base_model = 'gemini-1.5-flash' if 'flash' in model_name.lower() else 'gemini-1.5-pro'
    rates = PRICING.get(base_model, {'input': 0, 'output': 0})
    
    input_cost = (input_tokens / 1_000_000) * rates['input']
    output_cost = (output_tokens / 1_000_000) * rates['output']
    return input_cost + output_cost

def analyze_kyc_results(search_results: List[Dict], filters: Dict, priority_links: set = None, previous_findings: List[Dict] = None, syndication_filter: SyndicationFilter = None, existing_url_hashes: dict = None) -> Generator[Dict, None, None]:
    """
    Analyzes search results with a 3-phase pipeline:
    1. Scrape & Hash (Parallel)
    2. Deduplicate (Sequential against LSH and Existing Hashes)
    3. AI Analysis (Parallel on Unique items)
    """
    if not model or not lite_model:
        logging.error("Gemini models not initialized.")
        return

    subject_name = filters.get('subject_name')
    priority_links = priority_links or set()
    previous_findings = previous_findings or []
    existing_url_hashes = existing_url_hashes or {}
    
    # Session tracking
    session_usage = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    
    # --- Phase 1: Scrape & Hash ---
    logging.info("Phase 1: Scraping and Hashing...")
    scraped_data_list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as scrape_executor:
        future_to_item = {scrape_executor.submit(_scrape_and_hash_single_result, res, syndication_filter): res for res in search_results}
        for future in concurrent.futures.as_completed(future_to_item):
            try:
                data = future.result()
                # Merge original priority flag for later
                is_priority = future_to_item[future].get('link') in priority_links
                data['is_priority'] = is_priority
                data['original_item'] = future_to_item[future] # Keep ref to original snippet/title
                scraped_data_list.append(data)
            except Exception as e:
                logging.error(f"Scraping failed for an item: {e}")

    # --- Phase 2: Deduplication ---
    logging.info("Phase 2: Deduplication Check...")
    unique_items_to_analyze = []
    final_processed_results = []
    
    for item in scraped_data_list:
        # 1. Exact URL Deduplication (Metadata Layer)
        u_hash = item.get('url_hash')
        if u_hash and u_hash in existing_url_hashes:
            # Skip exact duplicate
            logging.info(f"Skipping known duplicate URL: {item['link']}")
            continue

        # Generate ID
        f_id = str(uuid.uuid4())
        item['finding_id'] = f_id
        
        is_syn = False
        parent_id = None
        
        if syndication_filter and item.get('minhash_obj'):
            # Check against the filter
            is_syn, parent_id = syndication_filter.check_syndication_by_hash(f_id, item['minhash_obj'])
            
        item['is_syndicated'] = is_syn
        item['parent_finding_id'] = parent_id
        
        if is_syn:
            # Create "Syndicated" result stub
            stub = {
                "finding_id": f_id,
                "source_title": item['original_item'].get('title', 'N/A'),
                "url": item['link'],
                "snippet": item['original_item'].get('snippet', 'N/A'),
                "full_content": item['full_content'],
                "gemini_insight": "Syndicated article (Duplicate content).",
                "risk_level": "Low", # Default for duplicates (or inherit?)
                "risk_category": "Syndicated News",
                "relevance_score": 0,
                "source_date": item['original_item'].get('pagemap', {}).get('metatags', [{}])[0].get('article:published_time', 'N/A').split('T')[0],
                "source_type": "syndicated_duplicate",
                "usage_metadata": {},
                "url_hash": item['url_hash'],
                "content_hash": item['content_hash'],
                "is_syndicated": True,
                "parent_finding_id": parent_id
            }
            final_processed_results.append(stub)
            yield {"type": "result", "data": stub}
        else:
            unique_items_to_analyze.append(item)

    # --- Phase 3: AI Analysis ---
    logging.info(f"Phase 3: Analying {len(unique_items_to_analyze)} unique items...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ai_executor:
        future_to_input = {
            ai_executor.submit(
                _analyze_single_kyc_result, 
                inp['original_item'], 
                filters, 
                inp['is_priority'], 
                inp['full_content'] # Pass pre-scraped
            ): inp 
            for inp in unique_items_to_analyze
        }
        
        for future in concurrent.futures.as_completed(future_to_input):
            try:
                inp = future_to_input[future]
                res = future.result()
                
                # Merge Deduplication Metadata back into result
                res['finding_id'] = inp['finding_id']
                res['url_hash'] = inp['url_hash']
                res['content_hash'] = inp['content_hash']
                res['is_syndicated'] = False
                res['parent_finding_id'] = None
                
                # Usage handling
                usage = res.pop('usage_metadata', None)
                if usage:
                    session_usage["input_tokens"] += usage.get('input_tokens', 0)
                    session_usage["output_tokens"] += usage.get('output_tokens', 0)
                    cost = calculate_cost('gemini-1.5-flash', usage.get('input_tokens', 0), usage.get('output_tokens', 0))
                    session_usage["cost"] += cost
                    yield {"type": "usage_update", "data": session_usage}
                
                final_processed_results.append(res)
                yield {"type": "result", "data": res}
            except Exception as exc:
                logging.error(f"Analysis failed: {exc}")

    if final_processed_results:
        # Persistence
        subject_id = filters.get('subject_id')
        if subject_id:
             try:
                 spanner_client.save_findings_batch(subject_id, final_processed_results)
             except Exception as e:
                 logging.error(f"Failed to persist findings batch: {e}")

        # Summary Generation
        try:
            # 1. Restore missing variable definitions
            unique_new_findings = [r for r in final_processed_results if not r.get('is_syndicated')]
            all_findings_for_context = unique_new_findings + previous_findings
            
            # Filter for high relevance (Score >= 5)
            relevant_findings = [r for r in all_findings_for_context if int(r.get("relevance_score", 0)) >= 5]
            
            logging.info(f"Generating summary based on {len(relevant_findings)} relevant findings...")

            # 2. Call reusable helper
            summary_data = generate_kyc_summary_logic(subject_name, relevant_findings, model, safety_settings, session_usage)
            
            if summary_data is None:
                logging.error("Summary generation returned None (likely AI error or block).")
                summary_data = {
                    "risk_score": "Unknown",
                    "summary": "The AI could not generate a summary at this time (Possible content block or service overload). Please check individual findings.",
                    "key_findings": [],
                    "recommendation": "Manual Review",
                    "reasoning": "AI Generation Error"
                }

            logging.info(f"Summary generated: {summary_data.get('risk_score', 'N/A')} Risk")
            yield {"type": "summary", "data": summary_data}

            # 3. Persistence
            if subject_id and summary_data:
                 try:
                     spanner_client.update_subject_summary(subject_id, json.dumps(summary_data))
                 except Exception as e:
                     logging.error(f"Failed to persist summary: {e}")

        except (json.JSONDecodeError, Exception) as e:
            logging.error(f"Failed to generate final KYC summary: {e}")
            yield {"type": "summary", "data": {
                "risk_score": "Unknown",
                "summary": f"Error: Could not generate the final summary. Reason: {e}",
                "key_findings": [],
                "recommendation": "Manual Review",
                "reasoning": "System error during generation."
            }}

def generate_kyc_summary_logic(subject_name, relevant_findings, model, safety_settings, session_usage):
    """
    Reusable logic for generating KYC summary with Map-Reduce for large contexts.
    """
    if not relevant_findings:
         logging.info("No relevant findings (Score >= 5) for summary.")
         return {
            "risk_score": "Low",
            "summary": f"No significant adverse media or KYC-related risks were identified for {subject_name} based on the open-source intelligence scan.",
            "key_findings": [],
            "recommendation": "Accept",
            "reasoning": "No negative news found in the search results."
         }

    # --- Chunking Logic to handle Token Limits ---
    # Heuristic: 1 token ~ 4 chars. 1M token limit -> ~4MB text.
    # Safe limit: 200,000 tokens per chunk (~800k chars) to be very safe and leave room for prompt.
    MAX_CHARS_PER_CHUNK = 800000 
    
    findings_json_str = json.dumps(relevant_findings, indent=2)
    
    if len(findings_json_str) < MAX_CHARS_PER_CHUNK:
        # Direct generation
        return _generate_summary_chunk(subject_name, findings_json_str, model, safety_settings, session_usage)
    else:
        # Map-Reduce: Split findings and summarize chunks
        logging.info(f"Input too large ({len(findings_json_str)} chars). Splitting into chunks...")
        chunk_size = 50 # findings per chunk
        chunks = [relevant_findings[i:i + chunk_size] for i in range(0, len(relevant_findings), chunk_size)]
        
        partial_summaries = []
        for i, chunk in enumerate(chunks):
            logging.info(f"Summarizing chunk {i+1}/{len(chunks)}...")
            chunk_json = json.dumps(chunk, indent=2)
            partial_sum_data = _generate_summary_chunk(subject_name, chunk_json, model, safety_settings, session_usage)
            if partial_sum_data and partial_sum_data.get("summary"):
                 partial_summaries.append(f"Part {i+1}: {partial_sum_data['summary']}")
        
        # Final Consolidation
        combined_text = "\n\n".join(partial_summaries)
        consolidation_prompt = f"""
        You are an expert analyst. Consolidate these partial risk summaries for {subject_name} into a single, cohesive final KYC risk report.
        Maintain the same high/medium/low risk scoring logic.
        
        Partial Summaries:
        {combined_text}
        
        Return ONLY the standard JSON format:
        {{
          "risk_score": "High" | "Medium" | "Low",
          "summary": "Merged summary...",
          "key_findings": [
            {{
              "finding": "Brief one-sentence description of the event.",
              "severity": "High" | "Medium" | "Low",
              "date": "YYYY-MM-DD",
              "citations": ["url1"]
            }}
          ],
          "recommendation": "Accept", "Reject", or "Manual Review" (Choose ONE. Do NOT add reasoning here.),
          "reasoning": "One sentence explaining the recommendation."
        }}
        """
        logging.info("Generating final consolidated summary...")
        return _generate_summary_from_prompt(consolidation_prompt, model, safety_settings, session_usage)


def regenerate_kyc_summary(subject_id: str):
    """
    Manually triggers regeneration of the KYC summary for a subject.
    Fetches existing findings from Spanner and runs the map-reduce summary logic.
    """
    try:
        if not model:
            logging.error("Gemini model not initialized.")
            return None

        # Fetch data
        subject = spanner_client.get_subject(subject_id)
        if not subject:
            logging.error(f"Subject {subject_id} not found.")
            return None
            
        subject_name = subject.get('name')
        findings = spanner_client.get_findings(subject_id)
        
        # Prepare valid findings
        # Findings from Spanner are dicts.
        # We need to ensure we use the 'relevant' ones (score >= 5) for summary, 
        # but the spanner findings might already be filtered? No, spanner has all.
        
        # Re-apply relevance filter
        relevant_findings = [f for f in findings if int(f.get("relevance_score", 0)) >= 5]
        
        logging.info(f"Regenerating summary for {subject_name} based on {len(relevant_findings)} findings.")
        
        session_usage = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
        
        summary_data = generate_kyc_summary_logic(
            subject_name, 
            relevant_findings, 
            model, 
            safety_settings, 
            session_usage
        )
        
        if summary_data:
             spanner_client.update_subject_summary(subject_id, json.dumps(summary_data))
             logging.info(f"Summary regenerated and saved for {subject_name}.")
             return summary_data
             
        return None

    except Exception as e:
        logging.error(f"Failed to regenerate summary for {subject_id}: {e}")
        return None

def _generate_summary_chunk(subject_name, context_json, model, safety_settings, session_usage):
    """Helper to generate a summary for a specific context chunk."""
    prompt = KYC_OVERALL_SUMMARY_PROMPT.format(
        subject_name=subject_name,
        formatted_results=context_json
    )
    return _generate_summary_from_prompt(prompt, model, safety_settings, session_usage)

def _generate_summary_from_prompt(prompt, model, safety_settings, session_usage):
    """Helper to call Gemini with retry logic for Quota limits."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt, safety_settings=safety_settings)
            
            if hasattr(response, 'usage_metadata'):
                in_tok = response.usage_metadata.prompt_token_count
                out_tok = response.usage_metadata.candidates_token_count
                session_usage["input_tokens"] += in_tok
                session_usage["output_tokens"] += out_tok
                cost = calculate_cost('gemini-1.5-pro', in_tok, out_tok)
                session_usage["cost"] += cost

            if not response.candidates:
                 logging.warning("Summary generation blocked.")
                 return None

            json_string = _extract_json_from_response(response.text)
            return json.loads(json_string)
            
        except Exception as e:
            if "429" in str(e) or "Quota exceeded" in str(e):
                 wait_time = (attempt + 1) * 20 # Aggressive backoff: 20s, 40s, 60s
                 logging.warning(f"Quota exceeded. Retrying summary generation in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                 time.sleep(wait_time)
            else:
                 logging.error(f"Summary generation error: {e}")
                 return None
    return None

def _scrape_with_bs4(url: str) -> str or None:
    """Attempts a direct scrape using requests and BeautifulSoup."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()

        main_content = soup.find('article') or soup.find('main') or soup.find('body')

        if main_content:
            paragraphs = main_content.find_all('p')
            content = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
            if content:
                logging.info(f"Direct scrape successful for {url}")
                return content

        logging.warning(f"Direct scrape for {url} yielded no content.")
        return None

    except requests.RequestException as e:
        logging.warning(f"Direct scrape failed for URL {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred during direct scraping of {url}: {e}")
        return None





def transform_result_for_export(item: Dict, subject_name: str, scraped_content: str) -> Dict:
    """
    Transforms a single result item into the desired JSON export format.
    """
    try:
        display_link = urlparse(item.get('url')).hostname if item.get('url') else "N/A"
    except Exception:
        display_link = "N/A"

    return {
        "Name": subject_name,
        "title": item.get('source_title', 'N/A'),
        "htmlTitle": item.get('source_title', 'N/A'),
        "link": item.get('url', 'N/A'),
        "displayLink": display_link,
        "snippet": item.get('snippet', 'N/A'),
        "htmlSnippet": item.get('snippet', 'N/A'),
        "news content": scraped_content,
        "news publish date": item.get('source_date', 'N/A')
    }

from .prompts import KYC_BULK_ENTITY_EXTRACTION_PROMPT, KYC_PROFILE_EXTRACTION_PROMPT, KYC_GRAPH_EXTRACTION_PROMPT
# ... (imports remain the same)

def extract_profile_data_with_gemini(findings: List[Dict], subject_name: str) -> Dict:
    """
    Extracts structured profile data (age, location, etc.) using Gemini.
    """
    if not model:
        logging.error("Gemini model not initialized for profile extraction.")
        return {}

    try:
        # Simplify findings to core info to save tokens/reduce noise
        simplified_findings = [{
            "title": f.get("title", ""),
            "date": f.get("source_date", ""),
            "insight": f.get("gemini_insight", ""),
            "snippet": f.get("snippet", "")
        } for f in findings]
        
        findings_json = json.dumps(simplified_findings, indent=2)
        
        prompt = KYC_PROFILE_EXTRACTION_PROMPT.format(subject_name=subject_name, findings_json=findings_json)
        
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # Clean markdown
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]
            
        return json.loads(text.strip())
    except Exception as e:
        logging.error(f"Profile extraction failed: {e}")
        return {}



def extract_entities_from_bulk_data(articles_data: List[Dict]) -> Dict:
    """
    Extracts entities and relationships from a bulk collection of articles using Gemini.
    """
    if not model:
        logging.error("Gemini model not initialized.")
        return {"nodes": [], "edges": []}

    try:
        # Prepare the data for the prompt
        # We limit the size of each article's content to avoid hitting token limits even with bulk processing
        # but since we are doing one shot, we can be a bit more generous if the model supports it.
        # For now, let's keep a reasonable limit per article to ensure the total JSON fits.
        
        sanitized_data = []
        for article in articles_data:
            sanitized_data.append({
                "title": article.get("title", "N/A"),
                "url": article.get("url", "N/A"),
                "content": article.get("content", "")[:10000] # Limit per article
            })
            
        json_data_str = json.dumps(sanitized_data, indent=2)
        
        logging.info(f"Starting bulk entity extraction for {len(articles_data)} articles...")
        
        prompt = KYC_BULK_ENTITY_EXTRACTION_PROMPT.format(json_data=json_data_str)
        
        # Use a generous timeout for the bulk operation
        response = model.generate_content(
            prompt, 
            safety_settings=safety_settings,
            request_options={'timeout': 300} # 5 minutes timeout for bulk op
        )
        
        if not response.candidates:
            logging.warning("Bulk entity extraction blocked.")
            return {"nodes": [], "edges": []}
        
        json_string = _extract_json_from_response(response.text)
        result = json.loads(json_string)
        logging.info("Bulk entity extraction finished successfully.")
        return result
        
    except Exception as e:
        logging.error(f"Bulk entity extraction failed: {e}")
        return {"nodes": [], "edges": []}

def generate_graph_html(nodes: List[Dict], edges: List[Dict], subject_name: str) -> str:
    """
    Generates a standalone HTML string with a Vis.js graph visualization.
    """
    # Pre-process nodes to highlight the subject
    for node in nodes:
        if node.get('label', '').lower() == subject_name.lower():
            node['size'] = 40
            node['color'] = {'background': '#ef4444', 'border': '#b91c1c'} # Red for subject
            node['font'] = {'size': 20, 'face': 'arial', 'color': '#b91c1c', 'strokeWidth': 2, 'strokeColor': '#ffffff'}
            node['shadow'] = {'enabled': True}
            # node['fixed'] = True # Optional: fix subject in center

    nodes_json = json.dumps(nodes)
    edges_json = json.dumps(edges)
    
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Entity Graph - {subject_name}</title>
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style type="text/css">
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #f8fafc; height: 100vh; display: flex; flex-direction: column; }}
        .header {{ 
            padding: 20px; 
            background-color: white; 
            border-bottom: 1px solid #e2e8f0; 
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            z-index: 10;
        }}
        h1 {{ margin: 0; color: #1e293b; font-size: 24px; }}
        .legend {{ margin-top: 8px; font-size: 14px; color: #64748b; }}
        #mynetwork {{
            flex-grow: 1;
            width: 100%;
            background-color: #f8fafc;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Entity Relationship Graph: {subject_name}</h1>
        <div class="legend">
            <i class="fas fa-user" style="color: #3b82f6;"></i> Person &nbsp;
            <i class="fas fa-building" style="color: #22c55e;"></i> Organization &nbsp;
            <i class="fas fa-map-marker-alt" style="color: #ef4444;"></i> Location &nbsp;
            <i class="fas fa-calendar-alt" style="color: #f59e0b;"></i> Event &nbsp;
            <i class="fas fa-box" style="color: #6366f1;"></i> Product
        </div>
    </div>
    <div id="mynetwork"></div>
    <script type="text/javascript">
        var nodes = new vis.DataSet({nodes_json});
        var edges = new vis.DataSet({edges_json});

        var container = document.getElementById('mynetwork');
        var data = {{
            nodes: nodes,
            edges: edges
        }};
        var options = {{
            nodes: {{
                shape: 'dot',
                size: 20,
                font: {{
                    size: 14,
                    color: '#334155',
                    face: 'arial',
                    strokeWidth: 2,
                    strokeColor: '#ffffff'
                }},
                borderWidth: 2,
                shadow: true
            }},
            edges: {{
                width: 1,
                color: {{ color: '#cbd5e1', highlight: '#3b82f6', hover: '#3b82f6' }},
                smooth: {{ type: 'continuous', roundness: 0.5 }},
                arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},
                font: {{ size: 10, align: 'middle', color: '#64748b', background: 'white', strokeWidth: 0 }}
            }},
            groups: {{
                person: {{ 
                    color: {{ background: '#bfdbfe', border: '#3b82f6' }}, 
                    shape: 'icon', 
                    icon: {{ code: '\\uf007', color: '#3b82f6', face: '"Font Awesome 6 Free"', weight: 'bold', size: 30 }} 
                }},
                organization: {{ 
                    color: {{ background: '#bbf7d0', border: '#22c55e' }}, 
                    shape: 'icon', 
                    icon: {{ code: '\\uf1ad', color: '#22c55e', face: '"Font Awesome 6 Free"', weight: 'bold', size: 30 }} 
                }},
                location: {{ 
                    color: {{ background: '#fecaca', border: '#ef4444' }}, 
                    shape: 'icon', 
                    icon: {{ code: '\\uf3c5', color: '#ef4444', face: '"Font Awesome 6 Free"', weight: 'bold', size: 30 }} 
                }},
                event: {{ 
                    color: {{ background: '#fde68a', border: '#f59e0b' }}, 
                    shape: 'icon', 
                    icon: {{ code: '\\uf073', color: '#f59e0b', face: '"Font Awesome 6 Free"', weight: 'bold', size: 30 }} 
                }},
                product: {{ 
                    color: {{ background: '#e0e7ff', border: '#6366f1' }}, 
                    shape: 'icon', 
                    icon: {{ code: '\\uf466', color: '#6366f1', face: '"Font Awesome 6 Free"', weight: 'bold', size: 30 }} 
                }},
                other: {{ 
                    color: {{ background: '#e2e8f0', border: '#94a3b8' }},
                    shape: 'dot'
                }}
            }},
            physics: {{
                stabilization: true,
                barnesHut: {{
                    gravitationalConstant: -10000,
                    centralGravity: 0.3,
                    springLength: 150,
                    springConstant: 0.04,
                    damping: 0.09,
                    avoidOverlap: 0.2
                }},
                solver: 'barnesHut'
            }},
            interaction: {{
                hover: true,
                tooltipDelay: 200,
                navigationButtons: true,
                keyboard: true
            }},
            layout: {{
                improvedLayout: true
            }}
        }};
        var network = new vis.Network(container, data, options);
        
        // Focus on the subject node if it exists
        var subjectNodeId = null;
        var allNodes = nodes.get();
        for (var i = 0; i < allNodes.length; i++) {{
            if (allNodes[i].label && allNodes[i].label.toLowerCase() === "{subject_name}".toLowerCase()) {{
                subjectNodeId = allNodes[i].id;
                break;
            }}
        }}

        if (subjectNodeId) {{
            network.selectNodes([subjectNodeId]);
            network.focus(subjectNodeId, {{
                scale: 1.2,
                animation: {{
                    duration: 1000,
                    easingFunction: 'easeInOutQuad'
                }}
            }});
        }}
    </script>
</body>
</html>
    """
    return html_content
