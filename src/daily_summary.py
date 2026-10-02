"""Envia por e-mail (SNS) o resumo de alertas do dia, agendado pelo EventBridge Scheduler às 20h de Brasília."""

import os
from datetime import date, timedelta

from boto3.dynamodb.conditions import Key

from common.db import plain, query_all, table
from common.notify import publish
from common.timeutil import brt_day, brt_days, format_brt, now_utc, to_utc_iso


def handler(event, context):
    """Sem `day`: cobre as últimas 24h (a execução das 20h pega também a noite anterior). Com `day`: o dia inteiro."""
    requested = (event or {}).get("day")
    now = now_utc()
    if requested:
        day, days, window = requested, [requested], None
    else:
        start = now - timedelta(hours=24)
        day, days = brt_day(now), brt_days(start, now)
        window = Key("timestamp").between(to_utc_iso(start), to_utc_iso(now))

    rows = []
    for d in days:
        condition = Key("day").eq(d) & window if window else Key("day").eq(d)
        rows += [plain(r) for r in query_all(table("EVENTS_TABLE"), IndexName="byDay", KeyConditionExpression=condition)]

    totals = {}
    for row in rows:
        key = row.get("driverId") or f"device:{row['deviceId']}"
        entry = totals.setdefault(key, {"count": 0, "worst": 0})
        entry["count"] += 1
        entry["worst"] = max(entry["worst"], row["score"])

    shown = date.fromisoformat(day).strftime("%d/%m/%Y")
    period = f"de {shown}" if requested else f"das últimas 24 horas (até {format_brt(now)})"
    lines = [f"Resumo de alertas de sonolência {period}.", ""]
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
