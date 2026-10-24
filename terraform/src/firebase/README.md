<!-- BEGIN_TF_DOCS -->


## Providers

| Name | Version |
| ---- | ------- |
| google | n/a |
| google-beta | n/a |

## Resources

| Name | Type |
| ---- | ---- |
| [google-beta_google_identity_platform_config.default](https://registry.terraform.io/providers/hashicorp/google-beta/latest/docs/resources/google_identity_platform_config) | resource |
| [google_secret_manager_secret_iam_member.appengine_sa_firebase_api_key](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/secret_manager_secret_iam_member) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| firebase\_api\_key\_secret\_id | The secret ID for the Firebase API key in Secret Manager. | `string` | n/a | yes |
| gcip\_login\_page\_base\_uri | The base URI for the GCIP login page. | `string` | n/a | yes |
| gcp\_env | The environment to deploy resources in (e.g. dev, staging, prod). | `string` | n/a | yes |
| project\_id | The ID of the GCP project to create. | `string` | n/a | yes |
| app\_domain | The domain of the application. Only set for the prod environment; other environments are accessed via appspot.com URLs. | `string` | `""` | no |
| app\_subdomain | The subdomain of the application. Only set for the prod environment; other environments are accessed via appspot.com URLs. | `string` | `""` | no |
| region | The region to deploy resources in. | `string` | `"europe-west2"` | no |
<!-- END_TF_DOCS -->
