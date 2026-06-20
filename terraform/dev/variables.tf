variable "project_id" {
  description = "The ID of the GCP project to create."
  type        = string
}

variable "billing_account_id" {
  description = "The ID of the billing account to link to the project."
  type        = string
}

variable "gcp_env" {
  description = "The environment to deploy resources in (e.g. dev, staging, prod)."
  type        = string
}

variable "region" {
  description = "The region to deploy resources in."
  type        = string
  default     = "europe-west2"
}

variable "iap_login_page_base_uri" {
  description = "Base URL of the GCIP-IAP sign-in page, without query parameters (e.g. https://example.firebaseapp.com)."
  type        = string
}
