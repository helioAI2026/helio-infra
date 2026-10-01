"""Recebe as mensagens da regra IoT `helio/devices/+/events`, grava eventos e viagens e avisa por e-mail."""

import hashlib
import logging
import os

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common.db import query_all, table, to_item
from common.notify import publish
from common.settings import load_thresholds
from common.severity import LABELS, at_least, normalize_score, severity_for
from common.timeutil import brt_day, format_brt, parse_iso, to_utc_iso

logger = logging.getLogger()
logger.setLevel(logging.INFO)

EAR_THRESHOLD = float(os.environ.get("EAR_THRESHOLD", "0.20"))
PERCLOS_TRIGGER = float(os.environ.get("PERCLOS_TRIGGER", "0.20"))


def handler(event, context):
    device_id = event.get("thingName")
    ride_id = event.get("ride_id")
    items = event.get("data")
    if not (_is_text(device_id) and _is_text(ride_id) and isinstance(items, list)):
        logger.warning("Mensagem descartada: %s", event)
        return {"stored": 0}

    thresholds = load_thresholds()
    driver = _driver_for_device(device_id)
    stored = 0
    for raw in items:
        item = _build_event(raw, device_id, ride_id, driver, thresholds)
        if item is None:
            logger.warning("Item inválido descartado: %s", raw)
            continue
        if not _insert_event(item):
            continue
        _update_trip(item)
        stored += 1
        if thresholds["emailAlerts"] and at_least(item["severity"], thresholds["notifyOn"]):
            _notify(item, driver)
    return {"stored": stored}


def _is_text(value) -> bool:
    return isinstance(value, str) and value != ""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _build_event(raw, device_id, ride_id, driver, thresholds):
    if not isinstance(raw, dict):
        return None
    score_raw = raw.get("score")
    if not _is_number(score_raw) or not 0 <= score_raw <= 1:
        return None
    try:
        moment = parse_iso(raw.get("timestamp"))
    except (TypeError, ValueError):
        return None

    timestamp = to_utc_iso(moment)
    score = normalize_score(score_raw)
    ear, perclos, duration = raw.get("ear"), raw.get("perclos"), raw.get("durationSec")
    triggers = []
    if _is_number(perclos) and perclos >= PERCLOS_TRIGGER:
        triggers.append("perclos")
    if _is_number(ear) and ear < EAR_THRESHOLD:
        triggers.append("eye-closure")

    return {
        "eventId": hashlib.sha256(f"{device_id}:{ride_id}:{timestamp}".encode()).hexdigest()[:16],
        "tripId": ride_id,
        "deviceId": device_id,
        "driverId": driver["driverId"] if driver else None,
        "timestamp": timestamp,
        "day": brt_day(moment),
        "score": score,
        "severity": severity_for(score, thresholds),
        "durationSec": duration if _is_number(duration) and duration >= 0 else 0,
        "triggers": triggers,
        "ear": ear if _is_number(ear) else None,
        "perclos": perclos if _is_number(perclos) else None,
        "edgeStatus": raw.get("status") if _is_text(raw.get("status")) else None,
    }


def _driver_for_device(device_id):
    found = query_all(
        table("DRIVERS_TABLE"),
        IndexName="byDevice",
        KeyConditionExpression=Key("assignedDeviceId").eq(device_id),
    )
    return found[0] if found else None


def _insert_event(item) -> bool:
    try:
        table("EVENTS_TABLE").put_item(Item=to_item(item), ConditionExpression="attribute_not_exists(eventId)")
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        logger.info("Evento %s já registrado (reentrega)", item["eventId"])
        return False


def _update_trip(item) -> None:
    trips = table("TRIPS_TABLE")
    key = {"tripId": item["tripId"]}
    values = {":device": item["deviceId"], ":t": item["timestamp"], ":score": item["score"], ":one": 1}
    expression = (
        "SET deviceId = :device, startedAt = if_not_exists(startedAt, :t), "
        "lastEventAt = if_not_exists(lastEventAt, :t), maxScore = if_not_exists(maxScore, :score)"
    )
    if item["driverId"]:
        expression += ", driverId = :driver"
        values[":driver"] = item["driverId"]
    trips.update_item(Key=key, UpdateExpression=expression + " ADD alertCount :one", ExpressionAttributeValues=values)
    _set_if(trips, key, "maxScore", item["score"], "<")
    _set_if(trips, key, "lastEventAt", item["timestamp"], "<")
    _set_if(trips, key, "startedAt", item["timestamp"], ">")


def _set_if(trips, key, attribute, value, comparison) -> None:
    """Atualiza o atributo só se o valor novo for maior (<) ou menor (>) que o atual."""
    try:
        trips.update_item(
            Key=key,
            UpdateExpression=f"SET {attribute} = :v",
            ConditionExpression=f"{attribute} {comparison} :v",
            ExpressionAttributeValues={":v": value},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def _notify(item, driver) -> None:
    name = driver["name"] if driver else f"Dispositivo {item['deviceId']} (sem motorista)"
    label = LABELS[item["severity"]]
    message = "\n".join(
        [
            "Alerta de sonolência detectado pelo Helio.",
            "",
            f"Motorista: {name}",
            f"Dispositivo: {item['deviceId']}",
            f"Severidade: {label} (score {item['score']}/100)",
            f"Horário: {format_brt(parse_iso(item['timestamp']))} (Brasília)",
            f"Viagem: {item['tripId'][:12]}",
            f"Gatilhos: {', '.join(item['triggers']) or 'nenhum'}",
            "",
            f"Ver no painel: {os.environ['DASHBOARD_URL']}/alertas/{item['eventId']}",
        ]
    )
    publish(f"[Helio] Alerta {label}: {name}", message)
