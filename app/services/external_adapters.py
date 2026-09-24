"""
Mock government-system adapters (SRS FR-8 - Cross-Database Verification).

The prototype ships read-only *mock* adapters that behave like the real
interfaces: same call signature, same latency characteristics, same response
contract. Swapping in a live endpoint is a one-line change per adapter, which is
exactly the FR-8 promise ("Mock -> Read integration").

    Bhulekh / RoR   -> ownership verification
    BhuNaksha       -> cadastral map reference
    IGR             -> registration / deed verification
    LRMS / DILRMP   -> downstream record status
    LGD master      -> village / tehsil validation
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from app import master_data

MODE = "mock"  # becomes "live" when credentials + endpoints are configured


@dataclass
class ExternalCheck:
    source_system: str
    mode: str
    match_status: str          # matched | mismatch | not_found
    severity: str
    external_reference: str
    detail: str
    payload: dict = field(default_factory=dict)
    latency_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "source_system": self.source_system,
            "mode": self.mode,
            "match_status": self.match_status,
            "severity": self.severity,
            "external_reference": self.external_reference,
            "detail": self.detail,
            "payload": self.payload,
            "latency_ms": self.latency_ms,
        }


# ---------------------------------------------------------------------------
# Deterministic demo ledger - stands in for the state Bhulekh database
# ---------------------------------------------------------------------------
BHULEKH_LEDGER: dict[str, dict] = {
    "118/2": {"owner": "Prafulla Kumar Sahoo", "khata": "204", "area_ha": 0.4613,
              "classification": "Irrigated land", "village": "Balarampur"},
    "118/4": {"owner": "Prafulla Kumar Sahoo", "khata": "204", "area_ha": 0.3035,
              "classification": "Irrigated land", "village": "Balarampur"},
    "227/1": {"owner": "Sunita Pradhan", "khata": "118", "area_ha": 0.8094,
              "classification": "Unirrigated land", "village": "Golabai"},
    "340": {"owner": "Ramesh Chandra Behera", "khata": "77", "area_ha": 1.2141,
            "classification": "Homestead land", "village": "Jankia"},
    "512/3": {"owner": "Minati Sahu", "khata": "311", "area_ha": 0.6071,
              "classification": "Irrigated land", "village": "Pipli"},
    "88/1": {"owner": "Bhagaban Nayak", "khata": "45", "area_ha": 0.2428,
             "classification": "Plantation land", "village": "Banapur"},
}

IGR_REGISTRY: dict[str, dict] = {
    "1234/2019": {"parties": ["Bhagaban Nayak", "Prafulla Kumar Sahoo"], "year": 2019,
                  "consideration_lakh": 18.5, "sro": "Khordha SRO", "status": "registered"},
    "876/2021": {"parties": ["Anonymous Trust", "Sunita Pradhan"], "year": 2021,
                 "consideration_lakh": 42.0, "sro": "Bhubaneswar SRO", "status": "registered"},
    "4455/2020": {"parties": ["Ramesh Chandra Behera", "Minati Sahu"], "year": 2020,
                  "consideration_lakh": 9.75, "sro": "Pipli SRO", "status": "registered"},
}


def _sleep_like_a_network_call(low_ms: int = 30, high_ms: int = 90) -> int:
    started = time.perf_counter()
    random.random()  # keep the branch predictor honest; timing comes from the sleep
    time.sleep(random.uniform(low_ms, high_ms) / 1000)
    return int((time.perf_counter() - started) * 1000)


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
def check_bhulekh(khasra_no: str, owner_name: str, khata_no: str = "", area_ha: float = 0.0) -> ExternalCheck:
    latency = _sleep_like_a_network_call()
    entry = BHULEKH_LEDGER.get(khasra_no)
    if entry is None:
        return ExternalCheck(
            "bhulekh", MODE, "not_found", "medium", f"bhulekh://ror/{khasra_no or 'unknown'}",
            f"Khasra {khasra_no or '(blank)'} is not present in the Bhulekh mock ledger; the plot may "
            "be newly created or the identifier may have been misread.",
            {"khasra_no": khasra_no, "ledger_size": len(BHULEKH_LEDGER)}, latency,
        )
    mismatches = []
    if owner_name and entry["owner"].lower() != owner_name.lower():
        mismatches.append(f"owner: Bhulekh='{entry['owner']}' vs extracted='{owner_name}'")
    if khata_no and entry["khata"] != khata_no:
        mismatches.append(f"khata: Bhulekh='{entry['khata']}' vs extracted='{khata_no}'")
    if area_ha and abs(entry["area_ha"] - area_ha) > max(area_ha * 0.05, 0.005):
        mismatches.append(
            f"area: Bhulekh={entry['area_ha']:.4f} ha vs extracted={area_ha:.4f} ha"
        )
    if mismatches:
        return ExternalCheck(
            "bhulekh", MODE, "mismatch", "high", f"bhulekh://ror/{khasra_no}",
            "Bhulekh cross-check found " + "; ".join(mismatches) + ".",
            {"khasra_no": khasra_no, "bhulekh_entry": entry, "mismatches": mismatches}, latency,
        )
    return ExternalCheck(
        "bhulekh", MODE, "matched", "low", f"bhulekh://ror/{khasra_no}",
        f"Record agrees with the Bhulekh entry for khasra {khasra_no} (khata {entry['khata']}, "
        f"{entry['area_ha']:.4f} ha).",
        {"khasra_no": khasra_no, "bhulekh_entry": entry}, latency,
    )


def check_bhunaksha(khasra_no: str) -> ExternalCheck:
    latency = _sleep_like_a_network_call()
    from app.services.gis_service import layer

    feature, match_type = layer().match(khasra_no)
    if feature is None:
        return ExternalCheck(
            "bhunaksha", MODE, "not_found", "medium", f"bhunaksha://plot/{khasra_no or 'unknown'}",
            "No BhuNaksha cadastral polygon is available for this plot in the sample layer.",
            {"khasra_no": khasra_no, "layer_features": len(layer().all_features())}, latency,
        )
    props = feature.get("properties", {})
    return ExternalCheck(
        "bhunaksha", MODE, "matched", "low",
        f"bhunaksha://plot/{props.get('plot_key', khasra_no)}",
        f"Cadastral polygon available (matched on {match_type}); village {props.get('village')}, "
        f"tehsil {props.get('tehsil')}.",
        {"plot_key": props.get("plot_key"), "village": props.get("village"),
         "tehsil": props.get("tehsil"), "match_type": match_type}, latency,
    )


def check_igr(registration_no: str, parties: list[str] | None = None) -> ExternalCheck:
    latency = _sleep_like_a_network_call()
    key = (registration_no or "").strip().upper().replace("NO.", "").replace("OF", "/").replace(" ", "")
    normalised = key.replace("//", "/")
    entry = IGR_REGISTRY.get(normalised) or IGR_REGISTRY.get((registration_no or "").strip())
    if entry is None:
        return ExternalCheck(
            "igr", MODE, "not_found", "medium", f"igr://deed/{registration_no or 'unknown'}",
            f"Registration reference '{registration_no or '(blank)'}' was not found in the IGR mock registry.",
            {"registration_no": registration_no, "registry_size": len(IGR_REGISTRY)}, latency,
        )
    parties = parties or []
    missing = [p for p in parties if p and not any(p.lower() in r.lower() for r in entry["parties"])]
    if missing:
        return ExternalCheck(
            "igr", MODE, "mismatch", "high", f"igr://deed/{normalised}",
            f"IGR deed {normalised} does not list {', '.join(missing)} among its parties "
            f"(registered parties: {', '.join(entry['parties'])}).",
            {"registration_no": normalised, "igr_entry": entry, "missing_parties": missing}, latency,
        )
    return ExternalCheck(
        "igr", MODE, "matched", "low", f"igr://deed/{normalised}",
        f"Deed {normalised} verified: {entry['status']} in {entry['year']} at {entry['sro']}, "
        f"consideration Rs {entry['consideration_lakh']} lakh.",
        {"registration_no": normalised, "igr_entry": entry}, latency,
    )


def check_lrms(khasra_no: str, khata_no: str) -> ExternalCheck:
    latency = _sleep_like_a_network_call()
    state = "digr"  # Digitised + Geo-referenced, the DILRMP target state
    return ExternalCheck(
        "lrms", MODE, "matched", "low", f"lrms://khata/{khata_no or 'unknown'}/{khasra_no or 'unknown'}",
        f"LRMS accepts khata {khata_no or '(blank)'} / khasra {khasra_no or '(blank)'}; current DILRMP "
        f"state '{state}'. BhuVerify output is API-ready for write-back once approval is granted.",
        {"khata_no": khata_no, "khasra_no": khasra_no, "dilrmp_state": state,
         "write_back_authorised": False}, latency,
    )


def check_lgd_master(village: str, tehsil: str) -> ExternalCheck:
    latency = _sleep_like_a_network_call(20, 50)
    result = master_data.lookup_village(village, tehsil)
    if result["status"] == "ok":
        return ExternalCheck(
            "lgd", MODE, "matched", "low", f"lgd://village/{village}",
            f"Village '{village}' resolves to tehsil '{result['expected_tehsil']}' in the "
            f"{master_data.DISTRICT} master.",
            {"village": village, "tehsil": result["expected_tehsil"]}, latency,
        )
    return ExternalCheck(
        "lgd", MODE, "mismatch" if result["status"] == "hierarchy_mismatch" else "not_found",
        "medium", f"lgd://village/{village or 'unknown'}",
        f"Master-data check failed for village '{village or '(blank)'}' (status {result['status']}).",
        {"village": village, "stated_tehsil": tehsil, "expected_tehsil": result["expected_tehsil"]},
        latency,
    )


def run_all_checks(
    *,
    khasra_no: str,
    owner_name: str,
    khata_no: str = "",
    area_ha: float = 0.0,
    registration_no: str = "",
    parties: list[str] | None = None,
    village: str = "",
    tehsil: str = "",
) -> list[ExternalCheck]:
    return [
        check_bhulekh(khasra_no, owner_name, khata_no, area_ha),
        check_bhunaksha(khasra_no),
        check_igr(registration_no, parties),
        check_lgd_master(village, tehsil),
        check_lrms(khasra_no, khata_no),
    ]


def adapter_status() -> dict:
    return {
        "mode": MODE,
        "adapters": [
            {"name": "bhulekh", "purpose": "Ownership / RoR verification", "mode": MODE},
            {"name": "bhunaksha", "purpose": "Cadastral map reference", "mode": MODE},
            {"name": "igr", "purpose": "Registration / deed verification", "mode": MODE},
            {"name": "lgd", "purpose": "Village-tehsil master data", "mode": MODE},
            {"name": "lrms", "purpose": "DILRMP downstream status", "mode": MODE},
        ],
        "note": "Prototype tier uses deterministic mocks. Pilot tier swaps each adapter for a "
                "read-only government API without changing the ExternalCheck contract.",
    }
