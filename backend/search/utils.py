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
import random
import concurrent.futures
from urllib.parse import urlparse, quote
from dotenv import load_dotenv
from typing import Dict, Generator, List, Optional, Tuple
import datetime
import requests
from bs4 import BeautifulSoup


from ..ai_client import ADCGenerativeModel, DEFAULT_GEMINI_MODEL
from ..identity import content_fingerprint
from ..secret_manager import get_secret
from .prompts import (
    KYC_RISK_CATEGORIES_PROMPT_TEXT,
    KYC_ANALYZE_SINGLE_RESULT_PROMPT,
    KYC_OVERALL_SUMMARY_PROMPT,
    KYC_RISK_CATEGORY_OPTIONS_LIST_STR,
    KYC_BULK_ENTITY_EXTRACTION_PROMPT,
    KYC_GRAPH_EXTRACTION_PROMPT
)
from .screening import (
    RECOMMENDATION_ESCALATE,
    RECOMMENDATION_INCOMPLETE,
    RECOMMENDATION_NO_ADVERSE_MEDIA,
    RECOMMENDATION_REQUIRES_REVIEW,
    RECOMMENDATION_SANCTIONS_HIT,
    SearchCoverage,
    SearchOutcome,
    incomplete_screening_summary,
    search_quota,
)

# Import Spanner Client
from ..database.spanner_client import spanner_client
from .deduplication import SyndicationFilter, compute_url_hash

load_dotenv()

try:
    # Initialize Gemini 3.8 Flash via Vertex AI using Application Default Credentials (ADC)
    model = ADCGenerativeModel(model_name=DEFAULT_GEMINI_MODEL)
    lite_model = ADCGenerativeModel(model_name=DEFAULT_GEMINI_MODEL)
except Exception as e:
    logging.error(f"Failed to configure Vertex AI Gemini ADC client: {e}.")
    model = None
    lite_model = None

# --- Query Budgets (risk-tiered) ---
# Custom Search is capped at 10,000 queries/day. At 100 queries per subject only
# ~97 subjects/day are possible, so the budget scales with the diligence level.
QUERY_BUDGET_BY_TIER = {
    "standard": int(os.environ.get("KYC_QUERIES_STANDARD", 25)),   # CDD / routine onboarding
    "enhanced": int(os.environ.get("KYC_QUERIES_ENHANCED", 100)),  # EDD / PEP / high-risk
    "monitoring": int(os.environ.get("KYC_QUERIES_MONITORING", 10)),  # perpetual KYC delta scans
}
DEFAULT_DILIGENCE_TIER = os.environ.get("KYC_DEFAULT_TIER", "standard")
DEFAULT_NUM_QUERIES = QUERY_BUDGET_BY_TIER[DEFAULT_DILIGENCE_TIER]
DEFAULT_NUM_RESULTS = int(os.environ.get("KYC_NUM_RESULTS", 10))

# --- Concurrency (bounded to stay inside Custom Search and Vertex AI QPM limits) ---
SEARCH_MAX_WORKERS = int(os.environ.get("KYC_SEARCH_WORKERS", 8))
SCRAPE_MAX_WORKERS = int(os.environ.get("KYC_SCRAPE_WORKERS", 12))
ANALYSIS_MAX_WORKERS = int(os.environ.get("KYC_ANALYSIS_WORKERS", 6))

# Maximum article text passed into the per-finding risk prompt. The snippet alone
# rarely carries the DOB/employer needed to confirm or reject an identity match.
MAX_ARTICLE_CHARS_FOR_ANALYSIS = int(os.environ.get("KYC_MAX_ARTICLE_CHARS", 30000))

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

