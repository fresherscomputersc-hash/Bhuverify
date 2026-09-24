"""Field-extraction regressions from real official formats (sample/).

Synthetic OCR text resembling the NIC ROR, Hindi ROR, UP khatauni and Odia
khatiyan layouts. No Tesseract needed: extract_fields works on text alone.
"""
from app.services.extraction import extract_fields


def _get(text, field):
    outcome = extract_fields(text, [], language="eng")
    return outcome.fields[field]


def test_multi_pair_line_does_not_bleed():
    text = "Village : Bada Sahi Khasra No. : 123/4\nKhata No. : 567"
    assert _get(text, "village").normalized_value == "Bada Sahi"
    assert _get(text, "khasra_no").normalized_value == "123/4"
    assert _get(text, "khata_no").normalized_value == "567"


def test_glued_label_khasra_no():
    text = "Village : Bada Sahi KhasraNo. : 123/4"
    assert _get(text, "khasra_no").normalized_value == "123/4"


def test_table_row_owner_fallback():
    text = (
        "Sl. No. Owner Name Relation Khasra No. Khata No. Area Remarks\n"
        "1 Ramesh Chandra Sahoo | S/o Late Balaram Sahoo | 123/4 | 567 | 0.2500"
    )
    owner = _get(text, "owner_name")
    assert owner.normalized_value == "Ramesh Chandra Sahoo"
    assert owner.source == "table_row"


def test_header_line_is_not_the_owner():
    text = "Owner Name Relation Khasra No. Khata No.\n1 Ramesh Chandra Sahoo | S/o X | 123/4 | 567"
    assert _get(text, "owner_name").normalized_value == "Ramesh Chandra Sahoo"


def test_date_fragment_is_not_khasra():
    text = "Mutation Date : 15/07/2024\nVillage : Rampur"
    assert _get(text, "khasra_no").normalized_value == ""
    assert _get(text, "mutation_date").normalized_value == "15/07/2024"


def test_prose_survey_is_not_survey_no():
    text = "as per the latest mutation and survey records available with the department"
    assert _get(text, "survey_no").normalized_value == ""


def test_ror_reference_extracted():
    text = "ROR Reference No. : OR-1234567890\nVillage : Bada Sahi"
    assert _get(text, "registration_no").normalized_value == "OR-1234567890"


def test_five_digit_khata():
    text = "Khasra No. : 986\nKhata No. : 00183"
    assert _get(text, "khata_no").normalized_value == "00183"


def test_khesra_spelling_variant():
    text = "Khata No. : 123\nKhesra No. : 456"
    assert _get(text, "khasra_no").normalized_value == "456"


def test_gata_alias():
    text = "Gata No. : 986\nKhata No. : 00183"
    assert _get(text, "khasra_no").normalized_value == "986"


def test_rakaba_area():
    text = "Rakaba (Hectare) : 0.50"
    area = _get(text, "area")
    assert area.normalized_value == "0.5"


def test_zero_area_stays_missing():
    text = "Area : 0"
    assert _get(text, "area").normalized_value == ""


def test_guardian_cell_boundary():
    text = "Guardian : S/o Late Balaram Sahoo | 123/4 | 567"
    assert _get(text, "guardian_name").normalized_value == "Late Balaram Sahoo"
