"""GCS storage sync for App Engine database persistence."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import storage

_commit_sync_registered = False
_maintenance_cache = (0.0, None)  # (fetched_monotonic, data_or_none)
_MAINTENANCE_TTL = 60  # seconds between GCS fetches


def get_secret(secret_id):
    """Fetch the latest version of a secret from Google Cloud Secret Manager."""
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        return None
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()
    except Exception as e:
        print(f"⚠ Failed to read secret '{secret_id}': {e}")
        return None


def get_gcs_config():
    """Get GCS configuration from environment variables or App Engine defaults.

    Returns:
        tuple: (bucket_name, db_blob_name) or (None, None) if not configured
    """
    db_blob_name = os.environ.get("GCS_DB_BLOB_NAME", "tennis.db")

    # Check for explicit GCS_BUCKET_NAME first
    bucket_name = os.environ.get("GCS_BUCKET_NAME")
    if bucket_name:
        return bucket_name, db_blob_name

    # On App Engine, use the default bucket
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project_id:
        bucket_name = f"{project_id}.appspot.com"
        return bucket_name, db_blob_name

    return None, None


def get_gcs_source_config():
    """Get GCS configuration for startup downloads (read source, may differ from write target).

    Non-production environments set GCS_SOURCE_BUCKET to always read from the
    production bucket while still writing to their own project bucket.

    Returns:
        tuple: (bucket_name, db_blob_name) or (None, None) if not configured
    """
    db_blob_name = os.environ.get("GCS_DB_BLOB_NAME", "tennis.db")
    source_bucket = os.environ.get("GCS_SOURCE_BUCKET")
    if source_bucket:
        return source_bucket, db_blob_name
    return get_gcs_config()


def download_testing_db(local_testing_db_path):
    """Download tennis.db from GCS and save it as a local testing database.

    Always overwrites any existing file so local dev starts from the latest
    production snapshot. Does NOT register any upload-on-commit — changes made
    locally will never be written back to GCS.

    Args:
        local_testing_db_path (str): Absolute path to write the testing database
            (typically {instance_path}/testing.db).

    Returns:
        bool: True if the file was downloaded successfully, False otherwise.
    """
    bucket_name, db_blob_name = get_gcs_source_config()

    if not bucket_name:
        print("ℹ GCS not configured — skipping local testing database download.")
        return False

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(db_blob_name)

        Path(local_testing_db_path).parent.mkdir(parents=True, exist_ok=True)

        if blob.exists():
            blob.download_to_filename(local_testing_db_path)
            print(
                f"✓ Local testing database created from "
                f"gs://{bucket_name}/{db_blob_name} → {local_testing_db_path}"
            )
            return True
        else:
            print(
                f"ℹ Source blob gs://{bucket_name}/{db_blob_name} not found — "
                "local testing database not created."
            )
            return False
    except Exception as e:
        print(f"⚠ Failed to download testing database from GCS: {e}")
        return False


def download_db_from_gcs(local_db_path):
    """Download database from GCS to local temporary directory.

    Args:
        local_db_path (str): Local path to write the database file (typically /tmp/tennis.db)
    """
    bucket_name, db_blob_name = get_gcs_source_config()

    if not bucket_name:
        return  # GCS not configured

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(db_blob_name)

        # Ensure parent directory exists
        Path(local_db_path).parent.mkdir(parents=True, exist_ok=True)

        if blob.exists():
            blob.download_to_filename(local_db_path)
            print(f"✓ Downloaded database from gs://{bucket_name}/{db_blob_name}")
        else:
            print(
                f"ℹ Database not found in GCS (gs://{bucket_name}/{db_blob_name}). "
                "Starting with empty database."
            )
    except Exception as e:
        print(f"⚠ Failed to download database from GCS: {e}")
        # Don't fail startup if GCS download fails - let app start with local db


def upload_db_to_gcs(local_db_path):
    """Upload database from local instance directory to GCS.

    Args:
        local_db_path (str): Local path to the database file
    """
    bucket_name, db_blob_name = get_gcs_config()

    if not bucket_name:
        return  # GCS not configured

    # Refuse to write to any bucket that doesn't belong to the running project.
    # This prevents a misconfigured GCS_BUCKET_NAME from writing dev data to prod.
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project_id and bucket_name != f"{project_id}.appspot.com":
        print(
            f"⛔ Upload blocked: target bucket '{bucket_name}' does not match "
            f"this project's default bucket '{project_id}.appspot.com'. "
            "Check GCS_BUCKET_NAME configuration."
        )
        return

    try:
        if not os.path.exists(local_db_path):
            print(f"ℹ Database file not found at {local_db_path}. Skipping upload.")
            return

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(db_blob_name)

        blob.upload_from_filename(local_db_path)
        print(f"✓ Uploaded database to gs://{bucket_name}/{db_blob_name}")
    except Exception as e:
        print(f"⚠ Failed to upload database to GCS: {e}")


def register_commit_sync(app, db):
    """Register a SQLAlchemy after_commit hook that uploads the DB to GCS on every write.

    No-op locally when GCS is not configured, and no-op when the app is using the
    local testing database (testing.db) so that local changes are never written back
    to the production GCS bucket.
    """
    global _commit_sync_registered
    if _commit_sync_registered:
        return

    # Only upload from App Engine instances — never from local development.
    app_engine = os.environ.get("APP_ENGINE", "").lower() in ("true", "1", "yes")
    if not app_engine:
        return

    bucket_name, _ = get_gcs_config()
    if not bucket_name:
        return

    # Derive the actual DB file path from the configured URI so that absolute paths
    # (e.g. sqlite:////tmp/tennis.db on App Engine) are handled correctly.
    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if db_uri.startswith("sqlite:////"):
        # Four slashes = absolute path: sqlite:////tmp/tennis.db → /tmp/tennis.db
        local_db_path = db_uri[len("sqlite:///") :]
    elif db_uri.startswith("sqlite:///"):
        # Three slashes = path relative to the instance folder
        local_db_path = os.path.join(app.instance_path, db_uri[len("sqlite:///") :])
    else:
        local_db_path = os.path.join(app.instance_path, "tennis.db")

    from sqlalchemy import event

    event.listen(db.session, "after_commit", lambda _: upload_db_to_gcs(local_db_path))

    _commit_sync_registered = True


def get_maintenance_info():
    """Return maintenance.json contents from GCS, or None if absent or past.

    Caches the GCS fetch for 60 seconds. Returns None once the scheduled
    migrate_at time has passed so the banner disappears automatically.
    """
    global _maintenance_cache
    fetched_at, data = _maintenance_cache

    if time.monotonic() - fetched_at >= _MAINTENANCE_TTL:
        bucket_name, _ = get_gcs_config()
        if not bucket_name:
            return None
        try:
            client = storage.Client()
            blob = client.bucket(bucket_name).blob("maintenance.json")
            data = json.loads(blob.download_as_text()) if blob.exists() else None
        except Exception as e:
            print(f"⚠ Failed to read maintenance.json: {e}")
            data = None
        _maintenance_cache = (time.monotonic(), data)

    if data is None:
        return None
    try:
        migrate_at = datetime.fromisoformat(data["migrate_at"])
        if datetime.now(timezone.utc) >= migrate_at:
            return None
    except (KeyError, ValueError):
        return None
    return data