def google_web_search(
    query: str,
    api_key: str,
    cse_id: str,
    num_results: int = 10,
    recency_days: int = 0,
    max_retries: int = 3,
) -> SearchOutcome:
    """
    Performs a thread-safe Google Custom Search JSON API request.

    Returns a SearchOutcome that distinguishes "completed with zero results" from
    "failed". Callers MUST NOT treat a failure as an empty result set - see
    backend/search/screening.py for why.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            # Hold the aggregate request rate below the per-second limit; the thread
            # pool would otherwise burst all queries simultaneously and trip a 429.
            search_quota.throttle()
            search_params = {'q': query, 'cx': cse_id, 'key': api_key, 'num': min(num_results, 10)}
            if recency_days and isinstance(recency_days, int) and recency_days > 0:
                search_params['dateRestrict'] = f'd{recency_days}'
            resp = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params=search_params,
                timeout=15,
            )

            # Quota / rate limiting needs a real backoff window; a flat 1s sleep does
            # not clear a per-minute quota and simply burns the remaining retries.
            if resp.status_code in (429, 503):
                retry_after = resp.headers.get("Retry-After")
                if retry_after and str(retry_after).isdigit():
                    wait = int(retry_after)
                else:
                    wait = (2 ** attempt) * 5 + random.uniform(0, 1.5)
                last_error = f"HTTP {resp.status_code} (rate limited / unavailable)"
                logging.warning(
                    f"Custom Search rate limited for '{query[:60]}'. "
                    f"Backing off {wait:.1f}s (attempt {attempt + 1}/{max_retries})."
                )
                if attempt + 1 < max_retries:
                    time.sleep(wait)
                continue

            resp.raise_for_status()
            payload = resp.json()
            return SearchOutcome(query=query, items=payload.get('items', []))

        except Exception as e:
            last_error = str(e)
            logging.warning(f"Attempt {attempt + 1} for google_web_search '{query[:60]}' failed: {e}")
            if attempt + 1 < max_retries:
                time.sleep((2 ** attempt) + random.uniform(0, 0.5))

    logging.error(f"All {max_retries} attempts failed for google_web_search '{query[:60]}': {last_error}")
    return SearchOutcome(query=query, items=[], error=last_error or "unknown error")


def perform_google_web_searches(
    queries: list,
    num_results: int,
    recency_days: int,
) -> Tuple[List[Dict], SearchCoverage]:
    """
    Executes search queries in parallel using credentials from Google Secret Manager.

    Returns (deduplicated_results, coverage). The coverage object tells the caller how
    much of the intended search surface was actually reached, so that a partial or
    total source failure can be surfaced rather than silently producing zero findings.
    """
    api_key = get_secret("GOOGLE_SEARCH_API_KEY") or get_secret("GOOGLE_API_KEY")
    cse_id = get_secret("GOOGLE_CSE_ID")

    if not api_key or not cse_id:
        logging.error("GOOGLE_SEARCH_API_KEY / GOOGLE_API_KEY or GOOGLE_CSE_ID is not configured.")
        # Credentials missing is a total failure, not an empty result set.
        return [], SearchCoverage(
            attempted=len(queries),
            succeeded=0,
            errors=["Search credentials are not configured."],
        )

    all_results: List[Dict] = []
    coverage = SearchCoverage(attempted=len(queries), succeeded=0)

    # Reserve daily quota up front. Any shortfall is recorded as FAILED coverage:
    # queries we never got to run are not queries that found nothing.
    granted, remaining = search_quota.try_reserve(len(queries))
    if granted < len(queries):
        shortfall = len(queries) - granted
        logging.error(
            f"Custom Search daily quota exhausted: {shortfall} of {len(queries)} queries "
            f"could not be executed ({remaining} remaining today)."
        )
        coverage.errors.append(
            f"Daily search quota exhausted; {shortfall} queries were not executed."
        )
        queries = queries[:granted]

    if not queries:
        return [], coverage

    with concurrent.futures.ThreadPoolExecutor(max_workers=SEARCH_MAX_WORKERS) as executor:
        future_to_query = {
            executor.submit(google_web_search, query, api_key, cse_id, num_results, recency_days): query
            for query in queries
        }
        for future in concurrent.futures.as_completed(future_to_query):
            query = future_to_query[future]
            try:
                outcome = future.result()
                if outcome.succeeded:
                    coverage.succeeded += 1
                    all_results.extend(outcome.items)
                else:
                    coverage.errors.append(f"{query[:60]}: {outcome.error}")
            except Exception as exc:
                logging.error(f"Query '{query[:60]}' generated an exception: {exc}")
                coverage.errors.append(f"{query[:60]}: {exc}")

    if not coverage.is_complete:
        logging.error(
            f"DEGRADED SEARCH COVERAGE: {coverage.describe()} "
            "Results from this run are not sufficient for an automated clearance."
        )

    # De-duplicate results based on link
    unique_results = {result['link']: result for result in all_results if result.get('link')}.values()
    return list(unique_results), coverage

def generate_entity_graph_with_gemini(findings, subject_name):
    """
    Generates a knowledge graph (nodes and edges) from the findings using Gemini 3.8 Flash via ADC.
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
        
        # Use Gemini 3.8 Flash via Vertex AI ADC
        graph_model = model or ADCGenerativeModel(model_name=DEFAULT_GEMINI_MODEL)
        response = graph_model.generate_content(prompt)
        
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

    # 2. Fallback: ScrapingBee (loaded from Google Secret Manager)
    logging.info(f"Direct scrape failed for {url}. Falling back to ScrapingBee.")
    api_key = get_secret("SB_API_KEY")
    if not api_key:
        logging.error("ScrapingBee API key (SB_API_KEY) not found in Secret Manager or environment.")
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

