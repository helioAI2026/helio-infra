from common.db import plain, table, to_item
from common.severity import DEFAULT_THRESHOLDS

SETTING_ID = "thresholds"


def load_thresholds() -> dict:
    item = table("SETTINGS_TABLE").get_item(Key={"settingId": SETTING_ID}).get("Item") or {}
    stored = {k: v for k, v in plain(item).items() if k != "settingId"}
    return {**DEFAULT_THRESHOLDS, **stored}


def save_thresholds(values: dict) -> dict:
    table("SETTINGS_TABLE").put_item(Item=to_item({"settingId": SETTING_ID, **values}))
    return load_thresholds()
