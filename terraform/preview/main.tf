module "core" {
  source     = "../src"
  project_id = var.project_id
  region     = var.region
  gcp_env    = var.gcp_env
}

module "app" {
  source          = "../src/app"
  project_id      = var.project_id
  region          = var.region
  gcp_env         = var.gcp_env
    gcip_login_page_base_uri = var.gcip_login_page_base_uri

  depends_on = [module.core, module.firebase]
}

module "firebase" {
  source     = "../src/firebase"
  project_id = var.project_id
  gcp_env = var.gcp_env
  firebase_api_key_secret_id = module.core.firebase_api_key_secret_id
  gcip_login_page_base_uri = var.gcip_login_page_base_uri

  depends_on = [module.core]
}
