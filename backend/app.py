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

# app.py
from dotenv import load_dotenv
load_dotenv()

import os
# Disable Google Cloud OpenTelemetry Metrics/Traces to prevent 503 errors in logs
os.environ["GOOGLE_CLOUD_DISABLE_OPENTELEMETRY"] = "true" 
os.environ["GOOGLE_CLOUD_ENABLE_OPENTELEMETRY"] = "false"
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["GRPC_VERBOSITY"] = "ERROR"

import logging
import json
import threading
import time
import concurrent.futures
from flask import Flask, jsonify, request, send_from_directory, abort, Response, stream_with_context
import uuid
from .search.screening import (
    RECOMMENDATION_INCOMPLETE,
    RECOMMENDATION_NO_ADVERSE_MEDIA,
    RECOMMENDATION_SANCTIONS_HIT,
)
from .search.watchlist import screen_subject_watchlists, watchlist_index
from .governance import (
    CASE_DECISIONS,
    CASE_STATES,
    DISPOSITION_VERDICTS,
    FourEyesViolationError,
    GovernanceValidationError,
    VALID_RISK_RATINGS,
    extract_reviewer_identity,
    validate_checker_decision,
    validate_disposition_input,
    validate_maker_submission,
    verify_audit_chain,
)
# Import Spanner Client
from .database.spanner_client import spanner_client
from .database.schema_manager import apply_schema
from .storage.gcs_client import gcs_client
from .identity import derive_subject_id, extract_identity_attributes

# --- Project Imports ---
from .search.utils import (
    generate_search_queries_with_gemini,
    perform_google_web_searches,
    analyze_kyc_results,
    scrape_url_content,
    transform_result_for_export,
    extract_entities_from_bulk_data,
    generate_graph_html,
    generate_entity_graph_with_gemini,
    DEFAULT_NUM_QUERIES,
    DEFAULT_NUM_RESULTS,
    calculate_cost,
    regenerate_kyc_summary,
)
from .config import Config

