locals {
    apis_services = [
        "appengine.googleapis.com",
        "cloudbuild.googleapis.com",
        "cloudscheduler.googleapis.com",
        "firebase.googleapis.com",
        "iap.googleapis.com",
        "identitytoolkit.googleapis.com",
        "secretmanager.googleapis.com",
    ]
}

resource "google_project_service" "required_apis" {
    for_each = toset(local.apis_services)
    project = var.project_id
    service = each.key
}
