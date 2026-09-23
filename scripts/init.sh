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


export PROJECT_NAME=turing-striker-452905-u3

export PROJECT_ID=turing-striker-452905-u3

export GCP_REGION="asia-southeast1"

# --- Application and Deployment Configuration ---
# Cloud Run service name for the Flask app
export APP_SERVICE_NAME="aikyc-app"
# Artifact Registry Repository name
export ARTIFACT_REGISTRY_REPO="bblleadgen-agent-repo" # CHANGE IF YOUR REPO NAME IS DIFFERENT
# Name of the Docker image
export IMAGE_NAME="aikyc-main-app"

# --- Cloud Spanner Configuration ---
export SPANNER_INSTANCE="ai-kyc-instance"
export SPANNER_DATABASE="kyc-db"

# --- Cloud Storage Configuration ---
export GCS_BUCKET_NAME="kyc-content-storage"
