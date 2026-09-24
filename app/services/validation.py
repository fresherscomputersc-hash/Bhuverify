"""
Business rule validation engine (SRS FR-6) + duplicate detection (FR-7).

Every rule is registered in RULE_CATALOG with a stable ID (BR-1 ... BR-10), a
plain-language purpose and a severity, so the discrepancy report can show the
exact failed rule and a recommended reviewer action (SRS FR-6 outputs).

Rules return zero or more `Discrepancy` dataclasses; nothing here mutates the
record. The worker persists the results and writes one audit row per rule run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from rapidfuzz import fuzz
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import master_data
from app.config import settings
from app.models import Discrepancy as DiscrepancyRow
from app.models import LandRecord, RecordStatus, Severity, SpatialGeometry
from app.services.extraction import normalize_unit, to_hectare

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass
class Discrepancy:
    rule_id: str
    rule_name: str
    severity: str
    message: str
    expected: str = ""
    actual: str = ""
    conflicting_values: dict = field(default_factory=dict)
    evidence_refs: dict = field(default_factory=dict)
    recommended_action: str = ""

    def as_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "conflicting_values": self.conflicting_values,
            "evidence_refs": self.evidence_refs,
            "recommended_action": self.recommended_action,
        }


@dataclass
class RuleOutcome:
    rule_id: str
    rule_name: str
    passed: bool
    discrepancies: list[Discrepancy]
    detail: str = ""
    latency_ms: float = 0.0


RULE_CATALOG: list[dict] = [
    {"id": "BR-1", "name": "Mandatory Field Check",
     "purpose": "Ensures all required land record fields were extracted.",
     "default_severity": "high"},
    {"id": "BR-2", "name": "Sub-Plot Area Sum Check",
     "purpose": "Child (sub-divided) plot areas must sum to the parent plot area.",
     "default_severity": "high"},
    {"id": "BR-3", "name": "Duplicate Survey/Khasra Check",
     "purpose": "The same plot identifier must not be held by conflicting owners.",
     "default_severity": "critical"},
    {"id": "BR-4", "name": "Chronological Validity",
     "purpose": "Mutation and registration dates must be parseable, in the past, and ordered.",
     "default_severity": "high"},
    {"id": "BR-5", "name": "Mutation Chain Continuity",
     "purpose": "The previous owner of a mutation must match the prior record holder.",
     "default_severity": "high"},
    {"id": "BR-6", "name": "Unit Normalisation",
     "purpose": "Local area units (acre/bigha/guntha/kanal/decimal) must convert to a standard unit.",
     "default_severity": "medium"},
    {"id": "BR-7", "name": "Village-Tehsil Master Check",
     "purpose": "Village / tehsil / district must exist in the administrative master and agree.",
     "default_severity": "medium"},
    {"id": "BR-8", "name": "Owner Fuzzy Match",
     "purpose": "Detects spelling variants of the same owner name across records.",
     "default_severity": "medium"},
    {"id": "BR-9", "name": "Boundary / Spatial Consistency",
     "purpose": "Recorded area must agree with the cadastral polygon area within tolerance.",
     "default_severity": "high"},
    {"id": "BR-10", "name": "Registration Format Check",
     "purpose": "Registration reference must match the SRO reference pattern.",
     "default_severity": "medium"},
]

RULE_BY_ID = {rule["id"]: rule for rule in RULE_CATALOG}


# ---------------------------------------------------------------------------
# Record context handed to each rule
# ---------------------------------------------------------------------------
@dataclass
class RecordContext:
    db: Session
    record: LandRecord
    fields: dict                      # field_name -> FieldExtraction-like dict
    geometry: dict | None = None      # GIS service output
    parent_area_hectare: float | None = None
    sub_plots: list | None = None     # child-plot rows read from a register page
    document_id: int = 0
    doc_id: str = ""

    def value(self, name: str) -> str:
        item = self.fields.get(name) or {}
        return (item.get("normalized_value") or item.get("value") or "").strip()

    def raw(self, name: str) -> str:
        item = self.fields.get(name) or {}
        return (item.get("value") or "").strip()

    def confidence(self, name: str) -> float:
        return float((self.fields.get(name) or {}).get("confidence") or 0.0)

    def evidence(self, name: str) -> dict:
        item = self.fields.get(name) or {}
        return {
            "document_id": self.doc_id,
            "field": name,
            "bbox": item.get("bbox") or {},
            "evidence_text": item.get("evidence_text", ""),
            "confidence": round(float(item.get("confidence") or 0.0), 2),
        }


def _evidence_for(ctx: RecordContext, *field_names: str) -> dict:
    return {name: ctx.evidence(name) for name in field_names}


# ---------------------------------------------------------------------------
# BR-1  Mandatory field check
# ---------------------------------------------------------------------------
def br1_mandatory_fields(ctx: RecordContext) -> list[Discrepancy]:
    from app.services.extraction import REQUIRED_FIELDS

    missing = [name for name in REQUIRED_FIELDS if not ctx.value(name)]
    if not missing:
        return []
    labels = {
        "owner_name": "Owner name", "khasra_no": "Khasra number", "khata_no": "Khata number",
        "village": "Village", "tehsil": "Tehsil/Block", "district": "District",
        "area": "Land area", "land_classification": "Land classification",
    }
    return [Discrepancy(
        rule_id="BR-1",
        rule_name=RULE_BY_ID["BR-1"]["name"],
        severity="critical" if {"owner_name", "khasra_no"} & set(missing) else "high",
        message=(
            f"{len(missing)} mandatory field(s) missing: "
            + ", ".join(labels.get(m, m) for m in missing)
        ),
        expected="all mandatory fields present",
        actual=", ".join(labels.get(m, m) for m in missing),
        conflicting_values={"missing_fields": missing},
        evidence_refs=_evidence_for(ctx, *missing),
        recommended_action="Re-check the source image region and enter the missing values manually.",
    )]


# ---------------------------------------------------------------------------
# BR-2  Sub-plot area sum
# ---------------------------------------------------------------------------
def br2_subplot_area_sum(ctx: RecordContext) -> list[Discrepancy]:
    """Child plot areas must reconcile with the parent plot area.

    Two sources are checked:
      * child-plot rows read off the same register page (`ctx.sub_plots`), and
      * separately digitised child records that declare this khasra as parent.
    """
    out: list[Discrepancy] = []
    parent = round(ctx.record.area_hectare or 0.0, 6)
    if not parent:
        return out

    declared = ctx.sub_plots or []
    if declared:
        child_sum = round(sum(p.area_hectare for p in declared), 6)
        tolerance = max(parent * 0.02, 0.0005)
        if abs(child_sum - parent) > tolerance:
            out.append(Discrepancy(
                rule_id="BR-2", rule_name=RULE_BY_ID["BR-2"]["name"], severity="high",
                message=(
                    f"Sub-plot areas listed for khasra {ctx.value('khasra_no')} sum to "
                    f"{child_sum:.4f} ha but the parent plot records {parent:.4f} ha "
                    f"(difference {abs(child_sum - parent):.4f} ha)."
                ),
                expected=f"{parent:.4f} ha", actual=f"{child_sum:.4f} ha",
                conflicting_values={
                    "parent_khasra": ctx.value("khasra_no"),
                    "parent_area_ha": parent,
                    "declared_sub_plots": [
                        {"khasra_no": p.khasra_no, "area": f"{p.area_value} {p.area_unit}",
                         "area_ha": round(p.area_hectare, 4)}
                        for p in declared
                    ],
                    "difference_ha": round(abs(child_sum - parent), 4),
                },
                evidence_refs=_evidence_for(ctx, "area", "khasra_no"),
                recommended_action=(
                    "Verify the subdivision deed and re-measure; the sub-plot areas do not "
                    "reconcile with the parent plot."
                ),
            ))

    children = (
        ctx.db.execute(
            select(LandRecord)
            .where(LandRecord.parent_khasra_no == ctx.value("khasra_no"))
            .where(LandRecord.id != ctx.record.id)
            .where(LandRecord.status != RecordStatus.REJECTED)
        ).scalars().all()
    )
    if children:
        child_sum = round(sum(child.area_hectare or 0.0 for child in children), 6)
        tolerance = max(parent * 0.02, 0.0005)
        if abs(child_sum - parent) > tolerance:
            out.append(Discrepancy(
                rule_id="BR-2", rule_name=RULE_BY_ID["BR-2"]["name"], severity="high",
                message=(
                    f"Digitised sub-plots of khasra {ctx.value('khasra_no')} sum to "
                    f"{child_sum:.4f} ha against a parent area of {parent:.4f} ha."
                ),
                expected=f"{parent:.4f} ha", actual=f"{child_sum:.4f} ha",
                conflicting_values={
                    "parent_khasra": ctx.value("khasra_no"),
                    "parent_area_ha": parent,
                    "child_records": [
                        {"record_id": c.record_id, "khasra_no": c.khasra_no,
                         "area_ha": round(c.area_hectare or 0, 4)}
                        for c in children
                    ],
                    "difference_ha": round(abs(child_sum - parent), 4),
                },
                evidence_refs=_evidence_for(ctx, "area", "khasra_no"),
                recommended_action=(
                    "Reconcile the digitised child records with the parent plot area."
                ),
            ))
    return out


# ---------------------------------------------------------------------------
# BR-3  Duplicate survey / khasra check  (FR-7)
# ---------------------------------------------------------------------------
def br3_duplicate_identifiers(ctx: RecordContext) -> list[Discrepancy]:
    out: list[Discrepancy] = []
    khasra = ctx.value("khasra_no")
    survey = ctx.value("survey_no")
    owner = ctx.value("owner_name")

    query = select(LandRecord).where(LandRecord.id != ctx.record.id)
    if khasra:
        query = query.where(LandRecord.khasra_no == khasra)
    elif survey:
        query = query.where(LandRecord.survey_no == survey)
    else:
        return out
    existing = ctx.db.execute(query).scalars().all()

    for other in existing:
        other_owner = (other.owner_name or "").strip()
        similarity = fuzz.token_sort_ratio(owner.lower(), other_owner.lower()) / 100 if owner and other_owner else 0.0
        same_owner = similarity >= 0.92
        severity = "medium" if same_owner else "critical"
        out.append(Discrepancy(
            rule_id="BR-3",
            rule_name=RULE_BY_ID["BR-3"]["name"],
            severity=severity,
            message=(
                f"{'Same' if same_owner else 'Conflicting'} owner found for khasra "
                f"{khasra or survey}: this record says '{owner}', existing record "
                f"{other.record_id} says '{other_owner}'."
                if not same_owner else
                f"Duplicate entry: khasra {khasra} already digitised in record {other.record_id} "
                f"with the same owner (name similarity {similarity:.0%})."
            ),
            expected="one unique owner per plot identifier",
            actual=f"owner '{owner}' vs owner '{other_owner}'",
            conflicting_values={
                "identifier": khasra or survey,
                "this_record": ctx.record.record_id,
                "existing_record": other.record_id,
                "this_owner": owner,
                "existing_owner": other_owner,
                "name_similarity": round(similarity, 3),
                "existing_status": other.status.value,
            },
            evidence_refs=_evidence_for(ctx, "khasra_no", "owner_name"),
            recommended_action=(
                "Hold approval: same plot identifier held by a different owner is a potential "
                "title conflict. Escalate to the Tehsildar for physical verification."
                if not same_owner else
                "Mark as duplicate and retain only the higher-confidence record."
            ),
        ))
    return out


# ---------------------------------------------------------------------------
# BR-4  Chronological validity
# ---------------------------------------------------------------------------
def _parse_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _registration_year(registration_ref: str) -> int | None:
    """Extract the deed *year* from a registration reference.

    References look like '1234 of 2019' or 'BBSR-2311-2019' - the first number
    is the book/deed number, so only a trailing 4-digit group that reads as a
    plausible year counts. Taking the first group instead would treat book
    number 3311 as a year and report a bogus chronological violation.
    """
    if not registration_ref:
        return None
    explicit = re.search(r"\bof\s*(\d{4})\b", registration_ref, re.IGNORECASE)
    if explicit:
        return int(explicit.group(1))
    candidates = [int(g) for g in re.findall(r"\b(1[89]\d{2}|20\d{2}|21\d{2})\b", registration_ref)]
    return candidates[-1] if candidates else None


def br4_chronological_validity(ctx: RecordContext) -> list[Discrepancy]:
    out: list[Discrepancy] = []
    mutation_raw = ctx.raw("mutation_date")
    registration_raw = ctx.raw("registration_no") or ctx.value("registration_no")
    mutation_date = _parse_date(mutation_raw)
    today = date.today()

    if mutation_raw and mutation_date is None:
        out.append(Discrepancy(
            rule_id="BR-4", rule_name=RULE_BY_ID["BR-4"]["name"], severity="high",
            message=f"Mutation date '{mutation_raw}' is not a parseable date.",
            expected="a valid DD/MM/YYYY date", actual=mutation_raw,
            conflicting_values={"mutation_date": mutation_raw},
            evidence_refs=_evidence_for(ctx, "mutation_date"),
            recommended_action="Re-read the mutation date from the source register.",
        ))
    elif mutation_date and mutation_date > today:
        out.append(Discrepancy(
            rule_id="BR-4", rule_name=RULE_BY_ID["BR-4"]["name"], severity="high",
            message=f"Mutation date {mutation_date.isoformat()} is in the future.",
            expected=f"on or before {today.isoformat()}", actual=mutation_date.isoformat(),
            conflicting_values={"mutation_date": mutation_date.isoformat(), "today": today.isoformat()},
            evidence_refs=_evidence_for(ctx, "mutation_date"),
            recommended_action="Correct the date - a future mutation date cannot be valid.",
        ))

    reg_year = _registration_year(registration_raw)
    if reg_year is not None and mutation_date:
        if reg_year > mutation_date.year:
            out.append(Discrepancy(
                rule_id="BR-4", rule_name=RULE_BY_ID["BR-4"]["name"], severity="high",
                message=(
                    f"Registration reference year {reg_year} is later than the mutation year "
                    f"{mutation_date.year}; mutation cannot precede its registration."
                ),
                expected=f"registration year <= {mutation_date.year}", actual=str(reg_year),
                conflicting_values={
                    "registration_ref": registration_raw,
                    "registration_year": reg_year,
                    "mutation_date": mutation_date.isoformat(),
                },
                evidence_refs=_evidence_for(ctx, "registration_no", "mutation_date"),
                recommended_action="Verify the registration reference and mutation date pair.",
            ))
    return out


# ---------------------------------------------------------------------------
# BR-5  Mutation chain continuity
# ---------------------------------------------------------------------------
def br5_mutation_chain(ctx: RecordContext) -> list[Discrepancy]:
    previous = ctx.value("previous_owner")
    new_owner = ctx.value("new_owner")
    owner = ctx.value("owner_name")
    if not previous and not new_owner:
        return []
    out: list[Discrepancy] = []

    if new_owner and owner:
        similarity = fuzz.token_sort_ratio(new_owner.lower(), owner.lower()) / 100
        if similarity < 0.85:
            out.append(Discrepancy(
                rule_id="BR-5", rule_name=RULE_BY_ID["BR-5"]["name"], severity="high",
                message=(
                    f"Mutation names '{new_owner}' as the new owner, but the record holder "
                    f"is '{owner}' (similarity {similarity:.0%})."
                ),
                expected=f"new owner == record holder ('{owner}')", actual=new_owner,
                conflicting_values={"new_owner": new_owner, "record_holder": owner,
                                    "similarity": round(similarity, 3)},
                evidence_refs=_evidence_for(ctx, "new_owner", "owner_name"),
                recommended_action="Confirm whether the mutation is complete; an incomplete "
                                   "mutation leaves the chain broken.",
            ))

    if previous and new_owner:
        similarity = fuzz.token_sort_ratio(previous.lower(), new_owner.lower()) / 100
        if similarity > 0.95:
            out.append(Discrepancy(
                rule_id="BR-5", rule_name=RULE_BY_ID["BR-5"]["name"], severity="high",
                message=f"Mutation records the same person as both previous and new owner ('{previous}').",
                expected="previous owner != new owner", actual=previous,
                conflicting_values={"previous_owner": previous, "new_owner": new_owner},
                evidence_refs=_evidence_for(ctx, "previous_owner", "new_owner"),
                recommended_action="Re-read the transferor/transferee columns of the register.",
            ))

    if previous:
        # a broken chain: prior holder exists elsewhere on the same plot
        prior = ctx.db.execute(
            select(LandRecord)
            .where(LandRecord.owner_name.ilike(previous))
            .where(LandRecord.khasra_no == ctx.value("khasra_no"))
            .where(LandRecord.id != ctx.record.id)
        ).scalars().first()
        if prior is None and ctx.value("khasra_no"):
            out.append(Discrepancy(
                rule_id="BR-5", rule_name=RULE_BY_ID["BR-5"]["name"], severity="medium",
                message=(
                    f"Previous owner '{previous}' has no earlier digitised record for khasra "
                    f"{ctx.value('khasra_no')}; ownership chain cannot be traced back."
                ),
                expected="a prior record naming the previous owner for the same plot",
                actual="no prior record found",
                conflicting_values={"previous_owner": previous, "khasra_no": ctx.value("khasra_no")},
                evidence_refs=_evidence_for(ctx, "previous_owner"),
                recommended_action="Digitise the predecessor record or confirm this is the first entry.",
            ))
    return out


# ---------------------------------------------------------------------------
# BR-6  Unit normalisation
# ---------------------------------------------------------------------------
def br6_unit_normalisation(ctx: RecordContext) -> list[Discrepancy]:
    raw_area = ctx.raw("area")
    if not raw_area:
        return []
    unit_match = re.search(
        r"\d+(?:\.\d+)?\s*([A-Za-z\u0900-\u097F\u0B00-\u0B7F]+)", raw_area
    )
    unit = unit_match.group(1) if unit_match else ""
    normalised = normalize_unit(unit)
    hectares, recognised = to_hectare(float(re.search(r"\d+(?:\.\d+)?", raw_area).group()), unit) \
        if re.search(r"\d+(?:\.\d+)?", raw_area) else (0.0, False)
    out: list[Discrepancy] = []

    if unit and not recognised:
        out.append(Discrepancy(
            rule_id="BR-6", rule_name=RULE_BY_ID["BR-6"]["name"], severity="medium",
            message=f"Area unit '{unit}' is not a known land measurement unit; area cannot be standardised.",
            expected="a known unit (acre, hectare, decimal, bigha, katha, guntha, kanal...)",
            actual=unit,
            conflicting_values={"raw_area": raw_area, "unit": unit, "normalised_unit": normalised},
            evidence_refs=_evidence_for(ctx, "area"),
            recommended_action="Identify the local unit used in the register and add it to the unit table.",
        ))
    if not unit:
        out.append(Discrepancy(
            rule_id="BR-6", rule_name=RULE_BY_ID["BR-6"]["name"], severity="medium",
            message=f"Area '{raw_area}' has no unit; standardised area cannot be computed.",
            expected="value with an explicit unit", actual=raw_area,
            conflicting_values={"raw_area": raw_area},
            evidence_refs=_evidence_for(ctx, "area"),
            recommended_action="Confirm the unit from the register header (Odisha RoR is usually decimal/acre).",
        ))
    if recognised and hectares > 50:
        out.append(Discrepancy(
            rule_id="BR-6", rule_name=RULE_BY_ID["BR-6"]["name"], severity="low",
            message=f"Standardised area {hectares:.4f} ha is unusually large for a single plot.",
            expected="< 50 ha for a typical plot", actual=f"{hectares:.4f} ha",
            conflicting_values={"raw_area": raw_area, "hectares": hectares},
            evidence_refs=_evidence_for(ctx, "area"),
            recommended_action="Confirm this is a single plot and not a whole-mouza aggregate.",
        ))
    return out


# ---------------------------------------------------------------------------
# BR-7  Village-tehsil master check
# ---------------------------------------------------------------------------
def br7_admin_hierarchy(ctx: RecordContext) -> list[Discrepancy]:
    village = ctx.value("village")
    tehsil = ctx.value("tehsil")
    district = ctx.value("district")
    result = master_data.lookup_village(village, tehsil)
    out: list[Discrepancy] = []

    if result["status"] == "hierarchy_mismatch":
        out.append(Discrepancy(
            rule_id="BR-7", rule_name=RULE_BY_ID["BR-7"]["name"], severity="medium",
            message=(
                f"Village '{village}' belongs to tehsil '{result['expected_tehsil']}' in the "
                f"administrative master, but the record states '{tehsil}'."
            ),
            expected=result["expected_tehsil"], actual=tehsil,
            conflicting_values={
                "village": village, "stated_tehsil": tehsil,
                "master_tehsil": result["expected_tehsil"], "district": district,
            },
            evidence_refs=_evidence_for(ctx, "village", "tehsil"),
            recommended_action="Correct the tehsil/block to the value in the LGD master data.",
        ))
    elif result["status"] == "not_in_master":
        out.append(Discrepancy(
            rule_id="BR-7", rule_name=RULE_BY_ID["BR-7"]["name"], severity="medium",
            message=f"Village '{village}' is not present in the {master_data.DISTRICT} administrative master.",
            expected="a village listed in the LGD master", actual=village,
            conflicting_values={"village": village, "master_source": "LGD demo subset"},
            evidence_refs=_evidence_for(ctx, "village"),
            recommended_action="Check spelling (OCR may have corrupted the name) or confirm the district scope.",
        ))
    canonical_district = master_data.canonical_district(district)
    if canonical_district and canonical_district.strip().lower() != master_data.DISTRICT.lower():
        out.append(Discrepancy(
            rule_id="BR-7", rule_name=RULE_BY_ID["BR-7"]["name"], severity="low",
            message=f"District '{district}' is outside the configured scope ({master_data.DISTRICT}).",
            expected=master_data.DISTRICT, actual=district,
            conflicting_values={"district": district},
            evidence_refs=_evidence_for(ctx, "district"),
            recommended_action="Route the record to the correct district office.",
        ))
    return out


# ---------------------------------------------------------------------------
# BR-8  Owner fuzzy match
# ---------------------------------------------------------------------------
def br8_owner_fuzzy_match(ctx: RecordContext) -> list[Discrepancy]:
    owner = ctx.value("owner_name")
    if not owner or len(owner) < 4:
        return []
    candidates = ctx.db.execute(
        select(LandRecord)
        .where(LandRecord.id != ctx.record.id)
        .where(LandRecord.owner_name != "")
        .where(LandRecord.village == ctx.value("village"))
    ).scalars().all()

    out: list[Discrepancy] = []
    seen: set[tuple[str, str]] = set()
    for other in candidates:
        other_owner = (other.owner_name or "").strip()
        if not other_owner or other_owner.lower() == owner.lower():
            continue
        ratio = fuzz.token_sort_ratio(owner.lower(), other_owner.lower()) / 100
        partial = fuzz.partial_ratio(owner.lower(), other_owner.lower()) / 100
        score = max(ratio, partial * 0.95)
        if 0.80 <= score < 0.98:
            key = tuple(sorted((owner.lower(), other_owner.lower())))
            if key in seen:
                continue
            seen.add(key)
            out.append(Discrepancy(
                rule_id="BR-8", rule_name=RULE_BY_ID["BR-8"]["name"], severity="medium",
                message=(
                    f"Owner '{owner}' is a likely spelling variant of '{other_owner}' "
                    f"({score:.0%} match) in record {other.record_id}."
                ),
                expected="one canonical owner spelling per person", actual=owner,
                conflicting_values={
                    "this_owner": owner, "other_owner": other_owner,
                    "similarity": round(score, 3), "other_record": other.record_id,
                },
                evidence_refs=_evidence_for(ctx, "owner_name"),
                recommended_action="Pick the canonical spelling; the variant pair has been added to the "
                                   "correction dataset for the next fine-tuning run.",
            ))
    return out[:3]


# ---------------------------------------------------------------------------
# BR-9  Boundary / spatial consistency
# ---------------------------------------------------------------------------
def br9_boundary_consistency(ctx: RecordContext) -> list[Discrepancy]:
    if not ctx.geometry or not ctx.geometry.get("linked"):
        return []
    delta_pct = float(ctx.geometry.get("area_delta_pct") or 0.0)
    if abs(delta_pct) <= settings.gis_area_tolerance_pct:
        return []
    return [Discrepancy(
        rule_id="BR-9", rule_name=RULE_BY_ID["BR-9"]["name"],
        severity="high" if abs(delta_pct) > 25 else "medium",
        message=(
            f"Cadastral polygon area ({ctx.geometry['polygon_area_ha']:.4f} ha) differs from the "
            f"recorded area ({ctx.geometry['record_area_ha']:.4f} ha) by {delta_pct:+.1f}%."
        ),
        expected=f"within +/- {settings.gis_area_tolerance_pct:.0f}% of recorded area",
        actual=f"{delta_pct:+.1f}%",
        conflicting_values={
            "polygon_area_ha": ctx.geometry["polygon_area_ha"],
            "record_area_ha": ctx.geometry["record_area_ha"],
            "delta_pct": round(delta_pct, 2),
            "plot_key": ctx.geometry.get("plot_key", ""),
        },
        evidence_refs=_evidence_for(ctx, "area", "boundaries"),
        recommended_action="Refer to the Survey Department for re-measurement of the plot boundary.",
    )]


# ---------------------------------------------------------------------------
# BR-10  Registration format check
# ---------------------------------------------------------------------------
REGISTRATION_PATTERN = re.compile(
    r"^(?:no\.?\s*)?(\d{1,5})\s*(?:of\s*)?(\d{4})(?:\s*[-/]\s*(\d{2,4}))?$", re.IGNORECASE
)
SRO_PATTERN = re.compile(r"^([A-Z]{2,5})[/-](\d{2,5})[/-](\d{4})$")
# Computer-generated RORs carry a system reference instead of a deed number
# (e.g. Odisha NIC "OR-1234567890"). It has no deed year to validate, but it
# is an official reference, not a malformed one.
ROR_REFERENCE_PATTERN = re.compile(r"^[A-Z]{2,5}[/-]\d{6,12}$")


def br10_registration_format(ctx: RecordContext) -> list[Discrepancy]:
    # Validate the *extracted* reference, not the raw OCR span: the span often
    # carries a fragment of the neighbouring stamp or the next column, which
    # would fail the format check even when the value itself is well formed.
    reference = (ctx.value("registration_no") or ctx.raw("registration_no")).strip()
    if not reference:
        return []
    if (REGISTRATION_PATTERN.match(reference) or SRO_PATTERN.match(reference.upper())
            or ROR_REFERENCE_PATTERN.match(reference.upper())):
        return []
    return [Discrepancy(
        rule_id="BR-10", rule_name=RULE_BY_ID["BR-10"]["name"], severity="medium",
        message=(
            f"Registration reference '{reference}' does not match the expected pattern "
            "(book no / year, SRO code-deed-year, or ROR reference like OR-1234567890)."
        ),
        expected="e.g. '1234 of 2019', 'BBSR-2311-2019' or 'OR-1234567890'", actual=reference,
        conflicting_values={"registration_no": reference,
                            "known_sro_codes": list(master_data.SUB_REGISTRAR_OFFICES)},
        evidence_refs=_evidence_for(ctx, "registration_no"),
        recommended_action="Re-read the registration reference from the deed page.",
    )]


RULE_FUNCTIONS = [
    ("BR-1", br1_mandatory_fields),
    ("BR-2", br2_subplot_area_sum),
    ("BR-3", br3_duplicate_identifiers),
    ("BR-4", br4_chronological_validity),
    ("BR-5", br5_mutation_chain),
    ("BR-6", br6_unit_normalisation),
    ("BR-7", br7_admin_hierarchy),
    ("BR-8", br8_owner_fuzzy_match),
    ("BR-9", br9_boundary_consistency),
    ("BR-10", br10_registration_format),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run_all_rules(ctx: RecordContext) -> list[RuleOutcome]:
    import time

    outcomes: list[RuleOutcome] = []
    for rule_id, function in RULE_FUNCTIONS:
        started = time.perf_counter()
        try:
            discrepancies = function(ctx)
            detail = ""
        except Exception as exc:  # a broken rule must not kill the pipeline
            discrepancies = []
            detail = f"rule error: {type(exc).__name__}: {exc}"
        outcomes.append(RuleOutcome(
            rule_id=rule_id,
            rule_name=RULE_BY_ID[rule_id]["name"],
            passed=not discrepancies and not detail,
            discrepancies=discrepancies,
            detail=detail,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        ))
    return outcomes


def highest_severity(discrepancies: list[Discrepancy]) -> str:
    if not discrepancies:
        return ""
    return max((d.severity for d in discrepancies), key=lambda s: SEVERITY_RANK.get(s, 0))


# ---------------------------------------------------------------------------
# Duplicate detection across the repository (FR-7)
# ---------------------------------------------------------------------------
def scan_duplicates(db: Session, record: LandRecord) -> list[dict]:
    """Repository-wide duplicate scan used by the search/records API."""
    findings: list[dict] = []
    if record.khasra_no:
        others = db.execute(
            select(LandRecord).where(LandRecord.khasra_no == record.khasra_no)
            .where(LandRecord.id != record.id)
        ).scalars().all()
        for other in others:
            similarity = fuzz.token_sort_ratio(
                (record.owner_name or "").lower(), (other.owner_name or "").lower()
            ) / 100
            findings.append({
                "type": "duplicate_khasra" if similarity < 0.92 else "repeat_digitisation",
                "identifier": f"khasra {record.khasra_no}",
                "record_id": other.record_id,
                "owner": other.owner_name,
                "similarity": round(similarity, 3),
                "severity": "critical" if similarity < 0.92 else "medium",
            })
    if record.survey_no:
        others = db.execute(
            select(LandRecord).where(LandRecord.survey_no == record.survey_no)
            .where(LandRecord.id != record.id)
        ).scalars().all()
        for other in others:
            findings.append({
                "type": "duplicate_survey",
                "identifier": f"survey {record.survey_no}",
                "record_id": other.record_id,
                "owner": other.owner_name,
                "severity": "high",
            })
    if record.owner_name and record.village:
        near = db.execute(
            select(LandRecord).where(LandRecord.id != record.id)
            .where(LandRecord.village == record.village)
        ).scalars().all()
        for other in near:
            score = fuzz.token_sort_ratio(
                record.owner_name.lower(), (other.owner_name or "").lower()
            ) / 100
            if 0.80 <= score < 0.98:
                findings.append({
                    "type": "owner_name_variant",
                    "identifier": other.owner_name,
                    "record_id": other.record_id,
                    "similarity": round(score, 3),
                    "severity": "medium",
                })
    return findings


def discrepancy_summary(db: Session) -> list[dict]:
    rows = db.execute(
        select(DiscrepancyRow.rule_id, DiscrepancyRow.severity, func.count(DiscrepancyRow.id))
        .group_by(DiscrepancyRow.rule_id, DiscrepancyRow.severity)
    ).all()
    return [
        {"rule_id": rule_id, "severity": sev.value if hasattr(sev, "value") else sev, "count": count}
        for rule_id, sev, count in rows
    ]
