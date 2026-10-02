import boto3

from common.settings import load_thresholds, save_thresholds
from common.severity import DEFAULT_THRESHOLDS


def test_defaults_when_missing(aws):
    assert load_thresholds() == DEFAULT_THRESHOLDS


def test_stored_values_override_defaults(aws):
    boto3.resource("dynamodb").Table("helio-settings").put_item(Item={"settingId": "thresholds", "mild": 25})
    thresholds = load_thresholds()
    assert thresholds["mild"] == 25
    assert thresholds["critical"] == 80
    assert "settingId" not in thresholds


def test_save_roundtrip(aws):
    saved = save_thresholds({**DEFAULT_THRESHOLDS, "critical": 90})
    assert saved["critical"] == 90
    assert load_thresholds()["critical"] == 90
