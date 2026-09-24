"""
BhuVerify data model.

Entity set is taken directly from SRS section 6.1 (Core Entities):
SourceDocument, LandRecord, Owner, ExtractionResult, MutationRecord,
Discrepancy, SpatialGeometry, User, AuditLog, Notification, CorrectionDataset.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """JSON-safe timestamp: SQLite strips tzinfo on read, so naive values are
    explicitly marked UTC. Without the offset the browser parses the string
    as local time (IST = +5:30 shows every upload as ~6h ago)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "+00:00"
    return value.isoformat()


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class Role(str, enum.Enum):
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    SUPERVISOR = "supervisor"
    SURVEY_OFFICIAL = "survey_official"
    AUDITOR = "auditor"
    ADMIN = "admin"


class DocumentStatus(str, enum.Enum):
    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    OCR_RUNNING = "ocr_running"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"


class RecordStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionType(str, enum.Enum):
    DOCUMENT_UPLOADED = "document_uploaded"
    DOCUMENT_DUPLICATE_FILE = "document_duplicate_file"
    PREPROCESSING_DONE = "preprocessing_done"
    OCR_EXECUTED = "ocr_executed"
    FIELD_EXTRACTED = "field_extracted"
    CONFIDENCE_GENERATED = "confidence_generated"
    VALIDATION_RULE_EXECUTED = "validation_rule_executed"
    DISCREPANCY_CREATED = "discrepancy_created"
    GIS_LINKED = "gis_linked"
    CROSS_DB_CHECK = "cross_database_check"
    FIELD_CORRECTED = "field_corrected"
    RECORD_APPROVED = "record_approved"
    RECORD_REJECTED = "record_rejected"
    RECORD_ESCALATED = "record_escalated"
    RECORD_ASSIGNED = "record_assigned"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    API_ACCESSED = "api_accessed"
    SEARCH_EXECUTED = "search_executed"
    NOTIFICATION_SENT = "notification_sent"


class NotificationStatus(str, enum.Enum):
    UNREAD = "unread"
    READ = "read"


# ---------------------------------------------------------------------------
# Identity / access (FR-14)
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(160))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.OPERATOR)
    designation: Mapped[str] = mapped_column(String(120), default="")
    district_scope: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Session(Base):
    """Opaque-token sessions (prototype RBAC; production swaps for SSO + MFA)."""

    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str] = mapped_column(String(64), default="")


