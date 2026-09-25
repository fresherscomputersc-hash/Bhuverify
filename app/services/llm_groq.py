"""
Groq LLM verifier (optional second pass, SRS FR-4 assist).

Flow per document (see worker.py Stage 3):

    OCR -> extract_multipage() [regex primary] -> try Groq -> validate ->
    fill empty/low-confidence fields only -> validation -> human review

Rules (anti-hallucination contract):
  * Never overwrites a high-confidence regex value.
  * Never fills a field prohibited by the document profile (Form 39-A).
  * Every suggestion must pass the same PATTERNS / _looks_like_name gate
    the regex path uses; failures are dropped, never stored.
  * Disabled by default; enabled only with GROQ_ENABLED=1 + GROQ_API_KEY.
  * Uses httpx (already a dependency) against the Groq OpenAI-compatible
    endpoint. No torch, no local model, Render-free safe.
"""
from __future__ import annotations

import json
import re

from app.config import settings

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MAX_OCR_CHARS = 6000
LLM_CONFIDENCE_CAP = 75.0

# Only these canonical fields may be filled by the LLM.
LLM_FILLABLE = {
    "owner_name", "guardian_name", "previous_owner", "new_owner",
    "khasra_no", "khata_no", "survey_no", "plot_no",
    "village", "tehsil", "district", "address",
    "area", "land_classification", "mutation_no",
    "mutation_date", "registration_no",
}


def is_enabled() -> bool:
    return bool(settings.groq_enabled and settings.groq_api_key)


def status() -> dict:
    return {
        "enabled": is_enabled(),
        "model": settings.groq_model if is_enabled() else "",
        "mode": "verifier-only (regex primary)",
        "has_key": bool(settings.groq_api_key),
        "timeout_s": settings.groq_timeout_s,
    }


def needs_llm(outcome) -> bool:
    return bool(outcome.low_confidence_fields or outcome.missing_required)


def build_prompt(ocr_text: str, outcome, profile: str) -> list[dict]:
    from app.services.extraction import PROHIBITED_BY_PROFILE

    prohibited = sorted(PROHIBITED_BY_PROFILE.get(profile, set()))
    targets = sorted(
        (set(outcome.missing_required) | set(outcome.low_confidence_fields))
        & LLM_FILLABLE - set(prohibited)
    )
    schema = {name: "string" for name in targets}
    system = (
        "Extract Indian land-record fields from OCR text. "
        "Return ONLY a JSON object with the requested keys. "
        "Use empty string when a value is not explicitly present. "
        "Never invent identifiers, names, or dates."
    )
    user = (
        f"Target fields (JSON keys): {json.dumps(schema)}\n"
        f"Prohibited (must stay empty): {json.dumps(prohibited)}\n"
        f"OCR text:\n{(ocr_text or '')[:GROQ_MAX_OCR_CHARS]}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def fetch_suggestions(messages: list[dict]) -> dict:
    import httpx

    resp = httpx.post(
        GROQ_ENDPOINT,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={
            "model": settings.groq_model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=settings.groq_timeout_s,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content) if isinstance(content, str) else content
    return parsed if isinstance(parsed, dict) else {}


def _valid_suggestion(field_name: str, value: str, profile: str) -> str:
    from app.services.extraction import (
        DATE_HINT,
        PATTERNS,
        PROHIBITED_BY_PROFILE,
        _inside_date,
        _looks_like_name,
    )

    if field_name not in LLM_FILLABLE:
        return ""
    if field_name in PROHIBITED_BY_PROFILE.get(profile, set()):
        return ""
    cleaned = re.sub(r"\s+", " ", (value or "")).strip(" ,.:;-\t")[:200]
    if not cleaned or len(cleaned) < 2:
        return ""
    if "|" in cleaned or "`" in cleaned:
        return ""
    pattern = PATTERNS.get(field_name)
    if pattern:
        matches = [m for m in pattern.finditer(cleaned)
                   if not _inside_date(cleaned, m)]
        if not matches:
            return ""
        if field_name == "mutation_date" and not DATE_HINT.search(cleaned):
            return ""
        return max((m.group(0).strip() for m in matches), key=len)
    if field_name in {"owner_name", "guardian_name", "previous_owner", "new_owner"}:
        ok, _score = _looks_like_name(cleaned)
        return cleaned if ok else ""
    return cleaned


def apply_suggestions(outcome, suggestions: dict) -> list[str]:
    """Fill empty/low-confidence fields in place. Returns applied field names."""
    from app.services.extraction import FieldExtraction, _fuse

    applied: list[str] = []
    profile = outcome.profile
    for field_name, raw in (suggestions or {}).items():
        if field_name not in LLM_FILLABLE or not isinstance(raw, str):
            continue
        current = outcome.fields.get(field_name)
        if current is None:
            continue
        if current.normalized_value and current.confidence >= settings.CONFIDENCE_MEDIUM:
            continue  # never overwrite a trusted regex value
        valid = _valid_suggestion(field_name, raw, profile)
        if not valid:
            continue
        if not isinstance(current, FieldExtraction):
            continue
        current.value = valid[:200]
        current.normalized_value = valid[:200]
        current.confidence = min(LLM_CONFIDENCE_CAP, max(current.confidence, 55.0))
        current.source = "groq-llm"
        current.method = "groq-llm-verifier"
        current.status = "extracted"
        current.reason = ""
        current.signals = {
            **(current.signals or {}),
            "llm_model": settings.groq_model,
            "llm_pattern_validated": True,
        }
        # Re-fuse lightly so the confidence stays explainable.
        try:
            current.confidence = _fuse(current.confidence, 75.0, 30.0, 70.0)
        except Exception:
            pass
        applied.append(field_name)

    if applied:
        outcome.low_confidence_fields = [
            name for name, f in outcome.fields.items()
            if f.value and f.confidence < settings.CONFIDENCE_MEDIUM
        ]
        for name in applied:
            if name in outcome.missing_required and outcome.fields[name].normalized_value:
                outcome.missing_required = [
                    m for m in outcome.missing_required if m != name
                ]
        scored = [f.confidence for f in outcome.fields.values() if f.value]
        if scored:
            outcome.record_confidence = round(sum(scored) / len(scored), 2)
    return applied


def enhance_outcome(ocr_text: str, outcome):
    """Full second pass. Never raises; returns (outcome, applied)."""
    if not is_enabled() or not needs_llm(outcome):
        return outcome, []
    try:
        suggestions = fetch_suggestions(build_prompt(ocr_text, outcome, outcome.profile))
    except Exception:
        return outcome, []
    try:
        applied = apply_suggestions(outcome, suggestions)
    except Exception:
        return outcome, []
    return outcome, applied
