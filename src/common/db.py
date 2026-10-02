import os
from decimal import Decimal

import boto3

_resource = None
_client = None


def reset() -> None:
    """Descarta os clientes em cache (usado pelos testes a cada mock novo)."""
    global _resource, _client
    _resource = None
    _client = None


def client():
    """Cliente de baixo nível (sem serialização automática), para transações."""
    global _client
    if _client is None:
        _client = boto3.client("dynamodb")
    return _client


def table(env_var: str):
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb")
    return _resource.Table(os.environ[env_var])


def to_item(value):
    """Prepara para o DynamoDB: float vira Decimal e chaves com None são removidas."""
    if isinstance(value, dict):
        return {k: to_item(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [to_item(v) for v in value]
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def plain(value):
    """Converte o que vem do DynamoDB para tipos JSON nativos."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def query_all(tbl, **kwargs) -> list[dict]:
    return _paginate(tbl.query, kwargs)


def scan_all(tbl, **kwargs) -> list[dict]:
    return _paginate(tbl.scan, kwargs)


def _paginate(operation, kwargs) -> list[dict]:
    items = []
    while True:
        page = operation(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs = {**kwargs, "ExclusiveStartKey": page["LastEvaluatedKey"]}
