"""Smoke tests for the catalog endpoints. Nothing persists — see conftest.py.

The fixtures below insert their own muscle groups and exercises rather than
relying on `make seed`, so the assertions hold on an unseeded database. They use
codes and slugs prefixed `zz-`/`zz_` so filters never collide with seed data.
"""

import pytest_asyncio
from httpx import AsyncClient
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import FakeRedis

from app.core.settings import settings
from app.models.catalog import (
    EquipmentType,
    Exercise,
    ExerciseMuscle,
    ForceType,
    MovementPattern,
    MuscleGroup,
    MuscleRole,
)
from app.services.catalog import CATALOG_CACHE_PREFIX

BASE = f"{settings.API_V1_STR}"


@pytest_asyncio.fixture
async def catalog(session: AsyncSession) -> dict[str, object]:
    region = MuscleGroup(
        code="zz_region", name_en="ZZ Region", name_vi="ZZ", depth=0, is_trackable=False
    )
    session.add(region)
    await session.flush()
    leaf = MuscleGroup(
        code="zz_leaf",
        name_en="ZZ Leaf",
        name_vi="ZZ Lá",
        depth=1,
        parent_id=region.id,
    )
    other = MuscleGroup(code="zz_other", name_en="ZZ Other", name_vi="ZZ Khác", depth=1)
    session.add_all([leaf, other])
    await session.flush()

    press = Exercise(
        slug="zz-bench-press",
        name_en="ZZ Bench Press",
        name_vi="ZZ Đẩy ngực",
        pattern=MovementPattern.HORIZONTAL_PUSH,
        equipment=EquipmentType.BARBELL,
        force=ForceType.PUSH,
        notes="fixture",
    )
    row = Exercise(
        slug="zz-row",
        name_en="ZZ Row",
        pattern=MovementPattern.HORIZONTAL_PULL,
        equipment=EquipmentType.DUMBBELL,
        force=ForceType.PULL,
        is_unilateral=True,
    )
    session.add_all([press, row])
    await session.flush()

    session.add_all(
        [
            ExerciseMuscle(
                exercise_id=press.id,
                muscle_group_id=leaf.id,
                role=MuscleRole.PRIMARY,
            ),
            ExerciseMuscle(
                exercise_id=press.id,
                muscle_group_id=other.id,
                role=MuscleRole.SECONDARY,
            ),
            ExerciseMuscle(
                exercise_id=row.id,
                muscle_group_id=other.id,
                role=MuscleRole.PRIMARY,
            ),
        ]
    )
    await session.flush()
    return {"region": region, "leaf": leaf, "other": other}


async def slugs(client: AsyncClient, **params: object) -> list[str]:
    response = await client.get(BASE + "/exercises", params={"q": "zz", **params})
    assert response.status_code == 200
    return [item["slug"] for item in response.json()["items"]]


async def test_list_returns_fixture_exercises(
    client: AsyncClient, catalog: dict[str, object]
) -> None:
    response = await client.get(BASE + "/exercises", params={"q": "zz"})
    body = response.json()

    assert response.status_code == 200
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0
    # Ordered by name_en.
    assert [item["slug"] for item in body["items"]] == ["zz-bench-press", "zz-row"]


async def test_list_filters(client: AsyncClient, catalog: dict[str, object]) -> None:
    assert await slugs(client, equipment="barbell") == ["zz-bench-press"]
    assert await slugs(client, force="pull") == ["zz-row"]
    assert await slugs(client, pattern="horizontal_push") == ["zz-bench-press"]
    assert await slugs(client, is_unilateral=True) == ["zz-row"]
    assert await slugs(client, muscle="zz_leaf") == ["zz-bench-press"]
    # Both map to zz_other, but only the row has it as its primary.
    assert sorted(await slugs(client, muscle="zz_other")) == [
        "zz-bench-press",
        "zz-row",
    ]
    assert await slugs(client, muscle="zz_other", role="primary") == ["zz-row"]


