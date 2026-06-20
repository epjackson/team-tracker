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
