from datetime import datetime, timezone

import pytest

import api_trips
from factories import THING, api_request, body, put_event, put_trip

FIXED_NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def now(monkeypatch):
    monkeypatch.setattr(api_trips, "now_utc", lambda: FIXED_NOW)


def seed():
    put_trip("t1", driverId="drv_ana", startedAt="2026-10-01T10:00:00.000Z", maxScore=90, alertCount=2)
    put_trip("t2", driverId="drv_ana", startedAt="2026-10-02T10:00:00.000Z")
    put_trip("t3", driverId="drv_bia", startedAt="2026-10-02T09:00:00.000Z")
    put_trip("old", driverId="drv_ana", startedAt="2026-09-01T10:00:00.000Z")


def list_trips(**query):
    return api_trips.handler(api_request("GET /api/trips", query=query or None), None)


def ids(response):
    return [t["id"] for t in body(response)]


def test_lists_last_7_days_newest_first(aws, now):
    seed()
    assert ids(list_trips()) == ["t2", "t3", "t1"]


def test_filters_by_driver_and_range(aws, now):
    seed()
    assert ids(list_trips(driverId="drv_ana")) == ["t2", "t1"]
    assert ids(list_trips(**{"from": "2026-10-02T00:00:00Z"})) == ["t2", "t3"]


def test_trip_shape(aws, now):
    seed()
    t1 = next(t for t in body(list_trips()) if t["id"] == "t1")
    assert t1 == {
        "id": "t1",
        "deviceId": THING,
        "driverId": "drv_ana",
        "startedAt": "2026-10-01T10:00:00.000Z",
        "lastEventAt": "2026-10-02T11:00:00.000Z",
        "maxScore": 90,
        "alertCount": 2,
    }


def test_get_trip_with_events_in_order(aws):
    put_trip("ride-1")
    put_event("late", timestamp="2026-10-02T11:00:00.000Z")
    put_event("early", timestamp="2026-10-02T10:30:00.000Z")
    put_event("other", tripId="ride-2")
    response = api_trips.handler(api_request("GET /api/trips/{id}", path={"id": "ride-1"}), None)
    assert response["statusCode"] == 200
    assert [e["id"] for e in body(response)["events"]] == ["early", "late"]


def test_get_missing_trip_is_404(aws):
    response = api_trips.handler(api_request("GET /api/trips/{id}", path={"id": "nope"}), None)
    assert response["statusCode"] == 404
    assert body(response) == {"message": "Viagem não encontrada"}


def test_bad_dates_are_400(aws, now):
    assert list_trips(**{"from": "ontem"})["statusCode"] == 400
    assert list_trips(**{"from": "2026-10-02T00:00:00Z", "to": "2026-10-01T00:00:00Z"})["statusCode"] == 400
