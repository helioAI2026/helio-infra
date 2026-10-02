# Helio — Infra AWS (CloudFormation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar em CloudFormation (nested stacks) a nuvem do Helio: Cognito, DynamoDB (motoristas, viagens, eventos, configurações), ingestão via IoT Core, Lambdas + SNS para e-mails de alerta e resumo diário, API HTTP protegida por JWT e hospedagem do dashboard em S3 + CloudFront.

**Architecture:** O edge publica via MQTT (certificado X.509) em `helio/devices/{thing}/events`. Uma Topic Rule chama a λ `ingest`, que normaliza o evento, grava em DynamoDB, atualiza a viagem e publica no SNS quando a severidade passa do limiar. O CloudFront serve o dashboard do S3 e encaminha `/api/*` para uma HTTP API com autorizador JWT do Cognito; quatro Lambdas atendem drivers, trips, events e settings. O EventBridge Scheduler dispara o resumo diário às 20h (BRT). Todo o código das Lambdas fica em `infra/src/` (um pacote só; cada função muda apenas o `Handler`).

**Tech Stack:** CloudFormation (YAML, nested stacks, `aws cloudformation package/deploy`), Python 3.13 (Lambda arm64, boto3 do runtime), uv, pytest, moto 5, cfn-lint, AWS CLI v2, openssl, jq.

**Spec:** `infra/docs/specs/2026-10-01-helio-aws-infra-design.md`

> Este é o **Plano 1 de 3**: só a infra (`infra/`). Os Planos 2 (edge: MQTT com certificado) e 3 (dashboard: Cognito + modo híbrido + `deploy-dashboard.sh`) dependem dos contratos daqui e são escritos depois que este estiver implantado.

## Ajustes ao spec (decididos ao detalhar)

- **Nome do Thing:** passa a ser `helio-edge-01`, porque nomes de Thing do IoT não aceitam `.` (`HELIO_V1.0` é inválido). O edge precisa conectar com `client_id` igual ao nome do Thing.
- **E-mails:** três parâmetros (`AlertEmail1` obrigatório, `AlertEmail2` e `AlertEmail3` opcionais) em vez de uma `CommaDelimitedList`. `Fn::Select` falha com listas curtas.
- **`eventId`:** `sha256(thing:ride_id:timestamp)[:16]`. O `nonce` já faz parte do `ride_id`.
- **Gatilho `perclos`:** usa `PERCLOS_TRIGGER=0.20`, o mesmo `PERCLOS_THRESHOLD` de `edge/config.py`, em vez de 0.3. `EAR_THRESHOLD=0.20`.
- **Ordem dos stacks:** auth → data → api → web → alerts → iot. O web precisa do domínio da API, e alerts/iot precisam da URL do dashboard para os links nos e-mails.
- **Certificados:** ficam em `infra/certs/` (no `.gitignore`), e o `deploy.sh` gera `infra/certs/edge.env` com as variáveis do edge.
- **Cognito:** o App Client também libera `ALLOW_USER_PASSWORD_AUTH`, para obter o token pela CLI nos testes com `curl`.

## Global Constraints

- Região `us-east-1`. Lambdas `python3.13`, `arm64`, 256 MB e timeout de 10 s (o resumo diário usa 30 s).
- DynamoDB `PAY_PER_REQUEST`, sem PITR. `DeletionPolicy: Delete` em tudo, porque é demo.
- Log groups com `RetentionInDays: 14`.
- `ProjectName` segue `^[a-z][a-z0-9]{1,15}$` (padrão `helio`), porque o nome de Topic Rule não aceita hífen.
- Tópico MQTT: `helio/devices/{thingName}/events`. O `thingName` vem do tópico (`topic(3)`), nunca do payload.
- Timestamps são gravados em UTC no formato `YYYY-MM-DDTHH:MM:SS.mmmZ`, igual ao `toISOString()` do JS. O "dia" (`day`) e o horário nos e-mails usam BRT fixo (UTC−3).
- As respostas da API seguem `dashboard/src/api/types.ts` (camelCase). Erros vêm como `{"message": "..."}` em pt-BR, e toda resposta leva `Cache-Control: no-store`.
- Grupos do Cognito: `Administrador`, `GestorDeFrota`, `Operador`.
- Assunto dos e-mails do SNS em ASCII (sem acentos); o corpo pode ter acentos.
- Commits em pt-BR no estilo `feat: ...`, terminando com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Reentrega QoS 1:** a mesma mensagem MQTT entregue duas vezes deve gerar um único evento, e o `alertCount` da viagem não pode dobrar. Teste: `test_duplicate_delivery_is_idempotent` (Task 3).
2. **Lote com lixo:** itens com score em texto ou booleano, score fora de 0–1, sem timestamp ou que não são objeto são descartados com log, e os válidos do mesmo lote são gravados, sem exceção (que causaria retry). Testes: `test_invalid_items_are_skipped_but_valid_ones_stored` e `test_malformed_message_is_dropped` (Task 3).
3. **Eventos perto da meia-noite em BRT:** um evento às 23:30 BRT (02:30Z do dia seguinte) cai no `day` certo e aparece num filtro `from/to` em BRT. Testes: `test_brt_day_uses_brazil_date` (Task 1) e `test_range_crossing_brt_midnight` (Task 6).
4. **Um dispositivo, dois motoristas:** vincular a outro motorista um dispositivo já vinculado retorna 409; revincular ao mesmo motorista funciona. Teste: `test_device_cannot_belong_to_two_drivers` (Task 7).
5. **Limiares inválidos ou usuário sem permissão:** um PUT que quebra `mild < drowsy < critical`, ou feito por quem não é Administrador, responde 400 ou 403 e não altera o que está gravado. Testes: `test_invalid_patch_rejected` e `test_operador_forbidden` (Task 5).

## File Structure

```
infra/
  pyproject.toml, .python-version, .gitignore
  root.yaml                      # parâmetros + composição dos nested stacks + outputs
  stacks/
    auth.yaml                    # Cognito User Pool, App Client, 3 grupos
    data.yaml                    # 4 tabelas DynamoDB + GSIs
    api.yaml                     # HTTP API, JWT authorizer, 4 Lambdas, 12 rotas
    web.yaml                     # S3 privado, CloudFront (OAC, SPA rewrite, /api/*)
    alerts.yaml                  # SNS + assinaturas, λ daily_summary + Scheduler
    iot.yaml                     # Thing, Policy, Certificate, TopicRule, λ ingest
  src/
    common/__init__.py
    common/timeutil.py           # parse/format ISO, dia BRT
    common/severity.py           # ordem, rótulos, limiares padrão, normalização
    common/db.py                 # Table lazy, Decimal<->nativo, paginação
    common/http.py               # HttpError, route(), respostas, parse de body/query
    common/auth.py               # grupos/e-mail dos claims JWT
    common/settings.py           # load/save limiares
    common/notify.py             # publish no SNS (assunto ASCII)
    common/serialize.py          # item DynamoDB -> JSON do dashboard
    ingest.py  daily_summary.py  api_settings.py  api_events.py  api_drivers.py  api_trips.py
  tests/
    conftest.py  factories.py
    test_timeutil.py  test_severity.py  test_db.py  test_http.py  test_auth.py
    test_settings.py  test_notify.py  test_serialize.py
    test_ingest.py  test_daily_summary.py  test_api_settings.py  test_api_events.py
    test_api_drivers.py  test_api_trips.py
  scripts/deploy.sh  scripts/seed.py  scripts/smoke.sh
  README.md
```

---

### Task 1: Projeto Python + utilitários de tempo e severidade

**Files:**
- Create: `infra/pyproject.toml`, `infra/.python-version`, `infra/src/common/__init__.py`, `infra/src/common/timeutil.py`, `infra/src/common/severity.py`
- Modify: `infra/.gitignore`
- Test: `infra/tests/test_timeutil.py`, `infra/tests/test_severity.py`

**Interfaces:**
- Produces:
  - `timeutil`: `BRT`, `now_utc() -> datetime`, `parse_iso(str) -> datetime` (aware; sem fuso é tratado como BRT; `TypeError` se não for str, `ValueError` se for inválido), `to_utc_iso(datetime) -> str`, `brt_day(datetime) -> "YYYY-MM-DD"`, `brt_days(start, end) -> list[str]`, `format_brt(datetime) -> "dd/mm/YYYY HH:MM:SS"`, `day_start_utc_iso("YYYY-MM-DD") -> str`.
  - `severity`: `SEVERITY_ORDER`, `LABELS`, `DEFAULT_THRESHOLDS`, `normalize_score(float) -> int`, `severity_for(int, dict) -> str`, `at_least(str, str) -> bool`.

- [ ] **Step 1: Criar o projeto**

`infra/pyproject.toml`:
```toml
[project]
name = "helio-infra"
version = "0.1.0"
description = "Infraestrutura AWS do Helio (CloudFormation + Lambdas)"
requires-python = ">=3.13"
dependencies = []

[dependency-groups]
dev = [
    "boto3>=1.35",
    "moto[dynamodb,sns,sqs]>=5.0",
    "pytest>=8.3",
    "cfn-lint>=1.20",
]

[tool.pytest.ini_options]
pythonpath = ["src", "tests"]
testpaths = ["tests"]
```

`infra/.python-version`:
```
3.13
```

Acrescente ao `infra/.gitignore`:
```
certs/
build/
```

`infra/src/common/__init__.py`: arquivo vazio.

Run: `cd /Users/matt/Projects/Helio/infra && uv sync`
Expected: cria `.venv` com Python 3.13 e instala boto3, moto, pytest e cfn-lint.

- [ ] **Step 2: Escrever os testes que devem falhar**

`infra/tests/test_timeutil.py`:
```python
from datetime import datetime, timezone

import pytest

from common.timeutil import (
    brt_day,
    brt_days,
    day_start_utc_iso,
    format_brt,
    parse_iso,
    to_utc_iso,
)


def test_parse_iso_keeps_offset():
    assert parse_iso("2026-10-01T22:15:03-03:00") == datetime(2026, 10, 2, 1, 15, 3, tzinfo=timezone.utc)


def test_parse_iso_accepts_z():
    assert parse_iso("2026-10-02T01:15:03Z") == datetime(2026, 10, 2, 1, 15, 3, tzinfo=timezone.utc)


def test_parse_iso_naive_is_brt():
    assert parse_iso("2026-10-01T22:15:03") == datetime(2026, 10, 2, 1, 15, 3, tzinfo=timezone.utc)


def test_parse_iso_rejects_garbage():
    with pytest.raises(ValueError):
        parse_iso("ontem")


def test_parse_iso_rejects_non_string():
    with pytest.raises(TypeError):
        parse_iso(123)


def test_to_utc_iso_matches_js_format():
    assert to_utc_iso(parse_iso("2026-10-01T22:15:03.5-03:00")) == "2026-10-02T01:15:03.500Z"


def test_brt_day_uses_brazil_date():
    assert brt_day(parse_iso("2026-10-02T02:30:00Z")) == "2026-10-01"


def test_brt_days_is_inclusive():
    start = parse_iso("2026-09-30T12:00:00-03:00")
    end = parse_iso("2026-10-02T00:30:00-03:00")
    assert brt_days(start, end) == ["2026-09-30", "2026-10-01", "2026-10-02"]


def test_format_brt():
    assert format_brt(parse_iso("2026-10-02T01:15:03Z")) == "01/10/2026 22:15:03"


def test_day_start_utc_iso():
    assert day_start_utc_iso("2026-10-01") == "2026-10-01T03:00:00.000Z"
```

`infra/tests/test_severity.py`:
```python
import pytest

from common.severity import DEFAULT_THRESHOLDS, at_least, normalize_score, severity_for


@pytest.mark.parametrize(
    "raw,expected",
    [(0.0, 0), (0.604, 60), (0.726, 73), (1.0, 100), (1.3, 100), (-0.2, 0)],
)
def test_normalize_score(raw, expected):
    assert normalize_score(raw) == expected


@pytest.mark.parametrize(
    "score,expected",
    [(0, "alert"), (29, "alert"), (30, "mild"), (59, "mild"), (60, "drowsy"), (79, "drowsy"), (80, "critical"), (100, "critical")],
)
def test_severity_bands_with_defaults(score, expected):
    assert severity_for(score, DEFAULT_THRESHOLDS) == expected


def test_at_least():
    assert at_least("critical", "drowsy")
    assert at_least("drowsy", "drowsy")
    assert not at_least("mild", "drowsy")
```

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_timeutil.py tests/test_severity.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'common.timeutil'`.

- [ ] **Step 4: Implementar**

`infra/src/common/timeutil.py`:
```python
"""Datas gravadas em UTC; dias e exibição no horário de Brasília (UTC-3, sem horário de verão desde 2019)."""

from datetime import date, datetime, time, timedelta, timezone

BRT = timezone(timedelta(hours=-3), "BRT")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp deve ser uma string ISO 8601")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=BRT)


def to_utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def brt_day(moment: datetime) -> str:
    return moment.astimezone(BRT).date().isoformat()


def brt_days(start: datetime, end: datetime) -> list[str]:
    first = start.astimezone(BRT).date()
    last = end.astimezone(BRT).date()
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def format_brt(moment: datetime) -> str:
    return moment.astimezone(BRT).strftime("%d/%m/%Y %H:%M:%S")


def day_start_utc_iso(day: str) -> str:
    return to_utc_iso(datetime.combine(date.fromisoformat(day), time(0), tzinfo=BRT))
```

`infra/src/common/severity.py`:
```python
"""Severidades no vocabulário do dashboard (dashboard/src/api/types.ts)."""

SEVERITY_ORDER = ("alert", "mild", "drowsy", "critical")

LABELS = {"alert": "atento", "mild": "leve", "drowsy": "sonolento", "critical": "crítico"}

DEFAULT_THRESHOLDS = {
    "mild": 30,
    "drowsy": 60,
    "critical": 80,
    "notifyOn": "drowsy",
    "emailAlerts": True,
    "smsAlerts": False,
}


def normalize_score(raw: float) -> int:
    """Converte o score do edge (0-1) para a escala do dashboard (0-100)."""
    return max(0, min(100, round(raw * 100)))


def severity_for(score: int, thresholds: dict) -> str:
    if score < thresholds["mild"]:
        return "alert"
    if score < thresholds["drowsy"]:
        return "mild"
    if score < thresholds["critical"]:
        return "drowsy"
    return "critical"


def at_least(severity: str, minimum: str) -> bool:
    return SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(minimum)
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_timeutil.py tests/test_severity.py -v`
Expected: todos PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add pyproject.toml uv.lock .python-version .gitignore src tests
git commit -m "feat: adiciona utilitários de tempo e severidade das Lambdas

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Base compartilhada (DynamoDB, HTTP, auth, settings, SNS, serialização) e fixtures de teste

**Files:**
- Create: `infra/src/common/db.py`, `http.py`, `auth.py`, `settings.py`, `notify.py`, `serialize.py`
- Create: `infra/tests/conftest.py`, `infra/tests/factories.py`
- Test: `infra/tests/test_db.py`, `test_http.py`, `test_auth.py`, `test_settings.py`, `test_notify.py`, `test_serialize.py`

**Interfaces:**
- Consumes: `common.timeutil.parse_iso`, `common.severity.DEFAULT_THRESHOLDS`.
- Produces:
  - `db`: `reset()`, `table(env_var) -> Table`, `to_item(obj)` (float→Decimal, remove `None`), `plain(obj)` (Decimal→int/float), `query_all(tbl, **kw) -> list`, `scan_all(tbl, **kw) -> list`.
  - `http`: `HttpError(status, message)`, `response(status, body)`, `ok(body, status=200)`, `error(status, message)`, `route(event, routes)`, `json_body(event) -> dict`, `query_params(event) -> dict`, `path_id(event) -> str`, `query_int(params, name, default, low, high) -> int`, `query_time(params, name) -> datetime | None`.
  - `auth`: `groups(event) -> set[str]`, `require_groups(event, *allowed)`, `email(event) -> str | None`.
  - `settings`: `SETTING_ID`, `load_thresholds() -> dict`, `save_thresholds(dict) -> dict`.
  - `notify`: `publish(subject, message)`.
  - `serialize`: `event_out(item) -> dict`, `trip_out(item) -> dict`.
  - Fixtures de teste: `aws` (moto + tabelas + tópico SNS com fila SQS; devolve a URL da fila) e `sent_emails` (função que retorna `[{"Subject", "Message", ...}]`).
  - `factories`: `THING`, `api_request(...)`, `body(resp)`, `iot_message(...)`, `alert_item(...)`, `put_driver(...)`, `put_event(...)`, `put_trip(...)`.
- Variáveis de ambiente usadas por todas as Lambdas: `DRIVERS_TABLE`, `TRIPS_TABLE`, `EVENTS_TABLE`, `SETTINGS_TABLE`, `ALERT_TOPIC_ARN`, `DASHBOARD_URL`.

- [ ] **Step 1: Fixtures e factories**

`infra/tests/conftest.py`:
```python
import json
import os

import boto3
import pytest
from moto import mock_aws

os.environ.update(
    {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "DRIVERS_TABLE": "helio-drivers",
        "TRIPS_TABLE": "helio-trips",
        "EVENTS_TABLE": "helio-events",
        "SETTINGS_TABLE": "helio-settings",
        "DASHBOARD_URL": "https://helio.example",
    }
)


def _create_table(client, name, key, attributes, indexes=()):
    """Espelha stacks/data.yaml. Mantenha os dois em sincronia."""
    kwargs = {
        "TableName": name,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [{"AttributeName": a, "AttributeType": "S"} for a in attributes],
        "KeySchema": [{"AttributeName": key, "KeyType": "HASH"}],
    }
    if indexes:
        kwargs["GlobalSecondaryIndexes"] = [
            {
                "IndexName": index,
                "KeySchema": [{"AttributeName": hash_key, "KeyType": "HASH"}]
                + ([{"AttributeName": range_key, "KeyType": "RANGE"}] if range_key else []),
                "Projection": {"ProjectionType": "ALL"},
            }
            for index, hash_key, range_key in indexes
        ]
    client.create_table(**kwargs)


@pytest.fixture
def aws():
    with mock_aws():
        from common import db

        db.reset()
        ddb = boto3.client("dynamodb")
        _create_table(ddb, "helio-drivers", "driverId", ["driverId", "assignedDeviceId"], [("byDevice", "assignedDeviceId", None)])
        _create_table(ddb, "helio-trips", "tripId", ["tripId", "driverId", "startedAt"], [("byDriver", "driverId", "startedAt")])
        _create_table(
            ddb,
            "helio-events",
            "eventId",
            ["eventId", "tripId", "driverId", "day", "timestamp"],
            [("byTrip", "tripId", "timestamp"), ("byDriver", "driverId", "timestamp"), ("byDay", "day", "timestamp")],
        )
        _create_table(ddb, "helio-settings", "settingId", ["settingId"])

        sns = boto3.client("sns")
        sqs = boto3.client("sqs")
        topic_arn = sns.create_topic(Name="helio-alerts")["TopicArn"]
        queue_url = sqs.create_queue(QueueName="emails")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)
        os.environ["ALERT_TOPIC_ARN"] = topic_arn
        yield queue_url


@pytest.fixture
def sent_emails(aws):
    """Lê o que foi publicado no SNS (via fila SQS assinante). Cada chamada consome as mensagens lidas."""

    def read():
        messages = boto3.client("sqs").receive_message(QueueUrl=aws, MaxNumberOfMessages=10).get("Messages", [])
        return [json.loads(m["Body"]) for m in messages]

    return read
```

`infra/tests/factories.py`:
```python
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
```

- [ ] **Step 2: Escrever os testes que devem falhar**

`infra/tests/test_db.py`:
```python
from decimal import Decimal

from common.db import plain, to_item


def test_to_item_converts_floats_and_drops_none():
    assert to_item({"a": 0.13, "b": None, "c": [0.5, 1], "d": {"e": 2.5, "f": None}, "g": True}) == {
        "a": Decimal("0.13"),
        "c": [Decimal("0.5"), 1],
        "d": {"e": Decimal("2.5")},
        "g": True,
    }


def test_plain_converts_decimals():
    result = plain({"a": Decimal("3"), "b": [Decimal("0.25")]})
    assert result == {"a": 3, "b": [0.25]}
    assert isinstance(result["a"], int)
```

`infra/tests/test_http.py`:
```python
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
```

`infra/tests/test_auth.py`:
```python
import pytest

from common.auth import email, groups, require_groups
from common.http import HttpError
from factories import api_request


def test_groups_from_http_api_string_claim():
    assert groups(api_request("GET /x", groups=("Administrador", "Operador"))) == {"Administrador", "Operador"}


def test_groups_from_list_claim():
    event = {"requestContext": {"authorizer": {"jwt": {"claims": {"cognito:groups": ["GestorDeFrota"]}}}}}
    assert groups(event) == {"GestorDeFrota"}


def test_groups_missing_is_empty():
    assert groups({}) == set()
    assert groups(api_request("GET /x", groups=())) == set()


def test_require_groups_forbids():
    with pytest.raises(HttpError) as exc:
        require_groups(api_request("PUT /x", groups=("Operador",)), "Administrador")
    assert exc.value.status == 403


def test_require_groups_allows():
    require_groups(api_request("PUT /x", groups=("GestorDeFrota",)), "Administrador", "GestorDeFrota")


def test_email_claim():
    assert email(api_request("GET /x", email="ana@helio.dev")) == "ana@helio.dev"
```

`infra/tests/test_settings.py`:
```python
import boto3

from common.settings import load_thresholds, save_thresholds
from common.severity import DEFAULT_THRESHOLDS


def test_defaults_when_missing(aws):
    assert load_thresholds() == DEFAULT_THRESHOLDS


def test_stored_values_override_defaults(aws):
    boto3.resource("dynamodb").Table("helio-settings").put_item(Item={"settingId": "thresholds", "mild": 25})
    thresholds = load_thresholds()
    assert thresholds["mild"] == 25
    assert thresholds["critical"] == 80
    assert "settingId" not in thresholds


def test_save_roundtrip(aws):
    saved = save_thresholds({**DEFAULT_THRESHOLDS, "critical": 90})
    assert saved["critical"] == 90
    assert load_thresholds()["critical"] == 90
```

`infra/tests/test_notify.py`:
```python
from common.notify import publish


def test_publish_strips_accents_from_subject_only(aws, sent_emails):
    publish("[Helio] Alerta crítico: João", "Horário: 22h — sonolência")
    [mail] = sent_emails()
    assert mail["Subject"] == "[Helio] Alerta critico: Joao"
    assert mail["Message"] == "Horário: 22h — sonolência"
```

`infra/tests/test_serialize.py`:
```python
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
```

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests -v`
Expected: os testes novos falham com `ModuleNotFoundError` (`common.db`, `common.http`...); os da Task 1 continuam PASS.

- [ ] **Step 4: Implementar**

`infra/src/common/db.py`:
```python
import os
from decimal import Decimal

import boto3

_resource = None


def reset() -> None:
    """Descarta o cliente em cache (usado pelos testes a cada mock novo)."""
    global _resource
    _resource = None


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
```

`infra/src/common/http.py`:
```python
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
```

`infra/src/common/auth.py`:
```python
from common.http import HttpError


def _claims(event: dict) -> dict:
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    return (authorizer.get("jwt") or {}).get("claims") or {}


def groups(event: dict) -> set[str]:
    """A HTTP API entrega claims de lista como texto ('[A B]'); aceita os dois formatos."""
    raw = _claims(event).get("cognito:groups", "")
    if isinstance(raw, list):
        return set(raw)
    return {g for g in raw.strip("[]").replace(",", " ").split() if g}


def require_groups(event: dict, *allowed: str) -> None:
    if not groups(event) & set(allowed):
        raise HttpError(403, "Você não tem permissão para esta ação")


def email(event: dict):
    return _claims(event).get("email")
```

`infra/src/common/settings.py`:
```python
from common.db import plain, table, to_item
from common.severity import DEFAULT_THRESHOLDS

SETTING_ID = "thresholds"


def load_thresholds() -> dict:
    item = table("SETTINGS_TABLE").get_item(Key={"settingId": SETTING_ID}).get("Item") or {}
    stored = {k: v for k, v in plain(item).items() if k != "settingId"}
    return {**DEFAULT_THRESHOLDS, **stored}


def save_thresholds(values: dict) -> dict:
    table("SETTINGS_TABLE").put_item(Item=to_item({"settingId": SETTING_ID, **values}))
    return load_thresholds()
```

`infra/src/common/notify.py`:
```python
import os
import unicodedata

import boto3


def _ascii_subject(text: str) -> str:
    """O assunto do SNS para e-mail deve ser ASCII e ter menos de 100 caracteres."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")[:99]


def publish(subject: str, message: str) -> None:
    boto3.client("sns").publish(
        TopicArn=os.environ["ALERT_TOPIC_ARN"],
        Subject=_ascii_subject(subject),
        Message=message,
    )
```

`infra/src/common/serialize.py`:
```python
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
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests -v`
Expected: todos PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add src/common tests
git commit -m "feat: adiciona base compartilhada das Lambdas (DynamoDB, HTTP, auth, SNS)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Lambda de ingestão (IoT → DynamoDB → SNS)

**Files:**
- Create: `infra/src/ingest.py`
- Test: `infra/tests/test_ingest.py`

**Interfaces:**
- Consumes: Task 1 e Task 2 (`table`, `to_item`, `query_all`, `load_thresholds`, `publish`, `severity_for`, `normalize_score`, `at_least`, `LABELS`, `parse_iso`, `to_utc_iso`, `brt_day`, `format_brt`).
- Produces: `ingest.handler(event, context) -> {"stored": int}`.
  - Entrada: o payload do edge (`device_id`, `ride_id`, `timestamp`, `nonce`, `data`) mais `thingName`, vindo do SQL da regra.
  - Item gravado em Events: `eventId`, `tripId`, `deviceId`, `driverId?`, `timestamp`, `day`, `score`, `severity`, `durationSec`, `triggers`, `ear?`, `perclos?`, `edgeStatus?`.
  - Item gravado em Trips: `tripId`, `deviceId`, `driverId?`, `startedAt`, `lastEventAt`, `maxScore`, `alertCount`.
- Variáveis de ambiente: as quatro tabelas, `ALERT_TOPIC_ARN`, `DASHBOARD_URL`, `EAR_THRESHOLD` (padrão `0.20`), `PERCLOS_TRIGGER` (padrão `0.20`).

- [ ] **Step 1: Escrever os testes que devem falhar**