def generate_search_queries_with_gemini(
    filters: Dict, prompt_template: str, max_retries: int = 3
) -> Tuple[List[str], Dict]:
    """Uses Gemini to brainstorm search queries.

    Returns (queries, usage_info). Every caller unpacks two values, so every
    return path must supply two -- the previous bare `return []` raised
    ValueError at the call site whenever the model was unavailable.
    """
    if not lite_model:
        logging.error("Gemini lite_model not initialized.")
        return [], {}

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
        num_queries=filters.get('num_queries') or DEFAULT_NUM_QUERIES,
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

def _safe_int(value, default: int = 0) -> int:
    """
    Coerces a model-supplied score to an int without ever raising.

    The model intermittently returns "N/A", None, "8/10" or a float. Letting that
    raise inside the relevance filter previously corrupted the entire summary, so
    the coercion is deliberately total.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        if match:
            try:
                return int(match.group())
            except ValueError:
                return default
    return default


def _content_is_usable(text: Optional[str]) -> bool:
    """True when scraped text is substantive enough to support identity matching."""
    if not text:
        return False
    if text.startswith("Scraping failed"):
        return False
    return len(text.strip()) >= 200


def _analyze_single_kyc_result(result: Dict, filters: Dict, is_priority: bool, pre_scraped_content: str = None, max_retries: int = 3) -> Dict:
    """Worker function to analyze one search result with Gemini."""
    subject_name = filters.get('subject_name')
    link = result.get('link', 'N/A')

    # Scrape BEFORE prompting. The article body is the primary evidence for both
    # identity resolution and severity; the snippet is a fallback, not the input.
    full_content_text = pre_scraped_content if pre_scraped_content is not None else scrape_url_content(link)
    content_usable = _content_is_usable(full_content_text)

    if content_usable:
        article_for_prompt = full_content_text[:MAX_ARTICLE_CHARS_FOR_ANALYSIS]
        if len(full_content_text) > MAX_ARTICLE_CHARS_FOR_ANALYSIS:
            article_for_prompt += "\n\n[... article truncated for length ...]"
    else:
        article_for_prompt = (
            "ARTICLE TEXT UNAVAILABLE - retrieval failed or returned no usable content. "
            "Assess from the title and snippet only and cap identity confidence at 2."
        )

    # Derive Media House from URL
    domain = urlparse(link).netloc
    media_house = domain.replace('www.', '') if domain else "Unknown"
    scraping_status = "Success" if content_usable else "Failed"

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
        link=link,
        full_article_text=article_for_prompt,
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

            identity_confidence = _safe_int(analysis.get("identity_confidence"), 0)
            match_status = analysis.get("match_status", "Possible")

            # An unreadable article cannot support a confirmed identity, whatever the
            # model asserts. Downgrade rather than trust an unevidenced match.
            if not content_usable:
                identity_confidence = min(identity_confidence, 2)
                if match_status == "Confirmed":
                    match_status = "HITL"

            return {
                "source_title": result.get('title', 'N/A'),
                "url": link,
                "media_house": media_house,
                "snippet": result.get('snippet', 'N/A'),
                "full_content": full_content_text,
                "gemini_insight": analysis.get("gemini_insight", "Error in analysis."),
                "risk_level": analysis.get("risk_level", "Unknown"),
                "risk_category": analysis.get("risk_category", "Uncategorized"),
                "match_status": match_status,
                "media_house_reputation": analysis.get("media_house_reputation", "Unknown"),
                "scraping_status": scraping_status,
                "relevance_score": _safe_int(analysis.get("relevance_score"), 0),
                "identity_confidence": identity_confidence,
                "risk_severity": _safe_int(analysis.get("risk_severity"), 0),
                "identity_evidence": analysis.get("identity_evidence", "None found"),
                "content_available": content_usable,
                "analysis_status": "Success",
                "source_date": final_date,
                "source_type": "priority_search" if is_priority else "gemini_search",
                "usage_metadata": usage_info
            }
        except (json.JSONDecodeError, Exception) as e:
            logging.warning(f"Failed to analyze result for '{subject_name}' on attempt {attempt+1}: {e}")
            if attempt + 1 < max_retries:
                time.sleep(1)

    # Fallback on total analysis failure.
    # NOTE: this is an UNSCREENED article, not a clean one. It is routed to human
    # review rather than being silently scored 0 and filtered away.
    logging.error(f"Analysis permanently failed for {link}; routing to human review.")
    return {
        "source_title": result.get('title', 'N/A'),
        "url": link,
        "media_house": media_house,
        "snippet": result.get('snippet', 'N/A'),
        "full_content": full_content_text,
        "gemini_insight": "AI analysis failed after multiple retries. This article was NOT screened and requires manual review.",
        "risk_level": "Unknown",
        "risk_category": "Uncategorized",
        "match_status": "HITL",
        "media_house_reputation": "Unknown",
        "scraping_status": scraping_status,
        "relevance_score": 0,
        "identity_confidence": 0,
        "risk_severity": 0,
        "identity_evidence": "None found",
        "content_available": content_usable,
        "analysis_status": "Failed",
        "source_date": result.get('pagemap', {}).get('metatags', [{}])[0].get('article:published_time', 'N/A').split('T')[0],
        "source_type": "priority_search" if is_priority else "gemini_search"
    }


# --- Pricing Constants (USD per 1M tokens) ---
# Estimates based on public pricing
PRICING = {
    'gemini-3.8-flash': {'input': 0.075, 'output': 0.30},
    'gemini-3-flash-preview': {'input': 0.075, 'output': 0.30},
    'gemini-1.5-flash': {'input': 0.075, 'output': 0.30},
    'gemini-1.5-pro': {'input': 3.50, 'output': 10.50}
}

def calculate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Calculates estimated cost for a request."""
    # Simple mapping to handle version suffixes
    base_model = 'gemini-3.8-flash' if 'flash' in model_name.lower() else 'gemini-1.5-pro'
    rates = PRICING.get(base_model, {'input': 0, 'output': 0})
    
    input_cost = (input_tokens / 1_000_000) * rates['input']
    output_cost = (output_tokens / 1_000_000) * rates['output']
    return input_cost + output_cost

