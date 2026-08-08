variable "project_id" {
  description = "The ID of the GCP project to create."
  type        = string
}

variable "region" {
  description = "The region to deploy resources in."
  type        = string
  default     = "europe-west2"
}

variable "gcp_env" {
  description = "The environment to deploy resources in (e.g. dev, staging, prod)."
  type        = string
}

variable "firebase_api_key_secret_id" {
  description = "The secret ID for the Firebase API key in Secret Manager."
  type        = string
}

variable "gcip_login_page_base_uri" {
  description = "The base URI for the GCIP login page."
  type        = string
}

variable "app_domain" {
  description = "The domain of the application. Only set for the prod environment; other environments are accessed via appspot.com URLs."
  type        = string
  default     = ""
}

variable "app_subdomain" {
  description = "The subdomain of the application. Only set for the prod environment; other environments are accessed via appspot.com URLs."
  type        = string
  default     = ""
}
