#!/bin/bash
set -e

# Configuration
# Every identifier is overridable from the environment so this script is not
# tied to one project. A hardcoded project id is how a deploy script silently
# ships to the wrong environment.
PROJECT_ID="${PROJECT_ID:-elevate-data-508005}"
REGION="${REGION:-asia-southeast1}"
REPO_NAME="${REPO_NAME:-kyc-repo}"
SERVICE_NAME="${SERVICE_NAME:-kyc-app}"
# Machine traffic (Pub/Sub push, Cloud Scheduler) cannot pass through IAP, so it
# gets its own service from the same image. See step 2b for the full reasoning.
WORKER_SERVICE_NAME="${WORKER_SERVICE_NAME:-kyc-worker}"
IMAGE_NAME="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$SERVICE_NAME"
TOPIC_NAME="${TOPIC_NAME:-kyc-batch-jobs}"
SUBSCRIPTION_NAME="${SUBSCRIPTION_NAME:-kyc-batch-jobs-push}"
BUCKET_NAME="${BUCKET_NAME:-$PROJECT_ID-kyc-content}"

# Spanner holds subjects, findings, the dedup hashes and the pKYC monitoring
# state. These must match the instance that actually exists in $PROJECT_ID.
SPANNER_INSTANCE="${SPANNER_INSTANCE:-ai-kyc-instance}"
SPANNER_DATABASE="${SPANNER_DATABASE:-kyc-db}"

# Vertex AI region for Gemini. "global" routes to the nearest available region,
# which is a data-residency consideration for a MAS-supervised entity: pin this
# to asia-southeast1 once the model is served there.
VERTEX_LOCATION="${GOOGLE_CLOUD_LOCATION:-global}"

echo "=================================================="
echo "Deploying KYC Application to Cloud Run"
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Service: $SERVICE_NAME"
echo "Image: $IMAGE_NAME"
echo "=================================================="

# 0. Setup Manual Resources (Fallback for Terraform)
echo "[0/5] Setting up dependencies..."
# spanner: subjects/findings persistence. cloudscheduler: the pKYC sweep.
# aiplatform: Gemini via Vertex ADC. iap: browser access control on Cloud Run.
gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  pubsub.googleapis.com \
  storage-component.googleapis.com \
  secretmanager.googleapis.com \
  spanner.googleapis.com \
  cloudscheduler.googleapis.com \
  aiplatform.googleapis.com \
  iap.googleapis.com \
  --project "$PROJECT_ID"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

# Roles for the Cloud Run runtime identity. The deploy itself fails without
# secretAccessor (--set-secrets is resolved at deploy time), and every Gemini
# call 403s without aiplatform.user, so these are granted before the deploy
# rather than after it.
RUNTIME_SA="${RUNTIME_SA:-$PROJECT_NUMBER-compute@developer.gserviceaccount.com}"
echo "Granting runtime roles to $RUNTIME_SA..."
for role in \
  roles/secretmanager.secretAccessor \
  roles/aiplatform.user \
  roles/spanner.databaseUser \
  roles/storage.objectAdmin \
  roles/pubsub.publisher \
  roles/logging.logWriter
do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$RUNTIME_SA" \
    --role="$role" \
    --condition=None > /dev/null 2>&1 \
    || echo "  WARN: could not grant $role (may already exist or need admin)"
done

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
gcloud builds submit --tag $IMAGE_NAME --project "$PROJECT_ID" .

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

# A redeploy must not demand that you re-paste keys that are already
# provisioned -- that is exactly the pressure that puts them back into the
# script. Supply a value to rotate; supply nothing and the existing version
# stands. Only a secret that is both unset and absent is fatal.
provision_secret() {
  local name="$1"
  local value="$2"
  if [ -n "$value" ]; then
    upsert_secret "$name" "$value"
  elif gcloud secrets describe "$name" --project="$PROJECT_ID" > /dev/null 2>&1; then
    echo "  $name: already in Secret Manager, leaving existing version in place."
  else
    echo "Error: $name is neither set in the environment nor present in Secret Manager." >&2
    echo "       Export it or add it to .env before deploying." >&2
    exit 1
  fi
}

