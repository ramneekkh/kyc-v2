# variables.tf

variable "project_id" {
  description = "The GCP Project ID to deploy the resources in."
  type        = string
  default     = "bbl-leadgen"
}

variable "gcp_region" {
  description = "The GCP region for deployment."
  type        = string
  default     = "asia-southeast1"
}

variable "app_service_name" {
  description = "The name of the Cloud Run service."
  type        = string
  default     = "bblleadgen-app"
}

variable "artifact_registry_repo" {
  description = "The name of the Artifact Registry repository."
  type        = string
  default     = "bblleadgen-agent-repo"
}

variable "image_name" {
  description = "The name of the Docker image."
  type        = string
  default     = "main-app"
}

variable "spanner_instance_name" {
  description = "The name of the Cloud Spanner instance."
  type        = string
  default     = "ai-kyc-instance"
}

variable "spanner_database_name" {
  description = "The name of the Cloud Spanner database."
  type        = string
  default     = "kyc-db"
}

variable "gcs_bucket_name" {
  description = "The name of the GCS bucket for content storage."
  type        = string
  default     = "kyc-content-storage"
}

# These variables will be populated from the environment variables
# set by sourcing the .env file.



variable "google_api_key" {
  description = "Legacy Google API key. Supply via TF_VAR_google_api_key; never commit a default."
  type        = string
  sensitive   = true
}

variable "google_cse_id" {
  description = "Google Custom Search Engine ID. Supply via TF_VAR_google_cse_id; never commit a default."
  type        = string
  sensitive   = true
}

variable "sb_api_key" {
  description = "ScrapingBee API key. Supply via TF_VAR_sb_api_key; never commit a default."
  type        = string
  sensitive   = true
}

# --- Perpetual KYC (pKYC) monitoring ----------------------------------------

variable "service_url" {
  description = <<-EOT
    Base HTTPS URL of the deployed Cloud Run service, e.g.
    https://ai-kyc-app-xxxxxxxxxx-as.a.run.app

    Cloud Scheduler calls <service_url>/api/tasks/pkyc-sweep with an OIDC token
    whose audience is this exact URL, so it must match the value the backend
    validates via KYC_OIDC_AUDIENCE.
  EOT
  type        = string
}

variable "pkyc_schedule" {
  description = <<-EOT
    Cron schedule for the ongoing-monitoring sweep. The default runs daily at
    02:00 so that high-risk subjects (1-day interval) are re-screened every day
    while the search quota is otherwise idle. Per-subject cadence is risk-tiered
    inside the application; this only sets how often the tiers are evaluated,
    so it must be at least as frequent as the shortest interval.
  EOT
  type        = string
  default     = "0 2 * * *"
}

variable "pkyc_schedule_timezone" {
  description = "IANA timezone for pkyc_schedule. Match the supervised entity's reporting timezone."
  type        = string
  default     = "Asia/Singapore"
}
