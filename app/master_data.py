"""
Administrative-hierarchy master data used by BR-7 (Village-Tehsil Master Check)
and the LGD-style mock adapter in FR-8.

PROTOTYPE DATASET: a demo subset modelled on Khordha district, Odisha. In the
pilot tier this is replaced by the authoritative LGD / state master-data extract
(see SRS 4.2 - "LGD/Master data | Sample dataset -> Real dataset").
"""
from __future__ import annotations

STATE = "Odisha"
DISTRICT = "Khordha"

# tehsil -> list of villages (demo subset)
TEHSIL_VILLAGES: dict[str, list[str]] = {
    "Balianta": ["Arjunpur", "Balianta", "Chandaka", "Daruthenga", "Kolaran"],
    "Balipatna": ["Badakolanda", "Balipatna", "Belagachhia", "Dharmansahi", "Madhapur"],
    "Banapur": ["Balisahi", "Banapur", "Godishala", "Kuanpado", "Ranjipur"],
    "Begunia": ["Baghamari", "Begunia", "Golabai", "Khajuripada", "Rameswarpur"],
    "Bhubaneswar": ["Bharatpur", "Bhubaneswar", "Mancheswar", "Patia", "Saheed Nagar"],
    "Chandaka": ["Dumuduma", "Gothapatna", "Janla", "Kamla Nagar", "Mankada"],
    "Jankia": ["Balabhadrapur", "Jankia", "Kusangi", "Mirjapur", "Rampur"],
    "Jatni": ["Badagopalpur", "Jatni", "Kesura", "Nandankanan", "Taraboi"],
    "Khordha Sadar": ["Balarampur", "Dadhibamanpur", "Khordha", "Sijua", "Sundarpada"],
    "Lembagara": ["Barunei", "Itamati", "Khamarang", "Lembagara", "Sanakhemundi"],
    "Nirakarpur": ["Basanta Nagar", "Golabai", "Nirakarpur", "Pipli", "Rahama"],
    "Saheednagar": ["Acharya Vihar", "Bapuji Nagar", "Laxmisagar", "Nayapalli", "Rasulgarh"],
    "Tangi": ["Balugaon", "Bhusanda", "Golabai", "Tangi", "Uchala"],
    "Uttara": ["Barunei", "Harirajpur", "Kantia", "Uttara", "Vijay Nagar"],
}

VILLAGE_TEHSIL_INDEX: dict[str, str] = {
    village: tehsil
    for tehsil, villages in TEHSIL_VILLAGES.items()
    for village in villages
}

# Indic-script spellings of the demo villages, mapped to the Latin master entry.
# Without these, a Devanagari/Odia register page can never satisfy BR-7 - the
# multilingual requirement would be satisfied in OCR but fail at validation.
INDIC_VILLAGE_ALIASES: dict[str, str] = {
    # Devanagari
    "बलरामपुर": "Balarampur",
    "गोलाबाई": "Golabai",
    "जानकीया": "Jankia",
    "जंकीया": "Jankia",
    "पिपली": "Pipli",
    "बालापुर": "Balarampur",
    "खुर्दा": "Khordha",
    "जटनी": "Jatni",
    "तंगी": "Tangi",
    "बेगुनिया": "Begunia",
    "बानपुर": "Banapur",
    # Odia
    "ବଲରାମପୁର": "Balarampur",
    "ଗୋଲାବାଇ": "Golabai",
    "ଜାନକିଆ": "Jankia",
    "ପିପିଲି": "Pipli",
    "ଖୋର୍ଦ୍ଧା": "Khordha",
    "ଜଟଣୀ": "Jatni",
    "ଟାଙ୍ଗୀ": "Tangi",
    "ବେଗୁନିଆ": "Begunia",
    "ବାଣପୁର": "Banapur",
}

# Indic-script tehsil spellings -> Latin master entry
INDIC_TEHSIL_ALIASES: dict[str, str] = {
    "खुर्दा सदर": "Khordha Sadar",
    "खुर्दा": "Khordha Sadar",
    "निराकारपुर": "Nirakarpur",
    "जंकीया": "Jankia",
    "जानकीया": "Jankia",
    "ଜଟଣୀ": "Jatni",
    "ଟାଙ୍ଗୀ": "Tangi",
    "ଖୋର୍ଦ୍ଧା ସଦର": "Khordha Sadar",
    "ନିରାକାରପୁର": "Nirakarpur",
    "ଜାନକିଆ": "Jankia",
}

# Indic-script district spellings -> Latin master entry
INDIC_DISTRICT_ALIASES: dict[str, str] = {
    "खुर्दा": "Khordha",
    "ଖୋର୍ଦ୍ଧା": "Khordha",
}