provision_secret "GOOGLE_CSE_ID" "${GOOGLE_CSE_ID:-}"
provision_secret "GOOGLE_SEARCH_API_KEY" "${GOOGLE_SEARCH_API_KEY:-}"
# GOOGLE_API_KEY is retained only for legacy call paths; Gemini itself runs on
# Vertex AI via ADC and does not use it.
provision_secret "GOOGLE_API_KEY" "${GOOGLE_API_KEY:-}"
provision_secret "SB_API_KEY" "${SB_API_KEY:-}"

# 2. Deploy Cloud Run Service
echo "[2/5] Deploying Cloud Run Service..."

# Prepare Non-Sensitive Env Vars (Gemini 3.8 Flash runs via Vertex AI ADC)
ENV_VARS="GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
ENV_VARS="$ENV_VARS,FLASK_ENV=production,PYTHONUNBUFFERED=True"
ENV_VARS="$ENV_VARS,GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.8-flash},GOOGLE_GENAI_USE_VERTEXAI=TRUE"
ENV_VARS="$ENV_VARS,GOOGLE_CLOUD_LOCATION=$VERTEX_LOCATION"
ENV_VARS="$ENV_VARS,SPANNER_INSTANCE=$SPANNER_INSTANCE"
ENV_VARS="$ENV_VARS,SPANNER_DATABASE=$SPANNER_DATABASE"
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
  --project $PROJECT_ID \
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

# 2b. Deploy the worker service (same image, no IAP).
#
# Why a second service rather than one service doing both jobs:
# IAP's direct Cloud Run integration authenticates against a Google-managed
# OAuth client, and it rejects service-account OIDC tokens outright --
# "Invalid JWT audience" -- no matter which audience they are minted for.
# There is also no per-path IAP bypass. So a single IAP-fronted service can
# serve the browser UI or receive Pub/Sub push and Cloud Scheduler calls, but
# not both. Worse, the failure is silent: IAP drops the request at the edge
# and nothing appears in the container log, so a broken pKYC sweep looks
# exactly like a sweep with no due subjects.
#
# The split keeps the human boundary (IAP sign-in) and the machine boundary
# (Cloud Run IAM + the app's own OIDC allow-list) independent. Both services
# scale to zero, so the idle cost of the second one is nil.
echo "[2b] Deploying worker service for machine traffic..."
gcloud run deploy "$WORKER_SERVICE_NAME" \
  --image $IMAGE_NAME \
  --region $REGION \
  --project $PROJECT_ID \
  --platform managed \
  --no-allow-unauthenticated \
  --memory 32Gi \
  --cpu 8 \
  --execution-environment=gen2 \
  --timeout 3600 \
  --concurrency 50 \
  --set-env-vars="$ENV_VARS" \
  --set-secrets="GOOGLE_CSE_ID=GOOGLE_CSE_ID:latest,GOOGLE_SEARCH_API_KEY=GOOGLE_SEARCH_API_KEY:latest,GOOGLE_API_KEY=GOOGLE_API_KEY:latest,SB_API_KEY=SB_API_KEY:latest"

# 3. Get Service URLs
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --project $PROJECT_ID --format 'value(status.url)')
WORKER_URL=$(gcloud run services describe "$WORKER_SERVICE_NAME" --platform managed --region $REGION --project $PROJECT_ID --format 'value(status.url)')
echo "UI Service URL:     $SERVICE_URL"
echo "Worker Service URL: $WORKER_URL"

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

gcloud run services add-iam-policy-binding "$WORKER_SERVICE_NAME" \
  --member="serviceAccount:$TASK_SA_EMAIL" \
  --role="roles/run.invoker" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
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
# Targets the worker service, not the UI service: the UI sits behind IAP, which
# rejects service-account tokens.
echo "[4/6] Configuring Pub/Sub Subscription..."

# --push-auth-service-account is REQUIRED: without it Pub/Sub sends no OIDC
# token and the worker endpoint will (correctly) reject every delivery.
if gcloud pubsub subscriptions describe $SUBSCRIPTION_NAME --project $PROJECT_ID > /dev/null 2>&1; then
    echo "Updating existing subscription..."
    gcloud pubsub subscriptions update $SUBSCRIPTION_NAME \
        --push-endpoint="$WORKER_URL/api/worker/push" \
        --push-auth-service-account="$TASK_SA_EMAIL" \
        --push-auth-token-audience="$WORKER_URL" \
        --project $PROJECT_ID
