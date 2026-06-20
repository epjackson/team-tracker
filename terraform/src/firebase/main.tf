resource "google_firebase_project" "default" {
    provider = google-beta
    project  = var.project_id
}

# Upgrades from Firebase Auth to Identity Platform, required for IAP GCIP integration.
# Without this the IAP API rejects tenant_ids with INVALID_PROJECT_ID.
resource "google_identity_platform_config" "default" {
    provider = google-beta
    project  = var.project_id

    authorized_domains = [
        "localhost",
        "${var.project_id}.appspot.com",
        "${var.project_id}.firebaseapp.com",
        "${var.project_id}.web.app",
    ]

    depends_on = [google_firebase_project.default]
}
