import boto3

import ingest
from common.settings import save_thresholds
from common.severity import DEFAULT_THRESHOLDS
from factories import THING, alert_item, iot_message, put_driver


def stored_events():
    return boto3.resource("dynamodb").Table("helio-events").scan()["Items"]


def stored_trip(trip_id="ride-1"):
    return boto3.resource("dynamodb").Table("helio-trips").get_item(Key={"tripId": trip_id}).get("Item")


def test_stores_normalized_event(aws):
    put_driver("drv_ana", "Ana Souza", device=THING)
    assert ingest.handler(iot_message(alert_item(score=0.72)), None) == {"stored": 1}
    [event] = stored_events()
    assert event["score"] == 72
    assert event["severity"] == "drowsy"
    assert event["driverId"] == "drv_ana"
    assert event["tripId"] == "ride-1"
    assert event["deviceId"] == THING
    assert event["timestamp"] == "2026-10-02T01:15:03.000Z"
    assert event["day"] == "2026-10-01"
    assert set(event["triggers"]) == {"perclos", "eye-closure"}
    assert event["edgeStatus"] == "ALERTA"


def test_event_without_assigned_driver_has_no_driver(aws):
    ingest.handler(iot_message(alert_item()), None)
    [event] = stored_events()
    assert "driverId" not in event
    assert "driverId" not in stored_trip()


def test_topic_thing_name_wins_over_payload_device_id(aws):
    message = iot_message(alert_item())
    message["device_id"] = "OUTRO"
    ingest.handler(message, None)
    assert stored_events()[0]["deviceId"] == THING


def test_duplicate_delivery_is_idempotent(aws):
    message = iot_message(alert_item())
    ingest.handler(message, None)
    assert ingest.handler(message, None) == {"stored": 0}
    assert len(stored_events()) == 1
    assert stored_trip()["alertCount"] == 1


def test_trip_aggregates_out_of_order_events(aws):
    ingest.handler(iot_message(alert_item(timestamp="2026-10-01T22:20:00-03:00", score=0.65)), None)
    ingest.handler(iot_message(alert_item(timestamp="2026-10-01T22:10:00-03:00", score=0.9)), None)
    trip = stored_trip()
    assert trip["alertCount"] == 2
    assert trip["maxScore"] == 90
    assert trip["startedAt"] == "2026-10-02T01:10:00.000Z"
    assert trip["lastEventAt"] == "2026-10-02T01:20:00.000Z"
    assert trip["deviceId"] == THING


def test_invalid_items_are_skipped_but_valid_ones_stored(aws):
    message = iot_message(
        alert_item(score="alto"),
        alert_item(score=True),
        alert_item(score=1.5),
        {"score": 0.7},
        alert_item(timestamp="ontem"),
        "lixo",
        alert_item(timestamp="2026-10-01T22:30:00-03:00"),
    )
    assert ingest.handler(message, None) == {"stored": 1}


def test_malformed_message_is_dropped(aws):
    assert ingest.handler({}, None) == {"stored": 0}
    assert ingest.handler({"thingName": THING, "ride_id": "r", "data": "nada"}, None) == {"stored": 0}
    assert ingest.handler({"thingName": "", "ride_id": "r", "data": []}, None) == {"stored": 0}


def test_emails_when_severity_reaches_notify_on(aws, sent_emails):
    put_driver("drv_ana", "Ana Souza", device=THING)
    ingest.handler(iot_message(alert_item(score=0.85)), None)
    [mail] = sent_emails()
    event_id = stored_events()[0]["eventId"]
    assert mail["Subject"] == "[Helio] Alerta critico: Ana Souza"
    assert "Motorista: Ana Souza" in mail["Message"]
    assert "score 85/100" in mail["Message"]
    assert "01/10/2026 22:15:03" in mail["Message"]
    assert f"https://helio.example/alertas/{event_id}" in mail["Message"]


def test_email_names_device_when_no_driver(aws, sent_emails):
    ingest.handler(iot_message(alert_item(score=0.85)), None)
    [mail] = sent_emails()
    assert mail["Subject"] == f"[Helio] Alerta critico: Dispositivo {THING} (sem motorista)"


def test_no_email_below_notify_on(aws, sent_emails):
    ingest.handler(iot_message(alert_item(score=0.45)), None)
    assert sent_emails() == []


def test_no_email_for_duplicate(aws, sent_emails):
    message = iot_message(alert_item(score=0.95))
    ingest.handler(message, None)
    ingest.handler(message, None)
    assert len(sent_emails()) == 1


def test_no_email_when_disabled(aws, sent_emails):
    save_thresholds({**DEFAULT_THRESHOLDS, "emailAlerts": False})
    ingest.handler(iot_message(alert_item(score=0.95)), None)
    assert sent_emails() == []


def test_uses_stored_thresholds(aws):
    save_thresholds({**DEFAULT_THRESHOLDS, "drowsy": 75, "critical": 90})
    ingest.handler(iot_message(alert_item(score=0.72)), None)
    assert stored_events()[0]["severity"] == "mild"


def test_event_carries_driver_vehicle(aws):
    put_driver("drv_ana", "Ana Souza", device=THING, assignedVehicleId="veh_1")
    ingest.handler(iot_message(alert_item()), None)
    assert stored_events()[0]["vehicleId"] == "veh_1"


def _fail_once(monkeypatch, name):
    original = getattr(ingest, name)
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("falha transitória")
        return original(*args, **kwargs)

    monkeypatch.setattr(ingest, name, flaky)


def test_retry_after_trip_failure_keeps_trip_consistent(aws, monkeypatch):
    ingest.handler(iot_message(alert_item(timestamp="2026-10-01T22:20:00-03:00", score=0.65)), None)
    _fail_once(monkeypatch, "_set_if")
    message = iot_message(alert_item(timestamp="2026-10-01T22:10:00-03:00", score=0.9))
    try:
        ingest.handler(message, None)
    except RuntimeError:
        pass
    ingest.handler(message, None)  # nova tentativa da Lambda
    trip = stored_trip()
    assert trip["alertCount"] == 2
    assert trip["maxScore"] == 90
    assert trip["startedAt"] == "2026-10-02T01:10:00.000Z"


def test_retry_after_email_failure_still_emails_once(aws, sent_emails, monkeypatch):
    _fail_once(monkeypatch, "publish")
    message = iot_message(alert_item(score=0.95))
    try:
        ingest.handler(message, None)
    except RuntimeError:
        pass
    ingest.handler(message, None)
    ingest.handler(message, None)
    assert len(sent_emails()) == 1
    assert stored_trip()["alertCount"] == 1
