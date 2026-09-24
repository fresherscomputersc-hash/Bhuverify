---
title: BhuVerify
emoji: 🗺️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# BhuVerify

**Intelligent Land Record Digitization and Validation System**
Smart Hackathon problem statement **SIH26018** · Theme: Smart Automation · Team: **Merge Conflict**

BhuVerify converts legacy land records — scanned registers, handwritten pages, PDFs and
cadastral maps — into structured, validated, GIS-linked digital records. It is not an OCR
wrapper: the pipeline runs computer-vision preprocessing, printed OCR and handwritten
recognition, structured field extraction with field-level confidence, ten named business
rules, duplicate detection, cross-database checks and cadastral linking, then hands the
result to a **government reviewer who holds final authority**. Every AI output and every
human correction is written to an immutable, hash-chained audit trail.

---

## What actually runs here

This is a working prototype, not a mock-up. The parts below are real:

| Component | Implementation | Evidence |
|---|---|---|
| OCR | **Tesseract 5.5.0** with `eng`, `hin`, `ori` packs installed | `tests/test_pipeline.py::test_ocr_reads_the_printed_demo_page` |
| Handwritten recognition | Tesseract HTR pipeline (PSM sweep + handwriting-tuned OpenCV chain) | `test_handwritten_pipeline_is_used_for_devanagari_register` |
| CV preprocessing | OpenCV deskew, denoise, CLAHE, adaptive binarisation, layout regions, stamp detection | `test_deskew_recovers_a_known_skew` (verified at 1, 2, −3, 5, −2.4°) |
| Field extraction | Label-alias index (Latin + Devanagari + Odia), regex validators, NER-style name scoring, unit normalisation, confidence fusion | `tests/test_extraction.py` (26 tests) |
| Validation | BR-1 … BR-10, each independently tested | `tests/test_validation.py` (35 tests) |
| GIS | Shapely polygon area on a local equirectangular projection, 12-polygon sample cadastral layer | `test_polygon_area_matches_the_declared_area` |
| Audit | SHA-256 hash chain; tampering is detected | `test_audit_chain_detects_tampering` |
| Demo documents | **Rendered images**, not text fixtures — PIL draws ruled register pages that Tesseract must genuinely read | `app/sample_documents.py` |

The parts that are **mocked** (deliberately, per SRS FR-8) are the five government-system
adapters — Bhulekh, BhuNaksha, IGR, LGD master data and LRMS. They are deterministic, have
the same call signature and response contract as the live endpoints, and swap over without
touching any caller.

---

## Quick start

```bash
# system dependency
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin tesseract-ocr-ori

python3 -m pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>. On first boot the app creates the schema, generates the six
sample documents and the cadastral layer, and runs each document through the full pipeline,
so you land on a populated reviewer queue.

Interactive API documentation: <http://localhost:8000/docs>

### Seeded accounts

| Username | Password | Role | Can |
|---|---|---|---|
| `operator` | `operator123` | Digitization Operator | upload, view queue |
| `reviewer` | `reviewer123` | Revenue Inspector | review, correct, approve/reject/escalate |
| `supervisor` | `supervisor123` | Tehsildar / Supervisor | dashboards, assign, export |
| `survey` | `survey123` | Survey Official | GIS and cadastral views |
| `auditor` | `auditor123` | Auditor | audit trail and reports |
| `admin` | `admin123` | Administrator | everything, incl. reseed |

---

## Demo walkthrough

The dataset is built so each document demonstrates a different failure mode.

| Document | What it shows |
|---|---|
| `sample_01_ror_clean.png` | Clean printed RoR → high confidence, GIS match, passes nearly everything |
| `sample_02_ror_degraded.jpg` | Skewed, noisy, JPEG-compressed scan → deskew engages; malformed registration reference trips **BR-10** |
| `sample_03_register_handwritten_hindi.png` | Handwritten Devanagari register → HTR path, lower confidence, owner name needs correction |
| `sample_04_conflicting_owner.png` | Khasra 227/1 with an area that disagrees with the polygon by +53% → **BR-9** |
| `sample_05_area_mismatch.png` | Khasra 118/2 held by a *different* owner → **BR-3 critical**, approval is blocked |
| `sample_06_multi_plot_page.png` | Multi-plot page: sub-plots don't sum to the parent (**BR-2**), village not in master (**BR-7**), classification missing (**BR-1**), unparseable date (**BR-4**) |

Suggested flow:

1. Sign in as `reviewer` → the queue is already populated.
2. Open the critical record (khasra 118/2, Bhagaban Nayak) → try **Approve**. It is
   refused with HTTP 409 because a critical finding is open. That is the human-in-the-loop
   guarantee, enforced server-side rather than in the UI.
3. Open the clean record → correct the guardian name → **Save corrections**. The change is
   audit-logged with old and new values and added to the training dataset.
4. **Approve** it, then open **Audit Trail** and click *Re-verify chain now*.
5. Open **Cadastral Map** → click a red-outlined plot to see the BR-9 area disagreement.
6. Open **Validation Rules** → run a dry-run against an ad-hoc record.
7. Sign in as `supervisor` → **MIS Dashboard** → export the CSV.

---

## Architecture

```
Document upload (FR-1)
      ↓
