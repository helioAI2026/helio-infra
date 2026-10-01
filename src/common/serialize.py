"""Itens do DynamoDB no formato JSON que o dashboard espera."""

from common.db import plain


def event_out(item: dict) -> dict:
    e = plain(item)
    return {
        "id": e["eventId"],
        "tripId": e.get("tripId"),
        "deviceId": e.get("deviceId"),
        "vehicleId": e.get("vehicleId"),
        "driverId": e.get("driverId"),
        "timestamp": e["timestamp"],
        "score": e["score"],
        "severity": e["severity"],
        "durationSec": e.get("durationSec", 0),
        "triggers": e.get("triggers", []),
        "location": e.get("location"),
        "frames": e.get("frames"),
        "acknowledgedAt": e.get("acknowledgedAt"),
        "acknowledgedBy": e.get("acknowledgedBy"),
    }


def trip_out(item: dict) -> dict:
    t = plain(item)
    return {
        "id": t["tripId"],
        "deviceId": t.get("deviceId"),
        "driverId": t.get("driverId"),
        "startedAt": t["startedAt"],
        "lastEventAt": t["lastEventAt"],
        "maxScore": t.get("maxScore", 0),
        "alertCount": t.get("alertCount", 0),
    }
