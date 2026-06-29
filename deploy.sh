#!/bin/bash

# Deploy to App Engine with automatic traffic routing and old version cleanup.
# Without --migrate-at, traffic switches immediately. With --migrate-at HH:MM,
# traffic is migrated at that time via Cloud Scheduler so users can be warned
# in advance and log out cleanly before cutover.

set -e

# --- Parse flags ---
ENV=""
MIGRATE_AT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV="$2"
      shift 2
      ;;
    --migrate-at)
      MIGRATE_AT="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 --env <dev|prod> [--migrate-at <HH:MM>]" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$ENV" ]]; then
  echo "Error: --env flag is required (e.g. --env dev)" >&2
  exit 1
fi

if [[ -n "$MIGRATE_AT" ]]; then
  if ! echo "$MIGRATE_AT" | grep -qE '^([01][0-9]|2[0-3]):[0-5][0-9]$'; then
    echo "Error: --migrate-at must be in HH:MM 24-hour format (e.g. 22:00)" >&2
    exit 1
  fi

  # Validate that the migration time is in the future (Europe/London).
  MIGRATE_AT_CHECK=$(python -c "
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
tz = ZoneInfo('Europe/London')
now = datetime.now(tz)
h, m = map(int, '$MIGRATE_AT'.split(':'))
target = now.replace(hour=h, minute=m, second=0, microsecond=0)
if target <= now:
    print('past')
elif target - now < timedelta(minutes=5):
    print('too_soon')
else:
    print('ok')
")
  if [[ "$MIGRATE_AT_CHECK" == "past" ]]; then
    echo "Error: --migrate-at $MIGRATE_AT is already in the past (Europe/London time)." >&2
    exit 1
  elif [[ "$MIGRATE_AT_CHECK" == "too_soon" ]]; then
    echo "Error: --migrate-at $MIGRATE_AT must be at least 5 minutes in the future (Europe/London time)." >&2
    exit 1
  fi
fi

case "$ENV" in
  dev)  PROJECT_ID="dev-team-tracker-498611" ;;
  prod) PROJECT_ID="thinking-prism-496011" ;;  # TODO: set prod project ID
  *)
    echo "Error: unknown environment '$ENV'. Valid values: dev, prod" >&2
    exit 1
    ;;
esac

echo "🌍 Environment: $ENV  (project: $PROJECT_ID)"
gcloud config set project "$PROJECT_ID"
gcloud auth application-default set-quota-project "$PROJECT_ID"

SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
echo "🔑 Generated new SECRET_KEY for this deploy"

FIREBASE_API_KEY=$(gcloud secrets versions access latest --secret=firebase-api-key --project="$PROJECT_ID" 2>/dev/null)
if [[ -z "$FIREBASE_API_KEY" ]]; then
  echo "⚠ Warning: could not read firebase-api-key secret — sign-in will be unavailable" >&2
fi
echo "🔑 Fetched Firebase API key from Secret Manager"

TEMP_YAML="./app_deploy_temp.yaml"
trap 'rm -f "$TEMP_YAML"' EXIT

# Dev bucket is seeded from prod above, so both envs read from their own bucket.
SRC_BUCKET=""

sed \
  -e "s/SECRET_KEY: \"\"/SECRET_KEY: \"$SECRET_KEY\"/" \
  -e "s/FIREBASE_API_KEY: \"\"/FIREBASE_API_KEY: \"$FIREBASE_API_KEY\"/" \
  -e "s|GCS_SOURCE_BUCKET: \"\"|GCS_SOURCE_BUCKET: \"$SRC_BUCKET\"|" \
  app.yaml > "$TEMP_YAML"

echo "📝 Writing deployment timestamp..."
python -c "
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo('Europe/London')
now = datetime.now(tz)
abbr = 'BST' if now.dst().total_seconds() > 0 else 'GMT'
ts = now.strftime(f'%d %b %Y, %H:%M {abbr}')
with open('app/version.py', 'w') as f:
    f.write(f'\"\"\"Auto-generated at deploy time.\"\"\"\nDEPLOYED_AT = \"{ts}\"\n')
"

if [[ "$ENV" == "dev" ]]; then
  PROD_BUCKET="thinking-prism-496011.appspot.com"
  DEV_BUCKET="dev-team-tracker-498611.appspot.com"
  echo "📋 Seeding dev bucket with prod database..."
  if gcloud storage cp "gs://${PROD_BUCKET}/tennis.db" "gs://${DEV_BUCKET}/tennis.db" --quiet 2>/dev/null; then
    echo "✓ Dev bucket seeded with prod tennis.db"
  else
    echo "⚠ Could not copy prod database to dev bucket — dev will start with existing data"
  fi
fi

BUCKET="${PROJECT_ID}.appspot.com"

