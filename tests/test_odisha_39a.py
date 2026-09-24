"""Odisha Schedule I Form 39-A extraction contract (anti-hallucination spec).

Covers: profile detection, prohibited canonical fields staying null/missing,
dedicated identifiers (khewat/khatiyan/tehsil-no), the praja person parser
with pi:/swa: relations, address from the residence marker, directional
boundary, dakhalkharij mutation numbers, and the 39-A BR-1 required set.
"""
from app.services.extraction import (
    classify_document_type,
    detect_profile,
    extract_fields,
    parse_boundary,
    parse_praja_section,
    required_fields_for,
)

FORM_39A_TEXT = """Schedule I Form No.39-A
ମୌଜା : କଇବଲପୁର ତହସିଲ : ଓଡଗାଁ
ଥାନା : ଓଡଗାଁ ତହସିଲ ନମ୍ବର : 232
ଥାନା ନମ୍ବର : 150 ଜିଲ୍ଲା : ନୟାଗଡ଼
ଓଡିଶା ସରକାର ଖେୱାଟ ନମ୍ବର 1
ଖତିୟାନର କ୍ରମିକ ନମ୍ବର 18
2) ପ୍ରଜାର ନାମ, ପିତାର ନାମ, ଜାତି ଓ ବାସସ୍ଥାନ
ଭରତ ଜେନା ପି: କୁଳମଣୀ ଜେନା, ସୁଲୋଚନା ଭଞ୍ଜ ସ୍ୱା: କୃଷ୍ଣ ଚନ୍ଦ୍ର ଜେନା ଜା: ଖଣ୍ଡାୟତ ବା: ମସାବାରୀ
3) ସ୍ୱତ୍ୱ ରୟତି
ଦାଖଲ ଖାରଜ କେସ ନଂ 4837/2025
ପ୍ଲଟ ନମ୍ବର 417
କିସମ ଶାରଦ ଦୋଫସଲି
ରକବା 0.6300 ହେକ୍ଟର
ଚୌହଦି ଉ: ରାସ୍ତା, ଦ: ପୋଖରୀ
ଅନ୍ତିମ ପ୍ରକାଶନ ତାରିଖ - 24/05/1983
"""


_OUTCOME_CACHE = {}
NAYAGARH = "\u0b28\u0b5f\u0b3e\u0b17\u0b21\u0b3c"  # Nayagarh
def _outcome():
    return extract_fields(FORM_39A_TEXT, [], language="ori")


def test_profile_detected():
    assert detect_profile(FORM_39A_TEXT) == "odisha_khatiyan_39a"
    outcome = _outcome()
    assert outcome.profile == "odisha_khatiyan_39a"
    doc_type, _conf = classify_document_type(outcome)
    assert doc_type == "odisha_khatiyan_39a"


def test_prohibited_fields_stay_missing():
    fields = _outcome().fields
    for name, reason in (
        ("khasra_no", "field_not_present"),
        ("survey_no", "field_not_present"),
        ("khata_no", "canonical_field_not_explicitly_present"),
        ("registration_no", "field_not_present"),
        ("mutation_date", "field_not_present"),
        ("previous_owner", "field_not_present"),
        ("new_owner", "field_not_present"),
    ):
        assert fields[name].normalized_value == "", name
        assert fields[name].status == "missing", name
        assert fields[name].reason == reason, name


def test_plot_and_admin_fields_extracted():
    fields = _outcome().fields
    assert fields["plot_no"].normalized_value == "417"
    assert fields["village"].normalized_value == "କଇବଲପୁର"
    assert fields["tehsil"].normalized_value == "ଓଡଗାଁ"
    assert fields["district"].normalized_value == NAYAGARH
    assert fields["mutation_no"].normalized_value == "4837/2025"
    assert fields["land_classification"].normalized_value == "ଶାରଦ ଦୋଫସଲି"


