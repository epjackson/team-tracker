# Read the Firebase API key from Secret Manager so it never appears in committed files.
data "google_secret_manager_secret_version" "firebase_api_key" {
  project = var.project_id
  secret  = var.firebase_api_key_secret_name
}

data "google_secret_manager_secret_version" "iap_oauth_client_id" {
  project = var.project_id
  secret  = "iap-oauth-client-id"

  depends_on = [google_secret_manager_secret.app_secrets]
}

data "google_secret_manager_secret_version" "iap_oauth_client_secret" {
  project = var.project_id
  secret  = "iap-oauth-client-secret"

  depends_on = [google_secret_manager_secret.app_secrets]
}

# Wire GCIP as the identity provider for the App Engine IAP resource.
# NOTE: The OAuth consent screen must be configured manually in Cloud Console →
# Google Auth Platform. There is no Terraform resource for it (google_iap_brand
# and google_iap_client were deprecated Jan 2025 and shut down March 2026).
resource "google_iap_settings" "app_engine" {
  name = "projects/${var.project_id}/iap_web/appengine-${var.project_id}"

  access_settings {
    oauth_settings {
      client_id     = data.google_secret_manager_secret_version.iap_oauth_client_id.secret_data
      client_secret = data.google_secret_manager_secret_version.iap_oauth_client_secret.secret_data
    }
  }

  depends_on = [google_app_engine_application.app]
}
