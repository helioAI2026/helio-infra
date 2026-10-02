from common.notify import publish


def test_publish_strips_accents_from_subject_only(aws, sent_emails):
    publish("[Helio] Alerta crítico: João", "Horário: 22h — sonolência")
    [mail] = sent_emails()
    assert mail["Subject"] == "[Helio] Alerta critico: Joao"
    assert mail["Message"] == "Horário: 22h — sonolência"
