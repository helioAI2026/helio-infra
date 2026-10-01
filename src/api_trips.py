from datetime import timedelta

from boto3.dynamodb.conditions import Attr, Key

from common.db import query_all, scan_all, table
from common.http import HttpError, ok, path_id, query_params, query_time, route
from common.serialize import event_out, trip_out
from common.timeutil import now_utc, to_utc_iso

DEFAULT_RANGE = timedelta(days=7)


def list_trips(event):
    params = query_params(event)
    end = query_time(params, "to") or now_utc()
    start = query_time(params, "from") or end - DEFAULT_RANGE
    if start > end:
        raise HttpError(400, "'from' deve ser anterior a 'to'")
    window = (to_utc_iso(start), to_utc_iso(end))

    trips = table("TRIPS_TABLE")
    if params.get("driverId"):
        items = query_all(
            trips,
            IndexName="byDriver",
            KeyConditionExpression=Key("driverId").eq(params["driverId"]) & Key("startedAt").between(*window),
        )
    else:
        items = scan_all(trips, FilterExpression=Attr("startedAt").between(*window))
    return ok(sorted((trip_out(i) for i in items), key=lambda t: t["startedAt"], reverse=True))


def get_trip(event):
    trip_id = path_id(event)
    item = table("TRIPS_TABLE").get_item(Key={"tripId": trip_id}).get("Item")
    if item is None:
        raise HttpError(404, "Viagem não encontrada")
    events = query_all(table("EVENTS_TABLE"), IndexName="byTrip", KeyConditionExpression=Key("tripId").eq(trip_id))
    return ok({**trip_out(item), "events": [event_out(e) for e in events]})


ROUTES = {
    "GET /api/trips": list_trips,
    "GET /api/trips/{id}": get_trip,
}


def handler(event, context):
    return route(event, ROUTES)
