module "core" {
  source     = "../src"
  project_id = var.project_id
}

module "app" {
  source          = "../src/app"
  project_id      = var.project_id
  region          = var.region
  gcp_env         = var.gcp_env
  iap_login_page_base_uri = var.iap_login_page_base_uri
}

module "firebase" {
  source     = "../src/firebase"
  project_id = var.project_id
  gcp_env = var.gcp_env
}
