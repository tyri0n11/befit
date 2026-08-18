"""Smoke tests for the workout session endpoints. Nothing persists — see
conftest.py.

Fixtures create their own exercises (slugs prefixed `zz-`) so the assertions
hold on an unseeded database.
"""

from datetime import date

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models.catalog import (
    EquipmentType,
    Exercise,
    ForceType,
    LoadType,
    MovementPattern,
)

BASE = f"{settings.API_V1_STR}/sessions"
AUTH = f"{settings.API_V1_STR}/auth"
TODAY = date.today().isoformat()

CREDENTIALS = {
    "email": "lifter@example.com",
    "display_name": "Lifter",
    "password": "correct-horse-battery",
}


@pytest_asyncio.fixture
async def exercises(session: AsyncSession) -> dict[str, Exercise]:
    press = Exercise(
        slug="zz-bench-press",
        name_en="ZZ Bench Press",
        pattern=MovementPattern.HORIZONTAL_PUSH,
        equipment=EquipmentType.BARBELL,
        force=ForceType.PUSH,
    )
    row = Exercise(
        slug="zz-one-arm-row",
        name_en="ZZ One Arm Row",
        pattern=MovementPattern.HORIZONTAL_PULL,
        equipment=EquipmentType.DUMBBELL,
        force=ForceType.PULL,
        is_unilateral=True,
    )
    dual_press = Exercise(
        slug="zz-shoulder-press-machine",
        name_en="ZZ Shoulder Press Machine",
        pattern=MovementPattern.VERTICAL_PUSH,
        equipment=EquipmentType.MACHINE,
        force=ForceType.PUSH,
        load_type=LoadType.DUAL,
    )
    session.add_all([press, row, dual_press])
    await session.flush()
    return {"press": press, "row": row, "dual_press": dual_press}


