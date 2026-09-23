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
import concurrent.futures
from flask import Flask, jsonify, request, send_from_directory, abort, Response, stream_with_context
import uuid
# Import Spanner Client
from .database.spanner_client import spanner_client
from .database.schema_manager import apply_schema
from .storage.gcs_client import gcs_client

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

# Global executor for background tasks (e.g. summary regeneration)
bg_executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

from .search.business_logic import get_risk_category_options
from .search.prompts import (
    KYC_SEARCH_QUERY_BRAINSTORMING_PROMPT,
)
from .search.content_processing import analyze_document_authenticity
import tempfile

# --- Worker & Pub/Sub Imports ---
import base64
from .worker import process_subject
from .events.publisher import publish_kyc_job


# --- Hard-coded Priority Query Templates ---
# These are based on the user's provided curl requests
PRIORITY_QUERY_TEMPLATES = [
    '"{subject_name}" AND (launder* OR terror* OR fraud OR corrupt* OR brib* OR traffick* OR "tax evasion" OR arrest* OR embezzle* OR illegal OR "insider deal" OR sanctions OR "sanctions evasion")',
    '"{subject_name}" AND (crime OR investigat* OR alleg* OR convict* OR sentenced OR lawsuit OR litigation OR misdemeanor* OR offen* OR prosecut* OR scam* OR "tax amnesty")',
    '"{subject_name}" AND (DPRK OR "Democratic People’s Republic of Korea" OR "North Korea" OR Iran OR Cuba OR Syria OR Crimea OR Donetsk OR Luhansk OR Kherson OR Zaporizhzhia OR Russia OR Venezuela OR Burma OR Myanmar OR Belarus)'
]


from .search.core import run_kyc_process

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

        filters = {
            "subject_name": subject_name,
            "profession": profession,
            "company": company,
            "region": region,
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
        subject_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, subject_name))
        filters['subject_id'] = subject_id
        
        # Save Subject to Spanner (Non-blocking or fast enough)
        try:
             spanner_client.save_subject(subject_id, subject_name, filters)
        except Exception as e:
             logging.warning(f"Failed to persist subject: {e}")

        def generate_events():
            try:
                # Use shared core logic
                process_generator = run_kyc_process(filters)
                
                for event in process_generator:
                    # Map core events to frontend SSE format
                    event_type = event.get("type")
                    if event_type == "status":
                        yield f'data: {json.dumps({"status": event.get("status"), "message": event.get("message"), "data": event.get("data")})}\n\n'
                    
                    elif event_type == "usage_update":
                        yield f'data: {json.dumps({"status": "usage_update", "data": event.get("data")})}\n\n'
                        
                    elif event_type == "result":
                        yield f'data: {json.dumps({"status": "kyc_result_generated", "message": "Analyzed a source.", "data": event.get("data")})}\n\n'
                        
                    elif event_type == "summary":
                        yield f'data: {json.dumps({"status": "kyc_summary_generated", "message": "Overall assessment complete.", "data": event.get("data")})}\n\n'
                    
                    elif event_type == "graph_data":
                        yield f'data: {json.dumps({"status": "kyc_graph_generated", "message": "Entity graph generated.", "data": event.get("data")})}\n\n'
                
            except Exception as e:
                logging.error(f"An error occurred during KYC check stream: {e}", exc_info=True)
                yield f'data: {json.dumps({"status": "error", "message": "An unexpected error occurred. Please check the logs."})}\n\n'

        return Response(stream_with_context(generate_events()), mimetype='text/event-stream')

    def _scrape_and_prepare_all_findings(findings, subject_name):
        """
        Helper to scrape all findings and transform them into the export-ready JSON format.
        Returns a list of finding dictionaries (with full 'news content').
        """
        # 1. Scrape Content (Parallel)
        scraped_content_map = {}
        app.logger.info(f"Scraping {len(findings)} URLs for full analysis...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_url = {executor.submit(scrape_url_content, res.get('url')): res for res in findings if res.get('url')}
            for future in concurrent.futures.as_completed(future_to_url):
                res = future_to_url[future]
                try:
                    scraped_content = future.result()
                    scraped_content_map[res.get('url')] = scraped_content
                except Exception as exc:
                    app.logger.error(f'Scraping generated an exception for {res.get("url")}: {exc}')
                    scraped_content_map[res.get('url')] = "Scraping failed."

        # 2. Transform Results
        processed_results = []
        for res in findings:
            url = res.get('url')
            scraped_content = scraped_content_map.get(url, "No URL provided.")
            transformed_res = transform_result_for_export(res, subject_name, scraped_content)
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
        """
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
        
        file = request.files['file']
        subject_id = request.form.get('subjectId')
        subject_name = request.form.get('subjectName')
        
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400
            
        if not subject_id or not subject_name:
             return jsonify({"error": "Subject context missing"}), 400

        try:
            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as temp:
                file.save(temp.name)
                temp_path = temp.name
            
            # Analyze
            mime_type = file.content_type or "application/pdf"
            analysis_result = analyze_document_authenticity(temp_path, mime_type)
            
            # Cleanup temp
            os.unlink(temp_path)
            
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
            logging.error(f"Upload failed: {e}")
            return jsonify({"error": str(e)}), 500

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
            
            # Generate deterministic ID
            s_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, name))

            # Check DB if mode is default
            if mode == 'default':
                existing = spanner_client.get_subject_by_name(name)
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

    @app.route('/api/worker/push', methods=['POST'])
    def worker_push():
        """
        Endpoint for Pub/Sub Push subscriptions.
        Receives a message, parses the payload, and executes the worker logic.
        """
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
                subj_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, subject_name))
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
            
            # Auto-Recovery: Check if summary is missing or failed, and retrigger
            summary_str = subject.get('summary')
            should_regenerate = False
            
            if not summary_str:
                should_regenerate = True
            else:
                try:
                    s_data = json.loads(summary_str)
                    if s_data.get('risk_score') == 'Unknown' or "Error" in s_data.get('summary', ''):
                        should_regenerate = True
                except:
                    should_regenerate = True
            
            if should_regenerate:
                logging.info(f"Summary for {subject_id} is missing/invalid. Retriggering regeneration in background.")
                bg_executor.submit(regenerate_kyc_summary, subject_id)

            findings = spanner_client.get_findings(subject_id)
            graph_data = spanner_client.get_graph_data(subject_id)
            
            return jsonify({
                "subject": subject,
                "findings": findings,
                "graph_data": graph_data
            })
        except Exception as e:
            logging.error(f"Failed to get details for {subject_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/config', methods=['GET'])
    def get_app_config():
        """Serves application configuration options to the frontend."""
        return jsonify({
            "SEARCH_TOOLTIPS": app.config['SEARCH_TOOLTIPS'],
            "RISK_CATEGORY_OPTIONS": get_risk_category_options(),
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

# Initialize Scheduler
try:
    from .scheduler import init_scheduler
    init_scheduler(app)
except Exception as e:
    logging.warning(f"Could not start scheduler: {e}")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    env = os.environ.get('FLASK_ENV', 'development')
    debug_mode = env == 'development'
    app.run(debug=debug_mode, use_reloader=False, host='0.0.0.0', port=port)
