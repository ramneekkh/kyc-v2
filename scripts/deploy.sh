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

#!/bin/bash

# === Configuration ===
# Define App identifiers and corresponding Cloud Run Service Names
APP_IDENTIFIER="app" # Argument for Flask app (used as APP_MODE_VAR)

# Define components of the image name
TAG="latest" # This tag remains local to deploy.sh

# === Script Setup ===
# Go to root directory relative to the script
cd "$(dirname "$0")/.."

# Source initialization variables (like PROJECT_ID, APP_SERVICE_NAME, etc.)
# Ensure init.sh sets the PROJECT_ID variable
if [ -f "./scripts/init.sh" ]; then
    echo "Sourcing configuration from init.sh..."
    . ./scripts/init.sh
else
    echo "Error: ./scripts/init.sh not found!"
    exit 1
fi

# Check if PROJECT_ID is set (from init.sh)
if [ -z "$PROJECT_ID" ]; then
    echo "Error: PROJECT_ID environment variable is not set after sourcing init.sh."
    exit 1
fi
echo "Using PROJECT_ID: $PROJECT_ID"

# Check if other required variables are set (from init.sh)
if [ -z "$APP_SERVICE_NAME" ] || \
   [ -z "$GCP_REGION" ] || \
   [ -z "$ARTIFACT_REGISTRY_REPO" ] || \
   [ -z "$IMAGE_NAME" ] || \
   [ -z "$SPANNER_INSTANCE" ] || \
   [ -z "$SPANNER_DATABASE" ] || \
   [ -z "$GCS_BUCKET_NAME" ]; then
    echo "Error: One or more required variables (APP_SERVICE_NAME, GCP_REGION, ARTIFACT_REGISTRY_REPO, IMAGE_NAME, SPANNER_INSTANCE, SPANNER_DATABASE, GCS_BUCKET_NAME) are not set in init.sh."
    exit 1
fi

# === Construct Full Image Name (AFTER PROJECT_ID and IMAGE_NAME are known) ===
MAIN_IMAGE_NAME="${GCP_REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REGISTRY_REPO}/${IMAGE_NAME}:${TAG}"

# === Set Target Specific Variables (Simplified) ===
# Script now deploys only the Flask app, no target argument needed
CLOUDRUN_SERVICE_NAME="$APP_SERVICE_NAME" # Sourced from init.sh
APP_MODE_VAR="$APP_IDENTIFIER" # APP_IDENTIFIER is local script var
echo "Deploying target: Flask App (Service: $CLOUDRUN_SERVICE_NAME, Mode: $APP_MODE_VAR)"

# === GCP Setup ===
echo "Setting GCP project to: $PROJECT_ID"
gcloud config set project "$PROJECT_ID"

# === Build Docker Image (using Cloud Build) ===
# This builds the *single* image containing both apps.
# Ensure cloudbuild.yaml uses the _IMAGE_NAME substitution variable.
echo "Submitting build to Cloud Build..."
echo "Image to be built: $MAIN_IMAGE_NAME" # This should now print the correct path

gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions=_IMAGE_NAME="$MAIN_IMAGE_NAME" \
  --timeout=1200s \
  . # Build context is current directory

# Check if build was successful
if [ $? -ne 0 ]; then
    echo "Error: Cloud Build failed."
    exit 1
fi
echo "Cloud Build finished successfully."

# === Deploy to Cloud Run ===
echo "Deploying image $MAIN_IMAGE_NAME to Cloud Run service $CLOUDRUN_SERVICE_NAME..."

# Prepare environment variables string for Cloud Run
# Non-sensitive variables sourced from init.sh or derived
ENV_VARS="APP_MODE=$APP_MODE_VAR"
ENV_VARS+=",GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
ENV_VARS+=",GEMINI_MODEL=gemini-3.8-flash"
ENV_VARS+=",GOOGLE_GENAI_USE_VERTEXAI=TRUE"
ENV_VARS+=",SPANNER_INSTANCE=$SPANNER_INSTANCE"
ENV_VARS+=",SPANNER_DATABASE=$SPANNER_DATABASE"
ENV_VARS+=",GCS_BUCKET_NAME=$GCS_BUCKET_NAME"
ENV_VARS+=",GOOGLE_CLOUD_DISABLE_OPENTELEMETRY=true"

