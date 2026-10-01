import base64
import json
from datetime import datetime, timezone

import pytest

from common.http import HttpError, json_body, query_int, query_time, route


def test_route_dispatches_by_route_key():
    routes = {"GET /x": lambda event: {"statusCode": 204}}
    assert route({"routeKey": "GET /x"}, routes) == {"statusCode": 204}


def test_route_unknown_returns_404_json():
    response = route({"routeKey": "GET /nope"}, {})
    assert response["statusCode"] == 404
    assert json.loads(response["body"]) == {"message": "Rota não encontrada"}
    assert response["headers"]["Cache-Control"] == "no-store"


def test_route_converts_http_error():
    def conflict(event):
        raise HttpError(409, "conflito")

    response = route({"routeKey": "GET /x"}, {"GET /x": conflict})
    assert response["statusCode"] == 409
    assert json.loads(response["body"]) == {"message": "conflito"}


@pytest.mark.parametrize("raw", ["{oops", "[1]", '"texto"'])
def test_json_body_rejects_non_objects(raw):
    with pytest.raises(HttpError) as exc:
        json_body({"body": raw})
    assert exc.value.status == 400


def test_json_body_empty_is_empty_dict():
    assert json_body({"body": None}) == {}


def test_json_body_base64():
    raw = base64.b64encode(b'{"a": 1}').decode()
    assert json_body({"body": raw, "isBase64Encoded": True}) == {"a": 1}


def test_query_int():
    assert query_int({}, "page", 1, 1, 10) == 1
    assert query_int({"page": "3"}, "page", 1, 1, 10) == 3
    for bad in ("0", "11", "x"):
        with pytest.raises(HttpError) as exc:
            query_int({"page": bad}, "page", 1, 1, 10)
        assert exc.value.status == 400


def test_query_time():
    assert query_time({}, "from") is None
    assert query_time({"from": "2026-10-01T00:00:00Z"}, "from") == datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(HttpError) as exc:
        query_time({"from": "ontem"}, "from")
    assert exc.value.status == 400
