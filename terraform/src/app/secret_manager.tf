locals {
    app_secrets = [
        "iap-oauth-client-id",
        "iap-oauth-client-secret"
    ]
}

resource "google_secret_manager_secret" "app_secrets" {
    for_each = toset(local.app_secrets)
    project      = var.project_id
    secret_id = each.key
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

    depends_on = [google_project_service.app_engine_reqd_apis]
}