def test_dedicated_identifiers_verbatim():
    dedicated = _outcome().dedicated
    assert dedicated["khewat_no"].normalized_value == "1"
    assert dedicated["khatiyan_no"].normalized_value == "18"
    assert dedicated["tehsil_no"].normalized_value == "232"


def test_area_value_and_unit():
    outcome = _outcome()
    assert outcome.fields["area"].normalized_value == "0.63"
    assert outcome.area_hectare == 0.63


def test_person_parser_relations():
    owners, residence = parse_praja_section(FORM_39A_TEXT.splitlines())
    assert residence == "ମସାବାରୀ"
    by_name = {o["name"]: o for o in owners}
    assert by_name["ଭରତ ଜେନା"]["relation_type"] == "father"
    assert by_name["ଭରତ ଜେନା"]["relation_name"] == "କୁଳମଣୀ ଜେନା"
    assert by_name["ସୁଲୋଚନା ଭଞ୍ଜ"]["relation_type"] == "spouse"
    assert by_name["ସୁଲୋଚନା ଭଞ୍ଜ"]["relation_name"] == "କୃଷ୍ଣ ଚନ୍ଦ୍ର ଜେନା"
    # spouse must never be rewritten as father
    assert all(o["relation_type"] != "father" or "ସ୍ୱା" not in o["name"] for o in owners)


def test_owner_and_address_from_persons():
    outcome = _outcome()
    assert outcome.fields["owner_name"].normalized_value == "ଭରତ ଜେନା"
    assert outcome.fields["guardian_name"].normalized_value == "କୁଳମଣୀ ଜେନା"
    assert outcome.fields["address"].normalized_value == "ମସାବାରୀ"
    assert len(outcome.owners) >= 2


def test_boundary_directional_only():
    boundary = parse_boundary(FORM_39A_TEXT.splitlines())
    assert boundary["north"] == "ରାସ୍ତା"
    assert boundary["south"] == "ପୋଖରୀ"
    assert boundary["east"] is None
    assert boundary["west"] is None
    assert _outcome().boundary["north"] == "ରାସ୍ତା"


def test_br1_required_set_for_39a():
    required = required_fields_for("odisha_khatiyan_39a")
    assert "plot_no" in required
    assert "khasra_no" not in required
    assert "khata_no" not in required
    assert "survey_no" not in required
    # generic default unchanged
    assert "khasra_no" in required_fields_for("record_of_rights")


def test_publication_date_is_not_mutation_date():
    fields = _outcome().fields
    assert fields["mutation_date"].normalized_value == ""
    assert fields["mutation_date"].status == "missing"


def test_plot_sweep_ignores_mutation_case_numbers():
    from app.services.extraction import extract_fields

    text = ("Schedule I Form No.39-A\n"
            "ଦାଖଲ ଖାରଜ କେସ ନଂ 4061/2000\n"
            "ପ୍ଲଟ ନମ୍ବର ଓ ଚକର ନାମ\n"
            "248/725 ଘରବାରି 0200")
    outcome = extract_fields(text, [], language="ori")
    assert outcome.profile == "odisha_khatiyan_39a"
    assert outcome.fields["plot_no"].normalized_value == "248/725"
    # mutation cases and khatiyan serials must not leak into identifiers
    assert outcome.fields["khasra_no"].normalized_value == ""
    assert outcome.fields["mutation_no"].normalized_value == "4061/2000"


def test_bare_plot_number_on_later_page():
    from app.services.extraction import extract_fields

    text = ("ଖେୱାଟ ନମ୍ବର 1\n"
            "ଖତିୟାନର କ୍ରମିକ ନଂ : 18\n"
            "ମୌଜା : ଗୋଠବଣ\n"
            "417 ଘରବାରି\n"
            "24/09/2026 IP :49.42.182.173")
    outcome = extract_fields(text, [], language="ori", page=2)
    assert outcome.fields["plot_no"].normalized_value == "417"
    # the khatiyan serial on the same page must not become the plot
    assert outcome.fields["plot_no"].normalized_value != "18"
