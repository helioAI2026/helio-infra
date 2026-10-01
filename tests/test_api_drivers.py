from datetime import datetime, timezone

import boto3
import pytest

import api_drivers
from factories import THING, api_request, body, put_driver, put_event, put_trip

FIXED_NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
EDITOR = ("GestorDeFrota",)


@pytest.fixture
def now(monkeypatch):
    monkeypatch.setattr(api_drivers, "now_utc", lambda: FIXED_NOW)


def call(route_key, **kwargs):
    return api_drivers.handler(api_request(route_key, **kwargs), None)


def stored_driver(driver_id):
    return boto3.resource("dynamodb").Table("helio-drivers").get_item(Key={"driverId": driver_id}).get("Item")


def seed_ana_on_shift():
    put_driver("drv_ana", "Ana Souza", device=THING, licenseNo="123", phone="+55 11 9")
    put_trip("ride-1", driverId="drv_ana", startedAt="2026-10-02T10:00:00.000Z", lastEventAt="2026-10-02T11:50:00.000Z")
    put_event("e1", driverId="drv_ana", timestamp="2026-10-02T11:00:00.000Z", score=50, severity="mild")
    put_event("e2", driverId="drv_ana", timestamp="2026-10-02T11:50:00.000Z", score=70, severity="drowsy")
    put_event("old", driverId="drv_ana", timestamp="2026-09-01T11:00:00.000Z", day="2026-09-01", score=99)


def test_list_drivers_sorted_with_stats(aws, now):
    put_driver("drv_bia", "Bia Lima")
    seed_ana_on_shift()
    response = call("GET /api/drivers")
    assert response["statusCode"] == 200
    ana, bia = body(response)
    assert ana == {
        "id": "drv_ana",
        "name": "Ana Souza",
        "licenseNo": "123",
        "phone": "+55 11 9",
        "assignedVehicleId": None,
        "assignedDeviceId": THING,
        "currentScore": 70,
        "currentSeverity": "drowsy",
        "onShift": True,
        "shiftStartedAt": "2026-10-02T10:00:00.000Z",
        "stats": {"avgScore7d": 60, "eventsThisWeek": 2, "hoursDrivenThisWeek": 1.8},
    }
    assert bia["onShift"] is False
    assert bia["currentScore"] == 0
    assert bia["currentSeverity"] == "alert"
    assert bia["stats"] == {"avgScore7d": 0, "eventsThisWeek": 0, "hoursDrivenThisWeek": 0}


def test_driver_leaves_shift_after_30_minutes(aws, monkeypatch):
    monkeypatch.setattr(api_drivers, "now_utc", lambda: datetime(2026, 10, 2, 12, 21, tzinfo=timezone.utc))
    seed_ana_on_shift()
    ana = body(call("GET /api/drivers/{id}", path={"id": "drv_ana"}))
    assert ana["onShift"] is False
    assert ana["currentScore"] == 0
    assert ana["shiftStartedAt"] is None


def test_get_missing_driver_is_404(aws, now):
    response = call("GET /api/drivers/{id}", path={"id": "nope"})
    assert response["statusCode"] == 404
    assert body(response) == {"message": "Motorista não encontrado"}


def test_history_is_daily_average_for_21_days(aws, now):
    put_driver("drv_ana", "Ana Souza")
    put_event("e1", driverId="drv_ana", timestamp="2026-10-01T15:00:00.000Z", score=40)
    put_event("e2", driverId="drv_ana", timestamp="2026-10-02T02:30:00.000Z", score=60)  # 23:30 BRT do dia 01
    points = body(call("GET /api/drivers/{id}/history", path={"id": "drv_ana"}))
    assert len(points) == 21
    assert points[0]["t"] == "2026-09-12T03:00:00.000Z"
    assert points[-2] == {"t": "2026-10-01T03:00:00.000Z", "score": 50}
    assert points[-1] == {"t": "2026-10-02T03:00:00.000Z", "score": 0}


def test_history_missing_driver_is_404(aws, now):
    assert call("GET /api/drivers/{id}/history", path={"id": "nope"})["statusCode"] == 404


def test_gestor_creates_driver(aws, now):
    response = call("POST /api/drivers", groups=EDITOR, body={"name": "  Carla Dias ", "licenseNo": "999", "phone": ""})
    assert response["statusCode"] == 201
    created = body(response)
    assert created["id"].startswith("drv_")
    assert created["name"] == "Carla Dias"
    assert created["phone"] == ""
    assert created["assignedDeviceId"] is None
    assert stored_driver(created["id"])["name"] == "Carla Dias"


def test_operador_cannot_create_or_update(aws, now):
    put_driver("drv_ana", "Ana")
    assert call("POST /api/drivers", groups=("Operador",), body={"name": "X"})["statusCode"] == 403
    assert call("PUT /api/drivers/{id}", groups=("Operador",), path={"id": "drv_ana"}, body={"name": "X"})["statusCode"] == 403


@pytest.mark.parametrize(
    "payload",
    [{}, {"name": ""}, {"name": "   "}, {"name": 5}, {"name": "X", "salary": 1}, {"name": "x" * 101}],
)
def test_create_validation(aws, now, payload):
    response = call("POST /api/drivers", groups=EDITOR, body=payload)
    assert response["statusCode"] == 400
    assert "message" in body(response)


def test_update_assigns_and_unassigns_device(aws, now):
    put_driver("drv_ana", "Ana")
    response = call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "drv_ana"}, body={"assignedDeviceId": THING, "phone": "+55"})
    assert response["statusCode"] == 200
    assert body(response)["assignedDeviceId"] == THING
    assert body(response)["phone"] == "+55"

    response = call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "drv_ana"}, body={"assignedDeviceId": None})
    assert body(response)["assignedDeviceId"] is None
    assert "assignedDeviceId" not in stored_driver("drv_ana")


def test_device_cannot_belong_to_two_drivers(aws, now):
    put_driver("drv_ana", "Ana", device=THING)
    put_driver("drv_bia", "Bia")
    assert call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "drv_bia"}, body={"assignedDeviceId": THING})["statusCode"] == 409
    assert call("POST /api/drivers", groups=EDITOR, body={"name": "Caio", "assignedDeviceId": THING})["statusCode"] == 409
    assert call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "drv_ana"}, body={"assignedDeviceId": THING})["statusCode"] == 200


def test_update_missing_driver_is_404(aws, now):
    assert call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "nope"}, body={"name": "X"})["statusCode"] == 404


def test_name_cannot_be_null(aws, now):
    put_driver("drv_ana", "Ana")
    assert call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "drv_ana"}, body={"name": None})["statusCode"] == 400
