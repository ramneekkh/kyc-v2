#!/bin/bash
set -e

# Configuration
PROJECT_ID="turing-striker-452905-u3"
REGION="asia-southeast1"
REPO_NAME="kyc-repo"
SERVICE_NAME="kyc-app"
IMAGE_NAME="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$SERVICE_NAME"
TOPIC_NAME="kyc-batch-jobs"
SUBSCRIPTION_NAME="kyc-batch-jobs-push"
BUCKET_NAME="$PROJECT_ID-kyc-content"

echo "=================================================="
echo "Deploying KYC Application to Cloud Run"
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Service: $SERVICE_NAME"
echo "Image: $IMAGE_NAME"
echo "=================================================="

# 0. Setup Manual Resources (Fallback for Terraform)
echo "[0/5] Setting up dependencies..."
gcloud services enable artifactregistry.googleapis.com run.googleapis.com pubsub.googleapis.com storage-component.googleapis.com secretmanager.googleapis.com

# Create AR Repo if not exists
if gcloud artifacts repositories describe $REPO_NAME --location=$REGION --project=$PROJECT_ID > /dev/null 2>&1; then
    echo "Artifact Registry repository $REPO_NAME exists."
else
    echo "Creating Artifact Registry repository $REPO_NAME..."
    gcloud artifacts repositories create $REPO_NAME \
        --repository-format=docker \
        --location=$REGION \
        --description="Docker repository for KYC App" \
        --project=$PROJECT_ID
fi

gcloud pubsub topics create $TOPIC_NAME --project $PROJECT_ID || echo "Topic exists."
gcloud storage buckets create gs://$BUCKET_NAME --project $PROJECT_ID --location $REGION || echo "Bucket exists."

# 1. Build Container
echo "[1/5] Submitting build to Cloud Build..."
# Note: Using '.' as context. Ensure Dockerfile is in root.
gcloud builds submit --tag $IMAGE_NAME .

# 1.5 Provision / Update Secrets in Google Cloud Secret Manager
echo "[1.5/5] Storing secrets in Google Cloud Secret Manager..."
upsert_secret() {
    local secret_name=$1
    local secret_val=$2
    if ! gcloud secrets describe "$secret_name" --project="$PROJECT_ID" > /dev/null 2>&1; then
        gcloud secrets create "$secret_name" --replication-policy="automatic" --project="$PROJECT_ID"
    fi
    printf "%s" "$secret_val" | gcloud secrets versions add "$secret_name" --data-file=- --project="$PROJECT_ID"
}

# Secret values are read from the environment, never committed. Source them from
# a local .env (git-ignored) or export them before running:
#   export GOOGLE_CSE_ID=... GOOGLE_SEARCH_API_KEY=... SB_API_KEY=...
# A key checked into a deploy script is a key in every clone, every fork and
# every CI log -- and rotating it means a commit.
if [ -f ".env" ]; then
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
fi

require_env() {
  local name="$1"
  if [ -z "${!name}" ]; then
    echo "Error: $name is not set. Export it or add it to .env before deploying." >&2
    exit 1
  fi
}

require_env GOOGLE_CSE_ID
require_env GOOGLE_SEARCH_API_KEY
require_env SB_API_KEY

upsert_secret "GOOGLE_CSE_ID" "$GOOGLE_CSE_ID"
upsert_secret "GOOGLE_SEARCH_API_KEY" "$GOOGLE_SEARCH_API_KEY"
# GOOGLE_API_KEY is retained only for legacy call paths; Gemini itself runs on
# Vertex AI via ADC and does not use it.
upsert_secret "GOOGLE_API_KEY" "${GOOGLE_API_KEY:-$GOOGLE_SEARCH_API_KEY}"
upsert_secret "SB_API_KEY" "$SB_API_KEY"

# 2. Deploy Cloud Run Service
echo "[2/5] Deploying Cloud Run Service..."

# Prepare Non-Sensitive Env Vars (Gemini 3.8 Flash runs via Vertex AI ADC)
ENV_VARS="GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
ENV_VARS="$ENV_VARS,FLASK_ENV=production,PYTHONUNBUFFERED=True"
ENV_VARS="$ENV_VARS,GEMINI_MODEL=gemini-3.8-flash,GOOGLE_GENAI_USE_VERTEXAI=TRUE"
ENV_VARS="$ENV_VARS,SPANNER_INSTANCE=kyc123"
ENV_VARS="$ENV_VARS,SPANNER_DATABASE=kycdb"
ENV_VARS="$ENV_VARS,GCS_BUCKET_NAME=$BUCKET_NAME"

