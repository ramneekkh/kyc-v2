# main.tf

# --- Provider & Project Configuration ---
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "6.42.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.gcp_region
}

# --- Enable Necessary Google Cloud APIs ---
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "aiplatform.googleapis.com",
    "generativelanguage.googleapis.com",
    "customsearch.googleapis.com",
    "logging.googleapis.com",
    "compute.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "spanner.googleapis.com",
    "storage-component.googleapis.com",
    "pubsub.googleapis.com",
    "cloudscheduler.googleapis.com"
  ])
  project                    = var.project_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = true
}

# --- Artifact Registry ---
resource "google_artifact_registry_repository" "repo" {
  provider      = google
  project       = var.project_id
  location      = var.gcp_region
  repository_id = var.artifact_registry_repo
  description   = "Repository for the AI LeadGen application"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis["artifactregistry.googleapis.com"]]
}

# --- Cloud Spanner ---
resource "google_spanner_instance" "main_instance" {
  config       = "regional-${var.gcp_region}"
  display_name = var.spanner_instance_name
  num_nodes    = 1
  project      = var.project_id
  
  # Ensure the instance name matches the variable
  name         = var.spanner_instance_name
  
  depends_on   = [google_project_service.apis["spanner.googleapis.com"]]
}

resource "google_spanner_database" "database" {
  instance            = google_spanner_instance.main_instance.name
  name                = var.spanner_database_name
  project             = var.project_id
  database_dialect    = "POSTGRESQL"
  deletion_protection = false
}

# --- Cloud Storage (GCS) ---
resource "google_storage_bucket" "content_bucket" {
  name          = "${var.project_id}-${var.gcs_bucket_name}" # Ensure global uniqueness
  location      = var.gcp_region
  force_destroy = true
  
  uniform_bucket_level_access = true
  depends_on = [google_project_service.apis["storage-component.googleapis.com"]]
}

# --- Pub/Sub ---
resource "google_pubsub_topic" "kyc_jobs" {
  name = "kyc-batch-jobs"
  project = var.project_id
  depends_on = [google_project_service.apis["pubsub.googleapis.com"]]
}

# --- Secret Manager (Non-ADC Secrets: Search Engine ID, Search Engine API Key, ScrapingBee Key) ---

resource "google_secret_manager_secret" "google_api_key" {
  provider  = google
  project   = var.project_id
  secret_id = "GOOGLE_API_KEY"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "google_api_key_version" {
  provider    = google
  secret      = google_secret_manager_secret.google_api_key.id
  secret_data = var.google_api_key
}

resource "google_secret_manager_secret" "google_search_api_key" {
  provider  = google
  project   = var.project_id
  secret_id = "GOOGLE_SEARCH_API_KEY"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "google_search_api_key_version" {
  provider    = google
  secret      = google_secret_manager_secret.google_search_api_key.id
  secret_data = var.google_api_key
}

resource "google_secret_manager_secret" "google_cse_id" {
  provider  = google
  project   = var.project_id
  secret_id = "GOOGLE_CSE_ID"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "google_cse_id_version" {
  provider    = google
  secret      = google_secret_manager_secret.google_cse_id.id
  secret_data = var.google_cse_id
}

resource "google_secret_manager_secret" "sb_api_key" {
  provider  = google
  project   = var.project_id
  secret_id = "SB_API_KEY"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "sb_api_key_version" {
  provider    = google
  secret      = google_secret_manager_secret.sb_api_key.id
  secret_data = var.sb_api_key
}

# --- IAM Permissions ---
data "google_project" "project" {}

resource "google_secret_manager_secret_iam_member" "secret_accessor" {
  for_each = toset([
    google_secret_manager_secret.google_api_key.secret_id,
    google_secret_manager_secret.google_search_api_key.secret_id,
    google_secret_manager_secret.google_cse_id.secret_id,
    google_secret_manager_secret.sb_api_key.secret_id
  ])
  project   = var.project_id
  secret_id = each.key
  role      = "roles/secretmanager.secretAccessor"
  # Granting permission to the Compute Engine default SA, which Cloud Run will use
  member    = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "vertex_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "spanner_user" {
  project = var.project_id
  role    = "roles/spanner.databaseUser"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "gcs_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "pubsub_subscriber" {
  project = var.project_id
  role    = "roles/pubsub.subscriber"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

# Grant Pub/Sub Service Account permission to invoke Cloud Run
# This is needed for Push Subscriptions
resource "google_project_service_identity" "pubsub_sa" {
  provider = google-beta
  project  = var.project_id
  service  = "pubsub.googleapis.com"
}

resource "google_project_iam_member" "pubsub_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# --- Perpetual KYC (pKYC) monitoring sweep ---------------------------------
#
# Ongoing monitoring runs as an external Cloud Scheduler job, NOT as an
# in-process APScheduler. Under `gunicorn -w N` an in-process scheduler runs
# once per worker, so the same subjects were re-screened N times per tick and
# the schedule died silently whenever the container was scaled to zero. An
# external trigger also gives us retries, an audit trail of every invocation,
# and a schedule that survives deploys -- all of which an examiner will ask for.

resource "google_service_account" "scheduler_invoker" {
  project      = var.project_id
  account_id   = "kyc-scheduler-invoker"
  display_name = "KYC pKYC scheduler invoker"
  description  = "Identity Cloud Scheduler uses to call the pKYC sweep endpoint."
}

resource "google_cloud_run_service_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = var.gcp_region
  service  = var.app_service_name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_invoker.email}"
}

resource "google_cloud_scheduler_job" "pkyc_sweep" {
  project     = var.project_id
  region      = var.gcp_region
  name        = "kyc-pkyc-sweep"
  description = "Enqueues an incremental re-screen for every subject whose monitoring interval has elapsed."
  schedule    = var.pkyc_schedule
  time_zone   = var.pkyc_schedule_timezone

  # The endpoint publishes to Pub/Sub and returns quickly; it does not screen inline.
  attempt_deadline = "320s"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "30s"
    max_backoff_duration = "300s"
  }

  http_target {
    http_method = "POST"
    uri         = "${var.service_url}/api/tasks/pkyc-sweep"

    oidc_token {
      service_account_email = google_service_account.scheduler_invoker.email
      audience              = var.service_url
    }
  }

  depends_on = [google_project_service.apis]
}


resource "google_project_iam_member" "cloud_build_permissions" {
  for_each = toset([
    "roles/run.admin",
    "roles/iam.serviceAccountUser",
    "roles/storage.objectViewer",
    "roles/artifactregistry.writer",
    "roles/logging.logWriter"
  ])
  project = var.project_id
  role    = each.key
  # Granting permissions to the Compute Engine default SA for Cloud Build
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
  depends_on = [google_project_service.apis["cloudbuild.googleapis.com"]]
}