@pytest_asyncio.fixture
async def auth(client: AsyncClient) -> dict[str, str]:
    response = await client.post(AUTH + "/register", json=CREDENTIALS)
    assert response.status_code == 201, response.text
    login = await client.post(
        AUTH + "/login",
        json={"email": CREDENTIALS["email"], "password": CREDENTIALS["password"]},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def other_auth(client: AsyncClient) -> dict[str, str]:
    """A second account, for the ownership assertions."""
    payload = {**CREDENTIALS, "email": "intruder@example.com"}
    await client.post(AUTH + "/register", json=payload)
    login = await client.post(
        AUTH + "/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def create_session(
    client: AsyncClient, auth: dict[str, str], **overrides: object
) -> dict:
    response = await client.post(
        BASE, json={"session_date": TODAY, **overrides}, headers=auth
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(BASE)).status_code == 401


async def test_create_session_with_planned_exercises(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    body = await create_session(
        client,
        auth,
        program_day="Push A",
        bodyweight_kg=72.5,
        exercises=[
            {"exercise_id": exercises["press"].id, "target_sets": 3},
            {"exercise_id": exercises["row"].id, "target_reps_min": 8},
        ],
    )

    assert body["status"] == "planned"
    assert body["bodyweight_kg"] == 72.5
    # order_index falls back to the position in the payload.
    assert [e["order_index"] for e in body["exercises"]] == [1, 2]
    assert [e["slug"] for e in body["exercises"]] == [
        "zz-bench-press",
        "zz-one-arm-row",
    ]
    assert body["tonnage"] == 0


async def test_unknown_exercise_is_422(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.post(
        BASE,
        json={"session_date": TODAY, "exercises": [{"exercise_id": 10_000_000}]},
        headers=auth,
    )

    assert response.status_code == 422
    assert "10000000" in response.json()["detail"]


async def test_unilateral_tonnage_counts_both_sides(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    """The logged weight is per side, so a unilateral set is worth double —
    the rule stated in the header of 03_init_training.sql."""
    workout = await create_session(
        client,
        auth,
        exercises=[
            {"exercise_id": exercises["press"].id},
            {"exercise_id": exercises["row"].id},
        ],
    )
    press, row = workout["exercises"]

    for item in (press, row):
        logged = await client.post(
            f"{BASE}/{workout['id']}/exercises/{item['id']}/sets",
            json={"reps": 10, "weight_kg": 20},
            headers=auth,
        )
        assert logged.status_code == 201, logged.text
        assert logged.json()["set_index"] == 1

    body = (await client.get(f"{BASE}/{workout['id']}", headers=auth)).json()
    by_slug = {e["slug"]: e for e in body["exercises"]}

    assert by_slug["zz-bench-press"]["tonnage"] == 200
    assert by_slug["zz-one-arm-row"]["tonnage"] == 400
    assert body["tonnage"] == 600


async def test_dual_loaded_machine_tonnage_counts_both_stacks(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    """A dual-loaded machine (two independent weight stacks) doubles the same
    way a unilateral movement does — the logged weight is per side, not the
    combined total."""
    workout = await create_session(
        client,
        auth,
        exercises=[{"exercise_id": exercises["dual_press"].id}],
    )
    (dual_press,) = workout["exercises"]

    logged = await client.post(
        f"{BASE}/{workout['id']}/exercises/{dual_press['id']}/sets",
        json={"reps": 10, "weight_kg": 40},
        headers=auth,
    )
    assert logged.status_code == 201, logged.text

    body = (await client.get(f"{BASE}/{workout['id']}", headers=auth)).json()

    assert body["exercises"][0]["tonnage"] == 800
    assert body["tonnage"] == 800


async def test_set_index_autoincrements_and_conflicts(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    workout = await create_session(
        client, auth, exercises=[{"exercise_id": exercises["press"].id}]
    )
    url = f"{BASE}/{workout['id']}/exercises/{workout['exercises'][0]['id']}/sets"

    first = await client.post(url, json={"reps": 5}, headers=auth)
    second = await client.post(url, json={"reps": 5}, headers=auth)
    clash = await client.post(url, json={"reps": 5, "set_index": 1}, headers=auth)

    assert [first.json()["set_index"], second.json()["set_index"]] == [1, 2]
    assert clash.status_code == 409


async def test_duplicate_order_index_is_409(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    workout = await create_session(
        client,
        auth,
        exercises=[{"exercise_id": exercises["press"].id, "order_index": 1}],
    )

    response = await client.post(
        f"{BASE}/{workout['id']}/exercises",
        json={"exercise_id": exercises["row"].id, "order_index": 1},
        headers=auth,
    )

    assert response.status_code == 409


async def test_add_exercise_appends_to_the_end(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    workout = await create_session(
        client, auth, exercises=[{"exercise_id": exercises["press"].id}]
    )

    response = await client.post(
        f"{BASE}/{workout['id']}/exercises",
        json={"exercise_id": exercises["row"].id},
        headers=auth,
    )

    assert response.status_code == 201
    assert response.json()["order_index"] == 2


async def test_update_session_status(client: AsyncClient, auth: dict[str, str]) -> None:
    workout = await create_session(client, auth)

    response = await client.patch(
        f"{BASE}/{workout['id']}",
        json={"status": "completed", "notes": "felt strong"},
        headers=auth,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["notes"] == "felt strong"


async def test_delete_session_hides_it_and_everything_under_it(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    workout = await create_session(
        client, auth, exercises=[{"exercise_id": exercises["press"].id}]
    )
    item_id = workout["exercises"][0]["id"]

    assert (
        await client.delete(f"{BASE}/{workout['id']}", headers=auth)
    ).status_code == 204
    orphan = await client.post(
        f"{BASE}/{workout['id']}/exercises/{item_id}/sets",
        json={"reps": 5},
        headers=auth,
    )
    assert orphan.status_code == 404
    gone = await client.get(f"{BASE}/{workout['id']}", headers=auth)
    assert gone.status_code == 404
    assert (await client.get(BASE, headers=auth)).json()["items"] == []


async def test_delete_is_soft_and_restore_brings_the_sets_back(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    workout = await create_session(
        client, auth, exercises=[{"exercise_id": exercises["press"].id}]
    )
    item_id = workout["exercises"][0]["id"]
    await client.post(
        f"{BASE}/{workout['id']}/exercises/{item_id}/sets",
        json={"reps": 8, "weight_kg": 60},
        headers=auth,
    )
    await client.delete(f"{BASE}/{workout['id']}", headers=auth)

    binned = await client.get(BASE, params={"deleted_only": True}, headers=auth)
    assert [s["id"] for s in binned.json()["items"]] == [workout["id"]]
    assert binned.json()["items"][0]["deleted_at"] is not None

    restored = await client.post(f"{BASE}/{workout['id']}/restore", headers=auth)
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None
    # Nothing was cascaded away while it sat in the bin.
    assert len(restored.json()["exercises"][0]["sets"]) == 1

    live = await client.get(BASE, headers=auth)
    assert [s["id"] for s in live.json()["items"]] == [workout["id"]]


async def test_restoring_a_live_session_is_a_conflict(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    workout = await create_session(client, auth)
    assert (
        await client.post(f"{BASE}/{workout['id']}/restore", headers=auth)
    ).status_code == 409


async def test_purge_requires_the_bin_first_and_is_permanent(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    workout = await create_session(client, auth)
    assert (
        await client.delete(f"{BASE}/{workout['id']}/purge", headers=auth)
    ).status_code == 409

    await client.delete(f"{BASE}/{workout['id']}", headers=auth)
    assert (
        await client.delete(f"{BASE}/{workout['id']}/purge", headers=auth)
    ).status_code == 204
    assert (
        await client.post(f"{BASE}/{workout['id']}/restore", headers=auth)
    ).status_code == 404


async def test_list_filters_and_summarises(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    await create_session(
        client,
        auth,
        exercises=[{"exercise_id": exercises["press"].id}],
        program_day="Push A",
    )
    await create_session(client, auth, session_date="2020-01-01", status="completed")

    everything = (await client.get(BASE, headers=auth)).json()
    completed = (
        await client.get(BASE, params={"status": "completed"}, headers=auth)
    ).json()
    windowed = (
        await client.get(BASE, params={"date_to": "2020-06-01"}, headers=auth)
    ).json()

    assert everything["total"] == 2
    # Newest first.
    assert everything["items"][0]["program_day"] == "Push A"
    assert everything["items"][0]["exercise_count"] == 1
    assert everything["items"][0]["set_count"] == 0
    assert completed["total"] == 1
    assert windowed["total"] == 1


async def test_another_users_session_is_404(
    client: AsyncClient, auth: dict[str, str], other_auth: dict[str, str]
) -> None:
    """Not 403: a distinguishable response would confirm the id exists."""
    workout = await create_session(client, auth)

    assert (
        await client.get(f"{BASE}/{workout['id']}", headers=other_auth)
    ).status_code == 404
    assert (
        await client.delete(f"{BASE}/{workout['id']}", headers=other_auth)
    ).status_code == 404
    assert (await client.get(BASE, headers=other_auth)).json()["total"] == 0


async def test_rpe_out_of_range_is_422(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    """Mirrors ck_rpe_range, so the caller gets a 422 rather than a 500 from the
    database."""
    workout = await create_session(
        client, auth, exercises=[{"exercise_id": exercises["press"].id}]
    )

    response = await client.post(
        f"{BASE}/{workout['id']}/exercises/{workout['exercises'][0]['id']}/sets",
        json={"reps": 5, "rpe": 11},
        headers=auth,
    )

    assert response.status_code == 422


async def test_target_reps_must_be_ordered(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    response = await client.post(
        BASE,
        json={
            "session_date": TODAY,
            "exercises": [
                {
                    "exercise_id": exercises["press"].id,
                    "target_reps_min": 12,
                    "target_reps_max": 8,
                }
            ],
        },
        headers=auth,
    )

    assert response.status_code == 422


async def test_update_and_delete_a_set(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    workout = await create_session(
        client, auth, exercises=[{"exercise_id": exercises["press"].id}]
    )
    url = f"{BASE}/{workout['id']}/exercises/{workout['exercises'][0]['id']}/sets"
    set_id = (await client.post(url, json={"reps": 5}, headers=auth)).json()["id"]

    patched = await client.patch(
        f"{url}/{set_id}", json={"reps": 8, "weight_kg": 60}, headers=auth
    )
    assert patched.status_code == 200
    assert (patched.json()["reps"], patched.json()["weight_kg"]) == (8, 60)

    assert (await client.delete(f"{url}/{set_id}", headers=auth)).status_code == 204
    body = (await client.get(f"{BASE}/{workout['id']}", headers=auth)).json()
    assert body["exercises"][0]["sets"] == []
