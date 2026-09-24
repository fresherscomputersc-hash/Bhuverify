"""
GIS / cadastral linking service (SRS FR-9).

Prototype tier: a preloaded sample GeoJSON cadastral layer matched to text
records by khasra / survey / plot number, with polygon area computed via
Shapely on an equirectangular projection (accurate enough for plot-scale work
at 20 deg N).

Pilot/production tier: replace `load_layer` with a PostGIS query and
`match_geometry` with a spatial join; the returned contract is unchanged.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from shapely.geometry import Polygon, shape
from shapely.ops import transform

from app.config import GEOJSON_DIR, settings

M_PER_DEG_LAT = 111_320.0


def _project(x: float, y: float, lat0: float) -> tuple[float, float]:
    return x * M_PER_DEG_LAT * math.cos(math.radians(lat0)), y * M_PER_DEG_LAT


def polygon_area_ha(geojson_geometry: dict) -> float:
    """Polygon area in hectares using a local equirectangular projection."""
    geom = shape(geojson_geometry)
    if geom.is_empty:
        return 0.0
    lat0 = geom.centroid.y
    projected = transform(lambda x, y, z=None: _project(x, y, lat0), geom)
    return round(projected.area / 10_000.0, 6)


class CadastralLayer:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or (GEOJSON_DIR / settings.geojson_file))
        self.features: list[dict] = []
        self.index: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.features, self.index = [], {}
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.features = data.get("features", [])
        self.index = {}
        for feature in self.features:
            props = feature.get("properties", {})
            for key in ("khasra_no", "survey_no", "plot_no"):
                value = str(props.get(key, "")).strip()
                if value:
                    self.index.setdefault(f"{key}:{value}", feature)

    def match(self, khasra_no: str = "", survey_no: str = "", plot_no: str = "") -> tuple[dict | None, str]:
        for key, value in (("khasra_no", khasra_no), ("survey_no", survey_no), ("plot_no", plot_no)):
            if not value:
                continue
            feature = self.index.get(f"{key}:{value}")
            if feature:
                return feature, key
        return None, "none"

    def all_features(self) -> list[dict]:
        return self.features

    def feature(self, plot_key: str) -> dict | None:
        for prefixed in self.index.values():
            props = prefixed.get("properties", {})
            if str(props.get("plot_key", "")) == plot_key:
                return prefixed
        return None

    def summary(self) -> dict:
        return {
            "layer_file": self.path.name,
            "feature_count": len(self.features),
            "indexed_keys": len(self.index),
            "villages": sorted({f.get("properties", {}).get("village", "") for f in self.features}),
            "crs": "EPSG:4326 (WGS84)",
            "area_projection": "local equirectangular",
        }


_LAYER: CadastralLayer | None = None


def layer() -> CadastralLayer:
    global _LAYER
    if _LAYER is None:
        _LAYER = CadastralLayer()
    return _LAYER


def reload_layer() -> CadastralLayer:
    global _LAYER
    _LAYER = CadastralLayer()
    return _LAYER


def link_record(
    *,
    khasra_no: str,
    survey_no: str = "",
    plot_no: str = "",
    record_area_ha: float = 0.0,
) -> dict:
    """Attempt GIS linkage for one record and compute the spatial mismatch.

    Returns the FR-9 output contract: linked flag, geometry, areas, delta and
    match type. BR-9 consumes `area_delta_pct`.
    """
    feature, match_type = layer().match(khasra_no, survey_no, plot_no)
    if feature is None:
        return {
            "linked": False,
            "match_type": "none",
            "plot_key": "",
            "geojson": {},
            "polygon_area_ha": 0.0,
            "record_area_ha": round(record_area_ha, 6),
            "area_delta_pct": 0.0,
            "spatial_mismatch": False,
            "message": (
                f"No cadastral polygon indexed for khasra '{khasra_no}'"
                + (f" / survey '{survey_no}'" if survey_no else "")
                + " in the sample layer."
            ),
        }

    geometry = feature.get("geometry", {})
    properties = feature.get("properties", {})
    polygon_area = polygon_area_ha(geometry)
    declared_area = float(properties.get("area_ha") or 0.0) or polygon_area
    delta_pct = 0.0
    if record_area_ha > 0:
        delta_pct = (polygon_area - record_area_ha) / record_area_ha * 100.0

    return {
        "linked": True,
        "match_type": match_type,
        "plot_key": str(properties.get("plot_key", "")),
        "village": properties.get("village", ""),
        "tehsil": properties.get("tehsil", ""),
        "geojson": geometry,
        "properties": properties,
        "polygon_area_ha": polygon_area,
        "declared_area_ha": round(declared_area, 6),
        "record_area_ha": round(record_area_ha, 6),
        "area_delta_pct": round(delta_pct, 2),
        "spatial_mismatch": abs(delta_pct) > settings.gis_area_tolerance_pct,
        "source_map_ref": str(properties.get("source_map", "sample_cadastral_layer")),
        "message": (
            f"Cadastral polygon matched via {match_type} "
            f"'{khasra_no or survey_no or plot_no}'; polygon area {polygon_area:.4f} ha vs "
            f"recorded {record_area_ha:.4f} ha ({delta_pct:+.1f}%)."
        ),
    }


def neighbours(plot_key: str, radius_m: float = 400.0) -> list[dict]:
    """Plots whose centroid falls within `radius_m` of the given plot."""
    target = layer().feature(plot_key)
    if target is None:
        return []
    centre = shape(target["geometry"]).centroid
    lat0 = centre.y
    cx, cy = _project(centre.x, centre.y, lat0)
    found = []
    for feature in layer().all_features():
        props = feature.get("properties", {})
        if props.get("plot_key") == plot_key:
            continue
        centroid = shape(feature["geometry"]).centroid
        fx, fy = _project(centroid.x, centroid.y, lat0)
        distance = math.hypot(fx - cx, fy - cy)
        if distance <= radius_m:
            found.append({
                "plot_key": props.get("plot_key"),
                "khasra_no": props.get("khasra_no"),
                "owner": props.get("owner_hint", ""),
                "distance_m": round(distance, 1),
            })
    return sorted(found, key=lambda item: item["distance_m"])
