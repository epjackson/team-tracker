<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| google | < 8.0 |
| google-beta | < 8.0 |

## Providers

| Name | Version |
| ---- | ------- |
| google | < 8.0 |

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| app | ../src/app | n/a |
| core | ../src | n/a |
| firebase | ../src/firebase | n/a |

## Resources

| Name | Type |
| ---- | ---- |
| [google_app_engine_domain_mapping.team_tracker](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/app_engine_domain_mapping) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| app\_domain | The domain of the application. | `string` | n/a | yes |
| app\_subdomain | The subdomain of the application. | `string` | n/a | yes |
| billing\_account\_id | The ID of the billing account to link to the project. | `string` | n/a | yes |
| gcip\_login\_page\_base\_uri | The base URI for the GCIP login page. | `string` | n/a | yes |
| gcp\_env | The environment to deploy resources in (e.g. dev, staging, prod). | `string` | n/a | yes |
| project\_id | The ID of the GCP project to create. | `string` | n/a | yes |
| region | The region to deploy resources in. | `string` | `"europe-west2"` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| domain\_mapping\_dns\_records | DNS records to create at the registrar (IONOS) for teamtracker.yarmtennisclub.uk. Typically a CNAME to ghs.googlehosted.com. |
<!-- END_TF_DOCS -->
