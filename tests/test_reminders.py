from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from focuspilot.reminders import ReminderSettings, delivery_policy, cooldown_remaining

NOW = datetime(2026, 10, 22, 10)


@pytest.mark.parametrize("mode,banner,reveal,sound", [
    ("panel", False, False, False),
    ("standard", True, False, False),
    ("strong", True, True, True),
])
def test_notification_strength_for_due_soon(mode, banner, reveal, sound):
    decision = delivery_policy(ReminderSettings(mode=mode), NOW, "soon")
    assert decision.allowed
    assert (decision.banner, decision.reveal, decision.sound) == (banner, reveal, sound)


@pytest.mark.parametrize("hour,minute,allowed", [(8, 59, False), (9, 0, True), (17, 59, True), (18, 0, False)])
def test_working_hours_are_start_inclusive_end_exclusive(hour, minute, allowed):
    settings = ReminderSettings(work_enabled=True, work_start="09:00", work_end="18:00")
    assert delivery_policy(settings, NOW.replace(hour=hour, minute=minute), "soon").allowed is allowed


@pytest.mark.parametrize("hour,allowed", [(21, False), (22, True), (0, True), (5, True), (6, False), (12, False)])
def test_working_hours_support_overnight_ranges(hour, allowed):
    settings = ReminderSettings(work_enabled=True, work_start="22:00", work_end="06:00")
    assert delivery_policy(settings, NOW.replace(hour=hour), "soon").allowed is allowed


def test_dnd_expires_at_exact_end():
    settings = ReminderSettings(quiet_until=(NOW + timedelta(minutes=30)).isoformat())
    assert not delivery_policy(settings, NOW + timedelta(minutes=29, seconds=59), "soon").allowed
    assert delivery_policy(settings, NOW + timedelta(minutes=30), "soon").allowed


def test_user_selected_overdue_exception_breaks_dnd_and_working_hours_with_strong_alert():
    settings = ReminderSettings(mode="panel", work_enabled=True, work_start="12:00", work_end="13:00",
                                quiet_until=(NOW + timedelta(hours=1)).isoformat())
    assert not delivery_policy(settings, NOW, "soon").allowed
    decision = delivery_policy(settings, NOW, "overdue")
    assert decision.allowed and decision.banner and decision.reveal and decision.sound
    assert not delivery_policy(replace(settings, overdue_override=False), NOW, "overdue").allowed


def test_standard_overdue_restores_panel_without_sound_when_exception_disabled():
    decision = delivery_policy(ReminderSettings(overdue_override=False), NOW, "overdue")
    assert decision.allowed and decision.banner and decision.reveal
    assert not decision.sound


def test_cooldown_has_exact_boundary_and_clock_rollback_does_not_suppress_indefinitely():
    assert cooldown_remaining(NOW, None, 1) == timedelta(0)
    assert cooldown_remaining(NOW, NOW, 1) == timedelta(minutes=1)
    assert cooldown_remaining(NOW + timedelta(seconds=59), NOW, 1) == timedelta(seconds=1)
    assert cooldown_remaining(NOW + timedelta(minutes=1), NOW, 1) == timedelta(0)
    assert cooldown_remaining(NOW, NOW + timedelta(minutes=2), 1) == timedelta(0)


@pytest.mark.parametrize("values", [
    {"mode": "invalid"}, {"work_start": "24:00"},
    {"work_enabled": True, "work_start": "09:00", "work_end": "09:00"},
    {"cooldown_minutes": 0}, {"cooldown_minutes": 61},
    {"work_enabled": "false"}, {"overdue_override": "true"},
    {"quiet_until": "2026-10-22T10:00:00+08:00"},
])
def test_invalid_settings_are_rejected(values):
    with pytest.raises(ValueError):
        ReminderSettings(**values)
