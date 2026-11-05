"""Conversion of declared schedules to EventBridge schedule expressions."""

from __future__ import annotations

import re
from typing import Any

_CRON_FIELD = re.compile(r"^[0-9A-Za-z*/,\-]+$")


_CRON_DAY_OF_WEEK_NAMES = re.compile(r"^[A-Za-z][A-Za-z,\-]*$")


def schedule_expression_problem(cron: Any) -> str | None:
    """Why ``cron`` cannot become an EventBridge schedule expression, or None."""
    return _convert_schedule(cron)[1]


def aws_schedule_expression(cron: str) -> str:
    """EventBridge schedule expression for a declared schedule.

    Accepts ``rate(...)`` / ``cron(...)`` expressions unchanged and converts
    5-field Unix cron (minute hour day-of-month month day-of-week) to the
    6-field EventBridge form. Raises ValueError for anything else.
    """
    expression, problem = _convert_schedule(cron)
    if problem:
        raise ValueError(problem)
    assert expression is not None
    return expression


_RATE = re.compile(r"rate\((\d+) (minute|minutes|hour|hours|day|days)\)")


_EVENTBRIDGE_CRON_FIELD = re.compile(r"^[0-9A-Za-z*/,\-?#]+$")


def _check_rate(text: str) -> tuple[str | None, str | None]:
    match = _RATE.fullmatch(text)
    if match is None:
        return None, f"'{text}' is not rate(<positive integer> minute|minutes|hour|hours|day|days)"
    value, unit = int(match.group(1)), match.group(2)
    if value < 1 or (value == 1) != unit.endswith(("minute", "hour", "day")):
        return None, f"'{text}': EventBridge needs the singular unit for 1 and the plural otherwise"
    return text, None


def _check_eventbridge_cron(text: str) -> tuple[str | None, str | None]:
    if not text.endswith(")"):
        return None, f"'{text}' is not cron(<6 fields>)"
    fields = text[len("cron("):-1].split()
    if len(fields) != 6 or not all(_EVENTBRIDGE_CRON_FIELD.match(item) for item in fields):
        return None, f"'{text}' needs exactly 6 fields (minute hour day-of-month month day-of-week year)"
    return text, None


def _convert_schedule(cron: Any) -> tuple[str | None, str | None]:
    if not isinstance(cron, str) or not cron.strip():
        return None, "needs a non-empty cron string"
    text = cron.strip()
    if text.startswith("rate("):
        return _check_rate(text)
    if text.startswith("cron("):
        return _check_eventbridge_cron(text)
    fields = text.split()
    if len(fields) != 5:
        return None, (
            f"'{cron}' is not a 5-field cron (minute hour day-of-month month day-of-week) "
            "or an EventBridge rate(...)/cron(...) expression"
        )
    if not all(_CRON_FIELD.match(item) for item in fields):
        return None, f"'{cron}' has characters outside digits, letters and * / , -"
    minute, hour, day, month, weekday = fields
    if weekday != "*" and not _CRON_DAY_OF_WEEK_NAMES.match(weekday):
        return None, (
            f"'{cron}' uses a numeric day-of-week, whose numbering differs between Unix cron and "
            "EventBridge; use names (MON-FRI) or an EventBridge cron(...) expression"
        )
    if day != "*" and weekday != "*":
        return None, f"'{cron}' sets both day-of-month and day-of-week, which EventBridge does not allow"
    if weekday == "*":
        weekday = "?"
    else:
        day = "?"
    return f"cron({minute} {hour} {day} {month} {weekday} *)", None