`infra/tests/test_ingest.py`:
```python
import boto3

import ingest
from common.settings import save_thresholds
from common.severity import DEFAULT_THRESHOLDS
from factories import THING, alert_item, iot_message, put_driver


def stored_events():
    return boto3.resource("dynamodb").Table("helio-events").scan()["Items"]


def stored_trip(trip_id="ride-1"):
    return boto3.resource("dynamodb").Table("helio-trips").get_item(Key={"tripId": trip_id}).get("Item")


def test_stores_normalized_event(aws):
    put_driver("drv_ana", "Ana Souza", device=THING)
    assert ingest.handler(iot_message(alert_item(score=0.72)), None) == {"stored": 1}
    [event] = stored_events()
    assert event["score"] == 72
    assert event["severity"] == "drowsy"
    assert event["driverId"] == "drv_ana"
    assert event["tripId"] == "ride-1"
    assert event["deviceId"] == THING
    assert event["timestamp"] == "2026-10-02T01:15:03.000Z"
    assert event["day"] == "2026-10-01"
    assert set(event["triggers"]) == {"perclos", "eye-closure"}
    assert event["edgeStatus"] == "ALERTA"


def test_event_without_assigned_driver_has_no_driver(aws):
    ingest.handler(iot_message(alert_item()), None)
    [event] = stored_events()
    assert "driverId" not in event
    assert "driverId" not in stored_trip()


def test_topic_thing_name_wins_over_payload_device_id(aws):
    message = iot_message(alert_item())
    message["device_id"] = "OUTRO"
    ingest.handler(message, None)
    assert stored_events()[0]["deviceId"] == THING


def test_duplicate_delivery_is_idempotent(aws):
    message = iot_message(alert_item())
    ingest.handler(message, None)
    assert ingest.handler(message, None) == {"stored": 0}
    assert len(stored_events()) == 1
    assert stored_trip()["alertCount"] == 1


def test_trip_aggregates_out_of_order_events(aws):
    ingest.handler(iot_message(alert_item(timestamp="2026-10-01T22:20:00-03:00", score=0.65)), None)
    ingest.handler(iot_message(alert_item(timestamp="2026-10-01T22:10:00-03:00", score=0.9)), None)
    trip = stored_trip()
    assert trip["alertCount"] == 2
    assert trip["maxScore"] == 90
    assert trip["startedAt"] == "2026-10-02T01:10:00.000Z"
    assert trip["lastEventAt"] == "2026-10-02T01:20:00.000Z"
    assert trip["deviceId"] == THING


def test_invalid_items_are_skipped_but_valid_ones_stored(aws):
    message = iot_message(
        alert_item(score="alto"),
        alert_item(score=True),
        alert_item(score=1.5),
        {"score": 0.7},
        alert_item(timestamp="ontem"),
        "lixo",
        alert_item(timestamp="2026-10-01T22:30:00-03:00"),
    )
    assert ingest.handler(message, None) == {"stored": 1}


def test_malformed_message_is_dropped(aws):
    assert ingest.handler({}, None) == {"stored": 0}
    assert ingest.handler({"thingName": THING, "ride_id": "r", "data": "nada"}, None) == {"stored": 0}
    assert ingest.handler({"thingName": "", "ride_id": "r", "data": []}, None) == {"stored": 0}


def test_emails_when_severity_reaches_notify_on(aws, sent_emails):
    put_driver("drv_ana", "Ana Souza", device=THING)
    ingest.handler(iot_message(alert_item(score=0.85)), None)
    [mail] = sent_emails()
    event_id = stored_events()[0]["eventId"]
    assert mail["Subject"] == "[Helio] Alerta critico: Ana Souza"
    assert "Motorista: Ana Souza" in mail["Message"]
    assert "score 85/100" in mail["Message"]
    assert "01/10/2026 22:15:03" in mail["Message"]
    assert f"https://helio.example/alertas/{event_id}" in mail["Message"]


def test_email_names_device_when_no_driver(aws, sent_emails):
    ingest.handler(iot_message(alert_item(score=0.85)), None)
    [mail] = sent_emails()
    assert mail["Subject"] == f"[Helio] Alerta critico: Dispositivo {THING} (sem motorista)"


def test_no_email_below_notify_on(aws, sent_emails):
    ingest.handler(iot_message(alert_item(score=0.45)), None)
    assert sent_emails() == []


def test_no_email_for_duplicate(aws, sent_emails):
    message = iot_message(alert_item(score=0.95))
    ingest.handler(message, None)
    ingest.handler(message, None)
    assert len(sent_emails()) == 1


def test_no_email_when_disabled(aws, sent_emails):
    save_thresholds({**DEFAULT_THRESHOLDS, "emailAlerts": False})
    ingest.handler(iot_message(alert_item(score=0.95)), None)
    assert sent_emails() == []


def test_uses_stored_thresholds(aws):
    save_thresholds({**DEFAULT_THRESHOLDS, "drowsy": 75, "critical": 90})
    ingest.handler(iot_message(alert_item(score=0.72)), None)
    assert stored_events()[0]["severity"] == "mild"
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_ingest.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'ingest'`.

- [ ] **Step 3: Implementar**

`infra/src/ingest.py`:
```python
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
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_ingest.py -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add src/ingest.py tests/test_ingest.py
git commit -m "feat: adiciona Lambda de ingestão de eventos do IoT Core

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Lambda de resumo diário

**Files:**
- Create: `infra/src/daily_summary.py`
- Test: `infra/tests/test_daily_summary.py`

**Interfaces:**
- Consumes: `query_all`, `table`, `plain`, `publish`, `brt_day`, `now_utc`.
- Produces: `daily_summary.handler(event, context) -> {"day": str, "events": int}`. Recebe `event["day"]` opcional (`YYYY-MM-DD`); sem ele, usa o dia de hoje em BRT.
- Variáveis de ambiente: `EVENTS_TABLE`, `DRIVERS_TABLE`, `ALERT_TOPIC_ARN`, `DASHBOARD_URL`.

- [ ] **Step 1: Escrever os testes que devem falhar**

`infra/tests/test_daily_summary.py`:
```python
from datetime import datetime, timezone

import daily_summary
from factories import put_driver, put_event


def test_summary_groups_by_driver(aws, sent_emails):
    put_driver("drv_ana", "Ana Souza")
    put_event("e1", driverId="drv_ana", timestamp="2026-10-01T13:00:00.000Z", score=70)
    put_event("e2", driverId="drv_ana", timestamp="2026-10-01T14:00:00.000Z", score=88)
    put_event("e3", deviceId="helio-edge-02", timestamp="2026-10-01T15:00:00.000Z", score=40)
    put_event("e4", driverId="drv_ana", timestamp="2026-09-30T13:00:00.000Z", day="2026-09-30", score=99)

    assert daily_summary.handler({"day": "2026-10-01"}, None) == {"day": "2026-10-01", "events": 3}

    [mail] = sent_emails()
    message = mail["Message"]
    assert mail["Subject"] == "[Helio] Resumo diario 01/10/2026"
    assert "Total de alertas: 3" in message
    assert "- Ana Souza: 2 alerta(s), pior score 88" in message
    assert "- Dispositivo helio-edge-02 (sem motorista): 1 alerta(s), pior score 40" in message
    assert message.index("Ana Souza") < message.index("helio-edge-02")
    assert "https://helio.example/alertas" in message


def test_summary_without_events_still_sends(aws, sent_emails):
    assert daily_summary.handler({"day": "2026-10-01"}, None)["events"] == 0
    assert "Nenhum alerta registrado no dia." in sent_emails()[0]["Message"]


def test_default_day_is_today_in_brazil(aws, sent_emails, monkeypatch):
    monkeypatch.setattr(daily_summary, "now_utc", lambda: datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc))
    assert daily_summary.handler({}, None)["day"] == "2026-10-01"
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_daily_summary.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'daily_summary'`.

- [ ] **Step 3: Implementar**

`infra/src/daily_summary.py`:
```python
"""Envia por e-mail (SNS) o resumo de alertas do dia, agendado pelo EventBridge Scheduler às 20h de Brasília."""

import os
from datetime import date

from boto3.dynamodb.conditions import Key

from common.db import plain, query_all, table
from common.notify import publish
from common.timeutil import brt_day, now_utc


