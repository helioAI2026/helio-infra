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