def canonical_district(raw: str) -> str:
    """Resolve a district name written in an Indic script to the Latin entry."""
    if not raw:
        return ""
    key = raw.strip()
    return INDIC_DISTRICT_ALIASES.get(key, key)

VILLAGE_TEHSIL_INDEX.update({alias: VILLAGE_TEHSIL_INDEX[latin]
                             for alias, latin in INDIC_VILLAGE_ALIASES.items()
                             if latin in VILLAGE_TEHSIL_INDEX})

# Canonical land classifications (Odisha RoR convention)
LAND_CLASSIFICATIONS: list[str] = [
    "Irrigated land",
    "Unirrigated land",
    "Agricultural land",
    "Homestead land",
    "Gair mumkin (waste/common land)",
    "Plantation land",
    "Commercial land",
    "Water body (pond/tank)",
    "Forest land",
]

# Classification alias -> canonical (handles OCR/HTR variants)
CLASSIFICATION_ALIASES: dict[str, str] = {
    "baad chash": "Irrigated land",
    "baad": "Unirrigated land",
    "bari": "Homestead land",
    "gharabari": "Homestead land",
    "gharbari": "Homestead land",
    "ଘରବାରି": "Homestead land",
    "घरबारी": "Homestead land",
    "homestead": "Homestead land",
    "irrigated": "Irrigated land",
    "unirrigated": "Unirrigated land",
    "agricultural": "Agricultural land",
    "agriculture": "Agricultural land",
    "krishi": "Agricultural land",
    "कृषि भूमि": "Agricultural land",
    "कृषि": "Agricultural land",
    "କୃଷି": "Agricultural land",
    "dry land": "Unirrigated land",
    "wet land": "Irrigated land",
    "gair mumkin": "Gair mumkin (waste/common land)",
    "waste": "Gair mumkin (waste/common land)",
    "common land": "Gair mumkin (waste/common land)",
    "plantation": "Plantation land",
    "commercial": "Commercial land",
    "pond": "Water body (pond/tank)",
    "tank": "Water body (pond/tank)",
    "forest": "Forest land",
}

# Registration authority codes used by BR-10
SUB_REGISTRAR_OFFICES: dict[str, str] = {
    "KH": "Khordha SRO",
    "BBSR": "Bhubaneswar SRO",
    "BAP": "Bhubaneswar (Bapuji Nagar) SRO",
    "PLP": "Pipli SRO",
    "BGR": "Balugaon SRO",
    "BNP": "Banapur SRO",
    "BGN": "Begunia SRO",
}


def lookup_village(village: str, tehsil: str = "") -> dict:
    """BR-7 lookup. Returns match status + the expected tehsil if mismatched."""
    if not village:
        return {"status": "missing", "expected_tehsil": "", "known_tehsils": []}
    # an Indic-script tehsil name is normalised to the Latin master entry first
    tehsil_key = INDIC_TEHSIL_ALIASES.get(tehsil.strip(), tehsil)
    normalised = village.strip().title()
    expected = VILLAGE_TEHSIL_INDEX.get(normalised) or VILLAGE_TEHSIL_INDEX.get(village.strip())
    if expected is None:
        # tolerate OCR noise with a light prefix/suffix match
        for known in VILLAGE_TEHSIL_INDEX:
            if known.lower().startswith(normalised.lower()[:5]) and len(normalised) >= 4:
                expected = VILLAGE_TEHSIL_INDEX[known]
                return {
                    "status": "partial",
                    "expected_tehsil": expected,
                    "matched_village": known,
                    "known_tehsils": list(TEHSIL_VILLAGES),
                }
        return {"status": "not_in_master", "expected_tehsil": "", "known_tehsils": list(TEHSIL_VILLAGES)}
    if tehsil and tehsil_key.strip().title() != expected:
        return {
            "status": "hierarchy_mismatch",
            "expected_tehsil": expected,
            "known_tehsils": list(TEHSIL_VILLAGES),
        }
    return {"status": "ok", "expected_tehsil": expected, "known_tehsils": list(TEHSIL_VILLAGES)}


def canonical_classification(raw: str) -> str:
    if not raw:
        return ""
    key = raw.strip().lower()
    if key in CLASSIFICATION_ALIASES:
        return CLASSIFICATION_ALIASES[key]
    for alias, canonical in CLASSIFICATION_ALIASES.items():
        if alias in key:
            return canonical
    for canonical in LAND_CLASSIFICATIONS:
        if canonical.lower() in key:
            return canonical
    return raw.strip().title()
