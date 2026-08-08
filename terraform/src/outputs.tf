output "firebase_api_key_secret_id" {
  value       = google_secret_manager_secret.firebase_api_key.secret_id
  description = "The secret ID for the Firebase API key in Secret Manager."
}
