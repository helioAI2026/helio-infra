import pytest

from common.severity import DEFAULT_THRESHOLDS, at_least, normalize_score, severity_for


@pytest.mark.parametrize(
    "raw,expected",
    [(0.0, 0), (0.604, 60), (0.726, 73), (1.0, 100), (1.3, 100), (-0.2, 0)],
)
def test_normalize_score(raw, expected):
    assert normalize_score(raw) == expected


@pytest.mark.parametrize(
    "score,expected",
    [(0, "alert"), (29, "alert"), (30, "mild"), (59, "mild"), (60, "drowsy"), (79, "drowsy"), (80, "critical"), (100, "critical")],
)
def test_severity_bands_with_defaults(score, expected):
    assert severity_for(score, DEFAULT_THRESHOLDS) == expected


def test_at_least():
    assert at_least("critical", "drowsy")
    assert at_least("drowsy", "drowsy")
    assert not at_least("mild", "drowsy")