def analyze_kyc_results(
    search_results: List[Dict],
    filters: Dict,
    priority_links: set = None,
    previous_findings: List[Dict] = None,
    syndication_filter: SyndicationFilter = None,
    existing_url_hashes: dict = None,
    existing_url_fingerprints: dict = None,
    coverage: Optional[SearchCoverage] = None,
    session_usage: Optional[Dict] = None,
    watchlist_screening: Optional[Dict] = None,
) -> Generator[Dict, None, None]:
    """
    Analyzes search results with a 3-phase pipeline:
    1. Scrape & Hash (Parallel)
    2. Deduplicate (Sequential against LSH and Existing Hashes)
    3. AI Analysis (Parallel on Unique items)

    `coverage` describes how much of the intended search surface actually succeeded.
    It is required for a defensible verdict: without it the summary cannot tell
    "nothing was found" apart from "nothing could be searched".

    `session_usage` is the caller's cost accumulator. It is mutated in place so the
    running total is monotonic across the whole run rather than restarting here.
    """
    if not model or not lite_model:
        logging.error("Gemini models not initialized.")
        return

    subject_name = filters.get('subject_name')
    priority_links = priority_links or set()
    previous_findings = previous_findings or []
    existing_url_hashes = existing_url_hashes or {}
    existing_url_fingerprints = existing_url_fingerprints or {}

    # Shared accumulator: the caller owns the running total.
    if session_usage is None:
        session_usage = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    
    # --- Phase 1: Scrape & Hash ---
    logging.info("Phase 1: Scraping and Hashing...")
    scraped_data_list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=SCRAPE_MAX_WORKERS) as scrape_executor:
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
    syndicated_items = []
    final_processed_results = []

    for item in scraped_data_list:
        # 1. Exact URL Deduplication (Metadata Layer)
        #
        # A URL match alone is NOT sufficient grounds to skip. News organisations
        # amend, correct and retract stories in place: the same URL that once
        # carried "X charged with fraud" may now carry "charges dropped", or vice
        # versa. Skipping on URL alone pins the original verdict forever, which is
        # both a false-positive risk (stale allegation) and a false-negative risk
        # (escalation we never saw). So we compare the stored content fingerprint
        # and re-analyse when it has moved.
        u_hash = item.get('url_hash')
        item['content_changed_at'] = None

        if u_hash and u_hash in existing_url_hashes:
            previous_fingerprint = existing_url_fingerprints.get(u_hash)
            current_fingerprint = content_fingerprint(item.get('full_content'))

            if not current_fingerprint:
                # We could not read the page this time. We have no evidence it
                # changed, so keep the stored assessment rather than overwrite a
                # good analysis with an empty one.
                logging.info(f"Skipping known duplicate URL (content unreadable): {item['link']}")
                continue

            if previous_fingerprint and previous_fingerprint == current_fingerprint:
                logging.info(f"Skipping known duplicate URL (content unchanged): {item['link']}")
                continue

            if previous_fingerprint is None:
                # Legacy row written before fingerprints were persisted. Treat it
                # as unchanged to avoid re-analysing the whole back catalogue on
                # the first run after deploy; the next genuine edit will be caught.
                logging.info(f"Skipping known duplicate URL (no stored fingerprint): {item['link']}")
                continue

            # Content changed. Re-analyse and overwrite the SAME row so the
            # reviewer sees one current assessment per URL, not two contradictory ones.
            logging.warning(
                f"Content changed under stable URL, re-analysing: {item['link']} "
                f"({previous_fingerprint[:12]} -> {current_fingerprint[:12]})"
            )
            item['finding_id'] = existing_url_hashes[u_hash]
            item['content_changed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            item['is_content_revision'] = True
            item['previous_content_fingerprint'] = previous_fingerprint
            item['is_priority'] = True  # a changed story deserves a fresh look
            unique_items_to_analyze.append(item)
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
            # Held back until Phase 3 completes so the stub can inherit the parent's
            # assessed risk. Emitting "Low" here would let a syndicated wire report of
            # a fraud conviction be filed as immaterial purely because it was reprinted.
            syndicated_items.append(item)
        else:
            unique_items_to_analyze.append(item)

    # --- Phase 3: AI Analysis ---
    logging.info(f"Phase 3: Analyzing {len(unique_items_to_analyze)} unique items...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=ANALYSIS_MAX_WORKERS) as ai_executor:
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

                # Content-revision metadata. An article that changed under a
                # stable URL is always routed to a human: the machine can tell
                # that the text moved, but not whether the move was a typo fix
                # or a retraction, and that distinction changes the verdict.
                if inp.get('is_content_revision'):
                    res['is_content_revision'] = True
                    res['content_changed_at'] = inp.get('content_changed_at')
                    res['previous_content_fingerprint'] = inp.get('previous_content_fingerprint')
                    res['match_status'] = 'HITL'
                    res['review_reason'] = (
                        'Source content changed after the original assessment '
                        '(possible correction, update or retraction).'
                    )
                
                # Usage handling
                usage = res.pop('usage_metadata', None)
                if usage:
                    session_usage["input_tokens"] += usage.get('input_tokens', 0)
                    session_usage["output_tokens"] += usage.get('output_tokens', 0)
                    cost = calculate_cost(DEFAULT_GEMINI_MODEL, usage.get('input_tokens', 0), usage.get('output_tokens', 0))
                    session_usage["cost"] += cost
                    yield {"type": "usage_update", "data": session_usage}
                
                final_processed_results.append(res)
                yield {"type": "result", "data": res}
            except Exception as exc:
                logging.error(f"Analysis failed: {exc}")

    # --- Phase 3b: Emit syndicated duplicates, inheriting parent risk ---
    # A duplicate is a duplicate of *something*. Its materiality is the parent's.
    parent_lookup = {r.get('finding_id'): r for r in final_processed_results if r.get('finding_id')}
    for prev in previous_findings:
        if prev.get('finding_id') and prev['finding_id'] not in parent_lookup:
            parent_lookup[prev['finding_id']] = prev

    for item in syndicated_items:
        parent = parent_lookup.get(item.get('parent_finding_id')) or {}
        inherited_risk = parent.get('risk_level', 'Unknown')
        inherited_category = parent.get('risk_category', 'Uncategorized')
        inherited_score = _safe_int(parent.get('relevance_score'), 0)
        inherited_match = parent.get('match_status', 'Unknown')

        if parent:
            insight = (
                f"Syndicated copy of an already-assessed article. "
                f"Inherited assessment: {inherited_risk} risk / {inherited_category}."
            )
        else:
            # Parent not in this batch and not in history - we cannot claim it is benign.
            insight = (
                "Syndicated duplicate whose source article could not be located in this "
                "run. Materiality is unverified and requires manual review."
            )
            inherited_match = "HITL"

        stub = {
            "finding_id": item['finding_id'],
            "source_title": item['original_item'].get('title', 'N/A'),
            "url": item['link'],
            "media_house": (urlparse(item['link']).netloc or "Unknown").replace('www.', ''),
            "snippet": item['original_item'].get('snippet', 'N/A'),
            "full_content": item['full_content'],
            "gemini_insight": insight,
            "risk_level": inherited_risk,
            "risk_category": inherited_category,
            "match_status": inherited_match,
            "media_house_reputation": "Unknown",
            "scraping_status": "Success" if _content_is_usable(item['full_content']) else "Failed",
            "relevance_score": inherited_score,
            "identity_confidence": _safe_int(parent.get('identity_confidence'), 0),
            "risk_severity": _safe_int(parent.get('risk_severity'), 0),
            "identity_evidence": parent.get('identity_evidence', 'Inherited from parent article'),
            "content_available": _content_is_usable(item['full_content']),
            "analysis_status": "Inherited" if parent else "Unresolved",
            "source_date": item['original_item'].get('pagemap', {}).get('metatags', [{}])[0].get('article:published_time', 'N/A').split('T')[0],
            "source_type": "syndicated_duplicate",
            "url_hash": item['url_hash'],
            "content_hash": item['content_hash'],
            "is_syndicated": True,
            "parent_finding_id": item['parent_finding_id'],
        }
        final_processed_results.append(stub)
        yield {"type": "result", "data": stub}

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
            unique_new_findings = [r for r in final_processed_results if not r.get('is_syndicated')]
            all_findings_for_context = unique_new_findings + previous_findings

            relevant_findings = [r for r in all_findings_for_context if is_material_finding(r)]
            review_queue = build_human_review_queue(all_findings_for_context)

            logging.info(
                f"Summary input: {len(relevant_findings)} material of "
                f"{len(all_findings_for_context)} findings; {len(review_queue)} flagged for human review."
            )

            summary_data = generate_kyc_summary_logic(
                subject_name,
                relevant_findings,
                model,
                safety_settings,
                session_usage,
                coverage=coverage,
                watchlist_screening=watchlist_screening,
            )

            if summary_data is None:
                logging.error("Summary generation returned None (likely AI error or block).")
                summary_data = {
                    "risk_score": "Unknown",
                    "summary": "The AI could not generate a summary at this time (Possible content block or service overload). Please check individual findings.",
                    "key_findings": [],
                    "recommendation": RECOMMENDATION_INCOMPLETE,
                    "reasoning": "AI Generation Error",
                }

            summary_data = finalize_summary(
                summary_data,
                review_queue,
                coverage,
                watchlist_screening=watchlist_screening,
            )

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
                "requires_human_review": [
                    {"reason": "Summary generation failed", "detail": str(e)}
                ],
                "recommendation": RECOMMENDATION_INCOMPLETE,
                "reasoning": "System error during generation.",
                "screening_coverage": coverage.to_dict() if coverage else None,
                "watchlist_screening": watchlist_screening,
            }}


