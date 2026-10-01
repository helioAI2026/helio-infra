from datetime import timedelta

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common.auth import email
from common.db import query_all, table
from common.http import HttpError, ok, path_id, query_int, query_params, query_time, route
from common.serialize import event_out
from common.severity import SEVERITY_ORDER
from common.timeutil import brt_days, now_utc, to_utc_iso

DEFAULT_RANGE = timedelta(days=7)
MAX_RANGE_DAYS = 31
SORT_KEYS = {
    "timestamp": lambda e: e["timestamp"],
    "score": lambda e: e["score"],
    "severity": lambda e: SEVERITY_ORDER.index(e["severity"]),
}


def list_events(event):
    params = query_params(event)
    end = query_time(params, "to") or now_utc()
    start = query_time(params, "from") or end - DEFAULT_RANGE
    if start > end:
        raise HttpError(400, "'from' deve ser anterior a 'to'")
    days = brt_days(start, end)
    if len(days) > MAX_RANGE_DAYS:
        raise HttpError(400, f"O intervalo máximo é de {MAX_RANGE_DAYS} dias")

    severities = [s for s in params.get("severity", "").split(",") if s]
    if any(s not in SEVERITY_ORDER for s in severities):
        raise HttpError(400, "Parâmetro 'severity' inválido")
    acknowledged = params.get("acknowledged")
    if acknowledged not in (None, "true", "false"):
        raise HttpError(400, "Parâmetro 'acknowledged' deve ser true ou false")
    sort = params.get("sort", "timestamp")
    if sort not in SORT_KEYS:
        raise HttpError(400, "Parâmetro 'sort' deve ser timestamp, score ou severity")
    direction = params.get("dir", "desc")
    if direction not in ("asc", "desc"):
        raise HttpError(400, "Parâmetro 'dir' deve ser asc ou desc")
    page = query_int(params, "page", 1, 1, 10_000)
    page_size = query_int(params, "pageSize", 20, 1, 100)

    # Volume de demo: consulta o GSI byDay dia a dia e filtra/ordena/pagina em memória.
    window = Key("timestamp").between(to_utc_iso(start), to_utc_iso(end))
    rows = []
    for day in days:
        items = query_all(table("EVENTS_TABLE"), IndexName="byDay", KeyConditionExpression=Key("day").eq(day) & window)
        rows.extend(event_out(i) for i in items)

    if severities:
        rows = [r for r in rows if r["severity"] in severities]
    for field in ("driverId", "vehicleId"):
        if params.get(field):
            rows = [r for r in rows if r[field] == params[field]]
    if acknowledged is not None:
        want = acknowledged == "true"
        rows = [r for r in rows if (r["acknowledgedAt"] is not None) == want]

    rows.sort(key=SORT_KEYS[sort], reverse=direction == "desc")
    first = (page - 1) * page_size
    return ok({"rows": rows[first : first + page_size], "total": len(rows), "page": page, "pageSize": page_size})


def _get_item(event_id):
    item = table("EVENTS_TABLE").get_item(Key={"eventId": event_id}).get("Item")
    if item is None:
        raise HttpError(404, "Alerta não encontrado")
    return item


def get_event(event):
    return ok(event_out(_get_item(path_id(event))))


def acknowledge_event(event):
    event_id = path_id(event)
    try:
        result = table("EVENTS_TABLE").update_item(
            Key={"eventId": event_id},
            UpdateExpression="SET acknowledgedAt = :at, acknowledgedBy = :by",
            ConditionExpression="attribute_exists(eventId) AND attribute_not_exists(acknowledgedAt)",
            ExpressionAttributeValues={":at": to_utc_iso(now_utc()), ":by": email(event) or "desconhecido"},
            ReturnValues="ALL_NEW",
        )
        return ok(event_out(result["Attributes"]))
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    # Não existe (404) ou já foi reconhecido (devolve como está).
    return ok(event_out(_get_item(event_id)))


ROUTES = {
    "GET /api/events": list_events,
    "GET /api/events/{id}": get_event,
    "POST /api/events/{id}/acknowledge": acknowledge_event,
}


def handler(event, context):
    return route(event, ROUTES)
