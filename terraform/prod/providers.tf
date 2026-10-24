terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "< 8.8"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "< 8.8"
    }
  }
}

provider "google" {
  project = var.project_id
}

provider "google-beta" {
  project               = var.project_id
  billing_project       = var.project_id
  user_project_override = true
}
