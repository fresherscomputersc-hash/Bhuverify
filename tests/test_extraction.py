"""Unit conversion and area normalisation (SRS FR-4 helper)."""
import pytest

from app.services.extraction import normalize_unit, to_hectare


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        (1.0, "hectare", 1.0),
        (1.0, "ha", 1.0),
        (1.0, "acre", 0.404686),
        (100.0, "decimal", 0.404686),
        (1.0, "bigha", 0.1011715),
        (1.0, "katha", 0.005058575),
        (1.0, "guntha", 0.01011715),
        (1.0, "kanal", 0.05058575),
        (1.0, "marla", 0.002529),  # to_hectare rounds to 6 decimals
        (0.75, "acre", round(0.75 * 0.404686, 6)),
    ],
)
def test_to_hectare_known_units(value, unit, expected):
    ha, ok = to_hectare(value, unit)
    assert ok is True
    assert ha == pytest.approx(expected, rel=1e-4)


def test_to_hectare_unknown_unit():
    ha, ok = to_hectare(1.0, "smoot")
    assert ok is False
    assert ha == 0.0


def test_normalize_unit_strips_noise():
    assert normalize_unit("Acres.") == "acres"
    assert normalize_unit("  HA ") == "ha"
    assert normalize_unit("decimal") == "decimal"


def test_normalize_unit_indic():
    assert normalize_unit("एकड़") == "एकड़"
    ha, ok = to_hectare(2.0, "एकड़")
    assert ok is True
    assert ha == pytest.approx(2 * 0.404686, rel=1e-4)
