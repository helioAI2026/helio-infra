import json
import os

import boto3

from common.db import to_item

THING = "helio-edge-01"


def api_request(route_key, *, groups=("Operador",), email="op@helio.dev", path=None, query=None, body=None):
    """Evento no formato payload 2.0 da HTTP API, já passado pelo autorizador JWT."""
    return {
        "routeKey": route_key,
        "pathParameters": path or {},
        "queryStringParameters": query,
        "body": None if body is None else json.dumps(body),
        "isBase64Encoded": False,
        "requestContext": {
            "authorizer": {"jwt": {"claims": {"email": email, "cognito:groups": "[" + " ".join(groups) + "]"}}}
        },
    }


def body(response):
    return json.loads(response["body"])


def iot_message(*items, ride_id="ride-1", thing=THING):
    """Mensagem como a Topic Rule entrega: payload do edge + thingName extraído do tópico."""
    return {
        "device_id": thing,
        "ride_id": ride_id,
        "timestamp": 1790000000,
        "nonce": "a1b2c3d4",
        "data": list(items),
        "thingName": thing,
    }


def alert_item(timestamp="2026-10-01T22:15:03-03:00", score=0.72, ear=0.13, perclos=0.41, status="ALERTA", duration=2.4):
    return {"timestamp": timestamp, "score": score, "ear": ear, "perclos": perclos, "status": status, "durationSec": duration}


def _put(env_var, item):
    boto3.resource("dynamodb").Table(os.environ[env_var]).put_item(Item=to_item(item))


def put_driver(driver_id, name, device=None, **extra):
    _put("DRIVERS_TABLE", {"driverId": driver_id, "name": name, "assignedDeviceId": device, **extra})


def put_event(event_id, **fields):
    _put(
        "EVENTS_TABLE",
        {
            "eventId": event_id,
            "tripId": "ride-1",
            "deviceId": THING,
            "driverId": None,
            "timestamp": "2026-10-01T13:00:00.000Z",
            "day": "2026-10-01",
            "score": 70,
            "severity": "drowsy",
            "durationSec": 2,
            "triggers": ["perclos"],
            **fields,
        },
    )


def put_trip(trip_id, **fields):
    _put(
        "TRIPS_TABLE",
        {
            "tripId": trip_id,
            "deviceId": THING,
            "driverId": None,
            "startedAt": "2026-10-02T10:00:00.000Z",
            "lastEventAt": "2026-10-02T11:00:00.000Z",
            "maxScore": 70,
            "alertCount": 1,
            **fields,
        },
    )
