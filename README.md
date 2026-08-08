# team-tracker
PoC tennis team tracker tool

# Setup
`{bash}
brew update && brew install --cask gcloud-cli
`

# Initial deployment (foundational APIs)
```bash
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com secretmanager.googleapis.com
```

# Deploy

Deploy to App Engine with `deploy.sh`. The GCP project is looked up from
`terraform/<env>/terraform.tfvars` (`project_id`), so it always matches the
terraform-provisioned project. The script generates a fresh `SECRET_KEY`, pulls the
Firebase API key from Secret Manager, deploys, and cleans up old versions (keeping the
last 3).

```bash
./deploy.sh --env dev    # deploy to the dev project (seeds dev's DB from prod first)
./deploy.sh --env prod   # deploy to the prod project
```

To route traffic at a specific time instead of immediately (e.g. to warn users and let
them log out before cutover), add `--migrate-at HH:MM` (24-hour, Europe/London time,
at least 5 minutes in the future):

```bash
./deploy.sh --env prod --migrate-at 22:00
```

This deploys without promoting traffic, shows a maintenance banner in the app until
cutover, and schedules the traffic switch via Cloud Scheduler.