# --- Document upload validation ------------------------------------------
# KYC evidence documents are uploaded by the subject or their agent, so the
# filename, Content-Type and size are all untrusted. Only these types are
# accepted, and the MIME type sent to Gemini is derived from the extension we
# validated rather than from the client-declared header.
ALLOWED_UPLOAD_EXTENSIONS = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}
MAX_UPLOAD_BYTES = int(os.getenv("KYC_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

# Global executor for background tasks (e.g. summary regeneration)
bg_executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

# --- Summary regeneration debounce ---------------------------------------
# Regeneration is triggered opportunistically from a GET handler, so without a
# guard a single broken subject generates one Gemini call per page view, forever.
# State is per-process; with multiple gunicorn workers the effective budget is
# MAX_REGENERATION_ATTEMPTS per worker. Move this to Spanner (SummaryAttempts /
# LastSummaryAttemptAt columns) if a hard global cap is required.
MAX_REGENERATION_ATTEMPTS = int(os.environ.get("KYC_MAX_SUMMARY_ATTEMPTS", 3))
REGENERATION_COOLDOWN_SECONDS = int(os.environ.get("KYC_SUMMARY_COOLDOWN_SECONDS", 300))

_regeneration_lock = threading.Lock()
_regeneration_state: dict = {}  # subject_id -> {"attempts": int, "last": float, "in_flight": bool}


def _claim_regeneration_slot(subject_id: str) -> bool:
    """Returns True if this caller may start a regeneration for `subject_id`."""
    now = time.monotonic()
    with _regeneration_lock:
        state = _regeneration_state.setdefault(
            subject_id, {"attempts": 0, "last": 0.0, "in_flight": False}
        )
        if state["in_flight"]:
            return False
        if state["attempts"] >= MAX_REGENERATION_ATTEMPTS:
            return False
        if now - state["last"] < REGENERATION_COOLDOWN_SECONDS:
            return False
        state["in_flight"] = True
        state["attempts"] += 1
        state["last"] = now
        return True


def _release_regeneration_slot(subject_id: str, succeeded: bool) -> None:
    with _regeneration_lock:
        state = _regeneration_state.get(subject_id)
        if not state:
            return
        state["in_flight"] = False
        if succeeded:
            # Reset the budget so a later, genuinely new failure can still retry.
            state["attempts"] = 0


def _regenerate_and_release(subject_id: str):
    """Runs regeneration and always releases the debounce slot."""
    succeeded = False
    try:
        result = regenerate_kyc_summary(subject_id)
        succeeded = bool(result)
        return result
    except Exception as e:
        logging.error(f"Background summary regeneration failed for {subject_id}: {e}")
        return None
    finally:
        _release_regeneration_slot(subject_id, succeeded)


# --- Service-to-service authentication -------------------------------------
# /api/worker/push and /api/tasks/pkyc-sweep are invoked by Pub/Sub and Cloud
# Scheduler respectively. Both were previously unauthenticated: any caller able to
# reach the service could enqueue screening work, consume the search quota, or
# write findings against an arbitrary subject id.
#
# Cloud Run's built-in IAM check is the primary control; this is defence in depth
# for deployments that must accept unauthenticated ingress.
ALLOWED_OIDC_SERVICE_ACCOUNTS = {
    sa.strip()
    for sa in os.environ.get("KYC_ALLOWED_INVOKER_SAS", "").split(",")
    if sa.strip()
}
OIDC_AUDIENCE = os.environ.get("KYC_OIDC_AUDIENCE", "")
# Only honoured when K_SERVICE is absent, i.e. never on Cloud Run.
ALLOW_UNAUTHENTICATED_TASKS = (
    os.environ.get("KYC_ALLOW_UNAUTHENTICATED_TASKS", "false").lower() == "true"
)


def _verify_oidc_caller(req) -> tuple:
    """
    Verifies the Google-issued OIDC bearer token on a service-to-service request.

    Returns (authorized, reason).
    """
    if ALLOW_UNAUTHENTICATED_TASKS:
        if os.environ.get("K_SERVICE"):
            # Fail closed: the escape hatch is for local development only and must
            # never silently disable authentication on a deployed revision.
            logging.error(
                "KYC_ALLOW_UNAUTHENTICATED_TASKS is set on a Cloud Run revision. "
                "Ignoring it and enforcing OIDC verification."
            )
        else:
            return True, "unauthenticated tasks permitted (local development)"

    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False, "missing bearer token"

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return False, "empty bearer token"

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=OIDC_AUDIENCE or None,
        )
    except Exception as e:
        return False, f"token verification failed: {e}"

    if not claims.get("email_verified"):
        return False, "token email is not verified"

    email = claims.get("email", "")
    if ALLOWED_OIDC_SERVICE_ACCOUNTS and email not in ALLOWED_OIDC_SERVICE_ACCOUNTS:
        return False, f"service account {email} is not in the allow-list"

    if not ALLOWED_OIDC_SERVICE_ACCOUNTS:
        logging.warning(
            "KYC_ALLOWED_INVOKER_SAS is unset; accepting any verified Google identity "
            "for task endpoints. Set it to the Pub/Sub and Cloud Scheduler service "
            "accounts to restrict access."
        )

    return True, f"verified {email}"


from .search.business_logic import get_risk_category_options
from .search.prompts import (
    KYC_SEARCH_QUERY_BRAINSTORMING_PROMPT,
)
from .search.content_processing import analyze_document_authenticity
import tempfile
from werkzeug.utils import secure_filename

# --- Worker & Pub/Sub Imports ---
import base64
from .worker import process_subject
from .events.publisher import publish_kyc_job


# Single source of truth; core.py imports the same list.
from .search.core import PRIORITY_QUERY_TEMPLATES, run_kyc_process
from .scheduler import MAX_SUBJECTS_PER_RUN, init_scheduler, run_daily_kyc_job

def _register_api_routes(app: Flask):
    """Registers all API routes for the Flask application."""

    @app.route('/api/kyc-check', methods=['GET'])
    def kyc_check_stream():
        """
        Streams progress updates and individual adverse media findings for a KYC check.
        Uses the shared core logic from backend/search/core.py.
        """
        subject_name = request.args.get('subjectName', '')
        if not subject_name:
            def error_stream():
                yield f'data: {json.dumps({"status": "error", "message": "Subject name is required."})}\n\n'
            return Response(stream_with_context(error_stream()), mimetype='text/event-stream', status=400)

        # Extract new profile fields
        profession = request.args.get('profession', '')
        company = request.args.get('company', '')
        region = request.args.get('region', '')
        dob = request.args.get('dob', '')
        age = request.args.get('age', '')
        ownership = request.args.get('ownership', '')
        spouse = request.args.get('spouse', '')
        alias = request.args.get('alias', '')
        custom_keywords_str = request.args.get('customKeywords', '')
        custom_keywords = [k.strip() for k in custom_keywords_str.split(',')] if custom_keywords_str else []
        
        # Parse incremental flag
        incremental_param = request.args.get('incremental', 'false').lower()
        is_incremental = incremental_param == 'true'

        customer_id = request.args.get('customerId', '').strip()
        national_id = request.args.get('nationalId', '').strip()

        filters = {
            "subject_name": subject_name,
            "customer_id": customer_id,
            "national_id": national_id,
            "profession": profession,
            "company": company,
            "region": region,
            "dob": dob,
            "age": age,
            "ownership": ownership,
            "spouse": spouse,
            "alias": alias,
            "custom_keywords": custom_keywords,
            "num_queries": DEFAULT_NUM_QUERIES,
            "num_results": DEFAULT_NUM_RESULTS,
            "recency_days": 365 * 10, # Search last 10 years for KYC
            "incremental": is_incremental # Pass explicit user preference
        }

        # --- Persistence: Generate Subject ID and Save ---
        # Keyed on the institution's customer identifier where available; see
        # backend/identity.py for why a name is not an acceptable primary key.
        reviewer = extract_reviewer_identity(request)
        subject_id, key_strategy = derive_subject_id(
            subject_name,
            customer_id=customer_id,
            attributes=extract_identity_attributes(filters),
        )
        filters['subject_id'] = subject_id
        filters['key_strategy'] = key_strategy
        filters['actor_email'] = reviewer.effective_email
        filters['actor_auth_source'] = reviewer.auth_source

        # Save Subject to Spanner (Non-blocking or fast enough)
        try:
             spanner_client.save_subject(subject_id, subject_name, filters)
        except Exception as e:
             logging.warning(f"Failed to persist subject: {e}")

        def generate_events():
            import queue as _queue

            # Tracks whether the run ended in a defensible state. The client must
            # be told explicitly: a silently-closed stream is indistinguishable
            # from a crashed one, and "the spinner stopped" is not a verdict.
            outcome = {
                "status": "complete",
                "message": "Screening complete.",
                "screening_incomplete": False,
                "subject_id": subject_id,
            }
            yield f'data: {json.dumps({"status": "subject_initialized", "subject_id": subject_id, "reviewer": reviewer.to_dict()})}\n\n'

            event_q = _queue.Queue()
            _SENTINEL = object()

            def _producer():
                try:
                    for ev in run_kyc_process(filters):
                        event_q.put(("event", ev))
                except Exception as exc:
                    logging.error(f"An error occurred during KYC check stream: {exc}", exc_info=True)
                    event_q.put(("error", exc))
                finally:
                    event_q.put((_SENTINEL, None))

            producer_thread = threading.Thread(target=_producer, daemon=True)
            producer_thread.start()

            while True:
                try:
                    kind, payload = event_q.get(timeout=8.0)
                except _queue.Empty:
                    # Emit an SSE keepalive comment + heartbeat payload every 8s
                    # during long phases (parallel scraping, GCS archival, Gemini
                    # map-reduce summary, and graph generation) so Cloud Run IAP /
                    # GFE and browser HTTP/2 proxies never drop an idle stream.
                    yield ": keepalive\n\n"
                    yield f'data: {json.dumps({"status": "heartbeat", "subject_id": subject_id})}\n\n'
                    continue

                if kind is _SENTINEL:
                    break
                if kind == "error":
                    yield f'data: {json.dumps({"status": "error", "message": "An unexpected error occurred. Please check the logs.", "subject_id": subject_id})}\n\n'
                    return

                event = payload
                event_type = event.get("type")
                if event_type == "status":
                    if event.get("status") == "screening_incomplete":
                        outcome["screening_incomplete"] = True
                        outcome["message"] = event.get("message") or "Screening could not be completed."
                    yield f'data: {json.dumps({"status": event.get("status"), "message": event.get("message"), "data": event.get("data"), "subject_id": subject_id})}\n\n'

                elif event_type == "watchlist_results":
                    yield f'data: {json.dumps({"status": "watchlist_results", "message": "Sanctions watchlist screening completed.", "data": event.get("data"), "subject_id": subject_id})}\n\n'

                elif event_type == "usage_update":
                    yield f'data: {json.dumps({"status": "usage_update", "data": event.get("data")})}\n\n'

                elif event_type == "result":
                    yield f'data: {json.dumps({"status": "kyc_result_generated", "message": "Analyzed a source.", "data": event.get("data")})}\n\n'

                elif event_type == "summary":
                    yield f'data: {json.dumps({"status": "kyc_summary_generated", "message": "Overall assessment complete.", "data": event.get("data"), "subject_id": subject_id})}\n\n'

                elif event_type == "graph_data":
                    yield f'data: {json.dumps({"status": "kyc_graph_generated", "message": "Entity graph generated.", "data": event.get("data")})}\n\n'

            # Terminal event. Always sent on a non-error path so the client can
            # distinguish "finished" from "connection dropped mid-screening".
            yield f'data: {json.dumps(outcome)}\n\n'

        return Response(
            stream_with_context(generate_events()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            },
        )

    def _resolve_finding_content(res):
        """
        Returns the article text for a finding, cheapest source first.

        Order matters for correctness, not just cost. The text we assessed is the
        text the export must show: re-scraping the live web can return an amended
        or paywalled page, so the exported evidence would no longer match the
        recorded verdict. The live scrape is a last resort, not the default.
        """
        # 1. Already in hand (fresh from this run's analysis).
        for key in ("full_content", "full_content_text", "news content"):
            content = res.get(key)
            if content and len(content.strip()) > 200:
                return content

        # 2. Archived copy of exactly what was assessed.
        gcs_uri = res.get("gcs_content_uri")
        if gcs_uri:
            cached = gcs_client.download_from_uri(gcs_uri)
            if cached and cached.strip():
                return cached
            app.logger.warning(f"GCS cache miss for {gcs_uri}; falling back to scrape.")

        # 3. Last resort: hit the live web.
        url = res.get("url")
        if not url:
            return "No URL provided."
        return scrape_url_content(url)

    def _scrape_and_prepare_all_findings(findings, subject_name):
        """
        Helper to resolve full article text for all findings and transform them
        into the export-ready JSON format.
        Returns a list of finding dictionaries (with full 'news content').
        """
        # 1. Resolve content (parallel; most will resolve without any network call)
        content_by_index = {}
        app.logger.info(f"Resolving content for {len(findings)} findings...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_index = {
                executor.submit(_resolve_finding_content, res): idx
                for idx, res in enumerate(findings)
            }
            for future in concurrent.futures.as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    content_by_index[idx] = future.result()
                except Exception as exc:
                    app.logger.error(
                        f'Content resolution failed for {findings[idx].get("url")}: {exc}'
                    )
                    content_by_index[idx] = "Content unavailable."

        # 2. Transform Results
        processed_results = []
        for idx, res in enumerate(findings):
            # Index the map rather than the URL: two findings can legitimately
            # share a URL (e.g. a revision), and a URL-keyed map would silently
            # collapse them onto one body of text.
            resolved = content_by_index.get(idx, "Content unavailable.")
            transformed_res = transform_result_for_export(res, subject_name, resolved)
            processed_results.append(transformed_res)
            
        return processed_results


    @app.route('/api/export-findings', methods=['POST'])
    def export_findings():
        """
        Receives findings from the frontend, scrapes content, extracts entities,
        formats for export, and returns JSON data + Graph HTML.
        """
        data = request.get_json()
        if not data or 'results' not in data or 'subjectName' not in data:
            return jsonify({"error": "Missing 'results' or 'subjectName' in request"}), 400

        results_to_process = data['results']
        subject_name = data['subjectName']
        
        # Reuse helper to get full JSON (same as download)
        processed_results = _scrape_and_prepare_all_findings(results_to_process, subject_name)

        # Extract Entities (using bulk prompt)
        # Prepare data for bulk extraction (mapping export keys to extraction expected keys if needed)
        articles_data_for_bulk = []
        for res in processed_results:
            content = res.get('news content', '')
            if len(content) > 500:
                articles_data_for_bulk.append({
                    "title": res.get('title', 'N/A'),
                    "url": res.get('link', 'N/A'),
                    "content": content
                })

        all_nodes = []
        all_edges = []
        
        if articles_data_for_bulk:
            logging.info(f"Sending {len(articles_data_for_bulk)} articles for bulk entity extraction...")
            extraction_result = extract_entities_from_bulk_data(articles_data_for_bulk)
            all_nodes = extraction_result.get('nodes', [])
            all_edges = extraction_result.get('edges', [])
        else:
            logging.info("No sufficient content found for entity extraction.")

        # Deduplicate nodes by ID
        unique_nodes = {node['id']: node for node in all_nodes}.values()
        
        # Generate Graph HTML
        graph_html = generate_graph_html(list(unique_nodes), all_edges, subject_name)

        return jsonify({
            "findings": processed_results,
            "graph_html": graph_html,
            "graph_data": {
                "nodes": list(unique_nodes),
                "edges": all_edges
            }
        })

    @app.route('/api/upload-document', methods=['POST'])
    def upload_document():
        """
        Handles document upload, validates authenticity with Gemini, and augments subject profile.

        KYC documents are attacker-influenced input: the uploader is the subject
        (or their agent), so the filename, declared MIME type and size are all
        untrusted. We validate the extension against an allow-list, cap the
        size, and never interpolate the client-supplied filename into a path.
        """
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
        
        file = request.files['file']
        subject_id = request.form.get('subjectId')
        subject_name = request.form.get('subjectName')
        
        if not file.filename:
            return jsonify({"error": "No selected file"}), 400
            
        if not subject_id or not subject_name:
             return jsonify({"error": "Subject context missing"}), 400

        # --- Validate extension against the allow-list -----------------------
        safe_name = secure_filename(file.filename)
        extension = os.path.splitext(safe_name)[1].lower().lstrip('.')
        if extension not in ALLOWED_UPLOAD_EXTENSIONS:
            return jsonify({
                "error": (
                    f"Unsupported file type '.{extension or 'unknown'}'. "
                    f"Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}."
                )
            }), 400

        # --- Enforce the size cap before writing anything to disk ------------
        file.stream.seek(0, os.SEEK_END)
        size_bytes = file.stream.tell()
        file.stream.seek(0)
        if size_bytes == 0:
            return jsonify({"error": "Uploaded file is empty."}), 400
        if size_bytes > MAX_UPLOAD_BYTES:
            return jsonify({
                "error": f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
            }), 413

        # Derive the MIME type from the validated extension rather than
        # trusting the client-declared Content-Type header.
        mime_type = ALLOWED_UPLOAD_EXTENSIONS[extension]

        temp_path = None
        try:
            # Suffix comes from our own allow-list, never from user input.
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{extension}") as temp:
                temp_path = temp.name
            file.save(temp_path)

            analysis_result = analyze_document_authenticity(temp_path, mime_type)

            if "error" in analysis_result:
                return jsonify(analysis_result), 500
                
            # Augmentation Logic
            extracted_data = analysis_result.get("extracted_data", {})
            if extracted_data:
                # Retrieve current profile
                existing_subject = spanner_client.get_subject(subject_id)
                current_profile = existing_subject.get('profile_data', {}) if existing_subject else {}
                
                # Merge: Extracted data from OFFICIAL docs is high confidence -> Prefer it if current is empty
                # Or append to lists? 
                # For simplicity, we merge non-empty values
                updated_profile = current_profile.copy()
                
                # Map extracted keys to profile keys (simple mapping)
                # Extracted: subject_name, companies, dates, ids
                # Profile: Age, Location, Ownership, Associates, Role
                
                # We can add a generic "documents_evidence" field to profile?
                # Or just standard fields.
                
                updated = False
                if extracted_data.get('companies'):
                    # Augment Ownership
                    current_ownership = updated_profile.get('ownership', '')
                    new_companies = ", ".join(extracted_data['companies'])
                    if not current_ownership or current_ownership == "Unknown":
                        updated_profile['ownership'] = new_companies
                        updated = True
                    elif new_companies not in current_ownership:
                         updated_profile['ownership'] += f", {new_companies}"
                         updated = True
                
                if extracted_data.get('ids'):
                     # Add IDs field if not exists
                     updated_profile['ids'] = extracted_data['ids']
                     updated = True
                     
                if updated:
                    spanner_client.save_subject(subject_id, subject_name, updated_profile)
                    analysis_result['profile_augmented'] = True
            
            return jsonify(analysis_result)

        except Exception as e:
            logging.error(f"Upload failed: {e}", exc_info=True)
            return jsonify({"error": "Document analysis failed. Please check the logs."}), 500
        finally:
            # Always remove the temp file, including when analysis raised.
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError as cleanup_error:
                    logging.warning(
                        f"Could not remove temp upload {temp_path}: {cleanup_error}"
                    )

    @app.route('/api/search/submit', methods=['POST'])
    def submit_search():
        """
        Accepts a JSON payload with subjects and a run mode.
        Payload: 
        { 
          "subjects": [{"name": "Authname", "company": "..."}],
          "mode": "default" | "fresh" | "incremental"
        }
        Returns: detailed job status.
        """
        data = request.json
        subjects = data.get('subjects', [])
        mode = data.get('mode', 'default').lower()
        
        # Support single subject in root for convenience
        if not subjects and data.get('name'):
            subjects = [data]
            
        if not subjects:
            return jsonify({"error": "No subjects provided"}), 400
        
        results = {
            "batch_id": str(uuid.uuid4()),
            "queued": [],
            "skipped_existing": [],
            "mode": mode
        }
        
        # Process Request
        for subj in subjects:
            name = subj.get('name')
            if not name: continue
            
            # Deterministic ID, keyed on customer identifier where supplied.
            s_id, key_strategy = derive_subject_id(
                name,
                customer_id=subj.get('customer_id') or subj.get('customerId'),
                attributes=extract_identity_attributes(subj),
            )
            subj['subject_id'] = s_id
            subj['key_strategy'] = key_strategy

            # Check DB if mode is default.
            # Look up by the derived id first: get_subject_by_name would match a
            # different customer who happens to share this name.
            if mode == 'default':
                existing = spanner_client.get_subject(s_id)
                if existing:
                    results["skipped_existing"].append({
                        "name": name,
                        "subject_id": existing.get('subject_id'),
                        "last_run": existing.get('updated_at')
                    })
                    continue
            
            # Submit for processing via Pub/Sub
            msg_id = publish_kyc_job(subj, mode)
            
            if msg_id:
                results["queued"].append({
                    "name": name,
                    "subject_id": s_id,
                    "message_id": msg_id
                })
            else:
                 # Fallback: simple logging or error (could fallback to local thread if needed?)
                 # For now, we return error for that item or just log it
                 logging.error(f"Failed to queue {name}")

        return jsonify(results)

    @app.route('/api/tasks/pkyc-sweep', methods=['POST'])
    def pkyc_sweep():
        """
        Ongoing-monitoring sweep, invoked by Cloud Scheduler.

        Replaces the in-process APScheduler job, which never enumerated any subjects
        and ran once per gunicorn worker. See backend/scheduler.py.
        """
        authorized, reason = _verify_oidc_caller(request)
        if not authorized:
            logging.warning(f"Rejected pKYC sweep request: {reason}")
            return jsonify({"error": "Unauthorized", "detail": reason}), 401

        try:
            limit = int(request.args.get('limit', MAX_SUBJECTS_PER_RUN))
        except (TypeError, ValueError):
            limit = MAX_SUBJECTS_PER_RUN

        try:
            result = run_daily_kyc_job(limit=limit)
            # A partial dispatch failure is a monitoring gap. Return 500 so Cloud
            # Scheduler records the failure and retries rather than reporting success.
            status = 200 if result.get("failed", 0) == 0 else 500
            return jsonify(result), status
        except Exception as e:
            logging.error(f"pKYC sweep failed: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route('/api/worker/push', methods=['POST'])
    def worker_push():
        """
        Endpoint for Pub/Sub Push subscriptions.
        Receives a message, parses the payload, and executes the worker logic.
        """
        # The push endpoint previously executed whatever it was sent, with no
        # authentication at all. Anyone who could reach the service could enqueue
        # arbitrary screening work and burn the search quota.
        authorized, reason = _verify_oidc_caller(request)
        if not authorized:
            logging.warning(f"Rejected worker push: {reason}")
            return jsonify({"error": "Unauthorized", "detail": reason}), 401

        envelope = request.get_json()
        if not envelope:
            return 'Bad Request: no message received', 400

        if not isinstance(envelope, dict) or 'message' not in envelope:
            return 'Bad Request: invalid message format', 400

        pubsub_message = envelope['message']

        if isinstance(pubsub_message, dict) and 'data' in pubsub_message:
            try:
                data_str = base64.b64decode(pubsub_message['data']).decode('utf-8').strip()
                payload = json.loads(data_str)
                
                subject_data = payload.get('subject')
                mode = payload.get('mode', 'default')
                
                if subject_data:
                     success = process_subject(subject_data, mode)
                     if success:
                         return 'OK', 200
                     else:
                         return 'Internal Error during processing', 500
                else:
                    return 'Bad Request: Missing subject data', 400

            except Exception as e:
                logging.error(f"Failed to process Pub/Sub message: {e}")
                return f'Bad Request: {e}', 400

        return 'Bad Request: invalid message data', 400

    @app.route('/api/results/<subject_id>', methods=['GET'])
    def get_processing_result(subject_id):
        """Retrieves the full analysis result from GCS."""
        try:
            blob_name = f"results/{subject_id}.json"
            content = gcs_client.download_as_string(blob_name)
            
            if content:
                return Response(content, mimetype='application/json')
            else:
                # Check if subject exists at least?
                # For now just 404
                return jsonify({"error": "Result not found or processing not complete"}), 404
        except Exception as e:
            logging.error(f"Error retrieving result for {subject_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/generate-graph', methods=['POST'])
    def generate_graph():
        data = request.json
        subject_name = data.get('subjectName')
        findings = data.get('findings', [])

        if not subject_name:
            return jsonify({"error": "Subject name is required"}), 400

        # Reuse helper to get full JSON (simulate "Download All" data prep)
        app.logger.info(f"Step 1: Starting scraping/download for {len(findings)} findings...")
        processed_results = _scrape_and_prepare_all_findings(findings, subject_name)
        app.logger.info("Step 2: Downloading/Scraping finished. JSON prepared.")
        
        # Now pass this "Export JSON" to the graph generation logic
        mapped_findings = []
        for r in processed_results:
            mapped_findings.append({
                "source_title": r.get('title'),
                "snippet": r.get('snippet'),
                "gemini_insight": "Generative Graph Analysis", 
                "url": r.get('link'),
                "scraped_content": r.get('news content')
            })

        # Call the graph generation logic
        try:
            app.logger.info("Step 3: Posting JSON to Gemini for entity extraction...")
            graph_data = generate_entity_graph_with_gemini(mapped_findings, subject_name)

            # --- Persistence: Save Graph ---
            try:
                # Prefer the id the caller is already working with; deriving it from
                # the name again would attach the graph to a different subject
                # whenever the name is shared or was keyed on a customer id.
                subj_id = (data.get('subject_id') or '').strip()
                if not subj_id:
                    subj_id, _ = derive_subject_id(
                        subject_name,
                        customer_id=data.get('customer_id') or data.get('customerId'),
                        attributes=extract_identity_attributes(data),
                    )
                if graph_data.get("nodes"):
                    spanner_client.save_graph_data(subj_id, graph_data["nodes"], graph_data["edges"])
            except Exception as e:
                logging.error(f"Failed to persist graph data: {e}")
            
            # Calculate cost for the graph generation
            if 'usage_metadata' in graph_data:
                usage = graph_data['usage_metadata']
                cost = calculate_cost('gemini-1.5-flash', usage.get('input_tokens', 0), usage.get('output_tokens', 0))
                usage['cost'] = cost
                
            app.logger.info(f"Step 4: Gemini generated entities. Received {len(graph_data.get('nodes', []))} nodes.")
            return jsonify(graph_data)
        except Exception as e:
            app.logger.error(f"Graph generation failed: {e}")
            return jsonify({"nodes": [], "edges": []}), 500

    @app.route('/api/subjects', methods=['GET'])
    def list_subjects():
        """Returns a list of all subjects."""
        try:
            subjects = spanner_client.get_all_subjects()
            return jsonify(subjects)
        except Exception as e:
            logging.error(f"Failed to list subjects: {e}")
            return jsonify({"error": "Failed to retrieve subjects"}), 500

    @app.route('/api/subjects/check', methods=['GET'])
    def check_subject():
        name = request.args.get('name')
        if not name:
            return jsonify({"error": "Name parameter is required"}), 400
        
        # We need to access spanner_client from outer scope or import
        # spanner_client is imported at module level, so it is accessible.
        try:
            existing_subject = spanner_client.get_subject_by_name(name)
            if existing_subject:
                return jsonify({"exists": True, "subject": existing_subject})
            else:
                return jsonify({"exists": False, "subject": None})
        except Exception as e:
            logging.error(f"Check subject failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/subjects/<subject_id>', methods=['GET'])
    def get_subject_details(subject_id):
        """Returns details for a specific subject, including findings."""
        try:
            subject = spanner_client.get_subject(subject_id)
            if not subject:
                return jsonify({"error": "Subject not found"}), 404
            
            # Auto-Recovery: retrigger summary generation only when it is genuinely
            # missing or broken, and only within a bounded retry budget.
            #
            # The previous version resubmitted on EVERY GET whose risk_score was
            # "Unknown". Since a failed generation also writes "Unknown", each page
            # view launched another Gemini call that failed and rewrote "Unknown" -
            # an unbounded, billable loop. It also cannot distinguish that failure
            # from a legitimate fail-closed verdict, which must NOT be retried away.
            summary_str = subject.get('summary')
            should_regenerate = False
            regenerate_reason = None

            if not summary_str:
                should_regenerate = True
                regenerate_reason = "no summary persisted"
            else:
                try:
                    s_data = json.loads(summary_str)
                    recommendation = s_data.get('recommendation', '')
                    is_deliberate_incomplete = recommendation == RECOMMENDATION_INCOMPLETE

                    if is_deliberate_incomplete:
                        # A fail-closed verdict is a correct result, not a broken one.
                        # Regenerating cannot fix it; only a re-screen can.
                        should_regenerate = False
                    elif s_data.get('risk_score') == 'Unknown' or "Error" in s_data.get('summary', ''):
                        should_regenerate = True
                        regenerate_reason = "summary records a generation error"
                except (json.JSONDecodeError, TypeError, AttributeError):
                    should_regenerate = True
                    regenerate_reason = "summary is not valid JSON"

            if should_regenerate and not _claim_regeneration_slot(subject_id):
                logging.info(
                    f"Suppressing summary regeneration for {subject_id}: "
                    "retry budget exhausted or attempt already in flight."
                )
                should_regenerate = False

            if should_regenerate:
                logging.info(
                    f"Regenerating summary for {subject_id} in background ({regenerate_reason})."
                )
                bg_executor.submit(_regenerate_and_release, subject_id)

            findings = spanner_client.get_findings(subject_id)
            graph_data = spanner_client.get_graph_data(subject_id)
            watchlist_hits = spanner_client.get_watchlist_hits(subject_id)
            case_review = spanner_client.get_case_review(subject_id)
            dispositions = spanner_client.get_finding_dispositions(subject_id)
            audit_events = spanner_client.get_audit_events(subject_id)
            audit_verification = verify_audit_chain(audit_events)
            reviewer = extract_reviewer_identity(request)
            
            return jsonify({
                "subject": subject,
                "findings": findings,
                "graph_data": graph_data,
                "watchlist_hits": watchlist_hits,
                "case_review": case_review,
                "dispositions": dispositions,
                "audit_events": audit_events,
                "audit_chain_verification": audit_verification,
                "reviewer": reviewer.to_dict(),
            })
        except Exception as e:
            logging.error(f"Failed to get details for {subject_id}: {e}")
            return jsonify({"error": str(e)}), 500

    # -----------------------------------------------------------------------
    # Watchlist / Sanctions Screening & Maker-Checker Governance Endpoints
    # -----------------------------------------------------------------------

    @app.route('/api/watchlist/status', methods=['GET'])
    def get_watchlist_status():
        """Returns live coverage and entity counts for SG_MAS, US_OFAC_SDN, UN_SC, and EU_FSF."""
        try:
            return jsonify(watchlist_index.get_status())
        except Exception as e:
            logging.error(f"Failed to fetch watchlist status: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/watchlist/refresh', methods=['POST'])
    def refresh_watchlists():
        """Forces an immediate live refresh of all sanctions watchlists from upstream authorities."""
        try:
            cov = watchlist_index.ensure_loaded(force_refresh=True)
            return jsonify(cov.to_dict()), (200 if cov.is_complete else 503)
        except Exception as e:
            logging.error(f"Failed to refresh watchlists: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/watchlist/screen', methods=['POST'])
    def screen_watchlist_endpoint():
        """Standalone instant sanctions & watchlist screening endpoint."""
        data = request.get_json(silent=True) or {}
        subject_name = (data.get('subject_name') or data.get('subjectName') or '').strip()
        if not subject_name:
            return jsonify({"error": "subject_name is required"}), 400
        filters = {
            "subject_name": subject_name,
            "alias": data.get("alias") or data.get("aliases") or "",
            "dob": data.get("dob") or "",
            "age": data.get("age") or "",
            "region": data.get("region") or data.get("country") or "",
            "company": data.get("company") or "",
            "ownership": data.get("ownership") or "",
            "spouse": data.get("spouse") or "",
        }
        result = screen_subject_watchlists(filters)
        return jsonify(result)

    @app.route('/api/governance/me', methods=['GET'])
    def get_current_reviewer():
        """Returns the authenticated IAP reviewer identity and governance vocabularies."""
        reviewer = extract_reviewer_identity(request)
        return jsonify({
            "reviewer": reviewer.to_dict(),
            "vocabularies": {
                "disposition_verdicts": DISPOSITION_VERDICTS,
                "case_decisions": CASE_DECISIONS,
                "risk_ratings": list(VALID_RISK_RATINGS),
                "case_states": list(CASE_STATES),
            },
        })

    @app.route('/api/subjects/<subject_id>/governance', methods=['GET'])
    def get_subject_governance(subject_id):
        """Returns full Maker-Checker state, item dispositions, watchlist hits, and hash-chained audit log."""
        reviewer = extract_reviewer_identity(request)
        case_review = spanner_client.get_case_review(subject_id)
        dispositions = spanner_client.get_finding_dispositions(subject_id)
        watchlist_hits = spanner_client.get_watchlist_hits(subject_id)
        audit_events = spanner_client.get_audit_events(subject_id)
        audit_verification = verify_audit_chain(audit_events)
        return jsonify({
            "subject_id": subject_id,
            "reviewer": reviewer.to_dict(),
            "case_review": case_review,
            "dispositions": dispositions,
            "watchlist_hits": watchlist_hits,
            "audit_events": audit_events,
            "audit_chain_verification": audit_verification,
            "vocabularies": {
                "disposition_verdicts": DISPOSITION_VERDICTS,
                "case_decisions": CASE_DECISIONS,
                "risk_ratings": list(VALID_RISK_RATINGS),
                "case_states": list(CASE_STATES),
            },
        })

    @app.route('/api/subjects/<subject_id>/dispositions', methods=['POST'])
    def record_item_disposition(subject_id):
        """Maker records a disposition and written rationale on a Finding or WatchlistHit."""
        reviewer = extract_reviewer_identity(request)
        data = request.get_json(silent=True) or {}

        # A locked/approved case cannot have its findings mutated without reopening
        current_review = spanner_client.get_case_review(subject_id)
        if current_review.get("review_state") == "APPROVED":
            return jsonify({
                "error": "Case is locked in APPROVED state. A Checker must return the case to Maker before dispositions can be changed."
            }), 409

        try:
            item_type, verdict, rationale = validate_disposition_input(
                item_id=data.get("item_id"),
                item_type=data.get("item_type", "FINDING"),
                verdict=data.get("verdict"),
                rationale=data.get("rationale"),
            )
        except GovernanceValidationError as e:
            return jsonify({"error": str(e)}), 400

        item_id = str(data.get("item_id")).strip()
        item_title = (data.get("item_title") or item_id).strip()

        disp = spanner_client.upsert_finding_disposition(
            subject_id=subject_id,
            item_id=item_id,
            item_type=item_type,
            item_title=item_title,
            verdict=verdict,
            rationale=rationale,
            maker_email=reviewer.effective_email,
            maker_auth_source=reviewer.auth_source,
        )

        prev_state = current_review.get("review_state") or "UNREVIEWED"
        new_state = "IN_MAKER_REVIEW" if prev_state in ("UNREVIEWED", "RETURNED_TO_MAKER") else prev_state
        if new_state != prev_state:
            current_review["review_state"] = new_state
            spanner_client.save_case_review(current_review)

        audit_event = spanner_client.append_audit_event(
            subject_id=subject_id,
            event_type="ITEM_DISPOSITION_RECORDED",
            actor_email=reviewer.effective_email,
            actor_auth_source=reviewer.auth_source,
            previous_state=prev_state,
            new_state=new_state,
            payload={
                "item_id": item_id,
                "item_type": item_type,
                "item_title": item_title,
                "verdict": verdict,
                "rationale": rationale,
                "authenticated_iap_email": reviewer.authenticated_email,
            },
        )

        all_events = spanner_client.get_audit_events(subject_id)
        return jsonify({
            "disposition": disp,
            "case_review": current_review,
            "dispositions": spanner_client.get_finding_dispositions(subject_id),
            "audit_event": audit_event,
            "audit_events": all_events,
            "audit_chain_verification": verify_audit_chain(all_events),
        })

    @app.route('/api/subjects/<subject_id>/review/submit', methods=['POST'])
    def submit_maker_case_review(subject_id):
        """Maker submits the case risk rating, decision, and justification for Checker sign-off."""
        import datetime as _dt

        reviewer = extract_reviewer_identity(request)
        data = request.get_json(silent=True) or {}

        proposed_risk_rating = (data.get("proposed_risk_rating") or "").strip()
        proposed_decision = (data.get("proposed_decision") or "").strip()
        maker_rationale = (data.get("maker_rationale") or "").strip()

        dispositions = spanner_client.get_finding_dispositions(subject_id)
        watchlist_hits = spanner_client.get_watchlist_hits(subject_id)

        # Load required human-review items from persisted subject summary if available
        required_review_items = []
        subject_row = spanner_client.get_subject(subject_id)
        if subject_row and subject_row.get("summary"):
            try:
                s_obj = json.loads(subject_row["summary"])
                required_review_items = s_obj.get("requires_human_review") or []
            except Exception:
                required_review_items = []

        try:
            validate_maker_submission(
                proposed_risk_rating=proposed_risk_rating,
                proposed_decision=proposed_decision,
                maker_rationale=maker_rationale,
                dispositions_by_item=dispositions,
                watchlist_hits=watchlist_hits,
                required_review_items=required_review_items,
            )
        except GovernanceValidationError as e:
            return jsonify({"error": str(e)}), 400

        current_review = spanner_client.get_case_review(subject_id)
        prev_state = current_review.get("review_state") or "UNREVIEWED"
        now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()

        updated_review = {
            "subject_id": subject_id,
            "review_state": "PENDING_CHECKER_APPROVAL",
            "proposed_risk_rating": proposed_risk_rating,
            "proposed_decision": proposed_decision,
            "maker_email": reviewer.effective_email,
            "maker_auth_source": reviewer.auth_source,
            "maker_rationale": maker_rationale,
            "maker_submitted_at": now_iso,
            "checker_email": None,
            "checker_auth_source": None,
            "checker_action": None,
            "checker_rationale": None,
            "checker_decided_at": None,
        }
        saved_review = spanner_client.save_case_review(updated_review)

        audit_event = spanner_client.append_audit_event(
            subject_id=subject_id,
            event_type="CASE_SUBMITTED_FOR_CHECKER_APPROVAL",
            actor_email=reviewer.effective_email,
            actor_auth_source=reviewer.auth_source,
            previous_state=prev_state,
            new_state="PENDING_CHECKER_APPROVAL",
            payload={
                "proposed_risk_rating": proposed_risk_rating,
                "proposed_decision": proposed_decision,
                "maker_rationale": maker_rationale,
                "disposition_count": len(dispositions),
                "authenticated_iap_email": reviewer.authenticated_email,
            },
        )
        all_events = spanner_client.get_audit_events(subject_id)
        return jsonify({
            "case_review": saved_review,
            "audit_event": audit_event,
            "audit_events": all_events,
            "audit_chain_verification": verify_audit_chain(all_events),
        })

    @app.route('/api/subjects/<subject_id>/review/decide', methods=['POST'])
    def decide_checker_case_review(subject_id):
        """
        Checker approves, returns to Maker, or escalates to MLRO.
        Enforces strict Four-Eyes separation of duties (`maker_email != checker_email`).
        """
        import datetime as _dt

        reviewer = extract_reviewer_identity(request)
        data = request.get_json(silent=True) or {}
        checker_action = (data.get("action") or "").strip().upper()
        checker_rationale = (data.get("checker_rationale") or "").strip()

        current_review = spanner_client.get_case_review(subject_id)
        prev_state = current_review.get("review_state") or "UNREVIEWED"
        maker_email = current_review.get("maker_email") or ""

        if prev_state != "PENDING_CHECKER_APPROVAL":
            return jsonify({
                "error": f"Case is in state '{prev_state}', not 'PENDING_CHECKER_APPROVAL'. Maker must submit the case first."
            }), 400

        try:
            new_state = validate_checker_decision(
                maker_email=maker_email,
                checker_identity=reviewer,
                checker_action=checker_action,
                checker_rationale=checker_rationale,
            )
        except FourEyesViolationError as e:
            # Record the blocked self-approval attempt in the immutable audit trail!
            spanner_client.append_audit_event(
                subject_id=subject_id,
                event_type="FOUR_EYES_VIOLATION_BLOCKED",
                actor_email=reviewer.effective_email,
                actor_auth_source=reviewer.auth_source,
                previous_state=prev_state,
                new_state=prev_state,
                payload={
                    "attempted_action": checker_action,
                    "maker_email": maker_email,
                    "checker_email": reviewer.effective_email,
                    "authenticated_iap_email": reviewer.authenticated_email,
                    "reason": str(e),
                },
            )
            all_events = spanner_client.get_audit_events(subject_id)
            return jsonify({
                "error": str(e),
                "code": "FOUR_EYES_VIOLATION",
                "audit_events": all_events,
                "audit_chain_verification": verify_audit_chain(all_events),
            }), 409
        except GovernanceValidationError as e:
            return jsonify({"error": str(e)}), 400

        now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
        current_review.update({
            "review_state": new_state,
            "checker_email": reviewer.effective_email,
            "checker_auth_source": reviewer.auth_source,
            "checker_action": checker_action,
            "checker_rationale": checker_rationale,
            "checker_decided_at": now_iso,
        })
        saved_review = spanner_client.save_case_review(current_review)

        audit_event = spanner_client.append_audit_event(
            subject_id=subject_id,
            event_type=f"CHECKER_DECISION_{new_state}",
            actor_email=reviewer.effective_email,
            actor_auth_source=reviewer.auth_source,
            previous_state=prev_state,
            new_state=new_state,
            payload={
                "checker_action": checker_action,
                "checker_rationale": checker_rationale,
                "maker_email": maker_email,
                "proposed_risk_rating": saved_review.get("proposed_risk_rating"),
                "proposed_decision": saved_review.get("proposed_decision"),
                "authenticated_iap_email": reviewer.authenticated_email,
            },
        )
        all_events = spanner_client.get_audit_events(subject_id)
        return jsonify({
            "case_review": saved_review,
            "audit_event": audit_event,
            "audit_events": all_events,
            "audit_chain_verification": verify_audit_chain(all_events),
        })

    @app.route('/api/config', methods=['GET'])
    def get_app_config():
        """Serves application configuration options to the frontend."""
        reviewer = extract_reviewer_identity(request)
        return jsonify({
            "SEARCH_TOOLTIPS": app.config['SEARCH_TOOLTIPS'],
            "RISK_CATEGORY_OPTIONS": get_risk_category_options(),
            "REVIEWER": reviewer.to_dict(),
            "GOVERNANCE_VOCABULARIES": {
                "disposition_verdicts": DISPOSITION_VERDICTS,
                "case_decisions": CASE_DECISIONS,
                "risk_ratings": list(VALID_RISK_RATINGS),
                "case_states": list(CASE_STATES),
            },
        })

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve(path):
        static_folder = app.static_folder
        if path != "" and os.path.exists(os.path.join(static_folder, path)):
            return send_from_directory(static_folder, path)
        else:
            index_path = os.path.join(static_folder, 'index.html')
            if os.path.exists(index_path):
                return send_from_directory(static_folder, 'index.html')
            else:
                logging.error(f"index.html not found in React build directory: {static_folder}")
                return abort(404, description="Application entry point (index.html) not found.")

def create_app():
    """Factory function to create and configure the Flask application."""
    app = Flask(__name__, static_folder='../frontend/dist', static_url_path='/')
    
    logging.basicConfig(level=logging.INFO)
    app.logger.info("Flask app created.")

    app.config.from_object(Config)
    
    # Ensure database schema is up to date - REMOVED for Gunicorn/gRPC stability
    # Schema migration is now handled in run_backend.sh before server start
    # try:
    #     with app.app_context():
    #         apply_schema()
    # except Exception as e:
    #     app.logger.warning(f"Schema migration skipped or failed: {e}")


    _register_api_routes(app)

    return app

app = create_app()

# Ongoing monitoring is NOT started here. Module-level init under `gunicorn -w 4`
# created four independent schedulers, each firing the same job. pKYC is now driven
# by Cloud Scheduler against POST /api/tasks/pkyc-sweep; see backend/scheduler.py
# for the provisioning command.
init_scheduler(app)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    env = os.environ.get('FLASK_ENV', 'development')
    debug_mode = env == 'development'
    app.run(debug=debug_mode, use_reloader=False, host='0.0.0.0', port=port)
