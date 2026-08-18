"""Seed catalog master data from YAML.

Run from the project root so that `app` is importable:

    uv run python -m scripts.seed --dry-run
    uv run python -m scripts.seed

Or via the Makefile: `make seed-dry` / `make seed`.

Upserts are keyed on the natural keys (`muscle_groups.code`,
`exercises.slug`), so re-running is safe and edits to the YAML propagate.
"""

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import db, db_lifespan
from app.core.logging import configure_logging
from app.core.redis import cache, redis_lifespan
from app.services.catalog import catalog_cache

DATA_DIR = Path(__file__).parent / "data"
MAX_DEPTH = 2
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ROLES = ("primary", "secondary", "stabilizer")


class SeedError(Exception):
    """Validation failed; nothing was written."""


class _Rollback(Exception):
    """Internal sentinel used to abandon the transaction on --dry-run."""


@dataclass
class MuscleGroup:
    code: str
    name_en: str
    name_vi: str
    depth: int
    parent_code: str | None
    is_trackable: bool


@dataclass
class Exercise:
    slug: str
    name_en: str
    name_vi: str | None
    pattern: str
    equipment: str
    force: str
    is_unilateral: bool
    load_type: str
    default_rest_sec: int
    requires_overhead: bool
    notes: str | None
    muscles: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Stats:
    mg_inserted: int = 0
    mg_updated: int = 0
    mg_removed: int = 0
    ex_inserted: int = 0
    ex_updated: int = 0
    link_written: int = 0
    link_removed: int = 0


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_yaml(path: Path, root_key: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SeedError(f"{path} not found")

    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    if not isinstance(doc, dict) or root_key not in doc:
        raise SeedError(f"{path}: expected a top-level '{root_key}:' key")

    items = doc[root_key]
    if not isinstance(items, list) or not items:
        raise SeedError(f"{path}: '{root_key}' must be a non-empty list")

    return items


def flatten_muscle_groups(
    nodes: list[dict[str, Any]],
    parent_code: str | None = None,
    depth: int = 0,
) -> list[MuscleGroup]:
    """Depth-first flatten. Parents always precede their children."""
    # Guard on `nodes`, not on depth alone: a leaf at MAX_DEPTH recurses once
    # more with an empty child list, which is legal.
    if nodes and depth > MAX_DEPTH:
        raise SeedError(
            f"muscle group '{parent_code}' has children below depth {MAX_DEPTH} "
            "(ck_depth_range allows 0..2)"
        )

    out: list[MuscleGroup] = []
    for node in nodes:
        for required in ("code", "name_en", "name_vi"):
            if not node.get(required):
                raise SeedError(
                    f"muscle group under '{parent_code}': missing {required}"
                )

        out.append(
            MuscleGroup(
                code=node["code"],
                name_en=node["name_en"],
                name_vi=node["name_vi"],
                depth=depth,
                parent_code=parent_code,
                is_trackable=node.get("is_trackable", True),
            )
        )
        children = node.get("children") or []
        out.extend(flatten_muscle_groups(children, node["code"], depth + 1))

    return out


def parse_exercises(items: list[dict[str, Any]]) -> list[Exercise]:
    out: list[Exercise] = []
    for item in items:
        slug = item.get("slug")
        if not slug:
            raise SeedError("exercise entry without a slug")

        for required in ("name_en", "pattern", "equipment", "force"):
            if not item.get(required):
                raise SeedError(f"exercise '{slug}': missing {required}")

        raw = item.get("muscles") or {}
        unknown_roles = set(raw) - set(ROLES)
        if unknown_roles:
            raise SeedError(
                f"exercise '{slug}': unknown muscle role(s) "
                f"{sorted(unknown_roles)}; allowed: {list(ROLES)}"
            )

        out.append(
            Exercise(
                slug=slug,
                name_en=item["name_en"],
                name_vi=item.get("name_vi"),
                pattern=item["pattern"],
                equipment=item["equipment"],
                force=item["force"],
                is_unilateral=item.get("is_unilateral", False),
                load_type=item.get("load_type", "single"),
                default_rest_sec=item.get("default_rest_sec", 90),
                requires_overhead=item.get("requires_overhead", False),
                notes=item.get("notes"),
                muscles={r: list(raw.get(r) or []) for r in ROLES},
            )
        )

    return out


# ---------------------------------------------------------------------------
# Validation — everything checkable without the database
# ---------------------------------------------------------------------------


def validate_offline(groups: list[MuscleGroup], exercises: list[Exercise]) -> None:
    errors: list[str] = []

    seen_codes: set[str] = set()
    for g in groups:
        if g.code in seen_codes:
            errors.append(f"muscle group code '{g.code}' is declared twice")
        seen_codes.add(g.code)

    trackable = {g.code for g in groups if g.is_trackable}

    seen_slugs: set[str] = set()
    for ex in exercises:
        if ex.slug in seen_slugs:
            errors.append(f"exercise slug '{ex.slug}' is declared twice")
        seen_slugs.add(ex.slug)

        if not SLUG_RE.match(ex.slug):
            errors.append(
                f"exercise '{ex.slug}': slug must be lowercase kebab-case "
                "(ck_slug_format)"
            )

        if ex.default_rest_sec <= 0:
            errors.append(
                f"exercise '{ex.slug}': default_rest_sec must be > 0 "
                "(ck_rest_sec_positive)"
            )

        # The rule 02_init_catalog.sql delegates to this layer.
        if not ex.muscles.get("primary"):
            errors.append(f"exercise '{ex.slug}': needs at least one primary muscle")

        assigned: dict[str, str] = {}
        for role in ROLES:
            for code in ex.muscles[role]:
                if code not in seen_codes:
                    errors.append(
                        f"exercise '{ex.slug}': unknown muscle group '{code}'"
                    )
                elif code not in trackable:
                    # Would trip trg_exercise_muscles_leaf_only at INSERT time.
                    errors.append(
                        f"exercise '{ex.slug}': muscle '{code}' is a parent node "
                        "(is_trackable = false) and cannot be mapped"
                    )

                if code in assigned:
                    # exercise_muscles PK is (exercise_id, muscle_group_id).
                    errors.append(
                        f"exercise '{ex.slug}': muscle '{code}' appears as both "
                        f"{assigned[code]} and {role}"
                    )
                else:
                    assigned[code] = role

    if errors:
        raise SeedError("\n".join(f"  - {e}" for e in errors))


async def _enum_labels(session: AsyncSession, enum_type: str) -> set[str]:
    result = await session.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :name"
        ),
        {"name": enum_type},
    )
    labels = {row[0] for row in result}
    if not labels:
        raise SeedError(
            f"enum type '{enum_type}' does not exist — run `make db-init` first"
        )
    return labels


