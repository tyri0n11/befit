"""User profile tests. Nothing persists — see conftest.py."""

import pytest_asyncio
from httpx import AsyncClient

from app.core.settings import settings

AUTH = f"{settings.API_V1_STR}/auth"
PROFILE = f"{settings.API_V1_STR}/users/me/profile"

CREDENTIALS = {
    "email": "profiled@example.com",
    "display_name": "Profiled",
    "password": "correct-horse-battery",
}


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


async def test_get_profile_creates_empty_row_on_first_read(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.get(PROFILE, headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "sex": None,
        "birth_date": None,
        "height_cm": None,
        "weight_kg": None,
        "goal": None,
        "activity_level": None,
        "bmr_kcal": None,
        "tdee_kcal": None,
    }


async def test_update_computes_bmr_and_tdee(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.put(
        PROFILE,
        headers=auth,
        json={
            "sex": "male",
            "birth_date": "1995-06-15",
            "height_cm": 175,
            "weight_kg": 70,
            "goal": "maintain",
            "activity_level": 3,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Mifflin-St Jeor: 10*70 + 6.25*175 - 5*age + 5, TDEE = BMR * 1.55 (level 3).
    assert body["bmr_kcal"] is not None
    assert body["tdee_kcal"] == round(body["bmr_kcal"] * 1.55, 1)


async def test_partial_update_preserves_other_fields(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    await client.put(
        PROFILE,
        headers=auth,
        json={
            "sex": "female",
            "birth_date": "1990-01-01",
            "height_cm": 165,
            "weight_kg": 60,
            "activity_level": 2,
        },
    )
    response = await client.put(PROFILE, headers=auth, json={"weight_kg": 58})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["height_cm"] == 165
    assert body["weight_kg"] == 58.0
    assert body["bmr_kcal"] is not None


async def test_bmr_stays_null_until_profile_is_complete(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.put(PROFILE, headers=auth, json={"weight_kg": 70})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bmr_kcal"] is None
    assert body["tdee_kcal"] is None


async def test_future_birth_date_rejected(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.put(
        PROFILE, headers=auth, json={"birth_date": "2099-01-01"}
    )
    assert response.status_code == 422


async def test_height_out_of_range_rejected(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.put(PROFILE, headers=auth, json={"height_cm": 10})
    assert response.status_code == 422


async def test_profile_requires_auth(client: AsyncClient) -> None:
    response = await client.get(PROFILE)
    assert response.status_code == 401