# ---------------------------------------------------------------------------
# Ingestion + repository (FR-1)
# ---------------------------------------------------------------------------
class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    batch_id: Mapped[str] = mapped_column(String(40), index=True, default="")
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    enhanced_path: Mapped[str] = mapped_column(String(512), default="")
    preview_path: Mapped[str] = mapped_column(String(512), default="")
    mime_type: Mapped[str] = mapped_column(String(80), default="")
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    doc_type: Mapped[str] = mapped_column(String(60), default="ror")
    language: Mapped[str] = mapped_column(String(16), default="eng")
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), default=DocumentStatus.QUEUED, index=True
    )
    # PDFs can carry the record across pages (39-A: page 1 admin + persons,
    # page 2 parcels). The pipeline OCRs every page up to the worker cap.
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    ocr_engine: Mapped[str] = mapped_column(String(60), default="")
    ocr_language: Mapped[str] = mapped_column(String(24), default="")
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    ocr_word_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_mean_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ocr_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    layout_json: Mapped[dict] = mapped_column(JSON, default=dict)
    preprocessing_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    total_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    record: Mapped["LandRecord | None"] = relationship(
        back_populates="document", uselist=False, cascade="all, delete-orphan"
    )
    audit_events: Mapped[list["AuditLog"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# Structured land record (FR-4)
# ---------------------------------------------------------------------------
class LandRecord(Base):
    __tablename__ = "land_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)

    # Administrative / identification fields
    khasra_no: Mapped[str] = mapped_column(String(60), default="", index=True)
    khata_no: Mapped[str] = mapped_column(String(60), default="", index=True)
    survey_no: Mapped[str] = mapped_column(String(60), default="", index=True)
    plot_no: Mapped[str] = mapped_column(String(60), default="", index=True)
    village: Mapped[str] = mapped_column(String(160), default="", index=True)
    tehsil: Mapped[str] = mapped_column(String(160), default="", index=True)
    district: Mapped[str] = mapped_column(String(160), default="", index=True)
    state: Mapped[str] = mapped_column(String(120), default="")

    # Ownership
    owner_name: Mapped[str] = mapped_column(String(200), default="", index=True)
    guardian_name: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    # Odisha Form 39-A and friends keep person-level owners with relations
    # (see services/extraction.parse_praja_section). The canonical
    # owner_name holds the primary holder; the full list lives here.
    owners_json: Mapped[dict] = mapped_column(JSON, default=list)

    # Area / classification
    area_value: Mapped[float] = mapped_column(Float, default=0.0)
    area_unit: Mapped[str] = mapped_column(String(40), default="")
    area_hectare: Mapped[float] = mapped_column(Float, default=0.0)
    parent_khasra_no: Mapped[str] = mapped_column(String(60), default="")
    land_classification: Mapped[str] = mapped_column(String(120), default="")
    boundaries: Mapped[str] = mapped_column(Text, default="")

    # Mutation / registration
    mutation_no: Mapped[str] = mapped_column(String(80), default="")
    mutation_date: Mapped[str] = mapped_column(String(20), default="")
    registration_no: Mapped[str] = mapped_column(String(120), default="")
    registration_date: Mapped[str] = mapped_column(String(20), default="")
    previous_owner: Mapped[str] = mapped_column(String(200), default="")
    new_owner: Mapped[str] = mapped_column(String(200), default="")
    document_type_label: Mapped[str] = mapped_column(String(120), default="")
    # Document-specific identifiers that are NOT canonical Khata/Khasra
    # (Odisha 39-A: Khewat No, Khatiyan serial No, Tehsil No). Stored
    # verbatim; never mapped into khata_no/khasra_no.
    khewat_no: Mapped[str] = mapped_column(String(60), default="")
    khatiyan_no: Mapped[str] = mapped_column(String(60), default="")
    tehsil_no: Mapped[str] = mapped_column(String(60), default="")
    # Directional boundary (chouhaddi): {north, south, east, west}; absent
    # directions stay null rather than invented.
    boundary_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # Pipeline outcome
    status: Mapped[RecordStatus] = mapped_column(
        Enum(RecordStatus), default=RecordStatus.PENDING_REVIEW, index=True
    )
    record_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    discrepancy_count: Mapped[int] = mapped_column(Integer, default=0)
    highest_severity: Mapped[str] = mapped_column(String(20), default="")
    assigned_to_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    gis_linked: Mapped[bool] = mapped_column(default=False)
    cross_db_status: Mapped[str] = mapped_column(String(40), default="not_checked")
    reviewed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[SourceDocument] = relationship(back_populates="record")
    extractions: Mapped[list["ExtractionResult"]] = relationship(
        back_populates="record", cascade="all, delete-orphan", order_by="ExtractionResult.id"
    )
    discrepancies: Mapped[list["Discrepancy"]] = relationship(
        back_populates="record", cascade="all, delete-orphan", order_by="Discrepancy.id"
    )
    geometry: Mapped["SpatialGeometry | None"] = relationship(
        back_populates="record", uselist=False, cascade="all, delete-orphan"
    )
    mutations: Mapped[list["MutationRecord"]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )
    cross_db_results: Mapped[list["CrossCheckResult"]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )


class ExtractionResult(Base):
    """Field-level extraction artifact with confidence + evidence (FR-4, FR-5)."""

    __tablename__ = "extraction_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("land_records.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(80), index=True)
    field_label: Mapped[str] = mapped_column(String(120), default="")
    value: Mapped[str] = mapped_column(Text, default="")
    normalized_value: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    source: Mapped[str] = mapped_column(String(40), default="ocr")  # ocr|htr|regex|ner|mock
    evidence_text: Mapped[str] = mapped_column(Text, default="")
    bbox: Mapped[dict] = mapped_column(JSON, default=dict)  # {x,y,w,h} in source px
    page: Mapped[int] = mapped_column(Integer, default=1)
    is_low_confidence: Mapped[bool] = mapped_column(default=False, index=True)
    corrected_value: Mapped[str] = mapped_column(Text, default="")
    # Per-field extraction status (spec section 7): extracted | missing |
    # needs_review, plus a machine reason (field_not_present,
    # canonical_field_not_explicitly_present, ocr_uncertain), the method that
    # produced the value (label_value, table_row, pattern_sweep, ...) and the
    # 1-based PDF page it came from.
    status: Mapped[str] = mapped_column(String(20), default="extracted", index=True)
    reason: Mapped[str] = mapped_column(String(60), default="")
    method: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    record: Mapped[LandRecord] = relationship(back_populates="extractions")


class MutationRecord(Base):
    __tablename__ = "mutation_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("land_records.id"), index=True)
    mutation_no: Mapped[str] = mapped_column(String(80), default="")
    prior_owner: Mapped[str] = mapped_column(String(200), default="")
    new_owner: Mapped[str] = mapped_column(String(200), default="")
    mutation_date: Mapped[str] = mapped_column(String(20), default="")
    registration_ref: Mapped[str] = mapped_column(String(120), default="")
    sequence: Mapped[int] = mapped_column(Integer, default=1)

    record: Mapped[LandRecord] = relationship(back_populates="mutations")


# ---------------------------------------------------------------------------
# Validation (FR-6, FR-7)
# ---------------------------------------------------------------------------
class Discrepancy(Base):
    __tablename__ = "discrepancies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("land_records.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(16), index=True)   # BR-1 ... BR-10
    rule_name: Mapped[str] = mapped_column(String(160))
    severity: Mapped[Severity] = mapped_column(Enum(Severity), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    actual: Mapped[str] = mapped_column(Text, default="")
    conflicting_values: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_refs: Mapped[dict] = mapped_column(JSON, default=dict)  # doc_id, field, bbox
    recommended_action: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    resolved_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    record: Mapped[LandRecord] = relationship(back_populates="discrepancies")


# ---------------------------------------------------------------------------
# Cross-database verification (FR-8)
# ---------------------------------------------------------------------------
class CrossCheckResult(Base):
    __tablename__ = "cross_check_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("land_records.id"), index=True)
    source_system: Mapped[str] = mapped_column(String(40))  # bhulekh|bhunaksha|igr|lrms|lgd
    mode: Mapped[str] = mapped_column(String(16), default="mock")  # mock|live
    match_status: Mapped[str] = mapped_column(String(24), default="no_match")
    severity: Mapped[str] = mapped_column(String(16), default="low")
    external_reference: Mapped[str] = mapped_column(String(160), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    record: Mapped[LandRecord] = relationship(back_populates="cross_db_results")


# ---------------------------------------------------------------------------
# GIS (FR-9)
# ---------------------------------------------------------------------------
class SpatialGeometry(Base):
    __tablename__ = "spatial_geometries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("land_records.id"), index=True, unique=True)
    geometry_id: Mapped[str] = mapped_column(String(40), default="")
    plot_key: Mapped[str] = mapped_column(String(60), default="", index=True)
    geojson: Mapped[dict] = mapped_column(JSON, default=dict)
    area_sqm: Mapped[float] = mapped_column(Float, default=0.0)
    source_map_ref: Mapped[str] = mapped_column(String(200), default="")
    match_type: Mapped[str] = mapped_column(String(40), default="")  # khasra|survey|plot|none
    spatial_mismatch: Mapped[bool] = mapped_column(default=False)
    area_delta_pct: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    record: Mapped[LandRecord] = relationship(back_populates="geometry")


# ---------------------------------------------------------------------------
# Audit (FR-12) - append-only
# ---------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    actor_label: Mapped[str] = mapped_column(String(120), default="system")
    action: Mapped[ActionType] = mapped_column(Enum(ActionType), index=True)
    entity_type: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str] = mapped_column(String(60), default="", index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), nullable=True)
    field_name: Mapped[str] = mapped_column(String(80), default="")
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    ai_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[str] = mapped_column(Text, default="")
    evidence_ref: Mapped[str] = mapped_column(String(200), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    entry_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    document: Mapped[SourceDocument | None] = relationship(back_populates="audit_events")
    user: Mapped[User | None] = relationship()


# ---------------------------------------------------------------------------
# Notifications (FR-15) and continuous learning (FR-16)
# ---------------------------------------------------------------------------
class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    role_target: Mapped[str] = mapped_column(String(30), default="")
    level: Mapped[str] = mapped_column(String(16), default="info")
    title: Mapped[str] = mapped_column(String(160), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus), default=NotificationStatus.UNREAD
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CorrectionDataset(Base):
    """Labeled (AI value -> human value) pairs used for model fine-tuning."""

    __tablename__ = "correction_dataset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("land_records.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(80), index=True)
    ai_value: Mapped[str] = mapped_column(Text, default="")
    corrected_value: Mapped[str] = mapped_column(Text, default="")
    ai_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    language: Mapped[str] = mapped_column(String(16), default="eng")
    source_ref: Mapped[str] = mapped_column(String(120), default="")
    corrected_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessingJob(Base):
    """Queue entry (SRS 5.3 async processing queue)."""

    __tablename__ = "processing_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)
    stage: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(160), default="")
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_duplicates: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
