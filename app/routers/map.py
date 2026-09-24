"""GIS / cadastral map endpoints (FR-9)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import security
from app.database import get_db
from app.models import LandRecord, SpatialGeometry, User
from app.services.gis_service import layer, neighbours, polygon_area_ha

router = APIRouter(prefix="/api/v1/map", tags=["gis"])


@router.get("/layer")
def full_layer(user: User = Depends(security.require("map:read"))):
    cadastral = layer()
    return {
        "type": "FeatureCollection",
        "name": "bhuverify_sample_cadastral",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": cadastral.all_features(),
        "summary": cadastral.summary(),
    }


@router.get("/summary")
def summary(user: User = Depends(security.require("map:read")),
            db: Session = Depends(get_db)):
    cadastral = layer()
    linked = db.execute(
        select(func.count(LandRecord.id)).where(LandRecord.gis_linked.is_(True))
    ).scalar() or 0
    total = db.execute(select(func.count(LandRecord.id))).scalar() or 0
    return {
        "layer": cadastral.summary(),
        "linked_records": linked,
        "total_records": total,
        "linkable_keys": sorted({key.split(":")[-1] for key in cadastral.index})[:50],
    }


@router.get("/plot/{plot_key}/geometry")
def plot_geometry(plot_key: str, user: User = Depends(security.require("map:read")),
                  db: Session = Depends(get_db)):
    feature = layer().feature(plot_key)
    if feature is None:
        raise HTTPException(status_code=404, detail=f"Plot '{plot_key}' is not in the sample layer.")

    geometry_row = db.execute(
        select(SpatialGeometry).where(SpatialGeometry.plot_key == plot_key).limit(1)
    ).scalar_one_or_none()
    record = db.get(LandRecord, geometry_row.record_id) if geometry_row else None
    geometry = feature.get("geometry", {})
    return {
        "plot_key": plot_key,
        "properties": feature.get("properties", {}),
        "geometry": geometry,
        "computed_area_ha": polygon_area_ha(geometry),
        "neighbours": neighbours(plot_key),
        "linked_record": (
            {
                "record_id": record.record_id,
                "khasra_no": record.khasra_no,
                "owner_name": record.owner_name,
                "record_area_ha": record.area_hectare,
                "status": record.status.value,
            }
            if record else None
        ),
    }


@router.get("/record/{record_id}")
def record_geometry(record_id: str, user: User = Depends(security.require("map:read")),
                    db: Session = Depends(get_db)):
    record = db.execute(
        select(LandRecord).where(LandRecord.record_id == record_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    if record.geometry is None:
        return {"linked": False, "record_id": record_id,
                "message": "No cadastral polygon is linked to this record."}
    return {
        "linked": True,
        "record_id": record_id,
        "plot_key": record.geometry.plot_key,
        "geometry": record.geometry.geojson,
        "area_sqm": record.geometry.area_sqm,
        "match_type": record.geometry.match_type,
        "spatial_mismatch": record.geometry.spatial_mismatch,
        "area_delta_pct": record.geometry.area_delta_pct,
        "neighbours": neighbours(record.geometry.plot_key),
    }
