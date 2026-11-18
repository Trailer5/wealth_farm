"""Bounded execution schedules; see the user-space module design notes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc


def instant(value: str) -> datetime:
    """Parse an offset-aware ISO timestamp; never guess a local timezone."""

    if not isinstance(value, str):
        raise ValueError("timestamp must be ISO text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


def boundary(value: str, zone: ZoneInfo, *, end: bool = False) -> datetime:
    """A date spans its local day; a timestamp denotes one exact instant."""

    if not isinstance(value, str):
        raise ValueError("date/time must be ISO text")
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), time.max if end else time.min, zone)
    return instant(value).astimezone(zone)


def normalize_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate start, optional deadline, repeat_days/until and IANA timezone.

    Repetition uses local calendar dates, preserving the local clock across DST.
    It is not a trading calendar. Repeated tasks require an inclusive until date.
    """

    if not isinstance(payload, dict) or set(payload) - {"start", "deadline", "repeat_days", "until", "timezone"}:
        raise ValueError("invalid schedule fields")
    result = dict(payload)
    result.setdefault("timezone", "Asia/Shanghai")
    zone = ZoneInfo(result["timezone"])
    start = boundary(result["start"], zone)
    if len(result["start"]) != 10:
        original = datetime.fromisoformat(result["start"].replace("Z", "+00:00"))
        if original.utcoffset() != start.utcoffset():
            raise ValueError("start offset does not match schedule timezone")
    deadline = result.get("deadline", result["start"])
    if boundary(deadline, zone, end=True) < start:
        raise ValueError("deadline precedes start")
    repeat = result.get("repeat_days")
    if repeat is not None:
        if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
            raise ValueError("repeat_days must be a positive integer")
        if "deadline" in result:
            raise ValueError("repeated tasks use a deadline for each occurrence, not a shared window")
        until = date.fromisoformat(result["until"])
        if until < start.date() or (until - start.date()).days // repeat > 10000:
            raise ValueError("repeat must end after start and contain at most 10001 occurrences")
    elif "until" in result:
        raise ValueError("until requires repeat_days")
    if repeat is not None:
        occurrences(result, result["until"])
    return result


def occurrences(schedule: dict[str, Any], through: str) -> list[dict[str, str]]:
    """Return stable occurrence keys, readiness and original deadlines through a cutoff."""

    zone = ZoneInfo(schedule["timezone"])
    cutoff = boundary(through, zone, end=True)
    start = boundary(schedule["start"], zone)
    repeat = schedule.get("repeat_days")
    until = date.fromisoformat(schedule["until"]) if repeat else start.date()
    result = []
    current = start
    while current <= cutoff and current.date() <= until:
        due_date = current.date().isoformat()
        date_only = len(schedule["start"]) == 10
        deadline = (
            boundary(due_date, zone, end=True) if date_only else current
        ) if repeat else boundary(schedule.get("deadline", schedule["start"]), zone, end=True)
        key = due_date if date_only else current.astimezone(UTC).isoformat()
        result.append({
            "occurrence": key,
            "ready_at": current.astimezone(UTC).isoformat(),
            "deadline_at": deadline.astimezone(UTC).isoformat(),
        })
        if repeat is None or (until - current.date()).days < repeat:
            break
        next_day = current.date() + timedelta(days=repeat)
        current = datetime.combine(next_day, start.timetz().replace(tzinfo=None), zone)
        if current.date() > until or current > cutoff:
            break
        if current.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != current.replace(tzinfo=None):
            raise ValueError("recurrence falls in a nonexistent DST time; register explicit times")
        if current.replace(fold=0).utcoffset() != current.replace(fold=1).utcoffset():
            raise ValueError("recurrence falls in an ambiguous DST time; register explicit times")
    return result