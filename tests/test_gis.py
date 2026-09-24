"""GIS linking and polygon areas (SRS FR-9)."""
import pytest

from app.sample_geojson import write_sample_layer
from app.services.gis_service import link_record, polygon_area_ha, reload_layer


@pytest.fixture(scope="module")
def layer():
    write_sample_layer()
    return reload_layer()


def test_layer_loads_twelve_polygons(layer):
    assert layer.summary()["feature_count"] == 12


def test_polygon_area_matches_declared_area(layer):
    for feature in layer.all_features():
        props = feature.get("properties", {})
        declared = float(props.get("area_ha") or 0.0)
        computed = polygon_area_ha(feature["geometry"])
        # equirectangular projection at ~20N is accurate to a few percent
        assert computed == pytest.approx(declared, rel=0.05)


def test_link_record_by_khasra(layer):
    out = link_record(khasra_no="118/2", record_area_ha=0.4613)
    assert out["linked"] is True
    assert out["match_type"] == "khasra_no"
    assert abs(out["area_delta_pct"]) < 15.0


def test_link_record_missing_returns_unlinked(layer):
    out = link_record(khasra_no="000/0", record_area_ha=1.0)
    assert out["linked"] is False
    assert out["match_type"] == "none"


def test_spatial_mismatch_flag(layer):
    out = link_record(khasra_no="118/2", record_area_ha=0.10)
    assert out["spatial_mismatch"] is True
    assert abs(out["area_delta_pct"]) > 15.0
