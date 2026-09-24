"""
Builds the sample cadastral GeoJSON layer used by FR-9.

Polygons are laid out as an irregular village plot grid near Bhubaneswar,
Khordha district, Odisha (EPSG:4326). Each polygon is sized so that the area
the GIS service computes matches the scenario the demo needs - including one
deliberate mismatch so BR-9 fires.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from app.config import GEOJSON_DIR, settings

M_PER_DEG_LAT = 111_320.0
ORIGIN_LON, ORIGIN_LAT = 85.8245, 20.2961  # Bhubaneswar, Khordha


def box_for_area(target_ha: float, lon: float, lat: float, aspect: float = 1.25) -> list[list[float]]:
    """Return a rectangle (lon/lat ring) covering `target_ha` hectares at (lon, lat)."""
    target_sqm = target_ha * 10_000.0
    height_m = math.sqrt(target_sqm / aspect)
    width_m = target_sqm / height_m
    dlat = height_m / M_PER_DEG_LAT
    dlon = width_m / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return [
        [round(lon, 7), round(lat, 7)],
        [round(lon + dlon, 7), round(lat, 7)],
        [round(lon + dlon, 7), round(lat + dlat, 7)],
        [round(lon, 7), round(lat + dlat, 7)],
        [round(lon, 7), round(lat, 7)],
    ]


# plot_key, khasra, survey, plot, village, tehsil, area_ha, owner hint, grid slot
PLOTS: list[dict] = [
    {"plot_key": "BLR-118-2", "khasra_no": "118/2", "survey_no": "118", "plot_no": "2",
     "village": "Balarampur", "tehsil": "Khordha Sadar", "area_ha": 0.4613,
     "owner_hint": "Prafulla Kumar Sahoo"},
    {"plot_key": "BLR-118-4", "khasra_no": "118/4", "survey_no": "118", "plot_no": "4",
     "village": "Balarampur", "tehsil": "Khordha Sadar", "area_ha": 0.3035,
     "owner_hint": "Prafulla Kumar Sahoo"},
    {"plot_key": "BLR-88-1", "khasra_no": "88/1", "survey_no": "88", "plot_no": "1",
     "village": "Balarampur", "tehsil": "Khordha Sadar", "area_ha": 0.2428,
     "owner_hint": "Prafulla Kumar Sahoo"},
    # Deliberate spatial mismatch: polygon is 0.62 ha, record says 0.4047 ha (BR-9)
    {"plot_key": "GOL-227-1", "khasra_no": "227/1", "survey_no": "227", "plot_no": "1",
     "village": "Golabai", "tehsil": "Nirakarpur", "area_ha": 0.6200,
     "owner_hint": "Sunita Pradhan"},
    {"plot_key": "JNK-340", "khasra_no": "340", "survey_no": "340", "plot_no": "",
     "village": "Jankia", "tehsil": "Jankia", "area_ha": 1.2141,
     "owner_hint": "Ramesh Chandra Behera"},
    {"plot_key": "JNK-340-1", "khasra_no": "340/1", "survey_no": "340", "plot_no": "1",
     "village": "Jankia", "tehsil": "Jankia", "area_ha": 0.6000,
     "owner_hint": "Ramesh Chandra Behera"},
    {"plot_key": "JNK-340-2", "khasra_no": "340/2", "survey_no": "340", "plot_no": "2",
     "village": "Jankia", "tehsil": "Jankia", "area_ha": 0.5000,
     "owner_hint": "Ramesh Chandra Behera"},
    {"plot_key": "PLP-512-3", "khasra_no": "512/3", "survey_no": "512", "plot_no": "3",
     "village": "Pipli", "tehsil": "Nirakarpur", "area_ha": 0.6071,
     "owner_hint": "Minati Sahu"},
    {"plot_key": "BNP-19-7", "khasra_no": "19/7", "survey_no": "19", "plot_no": "7",
     "village": "Banapur", "tehsil": "Banapur", "area_ha": 0.3500,
     "owner_hint": "Bhagaban Nayak"},
    {"plot_key": "JTN-77-5", "khasra_no": "77/5", "survey_no": "77", "plot_no": "5",
     "village": "Jatni", "tehsil": "Jatni", "area_ha": 0.9000,
     "owner_hint": "Sarat Kumar Das"},
    {"plot_key": "BGN-402-1", "khasra_no": "402/1", "survey_no": "402", "plot_no": "1",
     "village": "Begunia", "tehsil": "Begunia", "area_ha": 1.0500,
     "owner_hint": "Laxmidhar Prusty"},
    {"plot_key": "TNG-63-9", "khasra_no": "63/9", "survey_no": "63", "plot_no": "9",
     "village": "Tangi", "tehsil": "Tangi", "area_ha": 0.7800,
     "owner_hint": "Gitanjali Mohanty"},
]

SLOT_OFFSETS = [
    (0, 0), (1, 0), (2, 0),
    (0, 1), (1, 1), (2, 1), (3, 1),
    (0, 2), (1, 2), (2, 2),
    (0, 3), (1, 3),
]


def build_geojson() -> dict:
    features = []
    for index, plot in enumerate(PLOTS):
        col, row = SLOT_OFFSETS[index % len(SLOT_OFFSETS)]
        # irregular grid so the map looks like a real village layout
        lon = ORIGIN_LON + col * 0.0028 + (0.0004 if row % 2 else 0.0)
        lat = ORIGIN_LAT + row * 0.0022
        features.append({
            "type": "Feature",
            "id": plot["plot_key"],
            "properties": {
                "plot_key": plot["plot_key"],
                "khasra_no": plot["khasra_no"],
                "survey_no": plot["survey_no"],
                "plot_no": plot["plot_no"],
                "village": plot["village"],
                "tehsil": plot["tehsil"],
                "district": settings.district,
                "state": settings.state,
                "area_ha": plot["area_ha"],
                "owner_hint": plot["owner_hint"],
                "source_map": "BhuNaksha sample cadastral layer (prototype)",
                "map_sheet": f"KH-{col + 1:02d}-{row + 1:02d}",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [box_for_area(plot["area_ha"], lon, lat)],
            },
        })
    return {
        "type": "FeatureCollection",
        "name": "bhuverify_sample_cadastral",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }


def write_sample_layer(path: Path | None = None) -> Path:
    target = Path(path or (GEOJSON_DIR / settings.geojson_file))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_geojson(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


if __name__ == "__main__":  # pragma: no cover
    written = write_sample_layer()
    print(f"wrote {written}")
