"""Shared pytest fixtures for the test suite."""

import pytest

from app import create_app
from app.models import db


@pytest.fixture
def app():
    """Yield a Flask app with a fresh in-memory SQLite database."""
    flask_app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "TESTING": True,
        }
    )
    with flask_app.app_context():
        yield flask_app
        db.session.remove()
