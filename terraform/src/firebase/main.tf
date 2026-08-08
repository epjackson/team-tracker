# Upgrades from Firebase Auth to Identity Platform, required for IAP GCIP integration.
# Without this the IAP API rejects tenant_ids with INVALID_PROJECT_ID.
resource "google_identity_platform_config" "default" {
    provider = google-beta
    project  = var.project_id

    autodelete_anonymous_users = true
    sign_in {
        allow_duplicate_emails = true
        email {
            enabled = true
            password_required = false
        }
        phone_number {
            enabled = false
        }
    }
    authorized_domains = concat([
        "localhost",
        "${var.project_id}.appspot.com",
        "${var.project_id}.firebaseapp.com",
        "${var.project_id}.web.app",
        "${var.project_id}.nw.r.appspot.com",
        "iap.googleapis.com",
        "${var.gcip_login_page_base_uri}",
    ], var.app_domain != "" ? [
        "${var.app_domain}",
        "${var.app_subdomain}.${var.app_domain}",
    ] : [])
}
