from datetime import datetime, timezone

import pytest

import api_events
from factories import api_request, body, put_event

FIXED_NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def now(monkeypatch):
    monkeypatch.setattr(api_events, "now_utc", lambda: FIXED_NOW)


def seed():
    put_event("e1", driverId="drv_ana", timestamp="2026-10-01T12:00:00.000Z", day="2026-10-01", score=45, severity="mild")
    # 23:30 em Brasília do dia 01 = 02:30Z do dia 02
    put_event("e2", driverId="drv_ana", timestamp="2026-10-02T02:30:00.000Z", day="2026-10-01", score=85, severity="critical")
    put_event(
        "e3",
        driverId="drv_bia",
        timestamp="2026-10-02T11:00:00.000Z",
        day="2026-10-02",
        score=65,
        severity="drowsy",
        acknowledgedAt="2026-10-02T11:05:00.000Z",
        acknowledgedBy="op@helio.dev",
    )
    put_event("old", driverId="drv_ana", timestamp="2026-09-20T12:00:00.000Z", day="2026-09-20", score=90, severity="critical")


def list_events(**query):
    return api_events.handler(api_request("GET /api/events", query=query or None), None)


def ids(response):
    return [row["id"] for row in body(response)["rows"]]


def test_default_lists_last_7_days_newest_first(aws, now):
    seed()
    response = list_events()
    assert response["statusCode"] == 200
    assert ids(response) == ["e3", "e2", "e1"]
    assert {k: v for k, v in body(response).items() if k != "rows"} == {"total": 3, "page": 1, "pageSize": 20}


def test_range_crossing_brt_midnight(aws, now):
    seed()
    response = list_events(**{"from": "2026-10-01T23:00:00-03:00", "to": "2026-10-01T23:59:59-03:00"})
    assert ids(response) == ["e2"]


def test_filters(aws, now):
    seed()
    assert ids(list_events(severity="critical,drowsy")) == ["e3", "e2"]
    assert ids(list_events(driverId="drv_bia")) == ["e3"]
    assert ids(list_events(acknowledged="false")) == ["e2", "e1"]
    assert ids(list_events(acknowledged="true")) == ["e3"]
    assert ids(list_events(vehicleId="v1")) == []


def test_sort_and_paginate(aws, now):
    seed()
    response = list_events(sort="score", dir="asc", page="2", pageSize="2")
    assert ids(response) == ["e2"]
    assert body(response)["total"] == 3


def test_sort_by_severity(aws, now):
    seed()
    assert ids(list_events(sort="severity")) == ["e2", "e3", "e1"]


@pytest.mark.parametrize(
    "query",
    [
        {"severity": "panic"},
        {"sort": "name"},
        {"dir": "up"},
        {"page": "0"},
        {"pageSize": "500"},
        {"from": "ontem"},
        {"acknowledged": "talvez"},
        {"from": "2026-01-01T00:00:00Z"},
        {"from": "2026-10-02T00:00:00Z", "to": "2026-10-01T00:00:00Z"},
    ],
)
def test_bad_query_is_400(aws, now, query):
    response = list_events(**query)
    assert response["statusCode"] == 400
    assert "message" in body(response)


def test_get_event(aws):
    put_event("e1")
    response = api_events.handler(api_request("GET /api/events/{id}", path={"id": "e1"}), None)
    assert response["statusCode"] == 200
    assert body(response)["id"] == "e1"
    assert body(response)["driverId"] is None


def test_get_missing_event_is_404(aws):
    response = api_events.handler(api_request("GET /api/events/{id}", path={"id": "nope"}), None)
    assert response["statusCode"] == 404
    assert body(response) == {"message": "Alerta não encontrado"}


def acknowledge(event_id, email):
    return api_events.handler(
        api_request("POST /api/events/{id}/acknowledge", path={"id": event_id}, email=email, body={"by": "falso"}),
        None,
    )


def test_acknowledge_uses_token_email(aws, now):
    put_event("e1")
    response = acknowledge("e1", "gestor@helio.dev")
    assert response["statusCode"] == 200
    assert body(response)["acknowledgedBy"] == "gestor@helio.dev"
    assert body(response)["acknowledgedAt"] == "2026-10-02T12:00:00.000Z"


def test_acknowledge_twice_keeps_first(aws, now):
    put_event("e1")
    acknowledge("e1", "primeiro@helio.dev")
    response = acknowledge("e1", "segundo@helio.dev")
    assert response["statusCode"] == 200
    assert body(response)["acknowledgedBy"] == "primeiro@helio.dev"


def test_acknowledge_missing_is_404(aws, now):
    assert acknowledge("nope", "a@helio.dev")["statusCode"] == 404