CV preprocessing + layout detection (FR-2)      OpenCV
      ↓
OCR / HTR with word-level confidence (FR-3)     Tesseract 5, eng/hin/ori
      ↓
Structured field extraction (FR-4)              label aliases + regex + NER-style scoring
      ↓
Field-level confidence fusion (FR-5)            0.45·OCR + 0.30·pattern + 0.15·proximity + 0.10·consistency
      ↓
GIS / cadastral linking (FR-9)                  Shapely, sample GeoJSON layer
      ↓
Business rule validation BR-1…BR-10 (FR-6/7)
      ↓
Cross-database verification (FR-8)              mock adapters
      ↓
Reviewer verification (FR-10)                   approve / reject / escalate
      ↓
Immutable audit trail (FR-12)                   SHA-256 hash chain
      ↓
MIS dashboard, search, APIs (FR-11/13/17)
```

Stack: FastAPI · SQLAlchemy · OpenCV · Tesseract · Shapely · rapidfuzz · argon2.
Frontend is vanilla JS with a hash router and no build step, so it runs unchanged in a
network-isolated environment. The map is rendered as inline SVG rather than pulling
Leaflet from a CDN.

### Prototype → pilot → production

The prototype deliberately keeps the same contracts the later tiers need:

| Concern | Prototype (this repo) | Pilot / production swap |
|---|---|---|
| Database | SQLite | PostgreSQL + PostGIS (change the connection URL) |
| Queue | In-process thread worker | Redis/Celery — stage names and status transitions already match |
| HTR | Tesseract + OpenCV chain | TrOCR / IndicHTR transformer — `WordToken` contract unchanged |
| Layout | OpenCV heuristics | YOLO / Detectron2 — `detect_layout` returns the same labelled boxes |
| External systems | Deterministic mocks | Read-only government APIs — `ExternalCheck` contract unchanged |
| Auth | Seeded accounts + argon2 tokens | Department SSO + MFA — routes only ever see a `User` |
| GIS | Sample GeoJSON + Leaflet-free SVG | GeoServer + PostGIS |

---

## Tests

```bash
python3 -m pytest tests/ -q
```

**83 tests** (`python -m pytest tests/ -q`). They cover:

- unit conversion and area normalisation across acre/hectare/decimal/bigha/katha/guntha/kanal/marla
- field extraction on official layouts (NIC table-ROR, Hindi ROR, khatauni tables):
  multi-pair line splitting, table-row owner/cell fallback, date-fragment
  rejection, ROR references, 5–6 digit khatas, खेसरा/गाटा/रकबा/किसम aliases
- every one of BR-1 … BR-10, including the cases that must *not* fire
- deskew accuracy verified against synthetic skews rather than assumed
- real OCR of the generated demo pages, with a latency assertion against the SRS budget
- GIS polygon areas reconciled against declared areas
- audit chain verification **and** tamper detection
- timezone regression: naive SQLite timestamps must not crash the pending-review alerts
- API acceptance over HTTP (health, auth, RBAC, validation budget, upload guards)
- RBAC — including that an operator cannot read the audit trail

Two behaviours are asserted rather than claimed: validation runs in under 1 s per record
and the full pipeline in under 10 s per document (SRS 7.1).

---

## Repository layout

```
app/
  main.py                 FastAPI app + static mount + boot seeding
  config.py               confidence bands, performance budgets, paths
  models.py               14 tables (SRS 6.1 core entities)
  security.py             argon2 hashing, opaque tokens, RBAC matrix
  master_data.py          Khordha tehsil/village master + Indic-script aliases
  sample_documents.py     renders the six demo register pages
  sample_geojson.py       builds the sample cadastral layer
  seed.py                 idempotent boot seeding + demo replay
  routers/                auth, documents, records, audit, map, dashboard, system
  services/
    cv_preprocess.py      FR-2
    ocr_service.py        FR-3
    extraction.py         FR-4, FR-5
    validation.py         FR-6, FR-7
    gis_service.py        FR-9
    external_adapters.py  FR-8 (mocks)
    worker.py             pipeline orchestration
    audit.py              FR-12 hash chain
    metrics.py            FR-13
    notifications.py      FR-15
static/                   console (index.html, css, js) — no build step
tests/                    101 tests
tools/build_deck.py       regenerates the pitch deck from live API data
BhuVerify_SIH26018_Pitch_Deck.pptx   judge-facing deck (12 slides)
docs/                     reserved for the team's PRD / SRS source documents
```

---

## Legal position

BhuVerify is decision support. It does not adjudicate disputes, does not change ownership,
and does not write back to Bhulekh, BhuNaksha, IGR or LRMS. Discrepancies are flagged, not
resolved. Every record requires approval by an authorised reviewer, and every correction is
audit-logged. Write-back would require formal government approval.
