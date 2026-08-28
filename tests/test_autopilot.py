from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.autopilot import autopilot_candidate_slots


def test_autopilot_never_exceeds_groq_vision_image_limit():
    assert 1 <= settings.autopilot_frame_count <= 3


def test_autopilot_candidate_slots_respect_frequency_and_existing_schedule():
    paris = ZoneInfo("Europe/Paris")
    now = datetime(2026, 8, 24, 8, tzinfo=timezone.utc)
    existing = datetime(2026, 8, 26, 16, tzinfo=timezone.utc)

    slots = autopilot_candidate_slots(
        best_times=[{"weekday": "mercredi", "hour": 18}],
        existing_dates=[existing],
        posts_per_week=1,
        timezone_name="Europe/Paris",
        item_count=2,
        now=now,
    )

    assert len(slots) >= 2
    existing_week = existing.astimezone(paris).isocalendar()[:2]
    assert all(
        datetime.fromisoformat(value).astimezone(paris).isocalendar()[:2]
        != existing_week
        for value in slots
    )


def test_autopilot_candidate_slots_never_collide_within_three_hours():
    slots = autopilot_candidate_slots(
        best_times=[
            {"weekday": "mardi", "hour": 18},
            {"weekday": "jeudi", "hour": 18},
            {"weekday": "dimanche", "hour": 18},
        ],
        existing_dates=[],
        posts_per_week=3,
        timezone_name="Europe/Paris",
        item_count=5,
        now=datetime(2026, 8, 24, 8, tzinfo=timezone.utc),
    )

    parsed = [datetime.fromisoformat(value) for value in slots]
    assert len(parsed) >= 5
    assert all(
        abs((left - right).total_seconds()) >= 3 * 3600
        for index, left in enumerate(parsed)
        for right in parsed[index + 1 :]
    )
