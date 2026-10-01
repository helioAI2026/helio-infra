import base64
import json

from common.db import plain
from common.timeutil import parse_iso


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def response(status: int, body=None) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": "" if body is None else json.dumps(plain(body), ensure_ascii=False),
    }


def ok(body, status: int = 200) -> dict:
    return response(status, body)


def error(status: int, message: str) -> dict:
    return response(status, {"message": message})


def route(event: dict, routes: dict) -> dict:
    handler = routes.get(event.get("routeKey"))
    if handler is None:
        return error(404, "Rota não encontrada")
    try:
        return handler(event)
    except HttpError as exc:
        return error(exc.status, exc.message)


def json_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HttpError(400, "JSON inválido") from None
    if not isinstance(data, dict):
        raise HttpError(400, "O corpo da requisição deve ser um objeto JSON")
    return data


def query_params(event: dict) -> dict:
    return event.get("queryStringParameters") or {}


def path_id(event: dict) -> str:
    return (event.get("pathParameters") or {})["id"]


def query_int(params: dict, name: str, default: int, low: int, high: int) -> int:
    raw = params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise HttpError(400, f"Parâmetro '{name}' inválido") from None
    if not low <= value <= high:
        raise HttpError(400, f"Parâmetro '{name}' deve estar entre {low} e {high}")
    return value


def query_time(params: dict, name: str):
    raw = params.get(name)
    if raw is None:
        return None
    try:
        return parse_iso(raw)
    except (TypeError, ValueError):
        raise HttpError(400, f"Parâmetro '{name}' deve ser uma data ISO 8601") from None
