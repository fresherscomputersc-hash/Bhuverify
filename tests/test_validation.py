"""BR-1 .. BR-10: each rule fires when it should and stays quiet when it should not."""
from app import master_data
from app.config import settings
from app.models import LandRecord, RecordStatus
from app.services.extraction import SubPlot
from app.services.validation import (
    RULE_CATALOG,
    RecordContext,
    br1_mandatory_fields,
    br2_subplot_area_sum,
    br3_duplicate_identifiers,
    br4_chronological_validity,
    br5_mutation_chain,
    br6_unit_normalisation,
    br7_admin_hierarchy,
    br8_owner_fuzzy_match,
    br9_boundary_consistency,
    br10_registration_format,
    highest_severity,
    run_all_rules,
)


def _ctx(db, fields, **kw):
    from app.models import DocumentStatus, SourceDocument
    import uuid

    record = kw.pop("record", None)
    if record is None:
        doc = SourceDocument(
            doc_id=f"DOC-T-{uuid.uuid4().hex[:8].upper()}",
            original_filename="test.png",
            stored_path="/tmp/test.png",
            file_hash=uuid.uuid4().hex,
            status=DocumentStatus.QUEUED,
        )
        db.add(doc)
        db.flush()
        record = LandRecord(
            record_id=f"LR-{uuid.uuid4().hex[:8].upper()}",
            document_id=doc.id,
            khasra_no="",
            status=RecordStatus.PENDING_REVIEW,
        )
        db.add(record)
        db.flush()
    elif record.id is None:
        if not getattr(record, "document_id", None):
            doc = SourceDocument(
                doc_id=f"DOC-T-{uuid.uuid4().hex[:8].upper()}",
                original_filename="test.png",
                stored_path="/tmp/test.png",
                file_hash=uuid.uuid4().hex,
                status=DocumentStatus.QUEUED,
            )
            db.add(doc)
            db.flush()
            record.document_id = doc.id
        db.add(record)
        db.flush()
    return RecordContext(db=db, record=record, fields=fields, **kw)


def _mk_record(db, **kw):
    """Create a LandRecord with a backing SourceDocument (NOT NULL FK)."""
    import uuid

    from app.models import DocumentStatus, SourceDocument

    doc = SourceDocument(
        doc_id=f"DOC-T-{uuid.uuid4().hex[:8].upper()}",
        original_filename="test.png",
        stored_path="/tmp/test.png",
        file_hash=uuid.uuid4().hex,
        status=DocumentStatus.QUEUED,
    )
    db.add(doc)
    db.flush()
    kw.setdefault("status", RecordStatus.PENDING_REVIEW)
    kw.setdefault("record_id", f"LR-{uuid.uuid4().hex[:8].upper()}")
    rec = LandRecord(document_id=doc.id, **kw)
    db.add(rec)
    db.flush()
    return rec


def _f(value, normalized=None, confidence=85.0, **kw):
    norm = normalized if normalized is not None else value
    return {
        "value": value,
        "normalized_value": norm,
        "confidence": confidence,
        "bbox": {},
        "evidence_text": value,
        "signals": kw.get("signals", {}),
    }


def _full_fields(**over):
    base = {
        "owner_name": _f("Prafulla Kumar Sahoo"),
        "khasra_no": _f("118/2"),
        "khata_no": _f("204"),
        "village": _f("Balarampur"),
        "tehsil": _f("Khordha Sadar"),
        "district": _f("Khordha"),
        "area": _f("1.14 acre", "1.14", signals={"area_unit_detected": "acre"}),
        "land_classification": _f("Irrigated land"),
        "mutation_date": _f("04/01/2020"),
        "registration_no": _f("1234 of 2019"),
    }
    base.update(over)
    return base


def test_catalog_has_ten_rules():
    assert len(RULE_CATALOG) == 10
    assert [r["id"] for r in RULE_CATALOG] == [f"BR-{i}" for i in range(1, 11)]


# BR-1 -----------------------------------------------------------------------
def test_br1_passes_when_complete(db):
    assert br1_mandatory_fields(_ctx(db, _full_fields())) == []


