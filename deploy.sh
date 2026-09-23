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

# 2. Deploy Cloud Run Service
echo "[2/5] Deploying Cloud Run Service..."

# Prepare Env Vars
# Note: In production, use Secrets for API Keys. For now, passing as Env Vars for speed.
ENV_VARS="GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
ENV_VARS="$ENV_VARS,FLASK_ENV=production,PYTHONUNBUFFERED=True"
ENV_VARS="$ENV_VARS,GOOGLE_API_KEY=AIzaSyDJWAUv5hQA1p2zeK4YxVoIabmsecZGl-Y"
ENV_VARS="$ENV_VARS,GOOGLE_CSE_ID=e26abedfc9e4f49e1"
ENV_VARS="$ENV_VARS,SPANNER_INSTANCE=kyc123"
ENV_VARS="$ENV_VARS,SPANNER_DATABASE=kycdb"
ENV_VARS="$ENV_VARS,GCS_BUCKET_NAME=$BUCKET_NAME"

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
  --set-env-vars="$ENV_VARS"

# 3. Get Service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format 'value(status.url)')
echo "Service URL: $SERVICE_URL"

# 4. Create/Update Pub/Sub Push Subscription
echo "[3/5] Configuring Pub/Sub Subscription..."

# Check if subscription exists
if gcloud pubsub subscriptions describe $SUBSCRIPTION_NAME --project $PROJECT_ID > /dev/null 2>&1; then
    echo "Updating existing subscription..."
    gcloud pubsub subscriptions update $SUBSCRIPTION_NAME \
        --push-endpoint="$SERVICE_URL/api/worker/push" \
        --project $PROJECT_ID
else
    echo "Creating new subscription..."
    gcloud pubsub subscriptions create $SUBSCRIPTION_NAME \
        --topic $TOPIC_NAME \
        --push-endpoint="$SERVICE_URL/api/worker/push" \
        --ack-deadline=600 \
        --project $PROJECT_ID
fi

# 5. Service Account Permission
# Ensure the Pub/Sub service account has permission to invoke this specific service
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
PUBSUB_SA_EMAIL="service-$PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com"

echo "Granting invoker permission to $PUBSUB_SA_EMAIL..."
gcloud run services add-iam-policy-binding $SERVICE_NAME \
  --member="serviceAccount:$PUBSUB_SA_EMAIL" \
  --role="roles/run.invoker" \
  --region=$REGION \
  --platform=managed

# Grant Access to specific user (Restricted App)
USER_EMAIL="admin@ramneekkhurana.altostrat.com"
echo "Granting invoker permission to $USER_EMAIL..."
gcloud run services add-iam-policy-binding $SERVICE_NAME \
  --member="user:$USER_EMAIL" \
  --role="roles/run.invoker" \
  --region=$REGION \
  --platform=managed

# 6. Spanner Permissions for Cloud Run (Default Compute SA)
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
