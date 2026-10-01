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
