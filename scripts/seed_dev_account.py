"""Seed a sample local-dev account with a few completed workout sessions.

Dev-only convenience — unlike scripts/seed.py this is *not* run in CI or
production; it exists so a fresh `make db-reset && make seed` leaves something
to look at in the mobile/web client besides an empty history.

    uv run python -m scripts.seed_dev_account
    uv run python -m scripts.seed_dev_account --email me@example.com \
        --password Passw0rd!

Safe to re-run: the user is looked up by email, and sessions are only added if
that user doesn't already have any (use --force to add another batch).
"""

import argparse
import asyncio
import random
from datetime import date, timedelta

from sqlalchemy import select

from app.core.database import db, db_lifespan
from app.core.logging import configure_logging
from app.models.catalog import Exercise
from app.models.training import SessionStatus
from app.repositories.training import SessionFilters
from app.repositories.user import UserRepository
from app.schemas.training import (
    SessionExerciseCreate,
    SetLogCreate,
    WorkoutSessionCreate,
)
from app.services.training import TrainingService
from app.utils.security import hash_password

DEFAULT_EMAIL = "demo@befit.dev"
DEFAULT_PASSWORD = "DevPassword123!"
DEFAULT_NAME = "Demo Lifter"

# A simple push/pull/legs split, by slug — must exist in scripts/data/exercises.yaml.
SPLIT: dict[str, list[str]] = {
    "push": ["barbell-bench-press", "overhead-press", "dumbbell-lateral-raise"],
    "pull": ["pull-up", "barbell-row", "face-pull"],
    "legs": ["back-squat"],
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seed_dev_account",
        description="Seed a sample account with workout history for local dev.",
    )
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--display-name", default=DEFAULT_NAME)
    parser.add_argument(
        "--weeks", type=int, default=3, help="how many weeks of history to log"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="log another batch of sessions even if the account already has some",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    async with db_lifespan(echo=False), db.session() as session:
        users = UserRepository(session)
        user = await users.get_by_email(args.email)
        if user is None:
            user = await users.create_local_user(
                email=args.email,
                display_name=args.display_name,
                password_hash=hash_password(args.password),
            )
            await session.flush()
            print(f"created user {args.email} (id={user.id})")
        else:
            print(f"user {args.email} already exists (id={user.id})")

        training = TrainingService(session)
        if not args.force:
            existing = await training.repo.count_sessions(user.id, SessionFilters())
            if existing:
                print(
                    f"account already has {existing} session(s); "
                    "pass --force to add more"
                )
                return

        slugs = [slug for exercises in SPLIT.values() for slug in exercises]
        rows = (
            await session.execute(select(Exercise).where(Exercise.slug.in_(slugs)))
        ).scalars()
        exercise_ids = {ex.slug: ex.id for ex in rows}
        missing = set(slugs) - exercise_ids.keys()
        if missing:
            raise SystemExit(
                f"catalog is missing {sorted(missing)} — run `make seed` first"
            )

        day_order = ["push", "pull", "legs"]
        total_days = args.weeks * len(day_order)
        created = 0
        for i in range(total_days):
            day_name = day_order[i % len(day_order)]
            session_date = date.today() - timedelta(days=(total_days - i - 1) * 2)
            payload = WorkoutSessionCreate(
                session_date=session_date,
                status=SessionStatus.COMPLETED,
                program_day=day_name,
                exercises=[
                    SessionExerciseCreate(
                        exercise_id=exercise_ids[slug],
                        target_sets=3,
                        target_reps_min=6,
                        target_reps_max=10,
                    )
                    for slug in SPLIT[day_name]
                ],
            )
            workout = await training.create_session(user.id, payload)

            for item in workout.exercises:
                base_weight = random.choice([20.0, 40.0, 60.0, 80.0])
                for _ in range(3):
                    await training.log_set(
                        user.id,
                        workout.id,
                        item.id,
                        SetLogCreate(
                            reps=random.randint(6, 10),
                            weight_kg=base_weight,
                            rpe=random.choice([7, 7.5, 8, 8.5]),
                        ),
                    )
            created += 1

        print(f"logged {created} completed sessions for {args.email}")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    asyncio.run(run(parse_args(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
