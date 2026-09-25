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


def test_concurrent_writers_do_not_lock(db):
    """Worker thread + request threads share one SQLite writer lock.

    Regression test for 'sqlite3.OperationalError: database is locked':
    concurrent appends must all land with the hash chain intact.
    """
    import concurrent.futures

    from app.database import SessionLocal

    def _write_batch(n):
        with SessionLocal() as session:
            for i in range(10):
                log_action(
                    session, ActionType.API_ACCESSED, actor_label=f"w{n}",
                    entity_type="x", entity_id=f"{n}-{i}",
                )
        return n

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert sorted(pool.map(_write_batch, range(8))) == list(range(8))

    report = verify_chain(db)
    assert report["chain_intact"] is True
    assert report["entries_checked"] == 80
