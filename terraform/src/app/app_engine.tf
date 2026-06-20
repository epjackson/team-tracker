resource "google_app_engine_application" "app" {
  project     = var.project_id
  location_id = var.region

  depends_on = [google_project_service.app_engine_reqd_apis]
}

# The App Engine default SA needs Storage Admin so Cloud Build can use the
# staging bucket (staging.{project}.appspot.com) during `gcloud app deploy`.
# The bucket is auto-created by GCP and cannot be managed by Terraform directly
# (bucket names under appspot.com require domain ownership verification).
resource "google_project_iam_member" "appengine_sa_storage_admin" {
  project = var.project_id
  role    = "roles/storage.admin"
  member  = "serviceAccount:${var.project_id}@appspot.gserviceaccount.com"
}
