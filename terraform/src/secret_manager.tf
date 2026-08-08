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
