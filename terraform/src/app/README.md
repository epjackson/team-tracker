<!-- BEGIN_TF_DOCS -->


## Providers

| Name | Version |
| ---- | ------- |
| google | n/a |

## Resources

| Name | Type |
| ---- | ---- |
| [google_iap_app_engine_service_iam_member.iap_all_authenticated](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/iap_app_engine_service_iam_member) | resource |
| [google_iap_settings.app_engine](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/iap_settings) | resource |
| [google_project_iam_member.appengine_sa](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/project_iam_member) | resource |
| [google_secret_manager_secret.app_secrets](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/secret_manager_secret) | resource |
| [google_project.current](https://registry.terraform.io/providers/hashicorp/google/latest/docs/data-sources/project) | data source |
| [google_secret_manager_secret_version.firebase_api_key](https://registry.terraform.io/providers/hashicorp/google/latest/docs/data-sources/secret_manager_secret_version) | data source |
| [google_secret_manager_secret_version.iap_oauth_client_id](https://registry.terraform.io/providers/hashicorp/google/latest/docs/data-sources/secret_manager_secret_version) | data source |
| [google_secret_manager_secret_version.iap_oauth_client_secret](https://registry.terraform.io/providers/hashicorp/google/latest/docs/data-sources/secret_manager_secret_version) | data source |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| gcip\_login\_page\_base\_uri | The base URI for the GCIP login page. | `string` | n/a | yes |
| gcp\_env | The GCP environment to deploy resources in. | `string` | n/a | yes |
| project\_id | The ID of the GCP project to create. | `string` | n/a | yes |
| firebase\_api\_key\_secret\_name | Secret Manager secret name holding the Firebase Web API key. | `string` | `"firebase-api-key"` | no |
| region | The region to deploy resources in. | `string` | `"europe-west2"` | no |
<!-- END_TF_DOCS -->
