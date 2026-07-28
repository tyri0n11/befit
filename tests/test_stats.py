"""Volume view tests. Nothing persists — see conftest.py.

The fixture logs a known workload on fixed dates so every total is arithmetic
the test can restate: the assertions are the tonnage formula, not a snapshot.
"""

from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.models.training import Bucket
from app.schemas.stats import Period
from app.services.stats import resolve_range

BASE = f"{settings.API_V1_STR}/stats"
AUTH = f"{settings.API_V1_STR}/auth"
SESSIONS = f"{settings.API_V1_STR}/sessions"

CREDENTIALS = {
    "email": "stats@example.com",
    "display_name": "Stats",
    "password": "correct-horse-battery",
}

TODAY = date.today()
# Both inside a 4-week window, and far enough apart to land in different weeks.
RECENT = TODAY - timedelta(days=1)
EARLIER = TODAY - timedelta(days=10)
LONG_AGO = TODAY - timedelta(days=200)


@pytest_asyncio.fixture
async def auth(client: AsyncClient) -> dict[str, str]:
    await client.post(AUTH + "/register", json=CREDENTIALS)
    login = await client.post(
        AUTH + "/login",
        json={"email": CREDENTIALS["email"], "password": CREDENTIALS["password"]},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest_asyncio.fixture
async def catalog(session: AsyncSession) -> dict[str, Exercise]:
    chest = MuscleGroup(code="zz_chest", name_en="ZZ Chest", name_vi="ZZ Ngực", depth=1)
    back = MuscleGroup(code="zz_back", name_en="ZZ Back", name_vi="ZZ Lưng", depth=1)
    session.add_all([chest, back])
    await session.flush()

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
    pullup = Exercise(
        slug="zz-pull-up",
        name_en="ZZ Pull Up",
        pattern=MovementPattern.VERTICAL_PULL,
        equipment=EquipmentType.BODYWEIGHT,
        force=ForceType.PULL,
    )
    session.add_all([press, row, pullup])
    await session.flush()

    session.add_all(
        [
            ExerciseMuscle(
                exercise_id=press.id,
                muscle_group_id=chest.id,
                role=MuscleRole.PRIMARY,
            ),
            # Also hits the back, but only as a secondary — the role filter
            # must keep it out of the primary view.
            ExerciseMuscle(
                exercise_id=press.id,
                muscle_group_id=back.id,
                role=MuscleRole.SECONDARY,
            ),
            ExerciseMuscle(
                exercise_id=row.id, muscle_group_id=back.id, role=MuscleRole.PRIMARY
            ),
        ]
    )
    await session.flush()
    return {"press": press, "row": row, "pullup": pullup}


async def log(
    client: AsyncClient,
    auth: dict[str, str],
    when: date,
    exercise: Exercise,
    sets: list[dict[str, object]],
) -> None:
    created = await client.post(
        SESSIONS,
        json={
            "session_date": when.isoformat(),
            "exercises": [{"exercise_id": exercise.id}],
        },
        headers=auth,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    url = f"{SESSIONS}/{body['id']}/exercises/{body['exercises'][0]['id']}/sets"
    for payload in sets:
        response = await client.post(url, json=payload, headers=auth)
        assert response.status_code == 201, response.text


@pytest_asyncio.fixture
async def workload(
    client: AsyncClient, auth: dict[str, str], catalog: dict[str, Exercise]
) -> None:
    # 2 x 10 @ 60kg = 1200
    await log(
        client,
        auth,
        RECENT,
        catalog["press"],
        [{"reps": 10, "weight_kg": 60}, {"reps": 10, "weight_kg": 60}],
    )
    # Unilateral: 1 x 10 @ 30kg counts both sides = 600
    await log(client, auth, EARLIER, catalog["row"], [{"reps": 10, "weight_kg": 30}])
    # Bodyweight: no weight, so 0 tonnage but 8 reps.
    await log(client, auth, EARLIER, catalog["pullup"], [{"reps": 8}])
    # Outside every window shorter than 6 months.
    await log(client, auth, LONG_AGO, catalog["press"], [{"reps": 5, "weight_kg": 100}])


async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(BASE + "/volume")).status_code == 401


async def test_volume_totals_follow_the_tonnage_rule(
    client: AsyncClient, auth: dict[str, str], workload: None
) -> None:
    response = await client.get(BASE + "/volume", params={"period": "4w"}, headers=auth)
    totals = response.json()["totals"]

    assert response.status_code == 200
    # 1200 (press) + 600 (unilateral row, both sides) + 0 (bodyweight pull-up)
    assert totals["tonnage"] == 1800
    assert totals["sets"] == 4
    assert totals["reps"] == 38
    assert totals["sessions"] == 3


async def test_period_bounds_the_window(
    client: AsyncClient, auth: dict[str, str], workload: None
) -> None:
    week = await client.get(BASE + "/volume", params={"period": "1w"}, headers=auth)
    half_year = await client.get(
        BASE + "/volume", params={"period": "6m"}, headers=auth
    )
    year = await client.get(BASE + "/volume", params={"period": "1y"}, headers=auth)

    # Only the press from yesterday falls inside a week.
    assert week.json()["totals"]["tonnage"] == 1200
    assert week.json()["totals"]["sessions"] == 1
    # 200 days is past six months, so that session only appears in the year.
    assert half_year.json()["totals"]["tonnage"] == 1800
    assert year.json()["totals"]["tonnage"] == 2300


async def test_empty_buckets_are_zero_filled(
    client: AsyncClient, auth: dict[str, str], workload: None
) -> None:
    """A week with no training must appear as a zero, not vanish — a chart that
    closes the gap misreads a deload as continuous training."""
    response = await client.get(
        BASE + "/volume", params={"period": "4w", "bucket": "week"}, headers=auth
    )
    points = response.json()["points"]

    assert len(points) >= 4
    assert any(point["tonnage"] == 0 for point in points)
    assert sum(point["tonnage"] for point in points) == 1800
    # Ordered oldest first, one bucket per Monday.
    assert points == sorted(points, key=lambda p: p["bucket"])
    assert all(date.fromisoformat(p["bucket"]).weekday() == 0 for p in points)


async def test_day_bucket_matches_session_dates(
    client: AsyncClient, auth: dict[str, str], workload: None
) -> None:
    response = await client.get(
        BASE + "/volume", params={"period": "2w", "bucket": "day"}, headers=auth
    )
    logged = {p["bucket"]: p for p in response.json()["points"] if p["sets"]}

    assert set(logged) == {RECENT.isoformat(), EARLIER.isoformat()}
    assert logged[RECENT.isoformat()]["tonnage"] == 1200
    assert logged[EARLIER.isoformat()]["sessions"] == 2


async def test_explicit_dates_override_the_period(
    client: AsyncClient, auth: dict[str, str], workload: None
) -> None:
    response = await client.get(
        BASE + "/volume",
        params={
            "period": "1w",
            "date_from": EARLIER.isoformat(),
            "date_to": EARLIER.isoformat(),
        },
        headers=auth,
    )
    body = response.json()

    assert body["date_from"] == body["date_to"] == EARLIER.isoformat()
    assert body["totals"]["tonnage"] == 600
    assert body["totals"]["reps"] == 18


async def test_reversed_range_is_422(client: AsyncClient, auth: dict[str, str]) -> None:
    response = await client.get(
        BASE + "/volume",
        params={"date_from": TODAY.isoformat(), "date_to": EARLIER.isoformat()},
        headers=auth,
    )

    assert response.status_code == 422


async def test_muscle_view_respects_role(
    client: AsyncClient, auth: dict[str, str], workload: None
) -> None:
    primary = await client.get(BASE + "/muscles", params={"period": "4w"}, headers=auth)
    every_role = await client.get(
        BASE + "/muscles", params={"period": "4w", "role": "all"}, headers=auth
    )

    by_code = {item["code"]: item for item in primary.json()["items"]}
    assert by_code["zz_chest"]["tonnage"] == 1200
    assert by_code["zz_back"]["tonnage"] == 600

    # Without the role filter the press counts for the back as well.
    any_by_code = {item["code"]: item for item in every_role.json()["items"]}
    assert any_by_code["zz_back"]["tonnage"] == 1800


async def test_muscle_totals_may_exceed_the_range_total(
    client: AsyncClient, auth: dict[str, str], workload: None
) -> None:
    """One exercise counts for every muscle it maps to, so these sums overlap on
    purpose. Asserted so nobody 'fixes' it into a split."""
    volume = await client.get(BASE + "/volume", params={"period": "4w"}, headers=auth)
    muscles = await client.get(
        BASE + "/muscles", params={"period": "4w", "role": "all"}, headers=auth
    )

    per_muscle = sum(item["tonnage"] for item in muscles.json()["items"])
    assert per_muscle > volume.json()["totals"]["tonnage"]


async def test_exercise_view_ranks_by_tonnage(
    client: AsyncClient, auth: dict[str, str], workload: None
) -> None:
    response = await client.get(
        BASE + "/exercises", params={"period": "4w"}, headers=auth
    )
    items = response.json()["items"]

    assert [item["slug"] for item in items] == [
        "zz-bench-press",
        "zz-one-arm-row",
        "zz-pull-up",
    ]
    # Bodyweight work is invisible in tonnage; reps is what records it.
    assert items[-1]["tonnage"] == 0
    assert items[-1]["reps"] == 8


async def test_only_my_own_volume_is_counted(
    client: AsyncClient, auth: dict[str, str], workload: None
) -> None:
    payload = {**CREDENTIALS, "email": "other@example.com"}
    await client.post(AUTH + "/register", json=payload)
    login = await client.post(
        AUTH + "/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.get(
        BASE + "/volume", params={"period": "1y"}, headers=headers
    )

    assert response.json()["totals"] == {
        "tonnage": 0,
        "sets": 0,
        "reps": 0,
        "sessions": 0,
    }


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        (Period.WEEK_1, date(2026, 3, 24)),
        (Period.WEEK_2, date(2026, 3, 17)),
        (Period.MONTH_1, date(2026, 2, 28)),
        (Period.MONTH_3, date(2025, 12, 31)),
        (Period.YEAR_1, date(2025, 3, 31)),
    ],
)
def test_period_start_clamps_short_months(period: Period, expected: date) -> None:
    """31 March minus one month has no 31 February."""
    window = resolve_range(period, None, date(2026, 3, 31))

    assert window.date_from == expected


def test_bucket_default_is_a_week() -> None:
    assert Bucket.WEEK.value == "week"
