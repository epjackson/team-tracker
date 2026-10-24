"""Regression tests for the Autumn ParentLeague's seeded configuration.

_migrate_season_type_parent_leagues used to seed the "Cleveland Autumn
League" ParentLeague with pairs_per_round=3 (Summer's value) and leave
sets_per_rubber/games_per_set/tiebreak_at_games at their column defaults
(1/8/8 — also Summer's real values), since only pairs_per_round varied in
the old seed tuple. On any environment where this seed ran and no admin
had since gone into /parent-leagues to correct it (e.g. a freshly deployed
preview environment), the Autumn league was indistinguishable from Summer:
3 pairs, 1 set per rubber — exactly the reported bug. _migrate_season_type_
parent_leagues now seeds Autumn's real values (2 pairs, 2 sets to 6 games)
directly, and _migrate_autumn_parent_league_config corrects any existing
database still stuck at the old untouched defaults.
"""

import os
import tempfile

from app import create_app
from app.models import League, ParentLeague, build_rubber_plan, db


def test_fresh_seed_gives_autumn_and_summer_their_real_distinct_shapes():
    """Fresh seed gives autumn and summer their real distinct shapes."""
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        summer = ParentLeague.query.filter_by(season_type="summer").first()
        autumn = ParentLeague.query.filter_by(season_type="autumn").first()

        assert (summer.pairs_per_round, summer.sets_per_rubber, summer.games_per_set) == (3, 1, 8)
        assert (autumn.pairs_per_round, autumn.sets_per_rubber, autumn.games_per_set) == (2, 2, 6)


def test_corrective_migration_fixes_a_database_stuck_on_the_old_broken_seed():
    """A real environment whose Autumn ParentLeague was seeded by the old bug.

    Simulates e.g. a preview environment that already has a League created
    under the Autumn parent — the corrective migration must fix both the
    parent and cascade the corrected pairs_per_round onto the existing
    League row, since that normally only happens once, at League creation
    time.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}"})
        with app.app_context():
            # Roll the seeded Autumn parent back to the old broken values, as if
            # this database had been created before the seed fix existed.
            autumn = ParentLeague.query.filter_by(season_type="autumn").first()
            autumn.pairs_per_round = 3
            autumn.sets_per_rubber = 1
            autumn.games_per_set = 8
            autumn.tiebreak_at_games = 8
            db.session.commit()

            league = League(
                name="Cleveland Men's Doubles Autumn League 2026",
                pairs_per_round=3,  # cascaded from the broken parent at creation time
                format="cleveland_autumn",
                parent_league_id=autumn.id,
            )
            db.session.add(league)
            db.session.commit()

            plan = build_rubber_plan(league)
            assert (len(plan), plan[0].sets) == (9, 1)  # confirms the broken starting state

        # Re-creating the app re-runs startup migrations against the same file,
        # exactly as a redeploy against an existing database would.
        app2 = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}"})
        with app2.app_context():
            autumn2 = ParentLeague.query.filter_by(season_type="autumn").first()
            league2 = League.query.filter_by(parent_league_id=autumn2.id).first()

            assert (autumn2.pairs_per_round, autumn2.sets_per_rubber, autumn2.games_per_set) == (
                2,
                2,
                6,
            )
            assert league2.pairs_per_round == 2

            plan = build_rubber_plan(league2)
            assert (len(plan), plan[0].sets) == (4, 2)
    finally:
        os.remove(path)


def test_corrective_migration_never_overwrites_a_deliberately_customized_parent():
    """A deliberately customized Autumn parent must not be overwritten.

    An admin who has already customized the Autumn parent's pairs_per_round
    (to a value other than the untouched broken default) must not have that
    choice silently overwritten by the corrective migration.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}"})
        with app.app_context():
            autumn = ParentLeague.query.filter_by(season_type="autumn").first()
            autumn.pairs_per_round = 4  # a deliberate admin customization
            db.session.commit()

        app2 = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}"})
        with app2.app_context():
            autumn2 = ParentLeague.query.filter_by(season_type="autumn").first()
            assert autumn2.pairs_per_round == 4
    finally:
        os.remove(path)