else
    echo "Creating new subscription..."
    gcloud pubsub subscriptions create $SUBSCRIPTION_NAME \
        --topic $TOPIC_NAME \
        --push-endpoint="$WORKER_URL/api/worker/push" \
        --push-auth-service-account="$TASK_SA_EMAIL" \
        --push-auth-token-audience="$WORKER_URL" \
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
  --uri="$WORKER_URL/api/tasks/pkyc-sweep"
  --http-method=POST
  --oidc-service-account-email="$TASK_SA_EMAIL"
  --oidc-token-audience="$WORKER_URL"
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

# 7. Back-fill the worker's OIDC settings now that its URL is known.
# The worker verifies inbound tokens itself (audience + service-account
# allow-list) because nothing else stands in front of it. This is the machine
# equivalent of the IAP sign-in that protects the UI service.
echo "[6/6] Applying worker OIDC configuration..."
gcloud run services update "$WORKER_SERVICE_NAME" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --platform=managed \
  --update-env-vars="KYC_OIDC_AUDIENCE=$WORKER_URL,KYC_ALLOWED_INVOKER_SAS=$TASK_SA_EMAIL" \
  > /dev/null

# 8. Identity-Aware Proxy
# The service stays --no-allow-unauthenticated; IAP sits in front and performs
# the browser sign-in. This is what makes the URL clickable without making the
# live Custom Search and ScrapingBee keys reachable by anyone with the link.
echo "[7/7] Configuring Identity-Aware Proxy..."

# The IAP service agent must exist before it can be granted anything.
gcloud beta services identity create --service=iap.googleapis.com --project="$PROJECT_ID" > /dev/null 2>&1 || true
IAP_SA_EMAIL="service-$PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com"

gcloud beta run services update "$SERVICE_NAME" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --iap > /dev/null

# IAP invokes Cloud Run as itself once the user has passed sign-in.
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --member="serviceAccount:$IAP_SA_EMAIL" \
  --role="roles/run.invoker" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --platform=managed > /dev/null

# Who may sign in. Defaults to the whole Workspace domain; narrow this to
# individual users or a group for anything holding real customer data.
IAP_MEMBER="${IAP_MEMBER:-domain:ramneekkhurana.altostrat.com}"
echo "Granting IAP access to $IAP_MEMBER..."
gcloud beta iap web add-iam-policy-binding \
  --resource-type=cloud-run \
  --service="$SERVICE_NAME" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --member="$IAP_MEMBER" \
  --role="roles/iap.httpsResourceAccessor" > /dev/null

# NOTE: the task invoker SA is deliberately NOT granted IAP access.
# Machine callers do not go through IAP at all -- they hit $WORKER_SERVICE_NAME,
# which is protected by Cloud Run IAM and the app's own OIDC allow-list.
# Granting it here would not help in any case: IAP rejects service-account
# tokens with "Invalid JWT audience" no matter which audience they carry.

# Direct invoker for the operator, so `gcloud run services proxy` still works
# for debugging when IAP sign-in is not convenient.
USER_EMAIL="${USER_EMAIL:-admin@ramneekkhurana.altostrat.com}"
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --member="user:$USER_EMAIL" \
  --role="roles/run.invoker" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --platform=managed > /dev/null

echo "=================================================="
echo "Deployment Complete!"
echo ""
echo "  UI (IAP sign-in, $IAP_MEMBER):"
echo "    $SERVICE_URL"
echo ""
echo "  Worker (Cloud Run IAM + OIDC allow-list, no browser access):"
echo "    $WORKER_URL"
echo "    push:  $SUBSCRIPTION_NAME -> $WORKER_URL/api/worker/push"
echo "    pKYC:  $PKYC_JOB_NAME ($PKYC_SCHEDULE $PKYC_TIMEZONE) -> $WORKER_URL/api/tasks/pkyc-sweep"
echo "=================================================="