def test_br1_fires_on_missing_owner(db):
    fields = _full_fields(owner_name=_f("", ""))
    out = br1_mandatory_fields(_ctx(db, fields))
    assert len(out) == 1 and out[0].rule_id == "BR-1"
    assert out[0].severity == "critical"


def test_br1_fires_on_missing_classification(db):
    fields = _full_fields(land_classification=_f("", ""))
    out = br1_mandatory_fields(_ctx(db, fields))
    assert out and out[0].rule_id == "BR-1"


# BR-2 -----------------------------------------------------------------------
def test_br2_passes_when_no_parent_area(db):
    rec = _mk_record(db, record_id="LR-P", area_hectare=0.0)
    assert br2_subplot_area_sum(_ctx(db, _full_fields(), record=rec)) == []


def test_br2_fires_on_subplot_mismatch(db):
    rec = _mk_record(db, record_id="LR-P2", area_hectare=1.0)
    subs = [
        SubPlot("340/1", 0.6, "acre", 0.2428, "ev"),
        SubPlot("340/2", 0.6, "acre", 0.2428, "ev"),
    ]
    ctx = _ctx(db, _full_fields(khasra_no=_f("340")), record=rec, sub_plots=subs)
    out = br2_subplot_area_sum(ctx)
    assert any(d.rule_id == "BR-2" for d in out)


def test_br2_passes_when_subplots_sum(db):
    rec = _mk_record(db, record_id="LR-P3", area_hectare=0.4856)
    subs = [
        SubPlot("340/1", 0.6, "acre", 0.2428, "ev"),
        SubPlot("340/2", 0.6, "acre", 0.2428, "ev"),
    ]
    ctx = _ctx(db, _full_fields(khasra_no=_f("340")), record=rec, sub_plots=subs)
    assert br2_subplot_area_sum(ctx) == []


# BR-3 -----------------------------------------------------------------------
def test_br3_passes_with_no_conflict(db):
    assert br3_duplicate_identifiers(_ctx(db, _full_fields(khasra_no=_f("999/9")))) == []


def test_br3_critical_on_conflicting_owner(db):
    _mk_record(db, record_id="LR-OLD", khasra_no="118/2", owner_name="Someone Else")
    db.flush()
    out = br3_duplicate_identifiers(_ctx(db, _full_fields()))
    assert out and out[0].rule_id == "BR-3" and out[0].severity == "critical"


def test_br3_medium_on_same_owner_repeat(db):
    _mk_record(db, record_id="LR-OLD2", khasra_no="118/2",
               owner_name="Prafulla Kumar Sahoo")
    db.flush()
    out = br3_duplicate_identifiers(_ctx(db, _full_fields()))
    assert out and out[0].severity == "medium"


# BR-4 -----------------------------------------------------------------------
def test_br4_passes_on_valid_dates(db):
    assert br4_chronological_validity(_ctx(db, _full_fields())) == []


def test_br4_fires_on_unparseable_date(db):
    out = br4_chronological_validity(_ctx(db, _full_fields(mutation_date=_f("not-a-date"))))
    assert any(d.rule_id == "BR-4" for d in out)


def test_br4_fires_on_future_date(db):
    out = br4_chronological_validity(_ctx(db, _full_fields(mutation_date=_f("01/01/2999"))))
    assert any("future" in d.message for d in out)


def test_br4_fires_when_registration_after_mutation(db):
    fields = _full_fields(mutation_date=_f("04/01/2018"), registration_no=_f("1234 of 2019"))
    assert any(d.rule_id == "BR-4" for d in br4_chronological_validity(_ctx(db, fields)))


# BR-5 -----------------------------------------------------------------------
def test_br5_passes_with_no_mutation(db):
    fields = _full_fields(previous_owner=_f("", ""), new_owner=_f("", ""))
    assert br5_mutation_chain(_ctx(db, fields)) == []


def test_br5_fires_when_new_owner_differs(db):
    fields = _full_fields(previous_owner=_f("A"), new_owner=_f("Totally Different Person"))
    assert any(d.rule_id == "BR-5" for d in br5_mutation_chain(_ctx(db, fields)))


