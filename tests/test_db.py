from decimal import Decimal

from common.db import plain, to_item


def test_to_item_converts_floats_and_drops_none():
    assert to_item({"a": 0.13, "b": None, "c": [0.5, 1], "d": {"e": 2.5, "f": None}, "g": True}) == {
        "a": Decimal("0.13"),
        "c": [Decimal("0.5"), 1],
        "d": {"e": Decimal("2.5")},
        "g": True,
    }


def test_plain_converts_decimals():
    result = plain({"a": Decimal("3"), "b": [Decimal("0.25")]})
    assert result == {"a": 3, "b": [0.25]}
    assert isinstance(result["a"], int)
