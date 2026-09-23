import logging
import uuid
import json
import datetime
from .database.spanner_client import spanner_client
from .storage.gcs_client import gcs_client
from .search.core import run_kyc_process
from .search.utils import DEFAULT_NUM_QUERIES, DEFAULT_NUM_RESULTS
from .identity import derive_subject_id, extract_identity_attributes

def process_subject(subj_data, run_mode):
    """
    Background worker function to process a single subject.
    This function is now designed to be called by the Worker Endpoint (triggered by Pub/Sub).
    """
    try:
        s_name = subj_data.get("name")
        if not s_name:
            logging.error("Subject name missing in worker payload")
            return

        # Reuse the id assigned at submission. Recomputing from the name alone would
        # silently write this run's findings to a different subject whenever the
        # submitter keyed on a customer identifier.
        s_id = (subj_data.get("subject_id") or "").strip()
        key_strategy = subj_data.get("key_strategy")
        if not s_id:
            s_id, key_strategy = derive_subject_id(
                s_name,
                customer_id=subj_data.get("customer_id") or subj_data.get("customerId"),
                attributes=extract_identity_attributes(subj_data),
            )

        logging.info(f"Worker started for: {s_name} ({s_id}) Mode: {run_mode} Key: {key_strategy}")

        # Prepare filters
        s_filters = {
            "subject_name": s_name,
            "subject_id": s_id,
            "customer_id": subj_data.get("customer_id") or subj_data.get("customerId", ""),
            "national_id": subj_data.get("national_id", ""),
            "key_strategy": key_strategy,
            "company": subj_data.get("company", ""),
            "profession": subj_data.get("profession", ""),
            "region": subj_data.get("region", ""),
            "dob": subj_data.get("dob", ""),
            "age": subj_data.get("age", ""),
            "ownership": subj_data.get("ownership", ""),
            "spouse": subj_data.get("spouse", ""),
            "alias": subj_data.get("alias", ""),
            "custom_keywords": subj_data.get("custom_keywords", []),
            "num_queries": DEFAULT_NUM_QUERIES,
            "num_results": DEFAULT_NUM_RESULTS,
            "recency_days": 365 * 10, # Default for Fresh/Standard
            "incremental": (run_mode == 'incremental')
        }
        
        # Persist Subject (Ensure it exists/Updated)
        # In a distributed system, the API layer might have already saved it, 
        # but it doesn't hurt to ensure consistency here.
        try:
            spanner_client.save_subject(s_id, s_name, s_filters)
        except Exception as e:
            logging.warning(f"Failed to persist subject {s_name}: {e}")

        # Run Process (Consume iterator to trigger work)
        logging.info(f"Starting analysis for {s_name} in mode={run_mode}")
        
        collected_data = {
            "subject_id": s_id,
            "subject_name": s_name,
            "findings": [],
            "graph_data": {},
            "summary": None,
            "status": "completed",
            "mode": run_mode,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }

        for event in run_kyc_process(s_filters):
            ev_type = event.get("type")
            if ev_type == "result":
                collected_data["findings"].append(event.get("data"))
            elif ev_type == "graph_data":
                collected_data["graph_data"] = event.get("data")
            elif ev_type == "summary":
                collected_data["summary"] = event.get("data")
            
            # Optional: We could log progress here or update a "Job Status" table in Spanner if we had one.
        
        # Upload to GCS
        try:
            blob_name = f"results/{s_id}.json"
            gcs_client.upload_string(
                json.dumps(collected_data, indent=2), 
                blob_name, 
                "application/json"
            )
            logging.info(f"Uploaded results for {s_name} ({s_id}) to GCS.")
        except Exception as e:
            logging.error(f"Failed to upload results to GCS for {s_name}: {e}")

        logging.info(f"Finished analysis for {s_name}")
        return True

    except Exception as e:
        logging.error(f"Error in background process for {subj_data.get('name')}: {e}", exc_info=True)
        return False
