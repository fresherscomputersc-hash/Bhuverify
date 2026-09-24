"""Pytest bootstrap for BhuVerify.

Sets an isolated SQLite database BEFORE any app import (app.database reads
BHUVERIFY_DB at import time), ensures Tesseract is findable on Windows, and
provides per-test DB sessions with all tables created.
"""
import os
import tempfile
from pathlib import Path

# --- isolated DB: must precede `import app.*` -------------------------------
_tmp = tempfile.NamedTemporaryFile(prefix="bhuverify_test_", suffix=".db", delete=False)
_tmp.close()
os.environ["BHUVERIFY_DB"] = f"sqlite:///{_tmp.name}"
os.environ["BHUVERIFY_SKIP_SEED"] = "1"
os.environ["BHUVERIFY_SECRET"] = "test-secret"

# --- Tesseract on Windows ----------------------------------------------------
import shutil  # noqa: E402

if shutil.which("tesseract") is None:
    for cand in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if Path(cand).exists():
            import pytesseract  # noqa: E402

            pytesseract.pytesseract.tesseract_cmd = cand
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + str(Path(cand).parent)
            break
if "TESSDATA_PREFIX" not in os.environ:
    _local = Path(os.environ.get("LOCALAPPDATA", "") or Path.home()) / "Tesseract" / "tessdata"
    if _local.is_dir():
        os.environ["TESSDATA_PREFIX"] = str(_local)

import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine, init_db  # noqa: E402

init_db()


@pytest.fixture()
def db():
    """Fresh session with all tables truncated between tests."""
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        # truncate all tables to isolate tests
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())