def handler(event, context):
    day = (event or {}).get("day") or brt_day(now_utc())
    rows = [
        plain(r)
        for r in query_all(table("EVENTS_TABLE"), IndexName="byDay", KeyConditionExpression=Key("day").eq(day))
    ]

    totals = {}
    for row in rows:
        key = row.get("driverId") or f"device:{row['deviceId']}"
        entry = totals.setdefault(key, {"count": 0, "worst": 0})
        entry["count"] += 1
        entry["worst"] = max(entry["worst"], row["score"])

    shown = date.fromisoformat(day).strftime("%d/%m/%Y")
    lines = [f"Resumo de alertas de sonolência de {shown}.", ""]
    if rows:
        lines += [f"Total de alertas: {len(rows)}", ""]
        for key, entry in sorted(totals.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
            lines.append(f"- {_label(key)}: {entry['count']} alerta(s), pior score {entry['worst']}")
    else:
        lines.append("Nenhum alerta registrado no dia.")
    lines += ["", f"Painel: {os.environ['DASHBOARD_URL']}/alertas"]

    publish(f"[Helio] Resumo diario {shown}", "\n".join(lines))
    return {"day": day, "events": len(rows)}


def _label(key: str) -> str:
    if key.startswith("device:"):
        return f"Dispositivo {key.removeprefix('device:')} (sem motorista)"
    driver = table("DRIVERS_TABLE").get_item(Key={"driverId": key}).get("Item")
    return driver["name"] if driver else key
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_daily_summary.py -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add src/daily_summary.py tests/test_daily_summary.py
git commit -m "feat: adiciona Lambda de resumo diário por e-mail

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: API de limiares (`/api/settings/thresholds`)

**Files:**
- Create: `infra/src/api_settings.py`
- Test: `infra/tests/test_api_settings.py`

**Interfaces:**
- Consumes: `route`, `ok`, `HttpError`, `json_body`, `require_groups`, `load_thresholds`, `save_thresholds`, `SEVERITY_ORDER`.
- Produces: `api_settings.handler`, com as rotas `GET /api/settings/thresholds` (qualquer usuário autenticado) e `PUT /api/settings/thresholds` (só `Administrador`; aceita patch parcial e devolve o objeto completo de `AlertThresholds`).
- Variáveis de ambiente: `SETTINGS_TABLE`.

- [ ] **Step 1: Escrever os testes que devem falhar**

`infra/tests/test_api_settings.py`:
```python
import pytest

import api_settings
from common.settings import load_thresholds
from common.severity import DEFAULT_THRESHOLDS
from factories import api_request, body

ROUTE_GET = "GET /api/settings/thresholds"
ROUTE_PUT = "PUT /api/settings/thresholds"


def test_get_returns_defaults(aws):
    response = api_settings.handler(api_request(ROUTE_GET), None)
    assert response["statusCode"] == 200
    assert body(response) == DEFAULT_THRESHOLDS


def test_admin_can_patch(aws):
    response = api_settings.handler(
        api_request(ROUTE_PUT, groups=("Administrador",), body={"critical": 85, "notifyOn": "critical"}), None
    )
    assert response["statusCode"] == 200
    assert body(response) == {**DEFAULT_THRESHOLDS, "critical": 85, "notifyOn": "critical"}
    assert load_thresholds()["notifyOn"] == "critical"


@pytest.mark.parametrize("group", ["Operador", "GestorDeFrota"])
def test_operador_forbidden(aws, group):
    response = api_settings.handler(api_request(ROUTE_PUT, groups=(group,), body={"critical": 85}), None)
    assert response["statusCode"] == 403
    assert load_thresholds() == DEFAULT_THRESHOLDS


@pytest.mark.parametrize(
    "patch",
    [
        {"mild": 70},
        {"critical": 101},
        {"drowsy": "60"},
        {"drowsy": True},
        {"drowsy": 60.5},
        {"notifyOn": "panic"},
        {"emailAlerts": "sim"},
        {"color": "blue"},
    ],
)
def test_invalid_patch_rejected(aws, patch):
    response = api_settings.handler(api_request(ROUTE_PUT, groups=("Administrador",), body=patch), None)
    assert response["statusCode"] == 400
    assert "message" in body(response)
    assert load_thresholds() == DEFAULT_THRESHOLDS
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_api_settings.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'api_settings'`.

- [ ] **Step 3: Implementar**

`infra/src/api_settings.py`:
```python
from common.auth import require_groups
from common.http import HttpError, json_body, ok, route
from common.settings import load_thresholds, save_thresholds
from common.severity import SEVERITY_ORDER

LEVELS = ("mild", "drowsy", "critical")
FLAGS = ("emailAlerts", "smsAlerts")
FIELDS = {*LEVELS, *FLAGS, "notifyOn"}


def get_thresholds(event):
    return ok(load_thresholds())


def put_thresholds(event):
    require_groups(event, "Administrador")
    patch = json_body(event)

    unknown = sorted(set(patch) - FIELDS)
    if unknown:
        raise HttpError(400, f"Campo(s) desconhecido(s): {', '.join(unknown)}")
    for key in LEVELS:
        value = patch.get(key, 0)
        if not (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100):
            raise HttpError(400, f"'{key}' deve ser um número inteiro entre 0 e 100")
    for key in FLAGS:
        if key in patch and not isinstance(patch[key], bool):
            raise HttpError(400, f"'{key}' deve ser verdadeiro ou falso")
    if "notifyOn" in patch and patch["notifyOn"] not in SEVERITY_ORDER:
        raise HttpError(400, "'notifyOn' deve ser alert, mild, drowsy ou critical")

    merged = {**load_thresholds(), **patch}
    if not merged["mild"] < merged["drowsy"] < merged["critical"]:
        raise HttpError(400, "Os limiares devem ser crescentes: leve < sonolento < crítico")
    return ok(save_thresholds(merged))


ROUTES = {
    "GET /api/settings/thresholds": get_thresholds,
    "PUT /api/settings/thresholds": put_thresholds,
}


def handler(event, context):
    return route(event, ROUTES)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_api_settings.py -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add src/api_settings.py tests/test_api_settings.py
git commit -m "feat: adiciona API de limiares de alerta

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: API de eventos (`/api/events`)

**Files:**
- Create: `infra/src/api_events.py`
- Test: `infra/tests/test_api_events.py`

**Interfaces:**
- Consumes: `route`, `ok`, `HttpError`, `path_id`, `query_params`, `query_int`, `query_time`, `email`, `table`, `query_all`, `event_out`, `SEVERITY_ORDER`, `brt_days`, `now_utc`, `to_utc_iso`.
- Produces: `api_events.handler`, com as rotas:
  - `GET /api/events`: `Paginated<DrowsinessEvent>` = `{rows, total, page, pageSize}`. Parâmetros: `page` (1–10000, padrão 1), `pageSize` (1–100, padrão 20), `severity` (lista separada por vírgula), `driverId`, `vehicleId`, `acknowledged` (`true` ou `false`), `from` e `to` (ISO; padrão: últimos 7 dias; no máximo 31 dias BRT), `sort` (`timestamp`, `score` ou `severity`; padrão `timestamp`), `dir` (`asc` ou `desc`; padrão `desc`).
  - `GET /api/events/{id}`.
  - `POST /api/events/{id}/acknowledge`: idempotente; `acknowledgedBy` vem do claim `email` e o body é ignorado.
- Variáveis de ambiente: `EVENTS_TABLE`.

- [ ] **Step 1: Escrever os testes que devem falhar**

`infra/tests/test_api_events.py`:
```python
from datetime import datetime, timezone

import pytest

import api_events
from factories import api_request, body, put_event

FIXED_NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def now(monkeypatch):
    monkeypatch.setattr(api_events, "now_utc", lambda: FIXED_NOW)


def seed():
    put_event("e1", driverId="drv_ana", timestamp="2026-10-01T12:00:00.000Z", day="2026-10-01", score=45, severity="mild")
    # 23:30 em Brasília do dia 01 = 02:30Z do dia 02
    put_event("e2", driverId="drv_ana", timestamp="2026-10-02T02:30:00.000Z", day="2026-10-01", score=85, severity="critical")
    put_event(
        "e3",
        driverId="drv_bia",
        timestamp="2026-10-02T11:00:00.000Z",
        day="2026-10-02",
        score=65,
        severity="drowsy",
        acknowledgedAt="2026-10-02T11:05:00.000Z",
        acknowledgedBy="op@helio.dev",
    )
    put_event("old", driverId="drv_ana", timestamp="2026-09-20T12:00:00.000Z", day="2026-09-20", score=90, severity="critical")


def list_events(**query):
    return api_events.handler(api_request("GET /api/events", query=query or None), None)


def ids(response):
    return [row["id"] for row in body(response)["rows"]]


def test_default_lists_last_7_days_newest_first(aws, now):
    seed()
    response = list_events()
    assert response["statusCode"] == 200
    assert ids(response) == ["e3", "e2", "e1"]
    assert {k: v for k, v in body(response).items() if k != "rows"} == {"total": 3, "page": 1, "pageSize": 20}


def test_range_crossing_brt_midnight(aws, now):
    seed()
    response = list_events(**{"from": "2026-10-01T23:00:00-03:00", "to": "2026-10-01T23:59:59-03:00"})
    assert ids(response) == ["e2"]


def test_filters(aws, now):
    seed()
    assert ids(list_events(severity="critical,drowsy")) == ["e3", "e2"]
    assert ids(list_events(driverId="drv_bia")) == ["e3"]
    assert ids(list_events(acknowledged="false")) == ["e2", "e1"]
    assert ids(list_events(acknowledged="true")) == ["e3"]
    assert ids(list_events(vehicleId="v1")) == []


def test_sort_and_paginate(aws, now):
    seed()
    response = list_events(sort="score", dir="asc", page="2", pageSize="2")
    assert ids(response) == ["e2"]
    assert body(response)["total"] == 3


def test_sort_by_severity(aws, now):
    seed()
    assert ids(list_events(sort="severity")) == ["e2", "e3", "e1"]


@pytest.mark.parametrize(
    "query",
    [
        {"severity": "panic"},
        {"sort": "name"},
        {"dir": "up"},
        {"page": "0"},
        {"pageSize": "500"},
        {"from": "ontem"},
        {"acknowledged": "talvez"},
        {"from": "2026-01-01T00:00:00Z"},
        {"from": "2026-10-02T00:00:00Z", "to": "2026-10-01T00:00:00Z"},
    ],
)
def test_bad_query_is_400(aws, now, query):
    response = list_events(**query)
    assert response["statusCode"] == 400
    assert "message" in body(response)


def test_get_event(aws):
    put_event("e1")
    response = api_events.handler(api_request("GET /api/events/{id}", path={"id": "e1"}), None)
    assert response["statusCode"] == 200
    assert body(response)["id"] == "e1"
    assert body(response)["driverId"] is None


def test_get_missing_event_is_404(aws):
    response = api_events.handler(api_request("GET /api/events/{id}", path={"id": "nope"}), None)
    assert response["statusCode"] == 404
    assert body(response) == {"message": "Alerta não encontrado"}


def acknowledge(event_id, email):
    return api_events.handler(
        api_request("POST /api/events/{id}/acknowledge", path={"id": event_id}, email=email, body={"by": "falso"}),
        None,
    )


def test_acknowledge_uses_token_email(aws, now):
    put_event("e1")
    response = acknowledge("e1", "gestor@helio.dev")
    assert response["statusCode"] == 200
    assert body(response)["acknowledgedBy"] == "gestor@helio.dev"
    assert body(response)["acknowledgedAt"] == "2026-10-02T12:00:00.000Z"


def test_acknowledge_twice_keeps_first(aws, now):
    put_event("e1")
    acknowledge("e1", "primeiro@helio.dev")
    response = acknowledge("e1", "segundo@helio.dev")
    assert response["statusCode"] == 200
    assert body(response)["acknowledgedBy"] == "primeiro@helio.dev"


def test_acknowledge_missing_is_404(aws, now):
    assert acknowledge("nope", "a@helio.dev")["statusCode"] == 404
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_api_events.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'api_events'`.

- [ ] **Step 3: Implementar**

`infra/src/api_events.py`:
```python
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
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_api_events.py -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add src/api_events.py tests/test_api_events.py
git commit -m "feat: adiciona API de eventos de sonolência

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: API de motoristas (`/api/drivers`)

**Files:**
- Create: `infra/src/api_drivers.py`
- Test: `infra/tests/test_api_drivers.py`

**Interfaces:**
- Consumes: `route`, `ok`, `HttpError`, `json_body`, `path_id`, `require_groups`, `table`, `query_all`, `scan_all`, `to_item`, `plain`, `load_thresholds`, `severity_for`, `now_utc`, `parse_iso`, `to_utc_iso`, `brt_day`, `brt_days`, `day_start_utc_iso`.
- Produces: `api_drivers.handler`. O formato `Driver` é o do dashboard mais `assignedDeviceId`: `{id, name, licenseNo, phone, assignedVehicleId, assignedDeviceId, currentScore, currentSeverity, onShift, shiftStartedAt, stats: {avgScore7d, eventsThisWeek, hoursDrivenThisWeek}}`. Rotas:
  - `GET /api/drivers`: ordenado por nome.
  - `GET /api/drivers/{id}`.
  - `GET /api/drivers/{id}/history`: 21 pontos `{t, score}`, com `t` = meia-noite BRT em UTC e `score` = média diária ou 0.
  - `POST /api/drivers`: retorna 201.
  - `PUT /api/drivers/{id}`.
  - POST e PUT exigem `Administrador` ou `GestorDeFrota`.
  - Campos editáveis: `name`, `licenseNo`, `phone`, `assignedDeviceId`, `assignedVehicleId`. Os dois IDs aceitam `null` ou `""` para desvincular.
  - Um dispositivo já vinculado a outro motorista gera 409.
- "Em turno": último evento há menos de 30 min. `hoursDrivenThisWeek` é a soma de (`lastEventAt` − `startedAt`) das viagens dos últimos 7 dias. É uma aproximação, porque o edge só envia mensagens nos alertas.
- Variáveis de ambiente: `DRIVERS_TABLE`, `TRIPS_TABLE`, `EVENTS_TABLE`, `SETTINGS_TABLE`.

- [ ] **Step 1: Escrever os testes que devem falhar**

`infra/tests/test_api_drivers.py`:
```python
from datetime import datetime, timezone

import boto3
import pytest

import api_drivers
from factories import THING, api_request, body, put_driver, put_event, put_trip

FIXED_NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
EDITOR = ("GestorDeFrota",)


@pytest.fixture
def now(monkeypatch):
    monkeypatch.setattr(api_drivers, "now_utc", lambda: FIXED_NOW)


def call(route_key, **kwargs):
    return api_drivers.handler(api_request(route_key, **kwargs), None)


def stored_driver(driver_id):
    return boto3.resource("dynamodb").Table("helio-drivers").get_item(Key={"driverId": driver_id}).get("Item")


def seed_ana_on_shift():
    put_driver("drv_ana", "Ana Souza", device=THING, licenseNo="123", phone="+55 11 9")
    put_trip("ride-1", driverId="drv_ana", startedAt="2026-10-02T10:00:00.000Z", lastEventAt="2026-10-02T11:50:00.000Z")
    put_event("e1", driverId="drv_ana", timestamp="2026-10-02T11:00:00.000Z", score=50, severity="mild")
    put_event("e2", driverId="drv_ana", timestamp="2026-10-02T11:50:00.000Z", score=70, severity="drowsy")
    put_event("old", driverId="drv_ana", timestamp="2026-09-01T11:00:00.000Z", day="2026-09-01", score=99)


def test_list_drivers_sorted_with_stats(aws, now):
    put_driver("drv_bia", "Bia Lima")
    seed_ana_on_shift()
    response = call("GET /api/drivers")
    assert response["statusCode"] == 200
    ana, bia = body(response)
    assert ana == {
        "id": "drv_ana",
        "name": "Ana Souza",
        "licenseNo": "123",
        "phone": "+55 11 9",
        "assignedVehicleId": None,
        "assignedDeviceId": THING,
        "currentScore": 70,
        "currentSeverity": "drowsy",
        "onShift": True,
        "shiftStartedAt": "2026-10-02T10:00:00.000Z",
        "stats": {"avgScore7d": 60, "eventsThisWeek": 2, "hoursDrivenThisWeek": 1.8},
    }
    assert bia["onShift"] is False
    assert bia["currentScore"] == 0
    assert bia["currentSeverity"] == "alert"
    assert bia["stats"] == {"avgScore7d": 0, "eventsThisWeek": 0, "hoursDrivenThisWeek": 0}


def test_driver_leaves_shift_after_30_minutes(aws, monkeypatch):
    monkeypatch.setattr(api_drivers, "now_utc", lambda: datetime(2026, 10, 2, 12, 21, tzinfo=timezone.utc))
    seed_ana_on_shift()
    ana = body(call("GET /api/drivers/{id}", path={"id": "drv_ana"}))
    assert ana["onShift"] is False
    assert ana["currentScore"] == 0
    assert ana["shiftStartedAt"] is None


def test_get_missing_driver_is_404(aws, now):
    response = call("GET /api/drivers/{id}", path={"id": "nope"})
    assert response["statusCode"] == 404
    assert body(response) == {"message": "Motorista não encontrado"}


def test_history_is_daily_average_for_21_days(aws, now):
    put_driver("drv_ana", "Ana Souza")
    put_event("e1", driverId="drv_ana", timestamp="2026-10-01T15:00:00.000Z", score=40)
    put_event("e2", driverId="drv_ana", timestamp="2026-10-02T02:30:00.000Z", score=60)  # 23:30 BRT do dia 01
    points = body(call("GET /api/drivers/{id}/history", path={"id": "drv_ana"}))
    assert len(points) == 21
    assert points[0]["t"] == "2026-09-12T03:00:00.000Z"
    assert points[-2] == {"t": "2026-10-01T03:00:00.000Z", "score": 50}
    assert points[-1] == {"t": "2026-10-02T03:00:00.000Z", "score": 0}


def test_history_missing_driver_is_404(aws, now):
    assert call("GET /api/drivers/{id}/history", path={"id": "nope"})["statusCode"] == 404


def test_gestor_creates_driver(aws, now):
    response = call("POST /api/drivers", groups=EDITOR, body={"name": "  Carla Dias ", "licenseNo": "999", "phone": ""})
    assert response["statusCode"] == 201
    created = body(response)
    assert created["id"].startswith("drv_")
    assert created["name"] == "Carla Dias"
    assert created["phone"] == ""
    assert created["assignedDeviceId"] is None
    assert stored_driver(created["id"])["name"] == "Carla Dias"


def test_operador_cannot_create_or_update(aws, now):
    put_driver("drv_ana", "Ana")
    assert call("POST /api/drivers", groups=("Operador",), body={"name": "X"})["statusCode"] == 403
    assert call("PUT /api/drivers/{id}", groups=("Operador",), path={"id": "drv_ana"}, body={"name": "X"})["statusCode"] == 403


@pytest.mark.parametrize(
    "payload",
    [{}, {"name": ""}, {"name": "   "}, {"name": 5}, {"name": "X", "salary": 1}, {"name": "x" * 101}],
)
def test_create_validation(aws, now, payload):
    response = call("POST /api/drivers", groups=EDITOR, body=payload)
    assert response["statusCode"] == 400
    assert "message" in body(response)


def test_update_assigns_and_unassigns_device(aws, now):
    put_driver("drv_ana", "Ana")
    response = call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "drv_ana"}, body={"assignedDeviceId": THING, "phone": "+55"})
    assert response["statusCode"] == 200
    assert body(response)["assignedDeviceId"] == THING
    assert body(response)["phone"] == "+55"

    response = call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "drv_ana"}, body={"assignedDeviceId": None})
    assert body(response)["assignedDeviceId"] is None
    assert "assignedDeviceId" not in stored_driver("drv_ana")


def test_device_cannot_belong_to_two_drivers(aws, now):
    put_driver("drv_ana", "Ana", device=THING)
    put_driver("drv_bia", "Bia")
    assert call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "drv_bia"}, body={"assignedDeviceId": THING})["statusCode"] == 409
    assert call("POST /api/drivers", groups=EDITOR, body={"name": "Caio", "assignedDeviceId": THING})["statusCode"] == 409
    assert call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "drv_ana"}, body={"assignedDeviceId": THING})["statusCode"] == 200


def test_update_missing_driver_is_404(aws, now):
    assert call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "nope"}, body={"name": "X"})["statusCode"] == 404


def test_name_cannot_be_null(aws, now):
    put_driver("drv_ana", "Ana")
    assert call("PUT /api/drivers/{id}", groups=EDITOR, path={"id": "drv_ana"}, body={"name": None})["statusCode"] == 400
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_api_drivers.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'api_drivers'`.

- [ ] **Step 3: Implementar**

`infra/src/api_drivers.py`:
```python
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
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_api_drivers.py -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add src/api_drivers.py tests/test_api_drivers.py
git commit -m "feat: adiciona API de motoristas

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: API de viagens (`/api/trips`)

**Files:**
- Create: `infra/src/api_trips.py`
- Test: `infra/tests/test_api_trips.py`

**Interfaces:**
- Consumes: `route`, `ok`, `HttpError`, `path_id`, `query_params`, `query_time`, `table`, `query_all`, `scan_all`, `trip_out`, `event_out`, `now_utc`, `to_utc_iso`.
- Produces: `api_trips.handler`, com as rotas:
  - `GET /api/trips?driverId&from&to`: `Trip[]` = `{id, deviceId, driverId, startedAt, lastEventAt, maxScore, alertCount}`, filtrado por `startedAt` (padrão: últimos 7 dias) e com as mais recentes primeiro.
  - `GET /api/trips/{id}`: `Trip & {events: DrowsinessEvent[]}`, com os eventos em ordem crescente.
- Variáveis de ambiente: `TRIPS_TABLE`, `EVENTS_TABLE`.

- [ ] **Step 1: Escrever os testes que devem falhar**

`infra/tests/test_api_trips.py`:
```python
from datetime import datetime, timezone

import pytest

import api_trips
from factories import THING, api_request, body, put_event, put_trip

FIXED_NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def now(monkeypatch):
    monkeypatch.setattr(api_trips, "now_utc", lambda: FIXED_NOW)


def seed():
    put_trip("t1", driverId="drv_ana", startedAt="2026-10-01T10:00:00.000Z", maxScore=90, alertCount=2)
    put_trip("t2", driverId="drv_ana", startedAt="2026-10-02T10:00:00.000Z")
    put_trip("t3", driverId="drv_bia", startedAt="2026-10-02T09:00:00.000Z")
    put_trip("old", driverId="drv_ana", startedAt="2026-09-01T10:00:00.000Z")


def list_trips(**query):
    return api_trips.handler(api_request("GET /api/trips", query=query or None), None)


def ids(response):
    return [t["id"] for t in body(response)]


def test_lists_last_7_days_newest_first(aws, now):
    seed()
    assert ids(list_trips()) == ["t2", "t3", "t1"]


def test_filters_by_driver_and_range(aws, now):
    seed()
    assert ids(list_trips(driverId="drv_ana")) == ["t2", "t1"]
    assert ids(list_trips(**{"from": "2026-10-02T00:00:00Z"})) == ["t2", "t3"]


def test_trip_shape(aws, now):
    seed()
    t1 = next(t for t in body(list_trips()) if t["id"] == "t1")
    assert t1 == {
        "id": "t1",
        "deviceId": THING,
        "driverId": "drv_ana",
        "startedAt": "2026-10-01T10:00:00.000Z",
        "lastEventAt": "2026-10-02T11:00:00.000Z",
        "maxScore": 90,
        "alertCount": 2,
    }


def test_get_trip_with_events_in_order(aws):
    put_trip("ride-1")
    put_event("late", timestamp="2026-10-02T11:00:00.000Z")
    put_event("early", timestamp="2026-10-02T10:30:00.000Z")
    put_event("other", tripId="ride-2")
    response = api_trips.handler(api_request("GET /api/trips/{id}", path={"id": "ride-1"}), None)
    assert response["statusCode"] == 200
    assert [e["id"] for e in body(response)["events"]] == ["early", "late"]


def test_get_missing_trip_is_404(aws):
    response = api_trips.handler(api_request("GET /api/trips/{id}", path={"id": "nope"}), None)
    assert response["statusCode"] == 404
    assert body(response) == {"message": "Viagem não encontrada"}


def test_bad_dates_are_400(aws, now):
    assert list_trips(**{"from": "ontem"})["statusCode"] == 400
    assert list_trips(**{"from": "2026-10-02T00:00:00Z", "to": "2026-10-01T00:00:00Z"})["statusCode"] == 400
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest tests/test_api_trips.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'api_trips'`.

- [ ] **Step 3: Implementar**

`infra/src/api_trips.py`:
```python
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
```

- [ ] **Step 4: Rodar a suíte completa**

Run: `cd /Users/matt/Projects/Helio/infra && uv run pytest -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add src/api_trips.py tests/test_api_trips.py
git commit -m "feat: adiciona API de viagens

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Templates de dados e autenticação (`data.yaml`, `auth.yaml`)

**Files:**
- Create: `infra/stacks/data.yaml`, `infra/stacks/auth.yaml`

**Interfaces:**
- Produces:
  - `data.yaml`: outputs `DriversTableName/Arn`, `TripsTableName/Arn`, `EventsTableName/Arn`, `SettingsTableName/Arn`. Nomes das tabelas: `${ProjectName}-drivers|trips|events|settings`. As chaves e GSIs são idênticos aos de `tests/conftest.py`.
  - `auth.yaml`: outputs `UserPoolId`, `UserPoolArn`, `UserPoolClientId`, `Issuer`.

- [ ] **Step 1: Escrever `infra/stacks/data.yaml`**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: Helio - tabelas DynamoDB (motoristas, viagens, eventos, configuracoes)

Parameters:
  ProjectName:
    Type: String

Resources:
  DriversTable:
    Type: AWS::DynamoDB::Table
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      TableName: !Sub ${ProjectName}-drivers
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: driverId
          AttributeType: S
        - AttributeName: assignedDeviceId
          AttributeType: S
      KeySchema:
        - AttributeName: driverId
          KeyType: HASH
      GlobalSecondaryIndexes:
        - IndexName: byDevice
          KeySchema:
            - AttributeName: assignedDeviceId
              KeyType: HASH
          Projection:
            ProjectionType: ALL

  TripsTable:
    Type: AWS::DynamoDB::Table
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      TableName: !Sub ${ProjectName}-trips
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: tripId
          AttributeType: S
        - AttributeName: driverId
          AttributeType: S
        - AttributeName: startedAt
          AttributeType: S
      KeySchema:
        - AttributeName: tripId
          KeyType: HASH
      GlobalSecondaryIndexes:
        - IndexName: byDriver
          KeySchema:
            - AttributeName: driverId
              KeyType: HASH
            - AttributeName: startedAt
              KeyType: RANGE
          Projection:
            ProjectionType: ALL

  EventsTable:
    Type: AWS::DynamoDB::Table
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      TableName: !Sub ${ProjectName}-events
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: eventId
          AttributeType: S
        - AttributeName: tripId
          AttributeType: S
        - AttributeName: driverId
          AttributeType: S
        - AttributeName: day
          AttributeType: S
        - AttributeName: timestamp
          AttributeType: S
      KeySchema:
        - AttributeName: eventId
          KeyType: HASH
      GlobalSecondaryIndexes:
        - IndexName: byTrip
          KeySchema:
            - AttributeName: tripId
              KeyType: HASH
            - AttributeName: timestamp
              KeyType: RANGE
          Projection:
            ProjectionType: ALL
        - IndexName: byDriver
          KeySchema:
            - AttributeName: driverId
              KeyType: HASH
            - AttributeName: timestamp
              KeyType: RANGE
          Projection:
            ProjectionType: ALL
        - IndexName: byDay
          KeySchema:
            - AttributeName: day
              KeyType: HASH
            - AttributeName: timestamp
              KeyType: RANGE
          Projection:
            ProjectionType: ALL

  SettingsTable:
    Type: AWS::DynamoDB::Table
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      TableName: !Sub ${ProjectName}-settings
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: settingId
          AttributeType: S
      KeySchema:
        - AttributeName: settingId
          KeyType: HASH

Outputs:
  DriversTableName:
    Value: !Ref DriversTable
  DriversTableArn:
    Value: !GetAtt DriversTable.Arn
  TripsTableName:
    Value: !Ref TripsTable
  TripsTableArn:
    Value: !GetAtt TripsTable.Arn
  EventsTableName:
    Value: !Ref EventsTable
  EventsTableArn:
    Value: !GetAtt EventsTable.Arn
  SettingsTableName:
    Value: !Ref SettingsTable
  SettingsTableArn:
    Value: !GetAtt SettingsTable.Arn
```

- [ ] **Step 2: Escrever `infra/stacks/auth.yaml`**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: Helio - Cognito (usuarios do dashboard e grupos de acesso)

Parameters:
  ProjectName:
    Type: String

Resources:
  UserPool:
    Type: AWS::Cognito::UserPool
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      UserPoolName: !Sub ${ProjectName}-users
      UsernameAttributes:
        - email
      AutoVerifiedAttributes:
        - email
      AdminCreateUserConfig:
        AllowAdminCreateUserOnly: true
      Policies:
        PasswordPolicy:
          MinimumLength: 8
          RequireLowercase: true
          RequireUppercase: true
          RequireNumbers: true
          RequireSymbols: false
          TemporaryPasswordValidityDays: 7
      AccountRecoverySetting:
        RecoveryMechanisms:
          - Name: verified_email
            Priority: 1

  UserPoolClient:
    Type: AWS::Cognito::UserPoolClient
    Properties:
      UserPoolId: !Ref UserPool
      ClientName: !Sub ${ProjectName}-dashboard
      GenerateSecret: false
      PreventUserExistenceErrors: ENABLED
      ExplicitAuthFlows:
        - ALLOW_USER_SRP_AUTH
        - ALLOW_USER_PASSWORD_AUTH
        - ALLOW_REFRESH_TOKEN_AUTH

  AdministradorGroup:
    Type: AWS::Cognito::UserPoolGroup
    Properties:
      UserPoolId: !Ref UserPool
      GroupName: Administrador
      Description: Acesso total, inclusive limiares de alerta
      Precedence: 1

  GestorDeFrotaGroup:
    Type: AWS::Cognito::UserPoolGroup
    Properties:
      UserPoolId: !Ref UserPool
      GroupName: GestorDeFrota
      Description: Gerencia motoristas e reconhece alertas
      Precedence: 2

  OperadorGroup:
    Type: AWS::Cognito::UserPoolGroup
    Properties:
      UserPoolId: !Ref UserPool
      GroupName: Operador
      Description: Acompanha e reconhece alertas
      Precedence: 3

Outputs:
  UserPoolId:
    Value: !Ref UserPool
  UserPoolArn:
    Value: !GetAtt UserPool.Arn
  UserPoolClientId:
    Value: !Ref UserPoolClient
  Issuer:
    Value: !Sub https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}
```

- [ ] **Step 3: Validar**

Run: `cd /Users/matt/Projects/Helio/infra && uv run cfn-lint stacks/data.yaml stacks/auth.yaml`
Expected: nenhuma saída (exit 0). Confira também, à mão, que chaves e GSIs de `data.yaml` batem com `tests/conftest.py`.

- [ ] **Step 4: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add stacks/data.yaml stacks/auth.yaml
git commit -m "feat: adiciona stacks de dados (DynamoDB) e autenticação (Cognito)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Templates da API e da web (`api.yaml`, `web.yaml`)

**Files:**
- Create: `infra/stacks/api.yaml`, `infra/stacks/web.yaml`

**Interfaces:**
- Consumes: os outputs de `auth.yaml` (`Issuer`, `UserPoolClientId`) e de `data.yaml` (nomes e ARNs das tabelas). O código fica em `../src`, com handlers `api_drivers.handler`, `api_trips.handler`, `api_events.handler` e `api_settings.handler`.
- Produces:
  - `api.yaml`: outputs `ApiId`, `ApiEndpoint` e `ApiDomain` (host, sem `https://`).
  - `web.yaml`: recebe `ApiDomain`; outputs `SiteBucketName`, `DistributionId` e `DashboardUrl` (`https://xxxx.cloudfront.net`).
- `Code: ../src` é um caminho local que o `aws cloudformation package` troca por S3. Como o cfn-lint espera um objeto ali, cada função ignora a regra `E3012` via `Metadata`.

- [ ] **Step 1: Escrever `infra/stacks/api.yaml`**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: Helio - API HTTP (API Gateway + Lambdas) protegida pelo Cognito

Parameters:
  ProjectName:
    Type: String
  UserPoolIssuer:
    Type: String
  UserPoolClientId:
    Type: String
  DriversTableName:
    Type: String
  DriversTableArn:
    Type: String
  TripsTableName:
    Type: String
  TripsTableArn:
    Type: String
  EventsTableName:
    Type: String
  EventsTableArn:
    Type: String
  SettingsTableName:
    Type: String
  SettingsTableArn:
    Type: String

Resources:
  HttpApi:
    Type: AWS::ApiGatewayV2::Api
    Properties:
      Name: !Sub ${ProjectName}-api
      ProtocolType: HTTP

  JwtAuthorizer:
    Type: AWS::ApiGatewayV2::Authorizer
    Properties:
      ApiId: !Ref HttpApi
      Name: cognito
      AuthorizerType: JWT
      IdentitySource:
        - $request.header.Authorization
      JwtConfiguration:
        Audience:
          - !Ref UserPoolClientId
        Issuer: !Ref UserPoolIssuer

  DefaultStage:
    Type: AWS::ApiGatewayV2::Stage
    Properties:
      ApiId: !Ref HttpApi
      StageName: $default
      AutoDeploy: true
      DefaultRouteSettings:
        ThrottlingBurstLimit: 50
        ThrottlingRateLimit: 20

  # ─── Motoristas ──────────────────────────────────────────────────────────
  DriversFunctionRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - !Sub arn:${AWS::Partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
      Policies:
        - PolicyName: data
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action:
                  - dynamodb:GetItem
                  - dynamodb:PutItem
                  - dynamodb:UpdateItem
                  - dynamodb:Scan
                  - dynamodb:Query
                Resource:
                  - !Ref DriversTableArn
                  - !Sub ${DriversTableArn}/index/*
              - Effect: Allow
                Action: dynamodb:Query
                Resource:
                  - !Sub ${EventsTableArn}/index/byDriver
                  - !Sub ${TripsTableArn}/index/byDriver
              - Effect: Allow
                Action: dynamodb:GetItem
                Resource: !Ref SettingsTableArn

  DriversLogGroup:
    Type: AWS::Logs::LogGroup
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      LogGroupName: !Sub /aws/lambda/${ProjectName}-api-drivers
      RetentionInDays: 14

  DriversFunction:
    Type: AWS::Lambda::Function
    DependsOn: DriversLogGroup
    Metadata:
      cfn-lint:
        config:
          ignore_checks: [E3012]
    Properties:
      FunctionName: !Sub ${ProjectName}-api-drivers
      Runtime: python3.13
      Architectures: [arm64]
      Handler: api_drivers.handler
      Code: ../src
      MemorySize: 256
      Timeout: 10
      Role: !GetAtt DriversFunctionRole.Arn
      Environment:
        Variables:
          DRIVERS_TABLE: !Ref DriversTableName
          TRIPS_TABLE: !Ref TripsTableName
          EVENTS_TABLE: !Ref EventsTableName
          SETTINGS_TABLE: !Ref SettingsTableName

  DriversPermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !Ref DriversFunction
      Action: lambda:InvokeFunction
      Principal: apigateway.amazonaws.com
      SourceArn: !Sub arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*

  DriversIntegration:
    Type: AWS::ApiGatewayV2::Integration
    Properties:
      ApiId: !Ref HttpApi
      IntegrationType: AWS_PROXY
      IntegrationUri: !GetAtt DriversFunction.Arn
      PayloadFormatVersion: "2.0"

  RouteListDrivers:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: GET /api/drivers
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${DriversIntegration}

  RouteCreateDriver:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: POST /api/drivers
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${DriversIntegration}

  RouteGetDriver:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: GET /api/drivers/{id}
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${DriversIntegration}

  RouteUpdateDriver:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: PUT /api/drivers/{id}
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${DriversIntegration}

  RouteDriverHistory:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: GET /api/drivers/{id}/history
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${DriversIntegration}

  # ─── Viagens ─────────────────────────────────────────────────────────────
  TripsFunctionRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - !Sub arn:${AWS::Partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
      Policies:
        - PolicyName: data
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action:
                  - dynamodb:GetItem
                  - dynamodb:Scan
                Resource: !Ref TripsTableArn
              - Effect: Allow
                Action: dynamodb:Query
                Resource:
                  - !Sub ${TripsTableArn}/index/byDriver
                  - !Sub ${EventsTableArn}/index/byTrip

  TripsLogGroup:
    Type: AWS::Logs::LogGroup
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      LogGroupName: !Sub /aws/lambda/${ProjectName}-api-trips
      RetentionInDays: 14

  TripsFunction:
    Type: AWS::Lambda::Function
    DependsOn: TripsLogGroup
    Metadata:
      cfn-lint:
        config:
          ignore_checks: [E3012]
    Properties:
      FunctionName: !Sub ${ProjectName}-api-trips
      Runtime: python3.13
      Architectures: [arm64]
      Handler: api_trips.handler
      Code: ../src
      MemorySize: 256
      Timeout: 10
      Role: !GetAtt TripsFunctionRole.Arn
      Environment:
        Variables:
          TRIPS_TABLE: !Ref TripsTableName
          EVENTS_TABLE: !Ref EventsTableName

  TripsPermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !Ref TripsFunction
      Action: lambda:InvokeFunction
      Principal: apigateway.amazonaws.com
      SourceArn: !Sub arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*

  TripsIntegration:
    Type: AWS::ApiGatewayV2::Integration
    Properties:
      ApiId: !Ref HttpApi
      IntegrationType: AWS_PROXY
      IntegrationUri: !GetAtt TripsFunction.Arn
      PayloadFormatVersion: "2.0"

  RouteListTrips:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: GET /api/trips
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${TripsIntegration}

  RouteGetTrip:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: GET /api/trips/{id}
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${TripsIntegration}

  # ─── Eventos ─────────────────────────────────────────────────────────────
  EventsFunctionRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - !Sub arn:${AWS::Partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
      Policies:
        - PolicyName: data
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action:
                  - dynamodb:GetItem
                  - dynamodb:UpdateItem
                Resource: !Ref EventsTableArn
              - Effect: Allow
                Action: dynamodb:Query
                Resource: !Sub ${EventsTableArn}/index/byDay

  EventsLogGroup:
    Type: AWS::Logs::LogGroup
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      LogGroupName: !Sub /aws/lambda/${ProjectName}-api-events
      RetentionInDays: 14

  EventsFunction:
    Type: AWS::Lambda::Function
    DependsOn: EventsLogGroup
    Metadata:
      cfn-lint:
        config:
          ignore_checks: [E3012]
    Properties:
      FunctionName: !Sub ${ProjectName}-api-events
      Runtime: python3.13
      Architectures: [arm64]
      Handler: api_events.handler
      Code: ../src
      MemorySize: 256
      Timeout: 10
      Role: !GetAtt EventsFunctionRole.Arn
      Environment:
        Variables:
          EVENTS_TABLE: !Ref EventsTableName

  EventsPermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !Ref EventsFunction
      Action: lambda:InvokeFunction
      Principal: apigateway.amazonaws.com
      SourceArn: !Sub arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*

  EventsIntegration:
    Type: AWS::ApiGatewayV2::Integration
    Properties:
      ApiId: !Ref HttpApi
      IntegrationType: AWS_PROXY
      IntegrationUri: !GetAtt EventsFunction.Arn
      PayloadFormatVersion: "2.0"

  RouteListEvents:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: GET /api/events
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${EventsIntegration}

  RouteGetEvent:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: GET /api/events/{id}
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${EventsIntegration}

  RouteAcknowledgeEvent:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: POST /api/events/{id}/acknowledge
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${EventsIntegration}

  # ─── Limiares ────────────────────────────────────────────────────────────
  SettingsFunctionRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - !Sub arn:${AWS::Partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
      Policies:
        - PolicyName: data
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action:
                  - dynamodb:GetItem
                  - dynamodb:PutItem
                Resource: !Ref SettingsTableArn

  SettingsLogGroup:
    Type: AWS::Logs::LogGroup
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      LogGroupName: !Sub /aws/lambda/${ProjectName}-api-settings
      RetentionInDays: 14

  SettingsFunction:
    Type: AWS::Lambda::Function
    DependsOn: SettingsLogGroup
    Metadata:
      cfn-lint:
        config:
          ignore_checks: [E3012]
    Properties:
      FunctionName: !Sub ${ProjectName}-api-settings
      Runtime: python3.13
      Architectures: [arm64]
      Handler: api_settings.handler
      Code: ../src
      MemorySize: 256
      Timeout: 10
      Role: !GetAtt SettingsFunctionRole.Arn
      Environment:
        Variables:
          SETTINGS_TABLE: !Ref SettingsTableName

  SettingsPermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !Ref SettingsFunction
      Action: lambda:InvokeFunction
      Principal: apigateway.amazonaws.com
      SourceArn: !Sub arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*

  SettingsIntegration:
    Type: AWS::ApiGatewayV2::Integration
    Properties:
      ApiId: !Ref HttpApi
      IntegrationType: AWS_PROXY
      IntegrationUri: !GetAtt SettingsFunction.Arn
      PayloadFormatVersion: "2.0"

  RouteGetThresholds:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: GET /api/settings/thresholds
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${SettingsIntegration}

  RoutePutThresholds:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref HttpApi
      RouteKey: PUT /api/settings/thresholds
      AuthorizationType: JWT
      AuthorizerId: !Ref JwtAuthorizer
      Target: !Sub integrations/${SettingsIntegration}

Outputs:
  ApiId:
    Value: !Ref HttpApi
  ApiEndpoint:
    Value: !GetAtt HttpApi.ApiEndpoint
  ApiDomain:
    Value: !Sub ${HttpApi}.execute-api.${AWS::Region}.${AWS::URLSuffix}
```

- [ ] **Step 2: Escrever `infra/stacks/web.yaml`**

A API usa uma cache policy própria, com TTL 0 e `Authorization` na chave. É assim que o CloudFront repassa o header `Authorization` à origem; a política gerenciada `CachingDisabled` descartaria o header.

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: Helio - hospedagem do dashboard (S3 privado + CloudFront) com /api/* roteado para a HTTP API

Parameters:
  ProjectName:
    Type: String
  ApiDomain:
    Type: String

Resources:
  SiteBucket:
    Type: AWS::S3::Bucket
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      BucketName: !Sub ${ProjectName}-dashboard-${AWS::AccountId}
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      OwnershipControls:
        Rules:
          - ObjectOwnership: BucketOwnerEnforced
      BucketEncryption:
        ServerSideEncryptionConfiguration:
          - ServerSideEncryptionByDefault:
              SSEAlgorithm: AES256

  OriginAccessControl:
    Type: AWS::CloudFront::OriginAccessControl
    Properties:
      OriginAccessControlConfig:
        Name: !Sub ${ProjectName}-dashboard-oac
        OriginAccessControlOriginType: s3
        SigningBehavior: always
        SigningProtocol: sigv4

  SpaRewriteFunction:
    Type: AWS::CloudFront::Function
    Properties:
      Name: !Sub ${ProjectName}-spa-rewrite
      AutoPublish: true
      FunctionConfig:
        Comment: Rotas da SPA (sem extensao) servem o index.html
        Runtime: cloudfront-js-2.0
      FunctionCode: |
        function handler(event) {
          var request = event.request;
          if (request.uri.indexOf('.') === -1) {
            request.uri = '/index.html';
          }
          return request;
        }

  ApiCachePolicy:
    Type: AWS::CloudFront::CachePolicy
    Properties:
      CachePolicyConfig:
        Name: !Sub ${ProjectName}-api-no-cache
        Comment: Sem cache; Authorization na chave para ser repassado a origem
        DefaultTTL: 0
        MinTTL: 0
        MaxTTL: 1
        ParametersInCacheKeyAndForwardedToOrigin:
          EnableAcceptEncodingGzip: false
          HeadersConfig:
            HeaderBehavior: whitelist
            Headers:
              - Authorization
          QueryStringsConfig:
            QueryStringBehavior: all
          CookiesConfig:
            CookieBehavior: none

  Distribution:
    Type: AWS::CloudFront::Distribution
    Properties:
      DistributionConfig:
        Enabled: true
        Comment: !Sub ${ProjectName} dashboard
        DefaultRootObject: index.html
        HttpVersion: http2and3
        PriceClass: PriceClass_100
        Origins:
          - Id: site
            DomainName: !GetAtt SiteBucket.RegionalDomainName
            OriginAccessControlId: !GetAtt OriginAccessControl.Id
            S3OriginConfig:
              OriginAccessIdentity: ""
          - Id: api
            DomainName: !Ref ApiDomain
            CustomOriginConfig:
              OriginProtocolPolicy: https-only
              OriginSSLProtocols:
                - TLSv1.2
        DefaultCacheBehavior:
          TargetOriginId: site
          ViewerProtocolPolicy: redirect-to-https
          CachePolicyId: 658327ea-f89d-4fab-a63d-7e88639e58f6 # Managed-CachingOptimized
          Compress: true
          FunctionAssociations:
            - EventType: viewer-request
              FunctionARN: !GetAtt SpaRewriteFunction.FunctionARN
        CacheBehaviors:
          - PathPattern: /api/*
            TargetOriginId: api
            ViewerProtocolPolicy: https-only
            AllowedMethods: [GET, HEAD, OPTIONS, PUT, POST, PATCH, DELETE]
            CachedMethods: [GET, HEAD]
            CachePolicyId: !Ref ApiCachePolicy
            OriginRequestPolicyId: b689b0a8-53d0-40ab-baf2-68738e2966ac # Managed-AllViewerExceptHostHeader

  SiteBucketPolicy:
    Type: AWS::S3::BucketPolicy
    Properties:
      Bucket: !Ref SiteBucket
      PolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: cloudfront.amazonaws.com
            Action: s3:GetObject
            Resource: !Sub ${SiteBucket.Arn}/*
            Condition:
              StringEquals:
                AWS:SourceArn: !Sub arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}

Outputs:
  SiteBucketName:
    Value: !Ref SiteBucket
  DistributionId:
    Value: !Ref Distribution
  DashboardUrl:
    Value: !Sub https://${Distribution.DomainName}
```

- [ ] **Step 3: Validar**

Run: `cd /Users/matt/Projects/Helio/infra && uv run cfn-lint stacks/api.yaml stacks/web.yaml`
Expected: nenhuma saída (exit 0). Se o cfn-lint acusar `Code: ../src` com outro código (não `E3012`), troque o código em `ignore_checks` das quatro funções pelo código reportado e rode de novo. Não ignore nenhum outro erro.

- [ ] **Step 4: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add stacks/api.yaml stacks/web.yaml
git commit -m "feat: adiciona stacks da API HTTP e da hospedagem do dashboard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Templates de alertas, IoT e raiz (`alerts.yaml`, `iot.yaml`, `root.yaml`)

**Files:**
- Create: `infra/stacks/alerts.yaml`, `infra/stacks/iot.yaml`, `infra/root.yaml`

**Interfaces:**
- Consumes: os outputs de data, auth, api e web; os handlers `daily_summary.handler` e `ingest.handler`.
- Produces: outputs da raiz, lidos pelos scripts da Task 12 e pelo Plano 3: `DashboardUrl`, `ApiEndpoint`, `UserPoolId`, `UserPoolClientId`, `AlertTopicArn`, `SiteBucketName`, `DistributionId`, `ThingName`, `CertificateId`, `DriversTableName`, `TripsTableName`, `EventsTableName`, `SettingsTableName`.

- [ ] **Step 1: Escrever `infra/stacks/alerts.yaml`**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: Helio - topico SNS de alertas por e-mail e resumo diario agendado

Parameters:
  ProjectName:
    Type: String
  AlertEmail1:
    Type: String
  AlertEmail2:
    Type: String
    Default: ""
  AlertEmail3:
    Type: String
    Default: ""
  DriversTableName:
    Type: String
  DriversTableArn:
    Type: String
  EventsTableName:
    Type: String
  EventsTableArn:
    Type: String
  DashboardUrl:
    Type: String

Conditions:
  HasEmail2: !Not [!Equals [!Ref AlertEmail2, ""]]
  HasEmail3: !Not [!Equals [!Ref AlertEmail3, ""]]

Resources:
  AlertTopic:
    Type: AWS::SNS::Topic
    Properties:
      TopicName: !Sub ${ProjectName}-alerts
      DisplayName: Helio

  Email1Subscription:
    Type: AWS::SNS::Subscription
    Properties:
      TopicArn: !Ref AlertTopic
      Protocol: email
      Endpoint: !Ref AlertEmail1

  Email2Subscription:
    Type: AWS::SNS::Subscription
    Condition: HasEmail2
    Properties:
      TopicArn: !Ref AlertTopic
      Protocol: email
      Endpoint: !Ref AlertEmail2

  Email3Subscription:
    Type: AWS::SNS::Subscription
    Condition: HasEmail3
    Properties:
      TopicArn: !Ref AlertTopic
      Protocol: email
      Endpoint: !Ref AlertEmail3

  DailySummaryRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - !Sub arn:${AWS::Partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
      Policies:
        - PolicyName: summary
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action: dynamodb:Query
                Resource: !Sub ${EventsTableArn}/index/byDay
              - Effect: Allow
                Action: dynamodb:GetItem
                Resource: !Ref DriversTableArn
              - Effect: Allow
                Action: sns:Publish
                Resource: !Ref AlertTopic

  DailySummaryLogGroup:
    Type: AWS::Logs::LogGroup
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      LogGroupName: !Sub /aws/lambda/${ProjectName}-daily-summary
      RetentionInDays: 14

  DailySummaryFunction:
    Type: AWS::Lambda::Function
    DependsOn: DailySummaryLogGroup
    Metadata:
      cfn-lint:
        config:
          ignore_checks: [E3012]
    Properties:
      FunctionName: !Sub ${ProjectName}-daily-summary
      Runtime: python3.13
      Architectures: [arm64]
      Handler: daily_summary.handler
      Code: ../src
      MemorySize: 256
      Timeout: 30
      Role: !GetAtt DailySummaryRole.Arn
      Environment:
        Variables:
          EVENTS_TABLE: !Ref EventsTableName
          DRIVERS_TABLE: !Ref DriversTableName
          ALERT_TOPIC_ARN: !Ref AlertTopic
          DASHBOARD_URL: !Ref DashboardUrl

  SchedulerRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: scheduler.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: invoke
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action: lambda:InvokeFunction
                Resource: !GetAtt DailySummaryFunction.Arn

  DailySummarySchedule:
    Type: AWS::Scheduler::Schedule
    Properties:
      Name: !Sub ${ProjectName}-daily-summary
      Description: Resumo diario de alertas as 20h (Brasilia)
      ScheduleExpression: cron(0 20 * * ? *)
      ScheduleExpressionTimezone: America/Sao_Paulo
      FlexibleTimeWindow:
        Mode: "OFF"
      Target:
        Arn: !GetAtt DailySummaryFunction.Arn
        RoleArn: !GetAtt SchedulerRole.Arn
        Input: "{}"

Outputs:
  AlertTopicArn:
    Value: !Ref AlertTopic
  DailySummaryFunctionName:
    Value: !Ref DailySummaryFunction
```

- [ ] **Step 2: Escrever `infra/stacks/iot.yaml`**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: Helio - dispositivo IoT (Thing, certificado, policy) e regra que entrega eventos a Lambda de ingestao

Parameters:
  ProjectName:
    Type: String
  DeviceThingName:
    Type: String
  DeviceCsr:
    Type: String
    Description: CSR PEM gerado localmente (a chave privada nunca sai da maquina)
  DriversTableName:
    Type: String
  DriversTableArn:
    Type: String
  TripsTableName:
    Type: String
  TripsTableArn:
    Type: String
  EventsTableName:
    Type: String
  EventsTableArn:
    Type: String
  SettingsTableName:
    Type: String
  SettingsTableArn:
    Type: String
  AlertTopicArn:
    Type: String
  DashboardUrl:
    Type: String

Resources:
  Thing:
    Type: AWS::IoT::Thing
    Properties:
      ThingName: !Ref DeviceThingName

  DevicePolicy:
    Type: AWS::IoT::Policy
    Properties:
      PolicyName: !Sub ${ProjectName}-device
      PolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Action: iot:Connect
            Resource: !Sub arn:${AWS::Partition}:iot:${AWS::Region}:${AWS::AccountId}:client/${!iot:Connection.Thing.ThingName}
          - Effect: Allow
            Action: iot:Publish
            Resource: !Sub arn:${AWS::Partition}:iot:${AWS::Region}:${AWS::AccountId}:topic/helio/devices/${!iot:Connection.Thing.ThingName}/events

  DeviceCertificate:
    Type: AWS::IoT::Certificate
    Properties:
      CertificateSigningRequest: !Ref DeviceCsr
      Status: ACTIVE

  DevicePolicyAttachment:
    Type: AWS::IoT::PolicyPrincipalAttachment
    Properties:
      PolicyName: !Ref DevicePolicy
      Principal: !GetAtt DeviceCertificate.Arn

  DeviceThingAttachment:
    Type: AWS::IoT::ThingPrincipalAttachment
    Properties:
      ThingName: !Ref Thing
      Principal: !GetAtt DeviceCertificate.Arn

  IngestRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - !Sub arn:${AWS::Partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
      Policies:
        - PolicyName: ingest
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action: dynamodb:GetItem
                Resource: !Ref SettingsTableArn
              - Effect: Allow
                Action: dynamodb:Query
                Resource: !Sub ${DriversTableArn}/index/byDevice
              - Effect: Allow
                Action: dynamodb:PutItem
                Resource: !Ref EventsTableArn
              - Effect: Allow
                Action: dynamodb:UpdateItem
                Resource: !Ref TripsTableArn
              - Effect: Allow
                Action: sns:Publish
                Resource: !Ref AlertTopicArn

  IngestLogGroup:
    Type: AWS::Logs::LogGroup
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      LogGroupName: !Sub /aws/lambda/${ProjectName}-ingest
      RetentionInDays: 14

  IngestFunction:
    Type: AWS::Lambda::Function
    DependsOn: IngestLogGroup
    Metadata:
      cfn-lint:
        config:
          ignore_checks: [E3012]
    Properties:
      FunctionName: !Sub ${ProjectName}-ingest
      Runtime: python3.13
      Architectures: [arm64]
      Handler: ingest.handler
      Code: ../src
      MemorySize: 256
      Timeout: 10
      Role: !GetAtt IngestRole.Arn
      Environment:
        Variables:
          DRIVERS_TABLE: !Ref DriversTableName
          TRIPS_TABLE: !Ref TripsTableName
          EVENTS_TABLE: !Ref EventsTableName
          SETTINGS_TABLE: !Ref SettingsTableName
          ALERT_TOPIC_ARN: !Ref AlertTopicArn
          DASHBOARD_URL: !Ref DashboardUrl
          EAR_THRESHOLD: "0.20"
          PERCLOS_TRIGGER: "0.20"

  RuleErrorLogGroup:
    Type: AWS::Logs::LogGroup
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      LogGroupName: !Sub /aws/iot/${ProjectName}-rule-errors
      RetentionInDays: 14

  RuleErrorRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: iot.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: logs
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action:
                  - logs:CreateLogStream
                  - logs:PutLogEvents
                  - logs:DescribeLogStreams
                Resource: !GetAtt RuleErrorLogGroup.Arn

  EventsRule:
    Type: AWS::IoT::TopicRule
    Properties:
      RuleName: !Sub ${ProjectName}_device_events
      TopicRulePayload:
        Description: Eventos de sonolencia dos dispositivos para a Lambda de ingestao
        AwsIotSqlVersion: "2016-03-23"
        RuleDisabled: false
        Sql: SELECT *, topic(3) AS thingName FROM 'helio/devices/+/events'
        Actions:
          - Lambda:
              FunctionArn: !GetAtt IngestFunction.Arn
        ErrorAction:
          CloudwatchLogs:
            LogGroupName: !Ref RuleErrorLogGroup
            RoleArn: !GetAtt RuleErrorRole.Arn

  IngestPermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !Ref IngestFunction
      Action: lambda:InvokeFunction
      Principal: iot.amazonaws.com
      SourceArn: !GetAtt EventsRule.Arn
      SourceAccount: !Ref AWS::AccountId

Outputs:
  ThingName:
    Value: !Ref Thing
  CertificateId:
    Value: !Ref DeviceCertificate
  CertificateArn:
    Value: !GetAtt DeviceCertificate.Arn
```

- [ ] **Step 3: Escrever `infra/root.yaml`**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: Helio - infraestrutura principal (compoe os nested stacks)

Parameters:
  ProjectName:
    Type: String
    Default: helio
    AllowedPattern: ^[a-z][a-z0-9]{1,15}$
    Description: Prefixo dos recursos (minusculas e numeros; usado em nome de regra IoT, que nao aceita hifen)
  AlertEmail1:
    Type: String
    AllowedPattern: ^[^@\s]+@[^@\s]+$
    Description: E-mail do gestor que recebe os alertas
  AlertEmail2:
    Type: String
    Default: ""
    AllowedPattern: ^([^@\s]+@[^@\s]+)?$
  AlertEmail3:
    Type: String
    Default: ""
    AllowedPattern: ^([^@\s]+@[^@\s]+)?$
  DeviceThingName:
    Type: String
    Default: helio-edge-01
    AllowedPattern: ^[a-zA-Z0-9:_-]+$
  DeviceCsr:
    Type: String
    Description: Conteudo de certs/device.csr (gerado pelo scripts/deploy.sh)

Resources:
  Auth:
    Type: AWS::CloudFormation::Stack
    Properties:
      TemplateURL: stacks/auth.yaml
      Parameters:
        ProjectName: !Ref ProjectName

  Data:
    Type: AWS::CloudFormation::Stack
    Properties:
      TemplateURL: stacks/data.yaml
      Parameters:
        ProjectName: !Ref ProjectName

  Api:
    Type: AWS::CloudFormation::Stack
    Properties:
      TemplateURL: stacks/api.yaml
      Parameters:
        ProjectName: !Ref ProjectName
        UserPoolIssuer: !GetAtt Auth.Outputs.Issuer
        UserPoolClientId: !GetAtt Auth.Outputs.UserPoolClientId
        DriversTableName: !GetAtt Data.Outputs.DriversTableName
        DriversTableArn: !GetAtt Data.Outputs.DriversTableArn
        TripsTableName: !GetAtt Data.Outputs.TripsTableName
        TripsTableArn: !GetAtt Data.Outputs.TripsTableArn
        EventsTableName: !GetAtt Data.Outputs.EventsTableName
        EventsTableArn: !GetAtt Data.Outputs.EventsTableArn
        SettingsTableName: !GetAtt Data.Outputs.SettingsTableName
        SettingsTableArn: !GetAtt Data.Outputs.SettingsTableArn

  Web:
    Type: AWS::CloudFormation::Stack
    Properties:
      TemplateURL: stacks/web.yaml
      Parameters:
        ProjectName: !Ref ProjectName
        ApiDomain: !GetAtt Api.Outputs.ApiDomain

  Alerts:
    Type: AWS::CloudFormation::Stack
    Properties:
      TemplateURL: stacks/alerts.yaml
      Parameters:
        ProjectName: !Ref ProjectName
        AlertEmail1: !Ref AlertEmail1
        AlertEmail2: !Ref AlertEmail2
        AlertEmail3: !Ref AlertEmail3
        DriversTableName: !GetAtt Data.Outputs.DriversTableName
        DriversTableArn: !GetAtt Data.Outputs.DriversTableArn
        EventsTableName: !GetAtt Data.Outputs.EventsTableName
        EventsTableArn: !GetAtt Data.Outputs.EventsTableArn
        DashboardUrl: !GetAtt Web.Outputs.DashboardUrl

  Iot:
    Type: AWS::CloudFormation::Stack
    Properties:
      TemplateURL: stacks/iot.yaml
      Parameters:
        ProjectName: !Ref ProjectName
        DeviceThingName: !Ref DeviceThingName
        DeviceCsr: !Ref DeviceCsr
        DriversTableName: !GetAtt Data.Outputs.DriversTableName
        DriversTableArn: !GetAtt Data.Outputs.DriversTableArn
        TripsTableName: !GetAtt Data.Outputs.TripsTableName
        TripsTableArn: !GetAtt Data.Outputs.TripsTableArn
        EventsTableName: !GetAtt Data.Outputs.EventsTableName
        EventsTableArn: !GetAtt Data.Outputs.EventsTableArn
        SettingsTableName: !GetAtt Data.Outputs.SettingsTableName
        SettingsTableArn: !GetAtt Data.Outputs.SettingsTableArn
        AlertTopicArn: !GetAtt Alerts.Outputs.AlertTopicArn
        DashboardUrl: !GetAtt Web.Outputs.DashboardUrl

Outputs:
  DashboardUrl:
    Value: !GetAtt Web.Outputs.DashboardUrl
  ApiEndpoint:
    Value: !GetAtt Api.Outputs.ApiEndpoint
  UserPoolId:
    Value: !GetAtt Auth.Outputs.UserPoolId
  UserPoolClientId:
    Value: !GetAtt Auth.Outputs.UserPoolClientId
  AlertTopicArn:
    Value: !GetAtt Alerts.Outputs.AlertTopicArn
  SiteBucketName:
    Value: !GetAtt Web.Outputs.SiteBucketName
  DistributionId:
    Value: !GetAtt Web.Outputs.DistributionId
  ThingName:
    Value: !GetAtt Iot.Outputs.ThingName
  CertificateId:
    Value: !GetAtt Iot.Outputs.CertificateId
  DriversTableName:
    Value: !GetAtt Data.Outputs.DriversTableName
  TripsTableName:
    Value: !GetAtt Data.Outputs.TripsTableName
  EventsTableName:
    Value: !GetAtt Data.Outputs.EventsTableName
  SettingsTableName:
    Value: !GetAtt Data.Outputs.SettingsTableName
```

- [ ] **Step 4: Validar todos os templates**

Run: `cd /Users/matt/Projects/Helio/infra && uv run cfn-lint root.yaml stacks/*.yaml`
Expected: nenhuma saída (exit 0). Como o cfn-lint lê os `TemplateURL` locais, um parâmetro de nested stack faltando ou sobrando aparece aqui como erro (E3043) e deve ser corrigido no template.

- [ ] **Step 5: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add root.yaml stacks/alerts.yaml stacks/iot.yaml
git commit -m "feat: adiciona stacks de alertas (SNS + resumo diário), IoT e raiz

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Scripts de deploy, seed e smoke test, README e implantação real

**Files:**
- Create: `infra/scripts/deploy.sh`, `infra/scripts/seed.py`, `infra/scripts/smoke.sh`, `infra/README.md`

**Interfaces:**
- Consumes: os outputs da raiz (Task 11) e `common.severity.DEFAULT_THRESHOLDS`.
- Produces:
  - `deploy.sh`: usa as variáveis `ALERT_EMAIL_1` (obrigatória), `ALERT_EMAIL_2`, `ALERT_EMAIL_3`, `PROJECT_NAME` (padrão `helio`), `STACK_NAME` (padrão = projeto), `AWS_REGION` (padrão `us-east-1`) e `THING_NAME` (padrão `helio-edge-01`). Gera `certs/device.key`, `certs/device.csr`, `certs/device.pem.crt`, `certs/AmazonRootCA1.pem` e `certs/edge.env`. O Plano 2 usa o `edge.env`.
  - `seed.py`: grava os limiares padrão, os motoristas `drv_demo_ana` (vinculada ao Thing) e `drv_demo_bruno`, e cria o admin no Cognito.
  - `smoke.sh`: publica um alerta e confere a viagem no DynamoDB.
- **Pré-requisito humano:** a sessão AWS está expirada. Antes do Step 4, peça ao usuário para rodar `! aws login` e confirme com `aws sts get-caller-identity`.

- [ ] **Step 1: Escrever `infra/scripts/deploy.sh`**

```bash
#!/usr/bin/env bash
# Valida, empacota e implanta a infra do Helio; depois baixa o certificado do dispositivo.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${ALERT_EMAIL_1:?Defina ALERT_EMAIL_1 com o e-mail que recebe os alertas}"
PROJECT="${PROJECT_NAME:-helio}"
STACK="${STACK_NAME:-$PROJECT}"
REGION="${AWS_REGION:-us-east-1}"
THING="${THING_NAME:-helio-edge-01}"

echo "==> Validando templates"
uv run cfn-lint root.yaml stacks/*.yaml

echo "==> Chave e CSR do dispositivo"
mkdir -p certs build
if [ ! -f certs/device.key ]; then
  openssl req -new -newkey rsa:2048 -nodes \
    -keyout certs/device.key -out certs/device.csr -subj "/CN=$THING"
  chmod 600 certs/device.key
fi

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${PROJECT}-artifacts-${ACCOUNT}-${REGION}"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "==> Criando bucket de artefatos $BUCKET"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
fi

echo "==> Empacotando"
aws cloudformation package \
  --template-file root.yaml \
  --s3-bucket "$BUCKET" --s3-prefix "$STACK" \
  --output-template-file build/packaged.yaml \
  --region "$REGION" >/dev/null

echo "==> Implantando stack $STACK"
aws cloudformation deploy \
  --template-file build/packaged.yaml \
  --stack-name "$STACK" \
  --capabilities CAPABILITY_IAM \
  --region "$REGION" \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "ProjectName=$PROJECT" \
    "DeviceThingName=$THING" \
    "DeviceCsr=$(cat certs/device.csr)" \
    "AlertEmail1=$ALERT_EMAIL_1" \
    "AlertEmail2=${ALERT_EMAIL_2:-}" \
    "AlertEmail3=${ALERT_EMAIL_3:-}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

echo "==> Certificado do dispositivo"
aws iot describe-certificate --certificate-id "$(output CertificateId)" --region "$REGION" \
  --query certificateDescription.certificatePem --output text > certs/device.pem.crt
[ -f certs/AmazonRootCA1.pem ] || curl -fsSL https://www.amazontrust.com/repository/AmazonRootCA1.pem -o certs/AmazonRootCA1.pem
IOT_ENDPOINT=$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --region "$REGION" --query endpointAddress --output text)
cat > certs/edge.env <<EOF
HELIO_IOT_ENDPOINT=$IOT_ENDPOINT
HELIO_THING_NAME=$THING
HELIO_CERT_PATH=$PWD/certs/device.pem.crt
HELIO_KEY_PATH=$PWD/certs/device.key
HELIO_CA_PATH=$PWD/certs/AmazonRootCA1.pem
EOF

echo "==> Pronto"
aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" --query "Stacks[0].Outputs" --output table
echo "Variáveis do edge em certs/edge.env. Confirme as inscrições do SNS nos e-mails recebidos."
```

Run: `chmod +x /Users/matt/Projects/Helio/infra/scripts/deploy.sh`

- [ ] **Step 2: Escrever `infra/scripts/seed.py`**

```python
"""Popula os limiares padrão, dois motoristas de exemplo e o usuário administrador.

Uso: uv run python scripts/seed.py --admin-email voce@exemplo.com
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from common.severity import DEFAULT_THRESHOLDS  # noqa: E402


def stack_outputs(cloudformation, stack):
    outputs = cloudformation.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def put_if_absent(table, item, key):
    try:
        table.put_item(Item=item, ConditionExpression=f"attribute_not_exists({key})")
        print(f"criado: {item[key]}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        print(f"já existe: {item[key]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-name", default="Administrador Helio")
    parser.add_argument("--stack", default=os.environ.get("STACK_NAME", "helio"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    out = stack_outputs(session.client("cloudformation"), args.stack)
    dynamodb = session.resource("dynamodb")
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    put_if_absent(dynamodb.Table(out["SettingsTableName"]), {"settingId": "thresholds", **DEFAULT_THRESHOLDS}, "settingId")
    drivers = dynamodb.Table(out["DriversTableName"])
    put_if_absent(
        drivers,
        {
            "driverId": "drv_demo_ana",
            "name": "Ana Souza",
            "licenseNo": "12345678900",
            "phone": "+55 11 91234-5678",
            "assignedDeviceId": out["ThingName"],
            "createdAt": now,
        },
        "driverId",
    )
    put_if_absent(
        drivers,
        {"driverId": "drv_demo_bruno", "name": "Bruno Lima", "licenseNo": "98765432100", "phone": "+55 11 99876-5432", "createdAt": now},
        "driverId",
    )

    cognito = session.client("cognito-idp")
    try:
        cognito.admin_create_user(
            UserPoolId=out["UserPoolId"],
            Username=args.admin_email,
            UserAttributes=[
                {"Name": "email", "Value": args.admin_email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "name", "Value": args.admin_name},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
        print(f"usuário criado: {args.admin_email} (senha temporária enviada por e-mail)")
    except cognito.exceptions.UsernameExistsException:
        print(f"usuário já existe: {args.admin_email}")
    cognito.admin_add_user_to_group(UserPoolId=out["UserPoolId"], Username=args.admin_email, GroupName="Administrador")
    print("usuário no grupo Administrador")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Escrever `infra/scripts/smoke.sh`**

```bash
#!/usr/bin/env bash
# Publica um alerta falso no tópico do dispositivo e confere se a viagem chegou ao DynamoDB.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${PROJECT_NAME:-helio}"
STACK="${STACK_NAME:-$PROJECT}"
REGION="${AWS_REGION:-us-east-1}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

THING=$(output ThingName)
TRIPS=$(output TripsTableName)
ENDPOINT=$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --region "$REGION" --query endpointAddress --output text)
RIDE="smoke-$(date +%s)"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PAYLOAD=$(jq -nc --arg ride "$RIDE" --arg ts "$NOW" --arg thing "$THING" \
  '{device_id: $thing, ride_id: $ride, timestamp: (now | floor), nonce: "00000000",
    data: [{timestamp: $ts, score: 0.91, ear: 0.12, perclos: 0.55, status: "ALERTA", durationSec: 3.2}]}')

echo "==> Publicando em helio/devices/$THING/events (viagem $RIDE)"
aws iot-data publish --endpoint-url "https://$ENDPOINT" --region "$REGION" \
  --topic "helio/devices/$THING/events" --cli-binary-format raw-in-base64-out --payload "$PAYLOAD"

for _ in $(seq 1 15); do
  COUNT=$(aws dynamodb get-item --table-name "$TRIPS" --region "$REGION" \
    --key "{\"tripId\": {\"S\": \"$RIDE\"}}" --query Item.alertCount.N --output text)
  if [ "$COUNT" = "1" ]; then
    echo "OK: viagem $RIDE registrada com 1 alerta. Confira o e-mail '[Helio] Alerta critico: ...'."
    exit 0
  fi
  sleep 2
done

echo "FALHA: a viagem $RIDE não apareceu em $TRIPS."
echo "Logs: aws logs tail /aws/lambda/${PROJECT}-ingest --region $REGION"
echo "      aws logs tail /aws/iot/${PROJECT}-rule-errors --region $REGION"
exit 1
```

Run: `chmod +x /Users/matt/Projects/Helio/infra/scripts/smoke.sh`

- [ ] **Step 4: Implantar (exige sessão AWS ativa)**

Run:
```bash
cd /Users/matt/Projects/Helio/infra
aws sts get-caller-identity
ALERT_EMAIL_1=<e-mail informado pelo usuário> scripts/deploy.sh
```
Expected: `Successfully created/updated stack - helio`, a tabela de outputs impressa, `certs/edge.env` criado e um e-mail "AWS Notification - Subscription Confirmation" na caixa do gestor. Peça ao usuário para clicar no link de confirmação.

Se falhar: `aws cloudformation describe-stack-events --stack-name helio --max-items 30` e, para o nested stack que falhou, o mesmo comando com o `PhysicalResourceId` dele. Corrija o template, rode a suíte (`uv run pytest`) e repita o deploy.

- [ ] **Step 5: Seed e smoke test**

Run:
```bash
cd /Users/matt/Projects/Helio/infra
uv run python scripts/seed.py --admin-email <e-mail do usuário>
scripts/smoke.sh
```
Expected: o seed imprime `criado: thresholds`, `criado: drv_demo_ana`, `criado: drv_demo_bruno` e `usuário criado`. O smoke imprime `OK: viagem smoke-... registrada com 1 alerta`, e o e-mail "[Helio] Alerta critico: Ana Souza" chega (com a inscrição já confirmada).

- [ ] **Step 6: Verificar a API através do CloudFront**

Run:
```bash
cd /Users/matt/Projects/Helio/infra
URL=$(aws cloudformation describe-stacks --stack-name helio --query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" --output text)
POOL=$(aws cloudformation describe-stacks --stack-name helio --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)
CLIENT=$(aws cloudformation describe-stacks --stack-name helio --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text)

# 1) Sem token -> 401
curl -s -o /dev/null -w "%{http_code}\n" "$URL/api/drivers"

# 2) Operador de teste
aws cognito-idp admin-create-user --user-pool-id "$POOL" --username operador.teste@helio.dev \
  --user-attributes Name=email,Value=operador.teste@helio.dev Name=email_verified,Value=true --message-action SUPPRESS
aws cognito-idp admin-set-user-password --user-pool-id "$POOL" --username operador.teste@helio.dev --password 'Teste1234' --permanent
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL" --username operador.teste@helio.dev --group-name Operador
OP_TOKEN=$(aws cognito-idp initiate-auth --client-id "$CLIENT" --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=operador.teste@helio.dev,PASSWORD=Teste1234 --query AuthenticationResult.IdToken --output text)

# 3) Operador lê motoristas (200) mas não altera limiares (403)
curl -s -w "\n%{http_code}\n" -H "Authorization: Bearer $OP_TOKEN" "$URL/api/drivers"
curl -s -w "\n%{http_code}\n" -X PUT -H "Authorization: Bearer $OP_TOKEN" -H "Content-Type: application/json" \
  -d '{"critical": 85}' "$URL/api/settings/thresholds"

# 4) Eventos do smoke test
curl -s -H "Authorization: Bearer $OP_TOKEN" "$URL/api/events" | jq '.total, .rows[0].severity'

# 5) Resumo diário manual
aws lambda invoke --function-name helio-daily-summary --payload '{}' --cli-binary-format raw-in-base64-out /dev/stdout
```
Expected:
1. `401`.
2. Os comandos do Cognito terminam sem erro.
3. `200`, com a lista de motoristas incluindo "Ana Souza"; depois `403` com `{"message":"Você não tem permissão para esta ação"}`.
4. `1` (ou mais) e `"critical"`.
5. `{"day": "...", "events": N}`, e o e-mail "[Helio] Resumo diario dd/mm/aaaa" chega.

Se o item 3 der 401 com token válido, o CloudFront não está repassando `Authorization`. Revise `ApiCachePolicy` em `web.yaml`.

Ao final, remova o operador de teste:
`aws cognito-idp admin-delete-user --user-pool-id "$POOL" --username operador.teste@helio.dev`

- [ ] **Step 7: Escrever `infra/README.md`**

````markdown
# Helio — Infraestrutura AWS

CloudFormation (nested stacks) da nuvem do Helio. Design: `docs/specs/2026-10-01-helio-aws-infra-design.md`.

| Stack | Conteúdo |
|---|---|
| `stacks/auth.yaml` | Cognito: User Pool, App Client, grupos Administrador / GestorDeFrota / Operador |
| `stacks/data.yaml` | DynamoDB: drivers, trips, events, settings |
| `stacks/api.yaml` | HTTP API + autorizador JWT + Lambdas `api_*` |
| `stacks/web.yaml` | S3 privado + CloudFront (dashboard e `/api/*`) |
| `stacks/alerts.yaml` | SNS (e-mails dos gestores) + resumo diário às 20h |
| `stacks/iot.yaml` | Thing, certificado, policy, regra IoT → Lambda `ingest` |

## Pré-requisitos
AWS CLI v2 autenticada (`aws login`), [uv](https://docs.astral.sh/uv/), openssl, jq.

## Testes
```bash
uv sync
uv run pytest
uv run cfn-lint root.yaml stacks/*.yaml
```

## Deploy
```bash
ALERT_EMAIL_1=gestor@exemplo.com scripts/deploy.sh   # até 3: ALERT_EMAIL_2, ALERT_EMAIL_3
uv run python scripts/seed.py --admin-email voce@exemplo.com
scripts/smoke.sh
```
- Cada gestor precisa clicar no link "Confirm subscription" que a AWS envia por e-mail.
- O admin recebe uma senha temporária e escolhe a definitiva no primeiro login.
- As variáveis do edge ficam em `certs/edge.env`. A pasta `certs/` contém a chave privada e não vai para o git.

## Contrato do dispositivo
Tópico `helio/devices/{thingName}/events` (o `client_id` MQTT deve ser igual ao `thingName`):
```json
{"device_id": "helio-edge-01", "ride_id": "<hmac>", "timestamp": 1790000000, "nonce": "a1b2c3d4",
 "data": [{"timestamp": "2026-10-01T22:15:03-03:00", "score": 0.72, "ear": 0.13, "perclos": 0.41, "status": "ALERTA", "durationSec": 2.4}]}
```

## Custos
Tudo é sob demanda (DynamoDB on-demand, Lambda, HTTP API, SNS, IoT Core, CloudFront PriceClass_100). No volume de uma demo, fica no free tier, perto de US$ 0/mês.

## Remover tudo
```bash
aws s3 rm "s3://$(aws cloudformation describe-stacks --stack-name helio --query "Stacks[0].Outputs[?OutputKey=='SiteBucketName'].OutputValue" --output text)" --recursive
aws cloudformation delete-stack --stack-name helio
aws cloudformation wait stack-delete-complete --stack-name helio
```
O bucket de artefatos `helio-artifacts-<conta>-<região>` não faz parte do stack; apague-o à mão se quiser.
````

- [ ] **Step 8: Commit**

```bash
cd /Users/matt/Projects/Helio/infra
git add scripts README.md
git commit -m "feat: adiciona scripts de deploy, seed e smoke test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
