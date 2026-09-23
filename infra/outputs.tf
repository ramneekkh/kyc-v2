# outputs.tf

output "spanner_instance_id" {
  description = "The ID of the Cloud Spanner instance."
  value       = google_spanner_instance.main_instance.name
}

output "spanner_database_id" {
  description = "The ID of the Cloud Spanner database."
  value       = google_spanner_database.database.name
}

output "gcs_bucket_name" {
  description = "The name of the GCS bucket."
  value       = google_storage_bucket.content_bucket.name
}
output "cloud_run_service_account_email" {
  description = "The email of the service account used by Cloud Run."
  # The service account is the Compute Engine default SA
  value       = "${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}