async def validate_enums(session: AsyncSession, exercises: list[Exercise]) -> None:
    """Check enum values against the live types rather than a hardcoded copy."""
    errors: list[str] = []

    # ROLES has to exist before we connect — it is the set of YAML keys the parser
    # accepts. Cross-check it here so adding a muscle_role variant in SQL fails
    # loudly instead of being silently unsupported.
    db_roles = await _enum_labels(session, "muscle_role")
    if db_roles != set(ROLES):
        missing = sorted(db_roles - set(ROLES))
        extra = sorted(set(ROLES) - db_roles)
        detail = ", ".join(
            part
            for part in (
                f"missing from ROLES: {missing}" if missing else "",
                f"not in the database: {extra}" if extra else "",
            )
            if part
        )
        raise SeedError(
            f"muscle_role enum and scripts/seed.py ROLES disagree — {detail}"
        )

    for column, enum_type in (
        ("pattern", "movement_pattern"),
        ("equipment", "equipment_type"),
        ("force", "force_type"),
        ("load_type", "load_type"),
    ):
        allowed = await _enum_labels(session, enum_type)

        for ex in exercises:
            value = getattr(ex, column)
            if value not in allowed:
                errors.append(
                    f"exercise '{ex.slug}': {column}='{value}' is not a valid "
                    f"{enum_type} ({sorted(allowed)})"
                )

    if errors:
        raise SeedError("\n".join(f"  - {e}" for e in errors))


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

# `xmax = 0` is true only for a freshly inserted row, which is how we tell an
# INSERT apart from an ON CONFLICT UPDATE.
_UPSERT_MUSCLE_GROUP = text("""
    INSERT INTO muscle_groups (code, name_en, name_vi, parent_id, depth, is_trackable)
    VALUES (:code, :name_en, :name_vi, :parent_id, :depth, :is_trackable)
    ON CONFLICT (code) DO UPDATE SET
        name_en      = EXCLUDED.name_en,
        name_vi      = EXCLUDED.name_vi,
        parent_id    = EXCLUDED.parent_id,
        depth        = EXCLUDED.depth,
        is_trackable = EXCLUDED.is_trackable
    RETURNING id, (xmax = 0) AS inserted
""")

_UPSERT_EXERCISE = text("""
    INSERT INTO exercises (
        slug, name_en, name_vi, pattern, equipment, force,
        is_unilateral, load_type, default_rest_sec, requires_overhead, notes
    )
    VALUES (
        :slug, :name_en, :name_vi,
        CAST(:pattern AS movement_pattern),
        CAST(:equipment AS equipment_type),
        CAST(:force AS force_type),
        :is_unilateral, CAST(:load_type AS load_type),
        :default_rest_sec, :requires_overhead, :notes
    )
    ON CONFLICT (slug) DO UPDATE SET
        name_en           = EXCLUDED.name_en,
        name_vi           = EXCLUDED.name_vi,
        pattern           = EXCLUDED.pattern,
        equipment         = EXCLUDED.equipment,
        force             = EXCLUDED.force,
        is_unilateral     = EXCLUDED.is_unilateral,
        load_type         = EXCLUDED.load_type,
        default_rest_sec  = EXCLUDED.default_rest_sec,
        requires_overhead = EXCLUDED.requires_overhead,
        notes             = EXCLUDED.notes,
        updated_at        = now()
    RETURNING id, (xmax = 0) AS inserted
""")

_UPSERT_LINK = text("""
    INSERT INTO exercise_muscles (exercise_id, muscle_group_id, role)
    VALUES (:exercise_id, :muscle_group_id, CAST(:role AS muscle_role))
    ON CONFLICT (exercise_id, muscle_group_id) DO UPDATE SET
        role = EXCLUDED.role
""")

# Anything mapped in the database but no longer in the YAML is dropped, so the
# file stays the single source of truth.
_PRUNE_LINKS = text("""
    DELETE FROM exercise_muscles
    WHERE exercise_id = :exercise_id
      AND muscle_group_id <> ALL(:keep)
""")


async def seed_muscle_groups(
    session: AsyncSession, groups: list[MuscleGroup], stats: Stats
) -> dict[str, int]:
    """Upsert the tree top-down and return a code -> id map."""
    ids: dict[str, int] = {}

    for g in groups:
        parent_id = ids[g.parent_code] if g.parent_code else None
        row = (
            await session.execute(
                _UPSERT_MUSCLE_GROUP,
                {
                    "code": g.code,
                    "name_en": g.name_en,
                    "name_vi": g.name_vi,
                    "parent_id": parent_id,
                    "depth": g.depth,
                    "is_trackable": g.is_trackable,
                },
            )
        ).one()

        ids[g.code] = row.id
        if row.inserted:
            stats.mg_inserted += 1
        else:
            stats.mg_updated += 1

    return ids


# Deepest first, so a child is gone before its parent is considered — parent_id
# is ON DELETE RESTRICT. A node still referenced by exercise_muscles raises a
# ForeignKeyViolation rather than being silently kept, which is what we want:
# it means an exercise still maps to a muscle the YAML no longer declares.
_PRUNE_MUSCLE_GROUPS = text("""
    DELETE FROM muscle_groups
    WHERE id IN (
        SELECT id FROM muscle_groups
        WHERE code <> ALL(:keep) AND depth = :depth
    )
""")


