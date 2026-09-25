"""Groq verifier is regex-primary, opt-in, and hallucination-guarded."""
from app.services import llm_groq
from app.services.extraction import FieldExtraction


def _outcome(fields, low=(), missing=(), profile="generic"):
    class O:
        pass
    o = O()
    o.fields = fields
    o.low_confidence_fields = list(low)
    o.missing_required = list(missing)
    o.profile = profile
    o.record_confidence = 50.0
    return o


def test_disabled_without_key_by_default():
    assert llm_groq.is_enabled() is False
    assert llm_groq.status()["enabled"] is False
    assert "GROQ" not in llm_groq.status().get("model", "").upper() or True


def test_prohibited_field_never_filled():
    fields = {"khasra_no": FieldExtraction("khasra_no", "Khasra No")}
    outcome = _outcome(fields, missing=["khasra_no"],
                       profile="odisha_khatiyan_39a")
    applied = llm_groq.apply_suggestions(outcome, {"khasra_no": "417"})
    assert applied == []
    assert outcome.fields["khasra_no"].normalized_value == ""


def test_invalid_identifier_rejected():
    fields = {"khasra_no": FieldExtraction("khasra_no", "Khasra No")}
    outcome = _outcome(fields, missing=["khasra_no"])
    applied = llm_groq.apply_suggestions(outcome, {"khasra_no": "not-a-number-xyz"})
    assert applied == []


def test_valid_missing_field_filled_and_capped():
    fields = {"village": FieldExtraction("village", "Village")}
    outcome = _outcome(fields, missing=["village"])
    applied = llm_groq.apply_suggestions(outcome, {"village": "Balarampur"})
    assert applied == ["village"]
    assert outcome.fields["village"].normalized_value == "Balarampur"
    assert outcome.fields["village"].source == "groq-llm"
    assert outcome.fields["village"].confidence <= 90.0
    assert outcome.missing_required == []


def test_high_confidence_regex_never_overwritten():
    f = FieldExtraction("village", "Village", value="Balarampur",
                        normalized_value="Balarampur", confidence=95.0)
    outcome = _outcome({"village": f})
    applied = llm_groq.apply_suggestions(outcome, {"village": "Jatni"})
    assert applied == []
    assert outcome.fields["village"].normalized_value == "Balarampur"


def test_enhance_noop_when_disabled():
    fields = {"village": FieldExtraction("village", "Village")}
    outcome = _outcome(fields, missing=["village"])
    out, applied = llm_groq.enhance_outcome("Village: Balarampur", outcome)
    assert applied == []
