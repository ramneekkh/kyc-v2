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

# backend/config.py
import os

class Config:
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Database Configurations ---
    if os.environ.get('USE_CLOUD_SQL', 'false').lower() == 'true':
        # Cloud SQL PostgreSQL connection via Unix socket
        SQLALCHEMY_DATABASE_URI = (
            f"postgresql+psycopg2://{os.environ.get('DB_USER')}:"
            f"{os.environ.get('DB_PASSWORD')}@/"
            f"{os.environ.get('DB_NAME')}?"
            f"host=/cloudsql/{os.environ.get('CLOUD_SQL_INSTANCE_CONNECTION_NAME')}"
        )
        # Add robust connection pooling options for Cloud SQL
        # These settings help manage connections in a serverless environment
        # by recycling connections before they time out.
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_recycle": 280,  # Recycle connections every 280 seconds (less than the typical 5-10 minute idle timeout)
            "pool_timeout": 30,   # How long to wait for a connection from the pool
            "pool_pre_ping": True # Check if a connection is still alive before using it from the pool
        }
    else:
        # Fallback to local SQLite database
        SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///site.db')
        SQLALCHEMY_ENGINE_OPTIONS = {} # No special options needed for SQLite

    # --- Frontend Configurations (Sourced from business_logic.py in app.py) ---
    REGION_OPTIONS = [
        "ASEAN", "Singapore", "Malaysia", "Thailand", "Indonesia", "Vietnam",
        "Philippines", "Greater China Region", "Mainland China", "Hong Kong", "Taiwan"
    ]

    SLIDER_CONFIGS = {
        "num_queries": {"min": 5, "max": 10, "step": 1},
        "num_results": {"min": 5, "max": 10, "step": 1},
        "recency_days": {"min": 14, "max": 365, "step": 1}
    }

    SEARCH_TOOLTIPS = {
        "keywords": "Enter keywords relevant to the company or industry you are searching for. Press Enter to add each keyword.",
        "signal_type": "Select the types of FDI signals you want to discover. Multiple selections are allowed.",
        "region": "Choose the target geographic region for FDI. This narrows down the search scope.",
        "num_queries": "Adjust the number of AI-generated search queries for each selected signal type. Higher values may find more leads but increase search time.",
        "num_results": "Set the maximum number of Google search results to retrieve per query. More results provide richer context but prolong analysis.",
        "recency_days": "Define how recent the search results should be, in days. For fresh leads, keep this value lower."
    }