# --- Screening governance -------------------------------------------------
# Tenant scope for subject-id derivation. Two institutions sharing a deployment
# must not share a subject namespace.
ENV_VARS="$ENV_VARS,KYC_TENANT_ID=${KYC_TENANT_ID:-default}"
# Diligence-tier query budgets. These cap Custom Search spend per subject and
# are enforced in code, not just requested in the prompt.
ENV_VARS="$ENV_VARS,KYC_QUERIES_STANDARD=${KYC_QUERIES_STANDARD:-25}"
ENV_VARS="$ENV_VARS,KYC_QUERIES_ENHANCED=${KYC_QUERIES_ENHANCED:-100}"
ENV_VARS="$ENV_VARS,KYC_QUERIES_MONITORING=${KYC_QUERIES_MONITORING:-10}"
# Custom Search quota guardrails.
ENV_VARS="$ENV_VARS,KYC_SEARCH_DAILY_LIMIT=${KYC_SEARCH_DAILY_LIMIT:-10000}"
ENV_VARS="$ENV_VARS,KYC_SEARCH_MAX_QPS=${KYC_SEARCH_MAX_QPS:-8}"
# pKYC monitoring cadence (days) and per-sweep cap.
ENV_VARS="$ENV_VARS,KYC_MONITOR_DAYS_HIGH=${KYC_MONITOR_DAYS_HIGH:-1}"
ENV_VARS="$ENV_VARS,KYC_MONITOR_DAYS_MEDIUM=${KYC_MONITOR_DAYS_MEDIUM:-7}"
ENV_VARS="$ENV_VARS,KYC_MONITOR_DAYS_LOW=${KYC_MONITOR_DAYS_LOW:-30}"
ENV_VARS="$ENV_VARS,KYC_MONITOR_MAX_SUBJECTS=${KYC_MONITOR_MAX_SUBJECTS:-200}"
# Upload hardening.
ENV_VARS="$ENV_VARS,KYC_MAX_UPLOAD_BYTES=${KYC_MAX_UPLOAD_BYTES:-20971520}"
# Service accounts permitted to call /api/worker/push and /api/tasks/pkyc-sweep.
# Empty means "reject everything", which is the correct default: an unauthenticated
# task endpoint lets anyone enqueue screening work or replay results.
ENV_VARS="$ENV_VARS,KYC_ALLOWED_INVOKER_SAS=${KYC_ALLOWED_INVOKER_SAS:-}"

gcloud run deploy $SERVICE_NAME \
  --image $IMAGE_NAME \
  --region $REGION \
  --platform managed \
  --no-allow-unauthenticated \
  --memory 32Gi \
  --cpu 8 \
  --execution-environment=gen2 \
  --timeout 3600 \
  --concurrency 50 \
  --session-affinity \
  --set-env-vars="$ENV_VARS" \
  --set-secrets="GOOGLE_CSE_ID=GOOGLE_CSE_ID:latest,GOOGLE_SEARCH_API_KEY=GOOGLE_SEARCH_API_KEY:latest,GOOGLE_API_KEY=GOOGLE_API_KEY:latest,SB_API_KEY=SB_API_KEY:latest"

# 3. Get Service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format 'value(status.url)')
echo "Service URL: $SERVICE_URL"

# 4. Task invoker service account
# Both /api/worker/push and /api/tasks/pkyc-sweep now require a verified OIDC
# caller. Previously /api/worker/push was completely unauthenticated: anyone who
# learned the URL could enqueue screening work or inject worker payloads.
echo "[3/6] Configuring task invoker service account..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
TASK_SA_NAME="kyc-task-invoker"
TASK_SA_EMAIL="$TASK_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$TASK_SA_EMAIL" --project "$PROJECT_ID" > /dev/null 2>&1; then
    gcloud iam service-accounts create "$TASK_SA_NAME" \
        --display-name="KYC task invoker (Pub/Sub push + Cloud Scheduler)" \
        --project "$PROJECT_ID"
fi

gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --member="serviceAccount:$TASK_SA_EMAIL" \
  --role="roles/run.invoker" \
  --region="$REGION" \
  --platform=managed > /dev/null

# 4b. Pub/Sub service agent permissions
# With an authenticated push subscription, Pub/Sub does not call Cloud Run as
# itself -- it mints an OIDC token AS the task invoker SA. So the service agent
# needs serviceAccountTokenCreator on that SA. Missing this grant makes
# `subscriptions create --push-auth-service-account` fail outright.
PUBSUB_SA_EMAIL="service-$PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com"

