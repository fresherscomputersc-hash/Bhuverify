"""MIS / monitoring dashboard endpoints (FR-13)."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import security
from app.config import REPORTS_DIR, settings
from app.database import get_db
from app.models import User
from app.services.metrics import dashboard, pending_review_alerts

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("")
def mis_dashboard(user: User = Depends(security.require("dashboard:read")),
                  db: Session = Depends(get_db)):
    payload = dashboard(db)
    payload["scope"] = {
        "state": settings.state,
        "district": settings.district,
        "tier": "prototype",
        "note": "Single-district prototype scope. Pilot tier expands to district-wise and "
                "state-wise rollups without changing this contract.",
    }
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


@router.get("/alerts")
def alerts(user: User = Depends(security.require("dashboard:read")),
           db: Session = Depends(get_db)):
    return {
        "threshold_hours": settings.review_pending_hours_alert,
        "pending_beyond_threshold": pending_review_alerts(db, settings.review_pending_hours_alert),
    }


@router.get("/export.csv")
def export_csv(user: User = Depends(security.require("dashboard:export")),
               db: Session = Depends(get_db)):
    """Flat CSV of the MIS snapshot - what a supervisor actually forwards upward."""
    data = dashboard(db)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["BhuVerify MIS export", settings.district, settings.state,
                     datetime.now(timezone.utc).isoformat()])
    writer.writerow([])
    writer.writerow(["section", "metric", "value"])
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        for metric, value in values.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    writer.writerow([section, f"{metric}.{sub_key}", sub_value])
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        writer.writerow([section, metric, "; ".join(f"{k}={v}" for k, v in item.items())])
            else:
                writer.writerow([section, metric, value])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"bhuverify_mis_{stamp}.csv"
    path.write_text(buffer.getvalue(), encoding="utf-8")

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )
