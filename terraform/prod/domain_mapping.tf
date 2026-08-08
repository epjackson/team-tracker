# Custom domain for the prod App Engine service.
#
# Prerequisite (one-time, done outside Terraform): the domain
# yarmtennisclub.uk (or this specific subdomain) must be verified as owned
# by the Google account/project running `terraform apply`, via
# https://search.google.com/search-console -> Add property -> Domain,
# using the same account as GOOGLE_APPLICATION_CREDENTIALS / `gcloud auth`.
# `terraform apply` will fail with a 403 "not verified" error until this is done.
resource "google_app_engine_domain_mapping" "team_tracker" {
  project     = var.project_id
  domain_name = "teamtracker.yarmtennisclub.uk"

  ssl_settings {
    ssl_management_type = "AUTOMATIC"
  }

  depends_on = [module.app]
}

output "domain_mapping_dns_records" {
  value       = google_app_engine_domain_mapping.team_tracker.resource_records
  description = "DNS records to create at the registrar (IONOS) for teamtracker.yarmtennisclub.uk. Typically a CNAME to ghs.googlehosted.com."
}
