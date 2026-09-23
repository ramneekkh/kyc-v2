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

import logging
from typing import List, Dict, Optional

# Use relative imports to access functions and data from the 'search' module
from ..search.utils import generate_search_queries_with_gemini, perform_google_web_searches
from ..search.prompts import SEARCH_QUERY_BRAINSTORMING_PROMPT
from ..search.business_logic import FDI_SIGNALS

def agent_find_leads(keywords: List[str], region: str = 'ASEAN', signal_types: Optional[List[str]] = None) -> List[Dict]:
    """
    A specialized tool for the ADK agent to discover investment leads based on FDI signals.
    
    This tool first uses an AI model to brainstorm a series of effective Google search
    queries based on the provided keywords, region, and optional FDI signal types.
    It then executes these queries in parallel using the Google Custom Search API
    to gather recent news articles, press releases, and other public information.
    The function returns a list of raw, unprocessed search result objects for the
    agent to analyze, synthesize, and present to the user.

    Args:
        keywords (List[str]): A list of strings representing the core topics of the search.
                              These should be related to industries (e.g., "semiconductors"),
                              technologies (e.g., "EV charging"), or specific companies.
        region (str, optional): The target geographic area for the search.
                                Defaults to 'ASEAN'.
        signal_types (List[str], optional): A specific list of Foreign Direct Investment (FDI)
                                            signal types to focus the search on. If None,
                                            a default comprehensive list of all signals will be used.

    Returns:
        List[Dict]: A list of raw search result dictionaries from the Google Custom Search API.
                    Each dictionary contains details like 'title', 'link', and 'snippet'.
                    Returns an empty list if no results are found or if an error occurs.
    """
    logging.info(f"Agent starting lead discovery. Keywords: {keywords}, Region: {region}, Signals: {signal_types}")
    
    if not signal_types:
        all_signals = list(FDI_SIGNALS.get("Tier 1", {}).keys()) + \
                      list(FDI_SIGNALS.get("Tier 2", {}).keys()) + \
                      list(FDI_SIGNALS.get("Tier 3", {}).keys())
        logging.info(f"No signal types provided. Using default list: {all_signals}")
        signal_types = all_signals

    filters = {
        "keywords": keywords,
        "region": region,
        "signal_types": signal_types,
        "num_queries": 10,
        "num_results": 10,
        "recency_days": 30,
    }

    search_queries = generate_search_queries_with_gemini(filters, SEARCH_QUERY_BRAINSTORMING_PROMPT)
    if not search_queries:
        logging.warning("Agent could not brainstorm any search queries for lead discovery.")
        return []
    
    logging.info(f"Agent brainstormed lead discovery queries: {search_queries}")
    search_results = perform_google_web_searches(search_queries, filters['num_results'], filters['recency_days'])
    logging.info(f"Agent found {len(search_results)} raw results for lead discovery.")
    return search_results

def generic_search(query: str) -> List[Dict]:
    """
    A general-purpose web search tool for the ADK agent.
    
    Use this tool to find information on any topic, such as finding contact information
    for a person at a specific company, looking up financial data, or researching a
    particular market trend. The tool will generate its own targeted search queries
    based on your natural language input.

    Args:
        query (str): A natural language question or topic to search for.
                     For example: "who is the head of supply chain at Lego in Singapore?"
                     or "latest news on semiconductor manufacturing in Vietnam".

    Returns:
        List[Dict]: A list of raw search result dictionaries from the Google Custom Search API,
                    ready for the agent to analyze and summarize.
    """
    logging.info(f"Agent starting generic search for: '{query}'")
    
    # For a generic search, the 'filters' are simpler. We can treat the user's
    # entire query as the primary "keyword".
    filters = {
        "keywords": [query], # Use the whole query as the keyword
        "region": "", # No specific region for a generic search
        "signal_types": [], # Not applicable for generic search
        "num_queries": 3,  # Generate a few targeted queries
        "num_results": 5,
        "recency_days": 365, # Look back a full year for general info
    }

    # We can reuse the same brainstorming prompt, but the inputs make it generic.
    search_queries = generate_search_queries_with_gemini(filters, SEARCH_QUERY_BRAINSTORMING_PROMPT)
    if not search_queries:
        logging.warning("Agent could not brainstorm any search queries for the generic request.")
        return []

    logging.info(f"Agent brainstormed generic queries: {search_queries}")
    search_results = perform_google_web_searches(search_queries, filters['num_results'], filters['recency_days'])
    logging.info(f"Agent found {len(search_results)} raw results for the generic query.")
    return search_results