echo "Granting token creator on $TASK_SA_EMAIL to $PUBSUB_SA_EMAIL..."
gcloud iam service-accounts add-iam-policy-binding "$TASK_SA_EMAIL" \
  --member="serviceAccount:$PUBSUB_SA_EMAIL" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project "$PROJECT_ID" > /dev/null

# 5. Create/Update Pub/Sub Push Subscription
echo "[4/6] Configuring Pub/Sub Subscription..."

# --push-auth-service-account is REQUIRED: without it Pub/Sub sends no OIDC
# token and the worker endpoint will (correctly) reject every delivery.
if gcloud pubsub subscriptions describe $SUBSCRIPTION_NAME --project $PROJECT_ID > /dev/null 2>&1; then
    echo "Updating existing subscription..."
    gcloud pubsub subscriptions update $SUBSCRIPTION_NAME \
        --push-endpoint="$SERVICE_URL/api/worker/push" \
        --push-auth-service-account="$TASK_SA_EMAIL" \
        --push-auth-token-audience="$SERVICE_URL" \
        --project $PROJECT_ID
else
    echo "Creating new subscription..."
    gcloud pubsub subscriptions create $SUBSCRIPTION_NAME \
        --topic $TOPIC_NAME \
        --push-endpoint="$SERVICE_URL/api/worker/push" \
        --push-auth-service-account="$TASK_SA_EMAIL" \
        --push-auth-token-audience="$SERVICE_URL" \
        --ack-deadline=600 \
        --project $PROJECT_ID
fi

# 6. Perpetual KYC sweep schedule
# Ongoing monitoring is an external Cloud Scheduler job, not an in-process
# APScheduler: under `gunicorn -w N` the latter runs N times per tick and stops
# entirely when the container scales to zero.
echo "[5/6] Configuring pKYC monitoring schedule..."
gcloud services enable cloudscheduler.googleapis.com --project "$PROJECT_ID" > /dev/null 2>&1 || true

PKYC_JOB_NAME="kyc-pkyc-sweep"
PKYC_SCHEDULE="${PKYC_SCHEDULE:-0 2 * * *}"
PKYC_TIMEZONE="${PKYC_TIMEZONE:-Asia/Singapore}"

PKYC_ARGS=(
  --schedule="$PKYC_SCHEDULE"
  --time-zone="$PKYC_TIMEZONE"
  --uri="$SERVICE_URL/api/tasks/pkyc-sweep"
  --http-method=POST
  --oidc-service-account-email="$TASK_SA_EMAIL"
  --oidc-token-audience="$SERVICE_URL"
  --attempt-deadline=320s
  --max-retry-attempts=3
  --location="$REGION"
  --project="$PROJECT_ID"
)

if gcloud scheduler jobs describe "$PKYC_JOB_NAME" --location="$REGION" --project "$PROJECT_ID" > /dev/null 2>&1; then
    gcloud scheduler jobs update http "$PKYC_JOB_NAME" "${PKYC_ARGS[@]}"
else
    gcloud scheduler jobs create http "$PKYC_JOB_NAME" "${PKYC_ARGS[@]}" \
        --description="Enqueues an incremental re-screen for every subject whose monitoring interval has elapsed."
fi

# 7. Back-fill the OIDC settings now that the service URL is known.
# These cannot be set in the first deploy because the URL is assigned by it.
echo "[6/6] Applying OIDC configuration..."
gcloud run services update "$SERVICE_NAME" \
  --region="$REGION" \
  --platform=managed \
  --update-env-vars="KYC_OIDC_AUDIENCE=$SERVICE_URL,KYC_ALLOWED_INVOKER_SAS=$TASK_SA_EMAIL" \
  > /dev/null

# Grant Access to specific user (Restricted App)
USER_EMAIL="admin@ramneekkhurana.altostrat.com"
echo "Granting invoker permission to $USER_EMAIL..."
gcloud run services add-iam-policy-binding $SERVICE_NAME \
  --member="user:$USER_EMAIL" \
  --role="roles/run.invoker" \
  --region=$REGION \
  --platform=managed

# 8. Spanner Permissions for Cloud Run (Default Compute SA)
# Note: Ideally usage a dedicated Service Account for Cloud Run, but often people use default.
COMPUTE_SA_EMAIL="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
echo "Granting Spanner Database User to $COMPUTE_SA_EMAIL..."
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$COMPUTE_SA_EMAIL" \
    --role="roles/spanner.databaseUser" > /dev/null 2>&1 || echo "Could not grant Spanner role (might already exist or need admin)."

echo "=================================================="
echo "Deployment Complete!"
echo "Service URL: $SERVICE_URL"
echo "Push Subscription: $SUBSCRIPTION_NAME -> $SERVICE_URL/api/worker/push"
echo "=================================================="
