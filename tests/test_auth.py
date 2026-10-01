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
