variable "project_id" {
  description = "The ID of the GCP project to create."
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

variable "gcip_login_page_base_uri" {
  description = "The base URI for the GCIP login page."
  type        = string
}

variable "app_domain" {
  description = "The domain of the application."
  type        = string
}

variable "app_subdomain" {
  description = "The subdomain of the application."
  type        = string
}
