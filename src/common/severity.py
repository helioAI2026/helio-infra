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
