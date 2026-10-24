# TFLint configuration — run with `tflint --init && tflint --recursive`
# from the repo root (CI does this in .github/workflows/terraform.yml).
# Docs: https://github.com/terraform-linters/tflint

config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "google" {
  enabled = true
  version = "0.40.0"
  source  = "github.com/terraform-linters/tflint-ruleset-google"
}
