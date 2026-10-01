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
