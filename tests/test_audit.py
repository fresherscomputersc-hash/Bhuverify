"""Audit hash chain: append, verify, tamper detection (SRS FR-12)."""
from app.models import ActionType
from app.services.audit import log_action, verify_chain


def test_audit_chain_verifies(db):
    log_action(db, ActionType.API_ACCESSED, actor_label="t", entity_type="x", entity_id="1")
    log_action(db, ActionType.API_ACCESSED, actor_label="t", entity_type="x", entity_id="2")
    report = verify_chain(db)
    assert report["chain_intact"] is True
    assert report["entries_checked"] == 2


def test_audit_chain_detects_tampering(db):
    e1 = log_action(db, ActionType.API_ACCESSED, actor_label="t", entity_type="x", entity_id="1")
    log_action(db, ActionType.API_ACCESSED, actor_label="t", entity_type="x", entity_id="2")
    e1.new_value = "tampered"
    db.commit()
    report = verify_chain(db)
    assert report["chain_intact"] is False
    assert report["broken_at_log_id"] == e1.id