# --- Materiality and human-review routing -----------------------------------
# A single collapsed 0-10 score cannot express "serious allegation, weak source" and
# "trivial allegation, strong source" differently, yet those demand opposite handling.
# Materiality is therefore evaluated on severity AND identity, not on the score alone.

MATERIALITY_SCORE_THRESHOLD = int(os.environ.get("KYC_MATERIALITY_THRESHOLD", 5))


def is_material_finding(finding: Dict) -> bool:
    """
    True when a finding must reach the summarizer.

    Deliberately over-inclusive. A false positive costs an analyst a minute of
    reading; a false negative is an unreported financial crime typology.
    """
    match_status = (finding.get("match_status") or "").strip()

    # An explicit non-match is the only safe way to drop a finding.
    if match_status == "Negative":
        return False

    # Anything we failed to screen must be seen by a human.
    if finding.get("analysis_status") in ("Failed", "Unresolved"):
        return True
    if match_status == "HITL":
        return True

    if _safe_int(finding.get("relevance_score"), 0) >= MATERIALITY_SCORE_THRESHOLD:
        return True

    # Severity route: a serious allegation survives source-credibility penalties.
    if (finding.get("risk_level") or "") in ("High", "Medium"):
        return True
    if _safe_int(finding.get("risk_severity"), 0) >= 3:
        return True

    return False


