from datetime import datetime, timezone

import pytest

from common.timeutil import (
    brt_day,
    brt_days,
    day_start_utc_iso,
    format_brt,
    parse_iso,
    to_utc_iso,
)


def test_parse_iso_keeps_offset():
    assert parse_iso("2026-10-01T22:15:03-03:00") == datetime(2026, 10, 2, 1, 15, 3, tzinfo=timezone.utc)


def test_parse_iso_accepts_z():
    assert parse_iso("2026-10-02T01:15:03Z") == datetime(2026, 10, 2, 1, 15, 3, tzinfo=timezone.utc)


def test_parse_iso_naive_is_brt():
    assert parse_iso("2026-10-01T22:15:03") == datetime(2026, 10, 2, 1, 15, 3, tzinfo=timezone.utc)


def test_parse_iso_rejects_garbage():
    with pytest.raises(ValueError):
        parse_iso("ontem")


def test_parse_iso_rejects_non_string():
    with pytest.raises(TypeError):
        parse_iso(123)


def test_to_utc_iso_matches_js_format():
    assert to_utc_iso(parse_iso("2026-10-01T22:15:03.5-03:00")) == "2026-10-02T01:15:03.500Z"


def test_brt_day_uses_brazil_date():
    assert brt_day(parse_iso("2026-10-02T02:30:00Z")) == "2026-10-01"


def test_brt_days_is_inclusive():
    start = parse_iso("2026-09-30T12:00:00-03:00")
    end = parse_iso("2026-10-02T00:30:00-03:00")
    assert brt_days(start, end) == ["2026-09-30", "2026-10-01", "2026-10-02"]


def test_format_brt():
    assert format_brt(parse_iso("2026-10-02T01:15:03Z")) == "01/10/2026 22:15:03"


def test_day_start_utc_iso():
    assert day_start_utc_iso("2026-10-01") == "2026-10-01T03:00:00.000Z"
