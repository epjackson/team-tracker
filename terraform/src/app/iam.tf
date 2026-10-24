locals {
  appengine_sa_roles = [
    "roles/logging.logWriter",
    "roles/storage.admin",
    "roles/artifactregistry.writer",
    "roles/firebaseauth.admin",
    # Needed so the Cloud Scheduler-triggered traffic migration (deploy.sh's
    # --migrate-at flow) can PATCH the App Engine service's traffic split
    # (appengine.services.update — not covered by roles/appengine.deployer).
    # Without this, staged deploys silently stay at 0% traffic on the new
    # version — the scheduler job fires but the API call is PERMISSION_DENIED.
    "roles/appengine.appAdmin"
  ]
}

# The App Engine default SA needs Storage Admin so Cloud Build can use the
# staging bucket (staging.{project}.appspot.com) during `gcloud app deploy`.
# The bucket is auto-created by GCP and cannot be managed by Terraform directly
# (bucket names under appspot.com require domain ownership verification).
resource "google_project_iam_member" "appengine_sa" {
  for_each = toset(local.appengine_sa_roles)
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${var.project_id}@appspot.gserviceaccount.com"
}
