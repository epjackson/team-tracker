resource "google_iap_app_engine_service_iam_member" "iap_all_authenticated" {
  project = var.project_id
  app_id  = var.project_id
  service = "default"
  role    = "roles/iap.httpsResourceAccessor"
  member  = "allAuthenticatedUsers"
}