def build_human_review_queue(findings: List[Dict]) -> List[Dict]:
    """Collects findings a machine must not dispose of on its own."""
    queue = []
    for f in findings:
        url = f.get("url", "N/A")
        title = f.get("source_title", "Untitled")
        finding_id = f.get("finding_id") or str(uuid.uuid5(uuid.NAMESPACE_URL, str(url)))
        match_status = (f.get("match_status") or "").strip()
        severity = _safe_int(f.get("risk_severity"), 0)
        identity = _safe_int(f.get("identity_confidence"), 0)

        if f.get("is_content_revision"):
            # Distinct from an identity question: we know it is the subject, we
            # no longer know whether the story still says what it said. Only a
            # human can read a correction notice and decide if the original
            # adverse finding stands, was softened, or was retracted outright.
            queue.append({
                "finding_id": finding_id,
                "item_type": "FINDING",
                "title": title,
                "reason": "Source content changed after assessment",
                "detail": (
                    f"'{title}' was amended at its original URL since the last "
                    "screening. Re-read the source to confirm whether the adverse "
                    "content was corrected, updated or retracted."
                ),
                "citations": [url],
            })
        elif f.get("analysis_status") in ("Failed", "Unresolved"):
            queue.append({
                "finding_id": finding_id,
                "item_type": "FINDING",
                "title": title,
                "reason": "Article was not screened",
                "detail": f"Automated analysis did not complete for '{title}'.",
                "citations": [url],
            })
        elif match_status == "HITL":
            queue.append({
                "finding_id": finding_id,
                "item_type": "FINDING",
                "title": title,
                "reason": "Unresolved identity match",
                "detail": f"'{title}' could not be confirmed or excluded as the subject.",
                "citations": [url],
            })
        elif severity >= 3 and identity < 3:
            queue.append({
                "finding_id": finding_id,
                "item_type": "FINDING",
                "title": title,
                "reason": "Severe allegation, weak identity evidence",
                "detail": (
                    f"'{title}' alleges serious conduct but identity confidence is "
                    f"{identity}/4. A human must confirm whether this is the subject."
                ),
                "citations": [url],
            })
        elif severity >= 3 and not f.get("content_available", True):
            queue.append({
                "finding_id": finding_id,
                "item_type": "FINDING",
                "title": title,
                "reason": "Severe allegation, article text unavailable",
                "detail": f"'{title}' could not be retrieved; assessment rests on the snippet alone.",
                "citations": [url],
            })
    return queue


