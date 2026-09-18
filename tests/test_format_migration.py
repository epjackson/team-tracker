"""create_app() must succeed against a real pre-existing database.

Specifically one that predates the League/ParentLeague `format` columns.
In-memory tests that start from `db.create_all()` never exercise the ALTER
TABLE migration path (the fresh schema already has every column), so they
can't catch a migration ordered after another migration that ORM-queries
League/ParentLeague — such a query selects the `format` column (since the
model declares it) before the ALTER TABLE that adds it has run, raising
`OperationalError: no such column: leagues.format`. See
_migrate_parent_league_format/_migrate_league_format in app/__init__.py and
their comment about migration ordering.
"""

import os
import sqlite3
import tempfile

from app import create_app
from app.models import LEAGUE_FORMAT_CLEVELAND_AUTUMN, League


def test_create_app_migrates_pre_format_database():
    """Create app migrates pre format database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE parent_leagues (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(120) NOT NULL UNIQUE,
                colour VARCHAR(7) NOT NULL,
                description TEXT,
                pairs_per_round INTEGER NOT NULL,
                sets_per_rubber INTEGER NOT NULL,
                games_per_set INTEGER NOT NULL,
                tiebreak_at_games INTEGER NOT NULL,
                created_at DATETIME
            )
            """
        )
        c.execute(
            """
            CREATE TABLE leagues (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(120) NOT NULL UNIQUE,
                description TEXT,
                start_date DATE,
                round5_start_date DATE,
                round9_start_date DATE,
                pairs_per_round INTEGER,
                parent_league_id INTEGER REFERENCES parent_leagues (id),
                created_at DATETIME
            )
            """
        )
        c.execute("INSERT INTO leagues (id, name) VALUES (1, 'Old League')")
        conn.commit()
        conn.close()

        app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}", "TESTING": True})
        with app.app_context():
            league = League.query.get(1)
            assert league.format == LEAGUE_FORMAT_CLEVELAND_AUTUMN
    finally:
        os.remove(path)