# --- Screening governance -------------------------------------------------
# Tenant scope for subject-id derivation. Two institutions sharing a deployment
# must not share a subject namespace.
ENV_VARS+=",KYC_TENANT_ID=${KYC_TENANT_ID:-default}"
# Diligence-tier query budgets. These cap Custom Search spend per subject and
# are enforced in code, not just requested in the prompt.
ENV_VARS+=",KYC_QUERIES_STANDARD=${KYC_QUERIES_STANDARD:-25}"
ENV_VARS+=",KYC_QUERIES_ENHANCED=${KYC_QUERIES_ENHANCED:-100}"
ENV_VARS+=",KYC_QUERIES_MONITORING=${KYC_QUERIES_MONITORING:-10}"
# Custom Search quota guardrails.
ENV_VARS+=",KYC_SEARCH_DAILY_LIMIT=${KYC_SEARCH_DAILY_LIMIT:-10000}"
ENV_VARS+=",KYC_SEARCH_MAX_QPS=${KYC_SEARCH_MAX_QPS:-8}"
# pKYC monitoring cadence (days) and per-sweep cap.
ENV_VARS+=",KYC_MONITOR_DAYS_HIGH=${KYC_MONITOR_DAYS_HIGH:-1}"
ENV_VARS+=",KYC_MONITOR_DAYS_MEDIUM=${KYC_MONITOR_DAYS_MEDIUM:-7}"
ENV_VARS+=",KYC_MONITOR_DAYS_LOW=${KYC_MONITOR_DAYS_LOW:-30}"
ENV_VARS+=",KYC_MONITOR_MAX_SUBJECTS=${KYC_MONITOR_MAX_SUBJECTS:-200}"
# Upload hardening.
ENV_VARS+=",KYC_MAX_UPLOAD_BYTES=${KYC_MAX_UPLOAD_BYTES:-20971520}"
# Service accounts permitted to call /api/worker/push and /api/tasks/pkyc-sweep.
# Empty means "reject everything", which is the correct default: an unauthenticated
# task endpoint lets anyone enqueue screening work or replay results.
ENV_VARS+=",KYC_ALLOWED_INVOKER_SAS=${KYC_ALLOWED_INVOKER_SAS:-}"


# Sensitive variables (secrets) loaded from Secret Manager
gcloud run deploy "$CLOUDRUN_SERVICE_NAME" \
    --image "$MAIN_IMAGE_NAME" \
    --region "$GCP_REGION" \
    --platform managed \
    --port 8080 `# Port your container listens on (defined in entrypoint)`\
    --set-env-vars "$ENV_VARS" \
    --set-secrets="GOOGLE_API_KEY=GOOGLE_API_KEY:latest,GOOGLE_SEARCH_API_KEY=GOOGLE_SEARCH_API_KEY:latest,GOOGLE_CSE_ID=GOOGLE_CSE_ID:latest,SB_API_KEY=SB_API_KEY:latest" \
    --min-instances 1 \
    --max-instances 1 \
    --no-allow-unauthenticated `# Configured IAM` \
    --timeout=927s `# Set timeout to 927 seconds`
    # --add-cloudsql-instances "$CLOUD_SQL_INSTANCE_CONNECTION_NAME" \
    

# Check if deployment was successful
if [ $? -ne 0 ]; then
    echo "Error: Cloud Run deployment failed."
    exit 1
fi

echo "Successfully deployed $CLOUDRUN_SERVICE_NAME to Cloud Run in region $GCP_REGION."
SERVICE_URL=$(gcloud run services describe "$CLOUDRUN_SERVICE_NAME" --platform managed --region "$GCP_REGION" --format 'value(status.url)')
echo "Service URL: $SERVICE_URL"
