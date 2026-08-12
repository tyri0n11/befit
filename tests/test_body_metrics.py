"""Body metrics log tests. Nothing persists — see conftest.py."""

import pytest_asyncio
from httpx import AsyncClient

from app.core.settings import settings

AUTH = f"{settings.API_V1_STR}/auth"
BASE = f"{settings.API_V1_STR}/users/me/body-metrics"

CREDENTIALS = {
    "email": "tracked@example.com",
    "display_name": "Tracked",
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


async def test_log_requires_at_least_one_metric(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.post(BASE, headers=auth, json={"measured_at": "2026-08-01"})
    assert response.status_code == 422


async def test_log_out_of_range_weight_rejected(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.post(
        BASE, headers=auth, json={"measured_at": "2026-08-01", "weight_kg": 500}
    )
    assert response.status_code == 422


async def test_relogging_same_day_upserts_in_place(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    first = await client.post(
        BASE,
        headers=auth,
        json={"measured_at": "2026-08-01", "weight_kg": 80, "body_fat_percent": 20},
    )
    assert first.status_code == 201, first.text
    log_id = first.json()["id"]

    second = await client.post(
        BASE, headers=auth, json={"measured_at": "2026-08-01", "weight_kg": 79.5}
    )
    assert second.status_code == 201, second.text
    body = second.json()
    # Same day -> same row, and this is a full replace: body_fat_percent
    # wasn't sent this time, so it's cleared, not carried over.
    assert body["id"] == log_id
    assert body["weight_kg"] == 79.5
    assert body["body_fat_percent"] is None


async def test_list_orders_most_recent_first(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    await client.post(
        BASE, headers=auth, json={"measured_at": "2026-07-01", "weight_kg": 82}
    )
    await client.post(
        BASE, headers=auth, json={"measured_at": "2026-08-01", "weight_kg": 80}
    )

    response = await client.get(BASE, headers=auth, params={"period": "3m"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert [item["measured_at"] for item in body["items"]] == [
        "2026-08-01",
        "2026-07-01",
    ]


async def test_progress_computes_baseline_current_and_change(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    await client.post(
        BASE,
        headers=auth,
        json={
            "measured_at": "2026-07-01",
            "weight_kg": 80,
            "body_fat_percent": 20.5,
            "muscle_mass_kg": 35,
        },
    )
    await client.post(
        BASE,
        headers=auth,
        json={
            "measured_at": "2026-08-01",
            "weight_kg": 78,
            "body_fat_percent": 19,
            "muscle_mass_kg": 36,
        },
    )

    response = await client.get(
        BASE + "/progress", headers=auth, params={"period": "3m"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["weight_kg"] == {"baseline": 80.0, "current": 78.0, "change": -2.0}
    assert body["body_fat_percent"] == {
        "baseline": 20.5,
        "current": 19.0,
        "change": -1.5,
    }
    assert body["muscle_mass_kg"] == {"baseline": 35.0, "current": 36.0, "change": 1.0}


async def test_progress_is_null_when_nothing_logged(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.get(
        BASE + "/progress", headers=auth, params={"period": "3m"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["weight_kg"] == {"baseline": None, "current": None, "change": None}


async def test_delete_removes_entry(client: AsyncClient, auth: dict[str, str]) -> None:
    created = await client.post(
        BASE, headers=auth, json={"measured_at": "2026-08-01", "weight_kg": 80}
    )
    log_id = created.json()["id"]

    deleted = await client.delete(f"{BASE}/{log_id}", headers=auth)
    assert deleted.status_code == 204

    listed = await client.get(BASE, headers=auth, params={"period": "1y"})
    assert listed.json()["total"] == 0


async def test_delete_nonexistent_entry_is_404(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.delete(f"{BASE}/999999", headers=auth)
    assert response.status_code == 404


async def test_deleting_another_users_entry_is_404(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    created = await client.post(
        BASE, headers=auth, json={"measured_at": "2026-08-01", "weight_kg": 80}
    )
    log_id = created.json()["id"]

    other = await client.post(
        AUTH + "/register",
        json={**CREDENTIALS, "email": "someone-else@example.com"},
    )
    assert other.status_code == 201, other.text
    other_login = await client.post(
        AUTH + "/login",
        json={"email": "someone-else@example.com", "password": CREDENTIALS["password"]},
    )
    other_auth = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    response = await client.delete(f"{BASE}/{log_id}", headers=other_auth)
    assert response.status_code == 404


async def test_body_metrics_requires_auth(client: AsyncClient) -> None:
    response = await client.get(BASE)
    assert response.status_code == 401
