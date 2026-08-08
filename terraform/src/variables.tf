variable "project_id" {
  description = "The ID of the GCP project to create."
  type        = string
}
variable "gcp_env" {
  description = "The GCP environment (e.g., dev, prod)."
  type        = string
}
variable "region" {
  description = "The GCP region to deploy resources in."
  type        = string
}
