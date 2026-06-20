resource "google_secret_manager_secret" "firebase_api_key" {
    project      = var.project_id
    secret_id = "firebase-api-key"

    labels = {
        environment = var.gcp_env
    }
    replication {
        user_managed {
            replicas {
                location = var.region
            }
        }
    }
}

resource "google_secret_manager_secret_iam_member" "appengine_sa_firebase_api_key" {
    project   = var.project_id
    secret_id = google_secret_manager_secret.firebase_api_key.secret_id
    role      = "roles/secretmanager.secretAccessor"
    member    = "serviceAccount:${var.project_id}@appspot.gserviceaccount.com"
}