# BR-6 -----------------------------------------------------------------------
def test_br6_passes_on_known_unit(db):
    assert br6_unit_normalisation(_ctx(db, _full_fields())) == []


def test_br6_fires_on_unknown_unit(db):
    out = br6_unit_normalisation(_ctx(db, _full_fields(area=_f("5 smoots"))))
    assert any(d.rule_id == "BR-6" for d in out)


def test_br6_fires_on_missing_unit(db):
    out = br6_unit_normalisation(_ctx(db, _full_fields(area=_f("5"))))
    assert any(d.rule_id == "BR-6" for d in out)


# BR-7 -----------------------------------------------------------------------
def test_br7_passes_on_master_village(db):
    assert br7_admin_hierarchy(_ctx(db, _full_fields())) == []


def test_br7_fires_on_unknown_village(db):
    out = br7_admin_hierarchy(_ctx(db, _full_fields(village=_f("Janakpur"))))
    assert any(d.rule_id == "BR-7" for d in out)


def test_br7_fires_on_hierarchy_mismatch(db):
    fields = _full_fields(village=_f("Balarampur"), tehsil=_f("Jankia"))
    out = br7_admin_hierarchy(_ctx(db, fields))
    assert any("belongs to tehsil" in d.message for d in out)


# BR-8 -----------------------------------------------------------------------
def test_br8_passes_with_no_neighbours(db):
    assert br8_owner_fuzzy_match(_ctx(db, _full_fields())) == []


def test_br8_fires_on_spelling_variant(db):
    _mk_record(db, record_id="LR-V", owner_name="Prafull Kumar Sahoo",
               village="Balarampur")
    db.flush()
    out = br8_owner_fuzzy_match(_ctx(db, _full_fields()))
    assert any(d.rule_id == "BR-8" for d in out)


# BR-9 -----------------------------------------------------------------------
def test_br9_passes_when_unlinked(db):
    assert br9_boundary_consistency(_ctx(db, _full_fields(), geometry=None)) == []


def test_br9_passes_within_tolerance(db):
    geom = {"linked": True, "polygon_area_ha": 1.0, "record_area_ha": 1.05,
            "area_delta_pct": 5.0, "plot_key": "K"}
    assert br9_boundary_consistency(_ctx(db, _full_fields(), geometry=geom)) == []


def test_br9_fires_beyond_tolerance(db):
    geom = {"linked": True, "polygon_area_ha": 1.5, "record_area_ha": 1.0,
            "area_delta_pct": 50.0, "plot_key": "K"}
    out = br9_boundary_consistency(_ctx(db, _full_fields(), geometry=geom))
    assert out and out[0].rule_id == "BR-9" and out[0].severity == "high"


# BR-10 ----------------------------------------------------------------------
def test_br10_passes_on_valid_refs(db):
    for ref in ("1234 of 2019", "BBSR-2311-2019"):
        assert br10_registration_format(_ctx(db, _full_fields(registration_no=_f(ref)))) == []


def test_br10_fires_on_garbage(db):
    out = br10_registration_format(_ctx(db, _full_fields(registration_no=_f("REG-X-99-13"))))
    assert out and out[0].rule_id == "BR-10"


def test_br10_passes_when_blank(db):
    assert br10_registration_format(_ctx(db, _full_fields(registration_no=_f("", "")))) == []


# runner ---------------------------------------------------------------------
def test_run_all_rules_returns_ten_outcomes(db):
    outcomes = run_all_rules(_ctx(db, _full_fields(khasra_no=_f("999/9"))))
    assert len(outcomes) == 10
    assert all(o.latency_ms >= 0 for o in outcomes)


def test_highest_severity_ranking():
    from app.services.validation import Discrepancy

    def d(sev):
        return Discrepancy("BR-1", "n", sev, "m")

    assert highest_severity([]) == ""
    assert highest_severity([d("low"), d("high"), d("medium")]) == "high"
    assert highest_severity([d("medium"), d("critical")]) == "critical"


def test_master_lookup_indic_alias():
    assert master_data.lookup_village("बलरामपुर", "खुर्दा सदर")["status"] == "ok"
    assert settings.gis_area_tolerance_pct == 15.0
