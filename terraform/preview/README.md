<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| google | < 8.0 |
| google-beta | < 8.0 |

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| app | ../src/app | n/a |
| core | ../src | n/a |
| firebase | ../src/firebase | n/a |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| gcip\_login\_page\_base\_uri | The base URI for the GCIP login page. | `string` | n/a | yes |
| gcp\_env | The environment to deploy resources in (e.g. dev, staging, prod). | `string` | n/a | yes |
| project\_id | The ID of the GCP project to create. | `string` | n/a | yes |
| region | The region to deploy resources in. | `string` | `"europe-west2"` | no |
<!-- END_TF_DOCS -->
