"""Retrospective audit: was every player eligible for their team at the time they played?.

check_player_eligibility() in app/models.py evaluates the team-commitment rule against
whatever fixtures currently exist in the database, regardless of date — it answers
"is this player eligible right now", not "were they eligible on the day they played".
This script replays every fixture in a league in chronological order (by date, then by
fixture id as a tie-break for same-day fixtures) and re-derives, from only the fixtures
that occurred *before* each one, whether the player's team assignment was valid at that
point in the season. Any player who played for a team below their natural commitment
tier — as of that date — is flagged.

Note: this checks only the team-commitment/divisional rule (not the early-season
one-match-per-week rule).

By default this downloads a fresh copy of the production tennis.db from the GCS
bucket (same mechanism run.py uses for local dev) and audits that copy, so results
reflect the real production data rather than a possibly-stale local database. Pass
--no-download to instead audit whatever DATABASE_URL / local db is already configured.

Usage:
    python scripts/audit_eligibility.py [--league-id ID] [--no-download]
"""

import argparse
import os
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402

from app import create_app  # noqa: E402
from app.gcs_sync import download_testing_db  # noqa: E402
from app.models import (  # noqa: E402
    Division,
    Fixture,
    League,
    LeagueRestriction,
    Player,
    PlayerMatchResult,
    Rubber,
    Team,
    _team_rank,
    db,
)

load_dotenv()

AUDIT_DB_PATH = os.path.join(REPO_ROOT, "instance", "audit_tennis.db")
PROD_BUCKET = "project-c48df987-f9d9-448a-83e.appspot.com"


def _played_player_ids(fixture_id, side):
    """Return the distinct player ids who actually played (side) in this fixture."""
    rows = (
        db.session.query(PlayerMatchResult.player_id)
        .join(Rubber, PlayerMatchResult.rubber_id == Rubber.id)
        .filter(Rubber.fixture_id == fixture_id, PlayerMatchResult.side == side)
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def audit_league(league, restriction):
    """Replay a league's fixtures chronologically and return a list of violations."""
    threshold = restriction.team_commitment_threshold
    teams = (
        Team.query.join(Division, Team.division_id == Division.id)
        .filter(Division.league_id == league.id)
        .all()
    )
    teams.sort(key=_team_rank)
    team_ids = {t.id for t in teams}

    fixtures = Fixture.query.filter_by(league_id=league.id).order_by(Fixture.date, Fixture.id).all()

    # cumulative[player_id][team_id] = fixtures played for that team, strictly before
    # the fixture currently being examined.
    cumulative = defaultdict(lambda: defaultdict(int))
    violations = []

    for fixture in fixtures:
        sides = []
        if fixture.home_team_id in team_ids:
            sides.append(("home", fixture.home_team))
        if fixture.away_team_id in team_ids:
            sides.append(("away", fixture.away_team))

        fixture_plays = []  # (side, team, player_id) for updating cumulative afterwards
        for side, team in sides:
            for player_id in _played_player_ids(fixture.id, side):
                fixture_plays.append((side, team, player_id))

                running_total = 0
                natural_team = None
                for t in teams:
                    running_total += cumulative[player_id][t.id]
                    if running_total >= threshold:
                        natural_team = t
                        break

                if (
                    natural_team is not None
                    and natural_team.id != team.id
                    and _team_rank(team) >= _team_rank(natural_team)
                ):
                    violations.append(
                        {
                            "fixture": fixture,
                            "player_id": player_id,
                            "played_for": team,
                            "committed_to": natural_team,
                        }
                    )

        for _, team, player_id in fixture_plays:  # _ unused side variable
            cumulative[player_id][team.id] += 1

    return violations


def main():
    """Run the audit across all leagues (or one, via --league-id) and print a report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", type=int, default=None, help="Limit audit to one league")
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Skip the GCS download and audit whatever DATABASE_URL / local db is configured",
    )
    parser.add_argument(
        "--bucket",
        default=PROD_BUCKET,
        help=f"GCS bucket to download tennis.db from (default: {PROD_BUCKET})",
    )
    args = parser.parse_args()

    if args.no_download:
        print(f"Auditing local database: {os.environ.get('DATABASE_URL', 'instance/tennis.db')}")
    else:
        # Overrides GCS_SOURCE_BUCKET/GCS_BUCKET_NAME from .env so this always audits
        # the true production bucket regardless of what local dev is configured to sync.
        os.environ["GCS_SOURCE_BUCKET"] = args.bucket
        if not download_testing_db(AUDIT_DB_PATH):
            print(f"Aborting: could not fetch tennis.db from gs://{args.bucket}.")
            sys.exit(1)
        os.environ["DATABASE_URL"] = f"sqlite:///{AUDIT_DB_PATH}"
        print(f"Auditing production database copy from gs://{args.bucket}: {AUDIT_DB_PATH}")

    app = create_app()
    with app.app_context():
        restrictions = LeagueRestriction.query.filter(
            LeagueRestriction.team_commitment_threshold > 0
        ).all()
        if args.league_id is not None:
            restrictions = [r for r in restrictions if r.league_id == args.league_id]

        if not restrictions:
            print("No leagues have a team-commitment threshold configured — nothing to audit.")
            return

        total_violations = 0
        for restriction in restrictions:
            league = League.query.get(restriction.league_id)
            violations = audit_league(league, restriction)
            total_violations += len(violations)

            print(
                f"\n=== {league.name} "
                f"(commitment threshold: {restriction.team_commitment_threshold}) ==="
            )
            if not violations:
                print("  No violations found.")
                continue

            for v in violations:
                player = Player.query.get(v["player_id"])
                fixture = v["fixture"]
                print(
                    f"  {fixture.date}  Fixture #{fixture.id}: "
                    f"{player.name if player else v['player_id']} played for "
                    f"{v['played_for'].name} but was already committed to "
                    f"{v['committed_to'].name} as of that date."
                )

        print(f"\nTotal violations: {total_violations}")


if __name__ == "__main__":
    main()
