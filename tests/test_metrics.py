"""Metrics timezone regression: naive SQLite timestamps must not crash alerts."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models import DocumentStatus, LandRecord, RecordStatus, SourceDocument
from app.models import Discrepancy as DiscrepancyRow
from app.services.metrics import dashboard, pending_review_alerts


def _mk_record(db, **kw):
    doc = SourceDocument(
        doc_id=f"DOC-T-{uuid.uuid4().hex[:8].upper()}",
        original_filename="t.png", stored_path="/tmp/t.png",
        file_hash=uuid.uuid4().hex, status=DocumentStatus.QUEUED,
    )
    db.add(doc)
    db.flush()
    kw.setdefault("status", RecordStatus.PENDING_REVIEW)
    rec = LandRecord(document_id=doc.id, **kw)
    db.add(rec)
    db.commit()
    return rec


def test_pending_alerts_handles_naive_timestamps(db):
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=50)
    _mk_record(db, record_id="LR-OLD-P", khasra_no="1/1", village="V", created_at=old)
    out = pending_review_alerts(db, 48)
    assert any(r["record_id"] == "LR-OLD-P" for r in out)
    assert out[0]["hours_pending"] >= 48


def test_dashboard_runs_on_empty_db(db):
    data = dashboard(db)
    assert data["documents"]["uploaded"] == 0
    assert data["records"]["total"] == 0
    assert data["performance"]["within_budget"] is True
