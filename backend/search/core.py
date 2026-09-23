import logging
import json
import datetime
import uuid
from .utils import (
    generate_search_queries_with_gemini,
    perform_google_web_searches,
    analyze_kyc_results,
    calculate_cost,
    transform_result_for_export,
    extract_entities_from_bulk_data,
    generate_entity_graph_with_gemini,
    extract_profile_data_with_gemini,
    finalize_summary,
    DEFAULT_NUM_QUERIES,
)
from ..ai_client import DEFAULT_GEMINI_MODEL
from .screening import (
    RECOMMENDATION_NO_ADVERSE_MEDIA,
    SearchCoverage,
    incomplete_screening_summary,
)
from .watchlist import screen_subject_watchlists
from .deduplication import SyndicationFilter
from .prompts import KYC_SEARCH_QUERY_BRAINSTORMING_PROMPT
from ..database.spanner_client import spanner_client

# Priority adverse-media query templates.
# Structured sanctions/watchlist screening against SG_MAS, US_OFAC_SDN, UN_SC, and
# EU_FSF is executed in Step 0.5 via `screen_subject_watchlists` before these
# open-source media queries run.
PRIORITY_QUERY_TEMPLATES = [
    '"{subject_name}" AND (launder* OR terror* OR fraud OR corrupt* OR brib* OR traffick* OR "tax evasion" OR arrest* OR embezzle* OR illegal OR "insider deal" OR sanctions OR "sanctions evasion")',
    '"{subject_name}" AND (crime OR investigat* OR alleg* OR convict* OR sentenced OR lawsuit OR litigation OR misdemeanor* OR offen* OR prosecut* OR scam* OR "tax amnesty")',
    # Sanctions NEXUS, not geography. The previous version was a bare OR-list of
    # country names, so every article that merely mentioned Russia or Iran scored as
    # a sanctions hit. Each jurisdiction is now paired with a sanctions-related term.
    '"{subject_name}" AND ("sanctions evasion" OR "sanctioned entity" OR "designated person" OR "asset freeze" OR "export control" OR "dual-use goods" OR OFAC OR "SDN list" OR "restricted party") AND (DPRK OR "North Korea" OR Iran OR Cuba OR Syria OR Crimea OR Donetsk OR Luhansk OR Russia OR Venezuela OR Myanmar OR Belarus)',
]

def run_kyc_process(filters):
    """
    Orchestrates the full KYC process:
      0.5 Structured Watchlist & Sanctions Screening (SG_MAS, US_OFAC_SDN, UN_SC, EU_FSF)
      1.  Priority Adverse Media Searches
      2.  Gemini Multi-Angle Query Brainstorming & Distributed Searches
      3.  Full-Text Scraping, Deduplication & Risk Analysis
      4.  Fail-Closed Summary + Entity Graph Generation
    Yields event dictionaries: {"type": "status"|"watchlist_results"|"result"|"usage_update"|"summary", "data": ...}
    """
    subject_name = filters.get('subject_name')
    subject_id = filters.get('subject_id')
    
    session_usage = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    
    # 0. Check for Existing Subject (Incremental Mode)
    existing_subject = None
    existing_urls = set()
    incremental_mode = False
    
    # Try to find subject by name if we don't have an ID, or verify ID if we do
    if not subject_id and subject_name:
        existing_subject = spanner_client.get_subject_by_name(subject_name)
    elif subject_id:
        existing_subject = spanner_client.get_subject(subject_id)
        
    if existing_subject:
        subject_id = existing_subject['subject_id']
        filters['subject_id'] = subject_id # Ensure filter has the ID
        
        last_run = spanner_client.get_latest_update_time(subject_id)
        if last_run and filters.get("incremental", False):
            incremental_mode = True
            if last_run.tzinfo:
                now = datetime.datetime.now(datetime.timezone.utc)
            else:
                now = datetime.datetime.utcnow()
                
            delta = now - last_run
            days_diff = delta.days
            new_recency = min(days_diff + 1, int(filters.get('recency_days', 365)))
            filters['recency_days'] = new_recency
            
            yield {"type": "status", "status": "incremental_init", "message": f"Existing subject found. Incremental scan for last {new_recency} days."}
            
            existing_urls = spanner_client.get_all_finding_urls(subject_id)
        elif last_run:
             yield {"type": "status", "status": "init", "message": "New subject initialized. Creating first scan..."}

    # 0.5 Structured Watchlist & Sanctions Screening (SG_MAS, US_OFAC_SDN, UN_SC, EU_FSF)
    yield {
        "type": "status",
        "status": "performing_watchlist_screening",
        "message": "Screening against MAS, OFAC SDN, UN Security Council, and EU sanctions lists...",
    }
    watchlist_screening = screen_subject_watchlists(filters)
    yield {"type": "watchlist_results", "data": watchlist_screening}

    if subject_id:
        try:
            spanner_client.save_watchlist_hits(subject_id, watchlist_screening.get("hits") or [])
            spanner_client.append_audit_event(
                subject_id=subject_id,
                event_type="WATCHLIST_SCREENING_COMPLETED",
                actor_email=filters.get("actor_email") or "system@kyc.automated",
                actor_auth_source=filters.get("actor_auth_source") or "system",
                previous_state="",
                new_state=watchlist_screening.get("status") or "CLEAR",
                payload={
                    "subject_name": subject_name,
                    "status": watchlist_screening.get("status"),
                    "total_hits": watchlist_screening.get("total_hits", 0),
                    "confirmed_hits": watchlist_screening.get("confirmed_hits", 0),
                    "strong_hits": watchlist_screening.get("strong_hits", 0),
                    "potential_hits": watchlist_screening.get("potential_hits", 0),
                    "coverage": (watchlist_screening.get("coverage") or {}).get("description"),
                },
            )
        except Exception as e:
            logging.error(f"Failed to persist watchlist hits / audit event: {e}")

    # 1. Priority Searches
    yield {"type": "status", "status": "performing_priority_searches", "message": f"Performing {len(PRIORITY_QUERY_TEMPLATES)} priority searches..."}
    
    priority_queries = [q.format(subject_name=subject_name) for q in PRIORITY_QUERY_TEMPLATES]
    priority_search_results, priority_coverage = perform_google_web_searches(
        priority_queries, filters['num_results'], filters['recency_days']
    )

    if not priority_coverage.is_complete:
        yield {
            "type": "status",
            "status": "degraded_coverage",
            "message": f"Priority search degraded: {priority_coverage.describe()}",
        }

    # Deduplicate priority results against DB
    if incremental_mode:
        original_count = len(priority_search_results)
        priority_search_results = [r for r in priority_search_results if r.get('link') not in existing_urls]
        skipped_count = original_count - len(priority_search_results)
        if skipped_count > 0:
             yield {"type": "status", "status": "deduplication", "message": f"Skipped {skipped_count} existing priority articles."}

    priority_links = {res.get('link') for res in priority_search_results}

    yield {"type": "status", "status": "analyzing_priority_results", "message": f"Found {len(priority_search_results)} new priority sources."}

    # 2. Brainstorming / Gemini Queries (Run for both New and Incremental, applying filters)
    yield {"type": "status", "status": "brainstorming", "message": "Brainstorming additional search angles..."}
    gemini_queries, query_usage = generate_search_queries_with_gemini(filters, KYC_SEARCH_QUERY_BRAINSTORMING_PROMPT)
    
    # Track usage (brainstorming uses Lite/Flash)
    if query_usage:
        session_usage["input_tokens"] += query_usage.get('input_tokens', 0)
        session_usage["output_tokens"] += query_usage.get('output_tokens', 0)
        cost = calculate_cost(DEFAULT_GEMINI_MODEL, query_usage.get('input_tokens', 0), query_usage.get('output_tokens', 0))
        session_usage["cost"] += cost
        yield {"type": "usage_update", "data": session_usage}
    
    query_budget = int(filters.get('num_queries') or DEFAULT_NUM_QUERIES)
    if gemini_queries and len(gemini_queries) > query_budget:
        logging.info(
            f"Truncating {len(gemini_queries)} generated queries to the "
            f"tier budget of {query_budget}."
        )
        gemini_queries = gemini_queries[:query_budget]

    gemini_coverage = SearchCoverage()
    if gemini_queries:
        yield {"type": "status", "status": "queries_generated", "message": f"Generated {len(gemini_queries)} queries.", "data": gemini_queries}
        yield {"type": "status", "status": "performing_distributed_searches", "message": "Performing distributed searches..."}
        
        gemini_search_results, gemini_coverage = perform_google_web_searches(
            gemini_queries, filters['num_results'], filters['recency_days']
        )
        
        if existing_urls:
             gemini_search_results = [r for r in gemini_search_results if r.get('link') not in existing_urls]
    else:
         gemini_search_results = []

    total_coverage = priority_coverage.merge(gemini_coverage)

    # 3. Combine Results
    priority_results_dict = {res['link']: res for res in priority_search_results}
    gemini_results_dict = {res['link']: res for res in gemini_search_results if res['link'] not in priority_results_dict}
    all_search_results = list(priority_results_dict.values()) + list(gemini_results_dict.values())

    if not all_search_results:
        if not total_coverage.is_complete:
            summary_data = incomplete_screening_summary(subject_name, total_coverage)
            summary_data = finalize_summary(
                summary_data, [], total_coverage, watchlist_screening=watchlist_screening
            )
            logging.error(
                f"Screening for '{subject_name}' returned no results with degraded coverage: "
                f"{total_coverage.describe()} Refusing to report a clean result."
            )
            if subject_id:
                try:
                    spanner_client.update_subject_summary(subject_id, json.dumps(summary_data))
                except Exception as e:
                    logging.error(f"Failed to persist incomplete-screening summary: {e}")
            yield {"type": "summary", "data": summary_data}
            yield {
                "type": "status",
                "status": "screening_incomplete",
                "message": f"Screening could not be completed. {total_coverage.describe()}",
            }
            return

        summary_data = {
            "risk_score": "Low",
            "summary": (
                f"No adverse media was identified for {subject_name} in this open-source scan. "
                f"{total_coverage.describe()} This reflects the sources searched in this run only."
            ),
            "key_findings": [],
            "requires_human_review": [],
            "recommendation": RECOMMENDATION_NO_ADVERSE_MEDIA,
            "reasoning": "No adverse media returned by the sources searched.",
            "screening_coverage": total_coverage.to_dict(),
        }
        summary_data = finalize_summary(
            summary_data, [], total_coverage, watchlist_screening=watchlist_screening
        )
        if subject_id:
            try:
                spanner_client.update_subject_summary(subject_id, json.dumps(summary_data))
            except Exception as e:
                logging.error(f"Failed to persist clean summary: {e}")
        yield {"type": "summary", "data": summary_data}
        yield {"type": "status", "status": "complete", "message": "No articles found."}
        return

    yield {"type": "status", "status": "analyzing_results", "message": f"Analyzing {len(all_search_results)} sources..."}

    # 4. Analyze
    previous_findings = spanner_client.get_findings(subject_id)
    existing_url_hashes = {}
    existing_url_fingerprints = {}
    syndication_filter = SyndicationFilter()
    
    yield {"type": "status", "status": "context_loading", "message": f"Loaded {len(previous_findings)} existing findings for context."}
    
    try:
        u_hashes, c_hashes, u_fingerprints = spanner_client.get_existing_hashes_and_ids(subject_id)
        syndication_filter.preload_hashes(c_hashes)
        existing_url_hashes = u_hashes
        existing_url_fingerprints = u_fingerprints
        yield {"type": "status", "status": "dedup_loaded", "message": f"Loaded {len(c_hashes)} content hashes and {len(u_hashes)} URL hashes for deduplication."}
    except Exception as e:
        logging.error(f"Failed to preload hashes: {e}")

    analysis_generator = analyze_kyc_results(
        all_search_results,
        filters,
        priority_links,
        previous_findings=previous_findings,
        syndication_filter=syndication_filter,
        existing_url_hashes=existing_url_hashes,
        existing_url_fingerprints=existing_url_fingerprints,
        coverage=total_coverage,
        session_usage=session_usage,
        watchlist_screening=watchlist_screening,
    )
    
    collected_new_findings = []
    
    for result in analysis_generator:
        yield result
        if result.get('type') == 'result':
            collected_new_findings.append(result.get('data'))
            
    # --- Auto-Generate Entity Graph ---
    # Combine new findings with previous findings to generate a comprehensive graph
    all_findings_for_graph = collected_new_findings + previous_findings
    
    if all_findings_for_graph:
        # --- Auto-Extract Profile Data ---
        yield {"type": "status", "status": "profile_extraction", "message": "Extracting structured profile data..."}
        try:
             extracted_profile = extract_profile_data_with_gemini(all_findings_for_graph, subject_name)
             if extracted_profile:
                 existing_sub = spanner_client.get_subject(subject_id)
                 current_profile = existing_sub.get('profile_data', {}) if existing_sub else {}
                 
                 final_profile = extracted_profile.copy()
                 for k, v in current_profile.items():
                     if v and str(v).lower() not in ["", "unknown", "n/a", "none"]:
                         final_profile[k] = v
                 
                 spanner_client.save_subject(subject_id, subject_name, final_profile)
                 logging.info(f"Profile updated for {subject_name}: {final_profile}")
        except Exception as px:
             logging.error(f"Profile extraction/save failed: {px}")

        yield {"type": "status", "status": "graph_generation", "message": f"Generating Entity Graph from {len(all_findings_for_graph)} findings..."}
        
        graph_data = generate_entity_graph_with_gemini(all_findings_for_graph, subject_name)
        
        # Track usage for graph generation if available
        if graph_data.get("usage_metadata"):
            usage = graph_data.get("usage_metadata")
            session_usage["input_tokens"] += usage.get("input_tokens", 0)
            session_usage["output_tokens"] += usage.get("output_tokens", 0)
            cost = calculate_cost(DEFAULT_GEMINI_MODEL, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
            session_usage["cost"] += cost
            yield {"type": "usage_update", "data": session_usage}

        # Persist Graph
        if subject_id and graph_data.get("nodes"):
            try:
                spanner_client.save_graph_data(subject_id, graph_data["nodes"], graph_data["edges"])
            except Exception as e:
                logging.error(f"Failed to persist auto-generated graph: {e}")
        
        yield {"type": "graph_data", "data": graph_data}
        
    yield {"type": "status", "status": "complete", "message": "KYC analysis complete."}
