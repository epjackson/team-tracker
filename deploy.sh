#!/bin/bash

# Deploy to App Engine with automatic traffic routing and old version cleanup

set -e

# --- Parse --env flag ---
ENV=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 --env <dev|prod>" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$ENV" ]]; then
  echo "Error: --env flag is required (e.g. --env dev)" >&2
  exit 1
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
sed \
  -e "s/SECRET_KEY: \"\"/SECRET_KEY: \"$SECRET_KEY\"/" \
  -e "s/FIREBASE_API_KEY: \"\"/FIREBASE_API_KEY: \"$FIREBASE_API_KEY\"/" \
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

echo "🚀 Deploying to App Engine..."
gcloud app deploy "$TEMP_YAML" --promote --quiet
gcloud app deploy cron.yaml --quiet

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

echo "✅ Deployment complete"
echo "📊 View at: https://$(gcloud config get-value project).appspot.com"
