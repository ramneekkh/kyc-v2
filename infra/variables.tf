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
  description = "The Google API Key. Set via TF_VAR_google_api_key environment variable."
  type        = string
  sensitive   = true
}

variable "google_cse_id" {
  description = "The Google Custom Search Engine ID. Set via TF_VAR_google_cse_id environment variable."
  type        = string
  sensitive   = true
}
