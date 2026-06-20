"""Entry point for Team Tracker app with optional UK geo-restriction for App Engine."""

import atexit
import os
import signal
import sys

from dotenv import load_dotenv
from flask import abort, request

from app import create_app
from app.gcs_sync import download_db_from_gcs, download_testing_db, upload_db_to_gcs

load_dotenv()  # Load .env before create_app() reads env vars

# ── App Engine / local mode detection ────────────────────────────────────────
# Must be computed before create_app() so the startup sync can set DATABASE_URL.

port_env = os.environ.get("PORT", "")
APP_ENGINE_MODE = port_env == "8080" or os.environ.get("APP_ENGINE", "").lower() in (
    "true",
    "1",
    "yes",
)

if APP_ENGINE_MODE:
    port = int(os.environ.get("PORT", 8080))
    debug = False  # Disable debug mode on App Engine for security and performance
    host = "0.0.0.0"  # Listen on all interfaces for App Engine
else:
    port = 5001
    debug = True  # Enable debug mode for local development
    host = "localhost"  # Listen on localhost for local development (works on Windows too)


# ── GCS Database Sync ────────────────────────────────────────────────────────
# App Engine: download tennis.db from GCS on startup; upload on shutdown.
# Local:      download tennis.db from GCS and save as instance/testing.db so
#             local development always starts from the latest production snapshot.
#             Changes made locally are never written back to GCS.


def _sync_db_on_startup():
    """Download database from GCS on startup."""
    if APP_ENGINE_MODE:
        download_db_from_gcs("/tmp/tennis.db")
    else:
        # Compute the instance path (mirrors Flask's own resolution: repo_root/instance/).
        instance_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")
        testing_db_path = os.path.join(instance_path, "testing.db")
        download_testing_db(testing_db_path)
        if os.path.exists(testing_db_path):
            # Point SQLAlchemy at testing.db via the relative URI that Flask-SQLAlchemy
            # resolves against the instance folder.
            os.environ["DATABASE_URL"] = "sqlite:///testing.db"
        else:
            print("⚠ No testing.db available — falling back to tennis.db (will be empty)")


def _sync_db_on_shutdown():
    """Upload database to GCS before shutting down (App Engine only)."""
    if APP_ENGINE_MODE:
        upload_db_to_gcs("/tmp/tennis.db")


_sync_db_on_startup()

# Import and create the app AFTER the startup sync so DATABASE_URL is already set.
app = create_app()

# Register shutdown handlers for graceful sync
atexit.register(_sync_db_on_shutdown)


def _handle_shutdown_signal(_signum, _frame):
    """Handle SIGTERM/SIGINT to upload database before exiting."""
    _sync_db_on_shutdown()
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_shutdown_signal)
signal.signal(signal.SIGINT, _handle_shutdown_signal)


@app.before_request
def restrict_to_uk():
    """Block requests from outside the UK when running on App Engine."""
    if not APP_ENGINE_MODE:
        return  # Skip geo-check for local development

    country = request.headers.get("X-AppEngine-Country")

    # Allow only GB (United Kingdom)
    if country != "GB":
        abort(403)  # Returns "Forbidden"


if __name__ == "__main__":
    # When running locally: python run.py
    # When deployed to App Engine: set APP_ENGINE=true in environment
    # Set GCS_BUCKET_NAME and optionally GCS_DB_BLOB_NAME to enable GCS sync
    app.run(host=host, port=port, debug=debug)