async def test_muscle_filter_does_not_duplicate_rows(
    client: AsyncClient, catalog: dict[str, object]
) -> None:
    """A join instead of EXISTS would return the bench press twice — it maps to
    two muscles — and inflate `total`."""
    response = await client.get(BASE + "/exercises", params={"q": "zz"})
    body = response.json()

    assert body["total"] == len(body["items"]) == 2


async def test_pagination(client: AsyncClient, catalog: dict[str, object]) -> None:
    response = await client.get(
        BASE + "/exercises", params={"q": "zz", "limit": 1, "offset": 1}
    )
    body = response.json()

    assert body["total"] == 2
    assert [item["slug"] for item in body["items"]] == ["zz-row"]


async def test_get_exercise_by_slug(
    client: AsyncClient, catalog: dict[str, object]
) -> None:
    response = await client.get(BASE + "/exercises/zz-bench-press")
    body = response.json()

    assert response.status_code == 200
    assert body["name_vi"] == "ZZ Đẩy ngực"
    assert body["default_rest_sec"] == 90
    assert {(m["code"], m["role"]) for m in body["muscles"]} == {
        ("zz_leaf", "primary"),
        ("zz_other", "secondary"),
    }


async def test_unknown_slug_is_404(client: AsyncClient) -> None:
    response = await client.get(BASE + "/exercises/no-such-exercise")

    assert response.status_code == 404


async def test_muscle_groups_trackable_filter(
    client: AsyncClient, catalog: dict[str, object]
) -> None:
    everything = (await client.get(BASE + "/muscle-groups")).json()
    trackable = (
        await client.get(BASE + "/muscle-groups", params={"trackable_only": True})
    ).json()

    codes = {group["code"] for group in everything}
    assert {"zz_region", "zz_leaf", "zz_other"} <= codes
    assert "zz_region" not in {group["code"] for group in trackable}


async def test_muscle_group_tree_nests_children(
    client: AsyncClient, catalog: dict[str, object]
) -> None:
    response = await client.get(BASE + "/muscle-groups/tree")
    roots = {node["code"]: node for node in response.json()}

    assert response.status_code == 200
    assert [child["code"] for child in roots["zz_region"]["children"]] == ["zz_leaf"]
    # A parentless node is a root of its own, whatever its depth.
    assert "zz_other" in roots


async def test_responses_are_cached(
    client: AsyncClient, catalog: dict[str, object], fake_redis: FakeRedis
) -> None:
    """Master data only changes through `make seed`, so a repeat query is served
    from Redis. Asserted by writing to the database *behind* the cache."""
    first = await client.get(BASE + "/exercises/zz-bench-press")
    assert [k for k in fake_redis.store if k.startswith(CATALOG_CACHE_PREFIX)]

    second = await client.get(BASE + "/exercises/zz-bench-press")
    assert second.json() == first.json()

    fake_redis.store.clear()
    assert (await client.get(BASE + "/exercises/zz-bench-press")).json() == first.json()


async def test_cache_keys_separate_filter_sets(
    client: AsyncClient, catalog: dict[str, object], fake_redis: FakeRedis
) -> None:
    """Two filter sets must never share an entry, or one page would answer the
    other."""
    await slugs(client, equipment="barbell")
    await slugs(client, equipment="dumbbell")

    assert len(fake_redis.store) == 2


async def test_cache_outage_is_not_a_500(
    client: AsyncClient, catalog: dict[str, object], fake_redis: FakeRedis
) -> None:
    async def boom(*args: object, **kwargs: object) -> str:
        raise RedisError("connection refused")

    fake_redis.get = boom  # type: ignore[method-assign]
    fake_redis.set = boom  # type: ignore[method-assign]

    response = await client.get(BASE + "/exercises/zz-bench-press")

    assert response.status_code == 200
    assert response.json()["slug"] == "zz-bench-press"


async def test_invalid_enum_value_is_422(client: AsyncClient) -> None:
    response = await client.get(BASE + "/exercises", params={"equipment": "hammer"})

    assert response.status_code == 422
