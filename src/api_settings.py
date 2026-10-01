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
