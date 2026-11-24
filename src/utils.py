"""Shared utility functions for SenoNetwork (atomic I/O, sanitization, date & name normalization, PHI masking)."""

from datetime import date, datetime
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Optional
import unicodedata


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically writes text content to `path` via a temporary file and `os.replace`."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def sanitize_identifier(raw_id: Optional[str], default: str = "patient_unknown") -> str:
    """Sanitizes a patient/record identifier to prevent path traversal and unsafe filenames."""
    raw = str(raw_id or "").strip()
    if not raw:
        return default
    base_name = Path(raw).name
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", base_name).strip("_")
    return sanitized or default


def normalize_person_name(text: Optional[str]) -> str:
    """Normalizes a person name/surname (ASCII diacritics, apostrophes, punctuation) for matching."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", str(text)).encode("ASCII", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]", "", norm.lower())


def mask_phi(value: Optional[str]) -> str:
    """Masks personal identifiable strings for GDPR-compliant application logs."""
    if not value:
        return "-"
    cleaned = str(value).strip()
    if not cleaned:
        return "-"
    if len(cleaned) <= 1:
        return "*"
    return f"{cleaned[0]}***"


def parse_flexible_date(val: Any) -> Optional[date]:
    """Safely parses common date inputs (`date`, `datetime`, ISO, Italian DD/MM/YYYY) into a `date`."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s or s.lower() in {"none", "nan", "null", "n.d.", "nd"}:
        return None
    candidate = s[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None
