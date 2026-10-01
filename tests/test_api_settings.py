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
