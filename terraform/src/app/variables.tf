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
  description = "The GCP environment to deploy resources in."
  type        = string
}

variable "iap_login_page_base_uri" {
  description = "Base URL of the GCIP-IAP sign-in page, without query parameters (e.g. https://example.firebaseapp.com)."
  type        = string
}

variable "firebase_api_key_secret_name" {
  description = "Secret Manager secret name holding the Firebase Web API key."
  type        = string
  default     = "firebase-api-key"
}
