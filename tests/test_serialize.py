from decimal import Decimal

from common.serialize import event_out, trip_out


def test_event_out_matches_dashboard_shape():
    item = {
        "eventId": "e1",
        "tripId": "ride-1",
        "deviceId": "helio-edge-01",
        "timestamp": "2026-10-02T01:15:03.000Z",
        "day": "2026-10-01",
        "score": Decimal("72"),
        "severity": "drowsy",
        "durationSec": Decimal("2.4"),
        "triggers": ["perclos"],
    }
    assert event_out(item) == {
        "id": "e1",
        "tripId": "ride-1",
        "deviceId": "helio-edge-01",
        "vehicleId": None,
        "driverId": None,
        "timestamp": "2026-10-02T01:15:03.000Z",
        "score": 72,
        "severity": "drowsy",
        "durationSec": 2.4,
        "triggers": ["perclos"],
        "location": None,
        "frames": None,
        "acknowledgedAt": None,
        "acknowledgedBy": None,
    }


def test_trip_out():
    item = {
        "tripId": "t1",
        "deviceId": "helio-edge-01",
        "driverId": "drv_ana",
        "startedAt": "a",
        "lastEventAt": "b",
        "maxScore": Decimal("90"),
        "alertCount": Decimal("2"),
    }
    assert trip_out(item) == {
        "id": "t1",
        "deviceId": "helio-edge-01",
        "driverId": "drv_ana",
        "startedAt": "a",
        "lastEventAt": "b",
        "maxScore": 90,
        "alertCount": 2,
    }
