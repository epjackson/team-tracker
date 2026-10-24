<!-- BEGIN_TF_DOCS -->


## Providers

| Name | Version |
| ---- | ------- |
| google | n/a |
| google-beta | n/a |

## Resources

| Name | Type |
| ---- | ---- |
| [google-beta_google_firebase_project.default](https://registry.terraform.io/providers/hashicorp/google-beta/latest/docs/resources/google_firebase_project) | resource |
| [google_app_engine_application.app](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/app_engine_application) | resource |
| [google_project_service.required_apis](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/project_service) | resource |
| [google_secret_manager_secret.firebase_api_key](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/secret_manager_secret) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| gcp\_env | The GCP environment (e.g., dev, prod). | `string` | n/a | yes |
| project\_id | The ID of the GCP project to create. | `string` | n/a | yes |
| region | The GCP region to deploy resources in. | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| firebase\_api\_key\_secret\_id | The secret ID for the Firebase API key in Secret Manager. |
<!-- END_TF_DOCS -->