if [[ -n "$MIGRATE_AT" ]]; then
  # Write maintenance.json to GCS so the running app shows a warning banner.
  MAINTENANCE_JSON=$(python -c "
import json
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo('Europe/London')
now = datetime.now(tz)
h, m = map(int, '$MIGRATE_AT'.split(':'))
target = now.replace(hour=h, minute=m, second=0, microsecond=0)
abbr = 'BST' if target.dst().total_seconds() > 0 else 'GMT'
print(json.dumps({
    'migrate_at': target.isoformat(),
    'display_time': target.strftime(f'%H:%M {abbr}'),
}))
")
  echo "$MAINTENANCE_JSON" | gcloud storage cp - "gs://${BUCKET}/maintenance.json" --quiet
  echo "📢 Maintenance notice written to gs://${BUCKET}/maintenance.json"

  echo "🚀 Deploying to App Engine (traffic stays on current version)..."
  gcloud app deploy "$TEMP_YAML" --no-promote --quiet
else
  # Clear any leftover maintenance banner from a previous staged deploy.
  gcloud storage rm "gs://${BUCKET}/maintenance.json" --quiet 2>/dev/null || true

  echo "🚀 Deploying to App Engine..."
  gcloud app deploy "$TEMP_YAML" --promote --quiet
fi

gcloud app deploy cron.yaml --quiet

# Capture the version that was just created (most recently created).
NEW_VERSION=$(gcloud app versions list \
  --sort-by='~version.create_time' \
  --limit=1 \
  --format='value(version.id)')
echo "✓ New version: $NEW_VERSION"

echo "🧹 Cleaning up old versions (keeping last 3)..."
ALL_VERSIONS=$(gcloud app versions list --format='value(version.id)' --sort-by='~version.create_time')
VERSIONS_TO_DELETE=$(echo "$ALL_VERSIONS" | tail -n +4)

if [ -z "$VERSIONS_TO_DELETE" ]; then
  echo "✓ No old versions to delete"
else
  echo "Deleting old versions: $(echo $VERSIONS_TO_DELETE | tr '\n' ' ')"
  echo "$VERSIONS_TO_DELETE" | xargs -I {} gcloud app versions delete {} --quiet
  echo "✓ Old versions deleted"
fi

if [[ -n "$MIGRATE_AT" ]]; then
  # Schedule traffic migration via Cloud Scheduler → App Engine Admin API.
  # Cron includes today's date so it fires exactly once.
  CRON_EXPR=$(python -c "
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo('Europe/London')
now = datetime.now(tz)
h, m = map(int, '$MIGRATE_AT'.split(':'))
print(f'{m} {h} {now.day} {now.month} *')
")

  # Detect App Engine region; Cloud Scheduler must use the same region.
  APP_REGION=$(gcloud app describe --project="$PROJECT_ID" --format='value(locationId)' 2>/dev/null || echo "europe-west2")
  # App Engine uses shortened names for some regions that Cloud Scheduler spells out fully.
  [[ "$APP_REGION" == "europe-west" ]] && APP_REGION="europe-west1"
  [[ "$APP_REGION" == "us-central" ]]  && APP_REGION="us-central1"

  APP_SA="${PROJECT_ID}@appspot.gserviceaccount.com"
  PATCH_URI="https://appengine.googleapis.com/v1/apps/${PROJECT_ID}/services/default?updateMask=split"
  PATCH_BODY="{\"split\":{\"allocations\":{\"${NEW_VERSION}\":1}}}"
  JOB_NAME="maintenance-migrate-${ENV}"
  SCHEDULER_FLAGS=(
    --location="$APP_REGION"
    --project="$PROJECT_ID"
    --schedule="$CRON_EXPR"
    --time-zone="Europe/London"
    --uri="$PATCH_URI"
    --message-body="$PATCH_BODY"
    --oauth-service-account-email="$APP_SA"
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
    --http-method=POST
    --quiet
  )

  echo "📅 Scheduling traffic migration to ${NEW_VERSION} at ${MIGRATE_AT} (Europe/London)..."
  if gcloud scheduler jobs describe "$JOB_NAME" \
       --location="$APP_REGION" --project="$PROJECT_ID" &>/dev/null; then
    gcloud scheduler jobs update http "$JOB_NAME" "${SCHEDULER_FLAGS[@]}" \
      --update-headers="Content-Type=application/json,X-HTTP-Method-Override=PATCH"
  else
    gcloud scheduler jobs create http "$JOB_NAME" "${SCHEDULER_FLAGS[@]}" \
      --headers="Content-Type=application/json,X-HTTP-Method-Override=PATCH"
  fi
  echo "✓ Cloud Scheduler job '$JOB_NAME' set to fire at $MIGRATE_AT"

  echo ""
  echo "✅ Deployment staged. Summary:"
  echo "   New version : $NEW_VERSION (not yet serving)"
  echo "   Traffic cut : $MIGRATE_AT (Europe/London) — via Cloud Scheduler"
  echo "   Users warned: banner visible in the app until cutover"
else
  echo ""
  echo "✅ Deployment complete"
fi

echo "📊 View at: https://$(gcloud config get-value project).appspot.com"
