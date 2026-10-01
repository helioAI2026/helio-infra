import uuid
from datetime import timedelta

from boto3.dynamodb.conditions import Key

from common.auth import require_groups
from common.db import plain, query_all, scan_all, table, to_item
from common.http import HttpError, json_body, ok, path_id, route
from common.settings import load_thresholds
from common.severity import severity_for
from common.timeutil import brt_day, brt_days, day_start_utc_iso, now_utc, parse_iso, to_utc_iso

EDITORS = ("Administrador", "GestorDeFrota")
EDITABLE = {"name", "licenseNo", "phone", "assignedDeviceId", "assignedVehicleId"}
NULLABLE = {"assignedDeviceId", "assignedVehicleId"}
ACTIVE_WINDOW = timedelta(minutes=30)
HISTORY_DAYS = 21


def _driver_events(driver_id, since):
    return [
        plain(e)
        for e in query_all(
            table("EVENTS_TABLE"),
            IndexName="byDriver",
            KeyConditionExpression=Key("driverId").eq(driver_id) & Key("timestamp").gte(to_utc_iso(since)),
        )
    ]


def _driver_trips(driver_id, since):
    return [
        plain(t)
        for t in query_all(
            table("TRIPS_TABLE"),
            IndexName="byDriver",
            KeyConditionExpression=Key("driverId").eq(driver_id) & Key("startedAt").gte(to_utc_iso(since)),
        )
    ]


def _driver_out(item, thresholds, now):
    d = plain(item)
    week_ago = now - timedelta(days=7)
    events = sorted(_driver_events(d["driverId"], week_ago), key=lambda e: e["timestamp"])
    trips = _driver_trips(d["driverId"], week_ago)

    latest = events[-1] if events else None
    active = latest is not None and parse_iso(latest["timestamp"]) >= now - ACTIVE_WINDOW
    current_score = latest["score"] if active else 0
    current_trip = next((t for t in trips if t["tripId"] == latest["tripId"]), None) if active else None
    seconds = sum((parse_iso(t["lastEventAt"]) - parse_iso(t["startedAt"])).total_seconds() for t in trips)

    return {
        "id": d["driverId"],
        "name": d["name"],
        "licenseNo": d.get("licenseNo", ""),
        "phone": d.get("phone", ""),
        "assignedVehicleId": d.get("assignedVehicleId"),
        "assignedDeviceId": d.get("assignedDeviceId"),
        "currentScore": current_score,
        "currentSeverity": severity_for(current_score, thresholds),
        "onShift": active,
        "shiftStartedAt": current_trip["startedAt"] if current_trip else None,
        "stats": {
            "avgScore7d": round(sum(e["score"] for e in events) / len(events)) if events else 0,
            "eventsThisWeek": len(events),
            "hoursDrivenThisWeek": round(seconds / 3600, 1),
        },
    }


def _get_driver(driver_id):
    item = table("DRIVERS_TABLE").get_item(Key={"driverId": driver_id}).get("Item")
    if item is None:
        raise HttpError(404, "Motorista não encontrado")
    return item


def _validated(payload, *, creating):
    unknown = sorted(set(payload) - EDITABLE)
    if unknown:
        raise HttpError(400, f"Campo(s) não permitido(s): {', '.join(unknown)}")
    if creating and "name" not in payload:
        raise HttpError(400, "O nome é obrigatório")
    fields = {}
    for key, value in payload.items():
        if value is None and key in NULLABLE:
            fields[key] = None
            continue
        if not isinstance(value, str) or len(value.strip()) > 100:
            raise HttpError(400, f"Campo '{key}' inválido")
        value = value.strip()
        if key == "name" and not value:
            raise HttpError(400, "O nome é obrigatório")
        fields[key] = None if key in NULLABLE and not value else value
    return fields


def _ensure_device_free(device_id, driver_id):
    owners = query_all(
        table("DRIVERS_TABLE"),
        IndexName="byDevice",
        KeyConditionExpression=Key("assignedDeviceId").eq(device_id),
    )
    if any(o["driverId"] != driver_id for o in owners):
        raise HttpError(409, f"O dispositivo {device_id} já está vinculado a outro motorista")


def list_drivers(event):
    thresholds, now = load_thresholds(), now_utc()
    drivers = [_driver_out(i, thresholds, now) for i in scan_all(table("DRIVERS_TABLE"))]
    return ok(sorted(drivers, key=lambda d: d["name"].lower()))


def get_driver(event):
    return ok(_driver_out(_get_driver(path_id(event)), load_thresholds(), now_utc()))


def driver_history(event):
    driver_id = path_id(event)
    _get_driver(driver_id)
    now = now_utc()
    days = brt_days(now - timedelta(days=HISTORY_DAYS - 1), now)
    scores = {}
    for e in _driver_events(driver_id, parse_iso(day_start_utc_iso(days[0]))):
        scores.setdefault(brt_day(parse_iso(e["timestamp"])), []).append(e["score"])
    return ok(
        [
            {"t": day_start_utc_iso(day), "score": round(sum(s) / len(s)) if (s := scores.get(day)) else 0}
            for day in days
        ]
    )


def create_driver(event):
    require_groups(event, *EDITORS)
    fields = _validated(json_body(event), creating=True)
    if fields.get("assignedDeviceId"):
        _ensure_device_free(fields["assignedDeviceId"], None)
    now = now_utc()
    item = {"driverId": f"drv_{uuid.uuid4().hex[:12]}", **fields, "createdAt": to_utc_iso(now)}
    table("DRIVERS_TABLE").put_item(Item=to_item(item))
    return ok(_driver_out(to_item(item), load_thresholds(), now), 201)


def update_driver(event):
    require_groups(event, *EDITORS)
    driver_id = path_id(event)
    _get_driver(driver_id)
    fields = _validated(json_body(event), creating=False)
    if fields.get("assignedDeviceId"):
        _ensure_device_free(fields["assignedDeviceId"], driver_id)

    sets = {k: v for k, v in fields.items() if v is not None}
    removes = [k for k, v in fields.items() if v is None]
    clauses = []
    kwargs = {"ExpressionAttributeNames": {f"#{k}": k for k in fields}}
    if sets:
        clauses.append("SET " + ", ".join(f"#{k} = :{k}" for k in sets))
        kwargs["ExpressionAttributeValues"] = {f":{k}": v for k, v in sets.items()}
    if removes:
        clauses.append("REMOVE " + ", ".join(f"#{k}" for k in removes))
    if clauses:
        table("DRIVERS_TABLE").update_item(Key={"driverId": driver_id}, UpdateExpression=" ".join(clauses), **kwargs)
    return ok(_driver_out(_get_driver(driver_id), load_thresholds(), now_utc()))


ROUTES = {
    "GET /api/drivers": list_drivers,
    "POST /api/drivers": create_driver,
    "GET /api/drivers/{id}": get_driver,
    "PUT /api/drivers/{id}": update_driver,
    "GET /api/drivers/{id}/history": driver_history,
}


def handler(event, context):
    return route(event, ROUTES)
