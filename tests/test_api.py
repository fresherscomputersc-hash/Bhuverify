"""API acceptance + RBAC (SRS section 11 style, over HTTP)."""
import io

from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app
from app.models import Role
from app.security import hash_password
from app.models import User

client = TestClient(app, raise_server_exceptions=False)


def _seed_user(db, username="reviewer_t", role=Role.REVIEWER):
    init_db()
    from sqlalchemy import select

    from app.database import SessionLocal

    with SessionLocal() as s:
        u = s.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if u is None:
            u = User(username=username, password_hash=hash_password("pw123456"),
                     full_name="Test", role=role, designation="t", district_scope="Khordha")
            s.add(u)
            s.commit()
    return username


def _login(username, password="pw123456"):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_health_ok():
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_rejects_bad_credentials():
    r = client.post("/api/v1/auth/login", json={"username": "nope", "password": "bad"})
    assert r.status_code == 401


def test_cookie_auth_covers_img_tags(db):
    """Document previews load via <img>, which cannot send Authorization
    headers - the API must accept the bhuverify_token cookie instead."""
    _seed_user(db, "cookie_t", Role.REVIEWER)
    r = client.post("/api/v1/auth/login",
                    json={"username": "cookie_t", "password": "pw123456"})
    assert r.status_code == 200
    token = r.json()["token"]
    client.cookies.set("bhuverify_token", token)
    try:
        assert client.get("/api/v1/auth/me").status_code == 200
    finally:
        client.cookies.clear()


def test_rbac_operator_cannot_read_audit(db):
    _seed_user(db, "op_t", Role.OPERATOR)
    token = _login("op_t")
    r = client.get("/api/v1/audit", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_validate_endpoint_runs_all_rules(db):
    _seed_user(db, "rev_t", Role.REVIEWER)
    token = _login("rev_t")
    payload = {"fields": {
        "owner_name": "Prafulla Kumar Sahoo", "khasra_no": "999/9",
        "khata_no": "204", "village": "Balarampur", "tehsil": "Khordha Sadar",
        "district": "Khordha", "area": "1 acre", "land_classification": "Irrigated land",
        "registration_no": "1234 of 2019", "mutation_date": "04/01/2020",
    }}
    r = client.post("/api/v1/records/validate", json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "passed" in body and "failed" in body
    assert body["total_latency_ms"] < 1000  # SRS 7.1 validation budget


def test_upload_rejects_unsupported_extension(db):
    _seed_user(db, "op2_t", Role.OPERATOR)
    token = _login("op2_t")
    r = client.post("/api/v1/documents", files={"files": ("evil.exe", io.BytesIO(b"x"), "application/octet-stream")},
                    data={"doc_type": "ror", "language": "eng+hin+ori"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


def test_search_requires_auth():
    r = client.get("/api/v1/records?q=test")
    assert r.status_code == 401


def test_system_status_requires_auth():
    r = client.get("/api/v1/system/status")
    assert r.status_code == 401


def test_reupload_allowed_after_reject(db):
    """Same bytes are dedup-skipped while live, but accepted again once the
    previous attempt died (record rejected)."""
    import io
    import uuid

    from PIL import Image

    from app.models import DocumentStatus, RecordStatus, SourceDocument

    _seed_user(db, "opu_t", Role.OPERATOR)
    token = _login("opu_t")
    headers = {"Authorization": f"Bearer {token}"}

    buf = io.BytesIO()
    Image.new("RGB", (120, 60), "white").save(buf, "PNG")
    payload = buf.getvalue()

    def _upload(sync="true"):
        return client.post(
            "/api/v1/documents",
            files={"files": ("retry.png", io.BytesIO(payload), "image/png")},
            data={"doc_type": "ror", "language": "eng", "process_sync": sync},
            headers=headers,
        )

    # synchronous: returns after the full pipeline, so the record exists
    r1 = _upload("true")
    assert r1.status_code == 200, r1.text
    assert len(r1.json()["accepted"]) == 1
    doc_id = r1.json()["accepted"][0]

    r2 = _upload()
    assert r2.status_code == 200
    assert len(r2.json()["skipped_duplicates"]) == 1

    from sqlalchemy import select

    from app.database import SessionLocal

    with SessionLocal() as s:
        doc = s.execute(
            select(SourceDocument).where(SourceDocument.doc_id == doc_id)
        ).scalar_one()
        assert doc.record is not None
        doc.record.status = RecordStatus.REJECTED
        doc.status = DocumentStatus.COMPLETED
        s.commit()

    r3 = _upload()
    assert r3.status_code == 200, r3.text
    assert len(r3.json()["accepted"]) == 1, r3.json()
    assert r3.json()["accepted"][0] != doc_id
    assert len(r3.json()["skipped_duplicates"]) == 0


def test_correction_revalidates_and_unblocks_approve(db):
    """Filling the missing mandatory fields must clear BR-1 so approve works."""
    import uuid

    from app.models import (
        Discrepancy,
        DocumentStatus,
        LandRecord,
        RecordStatus,
        Role,
        Severity,
        SourceDocument,
    )

    _seed_user(db, "fixer_t", Role.REVIEWER)
    token = _login("fixer_t")
    headers = {"Authorization": f"Bearer {token}"}

    doc = SourceDocument(
        doc_id=f"DOC-T-{uuid.uuid4().hex[:8].upper()}",
        original_filename="t.png", stored_path="/tmp/t.png",
        file_hash=uuid.uuid4().hex, status=DocumentStatus.QUEUED,
    )
    db.add(doc)
    db.flush()
    rec = LandRecord(
        record_id=f"LR-{uuid.uuid4().hex[:8].upper()}", document_id=doc.id,
        khasra_no="99/9", khata_no="1", village="Balarampur",
        tehsil="Khordha Sadar", district="Khordha",
        mutation_date="04/01/2020", registration_no="1234 of 2019",
        status=RecordStatus.PENDING_REVIEW,
    )
    db.add(rec)
    db.flush()
    db.add(Discrepancy(
        record_id=rec.id, rule_id="BR-1", rule_name="Mandatory Field Check",
        severity=Severity.CRITICAL, message="missing fields", status="open",
    ))
    # an (empty) AI extraction row, like the real pipeline persists — this is
    # what made the area-alias crash: ExtractionResult has no signals column
    from app.models import ExtractionResult

    db.add(ExtractionResult(
        record_id=rec.id, field_name="area", field_label="Area",
        value="", normalized_value="", confidence=0.0, source="regex",
    ))
    db.commit()

    # blocked before the fix
    r = client.post(f"/api/v1/records/{rec.record_id}/verify",
                    json={"decision": "approve"}, headers=headers)
    assert r.status_code == 409

    # reviewer fills the gaps, including the "area" alias
    r = client.patch(
        f"/api/v1/records/{rec.record_id}",
        json={"corrections": {
            "owner_name": "Test Owner",
            "area": "1 acre",
            "land_classification": "Irrigated land",
        }},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revalidation"]["revalidated"] is True
    assert "BR-1" in body["revalidation"]["passed"]

    # now approval goes through
    r = client.post(f"/api/v1/records/{rec.record_id}/verify",
                    json={"decision": "approve"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