def finalize_summary(
    summary_data: Dict,
    review_queue: List[Dict],
    coverage: Optional[SearchCoverage],
    watchlist_screening: Optional[Dict] = None,
) -> Dict:
    """
    Applies non-negotiable post-conditions to a model-generated summary.

    The model is instructed to follow these rules, but instruction is not control.
    Anything that determines a compliance outcome is enforced in code.
    """
    summary_data = dict(summary_data or {})

    # 0. Prepend any Watchlist / Sanctions hits to the deterministic review queue.
    watchlist_queue_items: List[Dict] = []
    if isinstance(watchlist_screening, dict):
        summary_data["watchlist_screening"] = watchlist_screening
        for hit in (watchlist_screening.get("hits") or []):
            if not isinstance(hit, dict):
                continue
            watchlist_queue_items.append({
                "finding_id": hit.get("hit_id"),
                "item_type": "WATCHLIST_HIT",
                "title": f"[{hit.get('list_id')}] {hit.get('primary_name')}",
                "reason": (
                    f"Sanctions / Watchlist match ({hit.get('list_name')} — "
                    f"{hit.get('match_strength')} {float(hit.get('match_score', 0)) * 100:.0f}%)"
                ),
                "detail": hit.get("explanation") or "",
                "citations": [f"{hit.get('list_id')}:{hit.get('entity_id')}"],
            })

    # 1. Merge the deterministic review queue with anything the model flagged.
    model_flags = summary_data.get("requires_human_review") or []
    if not isinstance(model_flags, list):
        model_flags = []
    seen = set()
    merged = []
    for entry in watchlist_queue_items + list(model_flags) + list(review_queue):
        if not isinstance(entry, dict):
            continue
        key = (entry.get("reason"), tuple(entry.get("citations") or []))
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)
    summary_data["requires_human_review"] = merged

    # 2. Strip any decision verb the model may still emit.
    recommendation = str(summary_data.get("recommendation", "")).strip()
    banned = {"accept", "reject", "approve", "deny", "clear", "cleared", "decline"}
    if recommendation.lower() in banned or not recommendation:
        has_findings = bool(summary_data.get("key_findings"))
        recommendation = (
            RECOMMENDATION_REQUIRES_REVIEW if has_findings else RECOMMENDATION_NO_ADVERSE_MEDIA
        )
        logging.warning(
            "Model returned a decision verb as a recommendation; rewritten to '%s'.",
            recommendation,
        )

    # 3. Unresolved items always outrank a clean recommendation.
    if merged and recommendation == RECOMMENDATION_NO_ADVERSE_MEDIA:
        recommendation = RECOMMENDATION_REQUIRES_REVIEW
        summary_data["reasoning"] = (
            f"{summary_data.get('reasoning', '')} "
            f"{len(merged)} item(s) require human review."
        ).strip()

    # 3b. Enforce Watchlist / Sanctions screening outcomes.
    if isinstance(watchlist_screening, dict):
        wl_cov = watchlist_screening.get("coverage") or {}
        confirmed_wl = int(watchlist_screening.get("confirmed_hits") or 0)
        strong_wl = int(watchlist_screening.get("strong_hits") or 0)
        potential_wl = int(watchlist_screening.get("potential_hits") or 0)

        if confirmed_wl > 0 or strong_wl > 0:
            recommendation = RECOMMENDATION_SANCTIONS_HIT
            summary_data["risk_score"] = "Critical"
            alert_prefix = (
                f"SANCTIONS / WATCHLIST MATCH DETECTED ({confirmed_wl} confirmed, "
                f"{strong_wl} strong hit(s) across authoritative designations). "
            )
            curr_summary = str(summary_data.get("summary", ""))
            if not curr_summary.startswith("SANCTIONS / WATCHLIST MATCH"):
                summary_data["summary"] = alert_prefix + curr_summary
        elif potential_wl > 0:
            if summary_data.get("risk_score") in (None, "Low"):
                summary_data["risk_score"] = "Medium"
            if recommendation == RECOMMENDATION_NO_ADVERSE_MEDIA:
                recommendation = RECOMMENDATION_REQUIRES_REVIEW

        if wl_cov and not wl_cov.get("is_complete", True):
            recommendation = RECOMMENDATION_INCOMPLETE
            summary_data["risk_score"] = "Unknown"
            summary_data["summary"] = (
                f"INCOMPLETE WATCHLIST SCREENING. {wl_cov.get('description', '')} "
                + str(summary_data.get("summary", ""))
            )

    # 4. Degraded coverage overrides everything below it.
    if coverage is not None:
        summary_data["screening_coverage"] = coverage.to_dict()
        if not coverage.is_complete:
            recommendation = RECOMMENDATION_INCOMPLETE
            summary_data["risk_score"] = "Unknown"
            summary_data["summary"] = (
                f"INCOMPLETE SCREENING. {coverage.describe()} "
                "The findings below are partial and MUST NOT be read as a clean result. "
                + str(summary_data.get("summary", ""))
            )

    summary_data["recommendation"] = recommendation
    return summary_data

def generate_kyc_summary_logic(
    subject_name,
    relevant_findings,
    model,
    safety_settings,
    session_usage,
    coverage: Optional[SearchCoverage] = None,
    watchlist_screening: Optional[Dict] = None,
):
    """
    Reusable logic for generating KYC summary with Map-Reduce for large contexts.

    `coverage` is what separates "we searched and found nothing" from "we could not
    search". Without it, an expired API key renders as a clean customer.
    """
    # The empty-findings path is the single most dangerous branch in this system:
    # it is the one that declares a subject clean. It must never be reachable from
    # an infrastructure failure OR when a sanctions watchlist hit exists.
    if not relevant_findings:
        if coverage is not None and not coverage.is_complete:
            logging.error(
                "Refusing to issue a clean result for %s: %s", subject_name, coverage.describe()
            )
            res = incomplete_screening_summary(subject_name, coverage)
            return finalize_summary(res, [], coverage, watchlist_screening=watchlist_screening)

        logging.info("No material findings for summary.")
        base_clean = {
            "risk_score": "Low",
            "summary": (
                f"No adverse media was identified for {subject_name} in this open-source scan. "
                "This reflects the sources searched in this run only; it is not an assertion "
                "that no adverse information exists."
            ),
            "key_findings": [],
            "requires_human_review": [],
            "recommendation": RECOMMENDATION_NO_ADVERSE_MEDIA,
            "reasoning": "No adverse media returned by the sources searched.",
            "screening_coverage": coverage.to_dict() if coverage else None,
        }
        return finalize_summary(base_clean, [], coverage, watchlist_screening=watchlist_screening)

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
          "requires_human_review": [
            {{
              "reason": "Short label.",
              "detail": "What a human must resolve.",
              "citations": ["url1"]
            }}
          ],
          "recommendation": "No Adverse Media Found" | "Adverse Media - Requires Review" | "Adverse Media - Escalate" (Choose ONE. Never output Accept/Reject/Approve/Deny.),
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
        
        # Apply the SAME materiality rule as the live pipeline. Two different filters
        # on the same findings would let a regenerated summary contradict the original.
        relevant_findings = [f for f in findings if is_material_finding(f)]
        review_queue = build_human_review_queue(findings)

        logging.info(
            f"Regenerating summary for {subject_name}: {len(relevant_findings)} material "
            f"of {len(findings)} findings, {len(review_queue)} for human review."
        )

        session_usage = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}

        # Coverage is not re-derivable here (the search already happened), so the
        # persisted coverage from the original run is reused when present.
        prior_summary = subject.get('summary_data') or {}
        if isinstance(prior_summary, str):
            try:
                prior_summary = json.loads(prior_summary)
            except (json.JSONDecodeError, TypeError):
                prior_summary = {}
        prior_coverage = prior_summary.get('screening_coverage') if isinstance(prior_summary, dict) else None

        coverage = None
        if isinstance(prior_coverage, dict):
            coverage = SearchCoverage(
                attempted=_safe_int(prior_coverage.get('queries_attempted'), 0),
                succeeded=_safe_int(prior_coverage.get('queries_succeeded'), 0),
                errors=list(prior_coverage.get('sample_errors') or []),
            )

        summary_data = generate_kyc_summary_logic(
            subject_name,
            relevant_findings,
            model,
            safety_settings,
            session_usage,
            coverage=coverage,
        )

        if summary_data:
             summary_data = finalize_summary(summary_data, review_queue, coverage)
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
