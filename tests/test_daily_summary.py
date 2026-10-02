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


def test_scheduled_run_covers_last_24_hours(aws, sent_emails, monkeypatch):
    # 20:00 em Brasília do dia 02
    monkeypatch.setattr(daily_summary, "now_utc", lambda: datetime(2026, 10, 2, 23, 0, tzinfo=timezone.utc))
    put_event("evening", timestamp="2026-10-02T00:30:00.000Z", day="2026-10-01", score=80)  # 21:30 do dia 01
    put_event("today", timestamp="2026-10-02T15:00:00.000Z", day="2026-10-02", score=60)
    put_event("too-old", timestamp="2026-10-01T22:00:00.000Z", day="2026-10-01", score=99)  # 19:00 do dia 01
    assert daily_summary.handler({}, None) == {"day": "2026-10-02", "events": 2}
    [mail] = sent_emails()
    assert mail["Subject"] == "[Helio] Resumo diario 02/10/2026"
    assert "pior score 80" in mail["Message"]
    assert "últimas 24 horas" in mail["Message"]
