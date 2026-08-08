resource "google_secret_manager_secret_iam_member" "appengine_sa_firebase_api_key" {
    project   = var.project_id
    secret_id = var.firebase_api_key_secret_id
    role      = "roles/secretmanager.secretAccessor"
    member    = "serviceAccount:${var.project_id}@appspot.gserviceaccount.com"
}