async def prune_muscle_groups(
    session: AsyncSession, groups: list[MuscleGroup], stats: Stats
) -> None:
    """Drop nodes the YAML no longer declares."""
    keep = [g.code for g in groups]
    for depth in range(MAX_DEPTH, -1, -1):
        result = await session.execute(
            _PRUNE_MUSCLE_GROUPS, {"keep": keep, "depth": depth}
        )
        stats.mg_removed += result.rowcount or 0


async def seed_exercises(
    session: AsyncSession,
    exercises: list[Exercise],
    muscle_ids: dict[str, int],
    stats: Stats,
) -> None:
    for ex in exercises:
        row = (
            await session.execute(
                _UPSERT_EXERCISE,
                {
                    "slug": ex.slug,
                    "name_en": ex.name_en,
                    "name_vi": ex.name_vi,
                    "pattern": ex.pattern,
                    "equipment": ex.equipment,
                    "force": ex.force,
                    "is_unilateral": ex.is_unilateral,
                    "load_type": ex.load_type,
                    "default_rest_sec": ex.default_rest_sec,
                    "requires_overhead": ex.requires_overhead,
                    "notes": ex.notes,
                },
            )
        ).one()

        if row.inserted:
            stats.ex_inserted += 1
        else:
            stats.ex_updated += 1

        keep: list[int] = []
        for role in ROLES:
            for code in ex.muscles[role]:
                muscle_id = muscle_ids[code]
                keep.append(muscle_id)
                await session.execute(
                    _UPSERT_LINK,
                    {
                        "exercise_id": row.id,
                        "muscle_group_id": muscle_id,
                        "role": role,
                    },
                )
                stats.link_written += 1

        pruned = await session.execute(
            _PRUNE_LINKS, {"exercise_id": row.id, "keep": keep}
        )
        stats.link_removed += pruned.rowcount or 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seed",
        description="Seed muscle groups and exercises from YAML.",
    )
    parser.add_argument(
        "--muscle-groups",
        type=Path,
        default=DATA_DIR / "muscle_groups.yaml",
        help="path to the muscle group YAML (default: %(default)s)",
    )
    parser.add_argument(
        "--exercises",
        type=Path,
        default=DATA_DIR / "exercises.yaml",
        help="path to the exercise YAML (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and run inside a transaction that is rolled back",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="run offline validation only; never opens a connection",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> Stats:
    groups = flatten_muscle_groups(_load_yaml(args.muscle_groups, "muscle_groups"))
    exercises = parse_exercises(_load_yaml(args.exercises, "exercises"))
    validate_offline(groups, exercises)

    print(f"parsed {len(groups)} muscle groups, {len(exercises)} exercises")

    stats = Stats()
    if args.validate_only:
        print("offline validation passed (--validate-only, database untouched)")
        return stats

    async with db_lifespan(echo=False):
        try:
            async with db.session() as session:
                await validate_enums(session, exercises)
                muscle_ids = await seed_muscle_groups(session, groups, stats)
                await seed_exercises(session, exercises, muscle_ids, stats)
                # After link pruning, so dropped muscles are unreferenced.
                await prune_muscle_groups(session, groups, stats)
                if args.dry_run:
                    raise _Rollback
        except _Rollback:
            print("dry run — transaction rolled back, nothing persisted")
            return stats

    # The catalog cache keys on nothing but the query, because master data only
    # ever changes here. Dropping the namespace is therefore the invalidation.
    async with redis_lifespan():
        dropped = await catalog_cache(cache.client).invalidate()
        print(f"catalog cache: {dropped} keys dropped")

    return stats


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    try:
        stats = asyncio.run(run(args))
    except SeedError as exc:
        print(f"seed failed:\n{exc}", file=sys.stderr)
        return 1

    if not args.validate_only:
        print(
            f"muscle_groups: {stats.mg_inserted} inserted, "
            f"{stats.mg_updated} updated, {stats.mg_removed} removed\n"
            f"exercises:     {stats.ex_inserted} inserted, "
            f"{stats.ex_updated} updated\n"
            f"muscle links:  {stats.link_written} written, "
            f"{stats.link_removed} pruned"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
