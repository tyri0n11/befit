"""Workout template tests. Nothing persists — see conftest.py."""

from datetime import date

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models.catalog import EquipmentType, Exercise, ForceType, MovementPattern

BASE = f"{settings.API_V1_STR}/templates"
AUTH = f"{settings.API_V1_STR}/auth"
SESSIONS = f"{settings.API_V1_STR}/sessions"
TODAY = date.today().isoformat()

CREDENTIALS = {
    "email": "planner@example.com",
    "display_name": "Planner",
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
    session.add_all([press, row])
    await session.flush()
    return {"press": press, "row": row}


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    payload = {**CREDENTIALS, "email": email}
    await client.post(AUTH + "/register", json=payload)
    login = await client.post(
        AUTH + "/login",
        json={"email": email, "password": payload["password"]},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest_asyncio.fixture
async def auth(client: AsyncClient) -> dict[str, str]:
    return await _login(client, CREDENTIALS["email"])


@pytest_asyncio.fixture
async def other_auth(client: AsyncClient) -> dict[str, str]:
    return await _login(client, "someone-else@example.com")


async def create_template(
    client: AsyncClient, auth: dict[str, str], **overrides: object
) -> dict:
    response = await client.post(
        BASE, json={"name": "Push A", **overrides}, headers=auth
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(BASE)).status_code == 401


async def test_create_with_exercises(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    body = await create_template(
        client,
        auth,
        program_day="Push",
        exercises=[
            {
                "exercise_id": exercises["press"].id,
                "target_sets": 3,
                "target_reps_min": 8,
                "target_reps_max": 10,
            },
            {"exercise_id": exercises["row"].id, "target_weight_kg": 30},
        ],
    )

    assert body["name"] == "Push A"
    # order_index falls back to the position in the payload.
    assert [e["order_index"] for e in body["exercises"]] == [1, 2]
    assert [e["slug"] for e in body["exercises"]] == [
        "zz-bench-press",
        "zz-one-arm-row",
    ]
    assert body["exercises"][0]["target_reps_max"] == 10
    assert body["exercises"][1]["target_weight_kg"] == 30


async def test_duplicate_name_is_409(client: AsyncClient, auth: dict[str, str]) -> None:
    await create_template(client, auth)

    response = await client.post(BASE, json={"name": "Push A"}, headers=auth)

    assert response.status_code == 409


async def test_same_name_is_free_for_another_user(
    client: AsyncClient, auth: dict[str, str], other_auth: dict[str, str]
) -> None:
    """`uq_template_name` is scoped to the owner — two people may both run a
    "Push A"."""
    await create_template(client, auth)

    response = await client.post(BASE, json={"name": "Push A"}, headers=other_auth)

    assert response.status_code == 201


async def test_rename_to_a_taken_name_is_409(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    first = await create_template(client, auth)
    await create_template(client, auth, name="Pull A")

    clash = await client.patch(
        f"{BASE}/{first['id']}", json={"name": "Pull A"}, headers=auth
    )
    # Renaming to its own current name must stay allowed.
    same = await client.patch(
        f"{BASE}/{first['id']}", json={"name": "Push A"}, headers=auth
    )

    assert clash.status_code == 409
    assert same.status_code == 200


async def test_unknown_exercise_is_422(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.post(
        BASE,
        json={"name": "Broken", "exercises": [{"exercise_id": 10_000_000}]},
        headers=auth,
    )

    assert response.status_code == 422
    assert "10000000" in response.json()["detail"]


async def test_add_exercise_appends_and_conflicts(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    template = await create_template(
        client, auth, exercises=[{"exercise_id": exercises["press"].id}]
    )

    appended = await client.post(
        f"{BASE}/{template['id']}/exercises",
        json={"exercise_id": exercises["row"].id},
        headers=auth,
    )
    clash = await client.post(
        f"{BASE}/{template['id']}/exercises",
        json={"exercise_id": exercises["row"].id, "order_index": 1},
        headers=auth,
    )

    assert appended.json()["order_index"] == 2
    assert clash.status_code == 409


async def test_delete_exercise_drops_it_from_the_template(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    template = await create_template(
        client, auth, exercises=[{"exercise_id": exercises["press"].id}]
    )
    slot_id = template["exercises"][0]["id"]

    deleted = await client.delete(
        f"{BASE}/{template['id']}/exercises/{slot_id}", headers=auth
    )
    body = (await client.get(f"{BASE}/{template['id']}", headers=auth)).json()

    assert deleted.status_code == 204
    assert body["exercises"] == []


async def test_target_reps_must_be_ordered(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    """The same rule as sessions — both inherit `ExerciseTargets`."""
    response = await client.post(
        BASE,
        json={
            "name": "Backwards",
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


async def test_instantiate_copies_the_plan(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    template = await create_template(
        client,
        auth,
        program_day="Push",
        exercises=[
            {
                "exercise_id": exercises["press"].id,
                "target_sets": 3,
                "target_weight_kg": 60,
            },
            {"exercise_id": exercises["row"].id},
        ],
    )

    response = await client.post(
        f"{BASE}/{template['id']}/sessions",
        json={"session_date": TODAY, "bodyweight_kg": 72.5},
        headers=auth,
    )
    body = response.json()

    assert response.status_code == 201
    assert body["session_date"] == TODAY
    assert body["status"] == "planned"
    assert body["program_day"] == "Push"
    assert body["bodyweight_kg"] == 72.5
    assert [e["slug"] for e in body["exercises"]] == [
        "zz-bench-press",
        "zz-one-arm-row",
    ]
    assert body["exercises"][0]["target_sets"] == 3
    assert body["exercises"][0]["target_weight_kg"] == 60
    # A plan, not a log: nothing is logged until the user lifts.
    assert body["exercises"][0]["sets"] == []
    assert body["tonnage"] == 0


async def test_instantiated_session_outlives_the_template(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    """The session is a copy, not a link — deleting the template must not take
    training history with it."""
    template = await create_template(
        client, auth, exercises=[{"exercise_id": exercises["press"].id}]
    )
    workout = await client.post(
        f"{BASE}/{template['id']}/sessions",
        json={"session_date": TODAY},
        headers=auth,
    )
    session_id = workout.json()["id"]

    assert (
        await client.delete(f"{BASE}/{template['id']}", headers=auth)
    ).status_code == 204

    survivor = await client.get(f"{SESSIONS}/{session_id}", headers=auth)
    assert survivor.status_code == 200
    assert survivor.json()["exercises"][0]["slug"] == "zz-bench-press"


async def test_template_from_session_copies_targets_not_results(
    client: AsyncClient, auth: dict[str, str], exercises: dict[str, Exercise]
) -> None:
    """Targets come from the plan. Copying the last set's weight would bake a
    bad day into the template."""
    created = await client.post(
        SESSIONS,
        json={
            "session_date": TODAY,
            "program_day": "Push",
            "exercises": [
                {
                    "exercise_id": exercises["press"].id,
                    "target_sets": 3,
                    "target_weight_kg": 60,
                }
            ],
        },
        headers=auth,
    )
    workout = created.json()
    # A bad day: the actual set was well under the target.
    await client.post(
        f"{SESSIONS}/{workout['id']}/exercises/{workout['exercises'][0]['id']}/sets",
        json={"reps": 3, "weight_kg": 40},
        headers=auth,
    )

    response = await client.post(
        f"{BASE}/from-session/{workout['id']}",
        json={"name": "Saved Push"},
        headers=auth,
    )
    body = response.json()

    assert response.status_code == 201
    assert body["program_day"] == "Push"
    assert body["exercises"][0]["target_weight_kg"] == 60
    assert body["exercises"][0]["target_sets"] == 3


async def test_from_unknown_session_is_404(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.post(
        f"{BASE}/from-session/10000000", json={"name": "Nope"}, headers=auth
    )

    assert response.status_code == 404


async def test_list_filters_by_name(client: AsyncClient, auth: dict[str, str]) -> None:
    await create_template(client, auth)
    await create_template(client, auth, name="Pull A")

    everything = (await client.get(BASE, headers=auth)).json()
    matched = (await client.get(BASE, params={"q": "pull"}, headers=auth)).json()

    assert everything["total"] == 2
    # Ordered by name.
    assert [t["name"] for t in everything["items"]] == ["Pull A", "Push A"]
    assert everything["items"][0]["exercise_count"] == 0
    assert matched["total"] == 1


async def test_another_users_template_is_404(
    client: AsyncClient, auth: dict[str, str], other_auth: dict[str, str]
) -> None:
    """Not 403: a distinguishable response would confirm the id exists."""
    template = await create_template(client, auth)

    assert (
        await client.get(f"{BASE}/{template['id']}", headers=other_auth)
    ).status_code == 404
    assert (
        await client.delete(f"{BASE}/{template['id']}", headers=other_auth)
    ).status_code == 404
    assert (
        await client.post(
            f"{BASE}/{template['id']}/sessions",
            json={"session_date": TODAY},
            headers=other_auth,
        )
    ).status_code == 404
    assert (await client.get(BASE, headers=other_auth)).json()["total"] == 0
