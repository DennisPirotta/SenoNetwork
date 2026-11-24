"""Shared utility functions for SenoNetwork (atomic I/O, sanitization, date & name normalization, PHI masking)."""

from contextlib import contextmanager
from datetime import date, datetime
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Dict, Iterator, Optional, Union
import unicodedata


_LLM_SEMAPHORES_LOCK = threading.Lock()
_LLM_SEMAPHORES: Dict[int, threading.Semaphore] = {}
_LLM_SLOT_TLS = threading.local()

_PATIENT_LOCKS_LOCK = threading.Lock()
_PATIENT_LOCKS: Dict[str, threading.Lock] = {}


def get_llm_semaphore(max_concurrent: int = 1) -> threading.Semaphore:
    """Returns a process-wide bounded semaphore keyed by concurrency limit."""
    limit = max(1, int(max_concurrent if isinstance(max_concurrent, int) else 1))
    with _LLM_SEMAPHORES_LOCK:
        if limit not in _LLM_SEMAPHORES:
            _LLM_SEMAPHORES[limit] = threading.Semaphore(limit)
        return _LLM_SEMAPHORES[limit]


@contextmanager
def llm_slot(max_concurrent: int = 1) -> Iterator[None]:
    """Thread-reentrant semaphore guard so nested LLM calls on the same worker never self-deadlock."""
    limit = max(1, int(max_concurrent if isinstance(max_concurrent, int) else 1))
    depths = getattr(_LLM_SLOT_TLS, "depths", None)
    if depths is None:
        depths = {}
        _LLM_SLOT_TLS.depths = depths
    current_depth = depths.get(limit, 0)
    if current_depth > 0:
        depths[limit] = current_depth + 1
        try:
            yield
        finally:
            depths[limit] = current_depth
        return

    sem = get_llm_semaphore(limit)
    sem.acquire()
    depths[limit] = 1
    try:
        yield
    finally:
        depths[limit] = 0
        sem.release()


def get_patient_lock(patient_id: str) -> threading.Lock:
    """Returns a process-wide mutex keyed by sanitized patient_id to serialize writes per patient."""
    key = sanitize_identifier(patient_id)
    with _PATIENT_LOCKS_LOCK:
        if len(_PATIENT_LOCKS) > 1000:
            stale = [k for k, lck in _PATIENT_LOCKS.items() if not lck.locked()]
            for k in stale[: len(stale) // 2]:
                _PATIENT_LOCKS.pop(k, None)
        if key not in _PATIENT_LOCKS:
            _PATIENT_LOCKS[key] = threading.Lock()
        return _PATIENT_LOCKS[key]


def _atomic_write_raw(path: Path, data: Union[str, bytes], binary: bool = False, encoding: str = "utf-8") -> None:
    """Internal helper writing text or binary payload atomically via temporary file and `os.replace`."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Optional[Path] = None
    try:
        kwargs: dict[str, Any] = {
            "mode": "wb" if binary else "w",
            "dir": target.parent,
            "prefix": f".{target.name}.",
            "suffix": ".tmp",
            "delete": False,
        }
        if not binary:
            kwargs["encoding"] = encoding
        with tempfile.NamedTemporaryFile(**kwargs) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(data)
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


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically writes text content to `path` via a temporary file and `os.replace`."""
    _atomic_write_raw(path, content, binary=False, encoding=encoding)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Atomically writes binary content to `path` via a temporary file and `os.replace`."""
    _atomic_write_raw(path, content, binary=True)


def sanitize_identifier(raw_id: Optional[str], default: str = "patient_unknown", max_len: Optional[int] = None) -> str:
    """Sanitizes a patient/record identifier to prevent path traversal and unsafe filenames."""
    raw = str(raw_id or "").strip()
    if not raw:
        return default
    base_name = Path(raw).name
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", base_name).strip("_")
    if max_len is not None and max_len > 0:
        sanitized = sanitized[:max_len].strip("_")
    return sanitized or default


def normalize_person_name(text: Optional[str]) -> str:
    """Normalizes a person name/surname (ASCII diacritics, apostrophes, punctuation) for matching."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", str(text)).encode("ASCII", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]", "", norm.lower())


def build_patient_slug(
    name: Optional[str],
    surname: Optional[str],
    date_of_birth: Any = None,
    default: str = "paziente-anonimo",
) -> str:
    """Builds a consistent ASCII hyphenated slug (NFKD accent-folded) from patient demographics."""
    dob_parsed = parse_flexible_date(date_of_birth)
    if dob_parsed is not None:
        dob_str = dob_parsed.strftime("%Y-%m-%d")
    elif date_of_birth:
        dob_str = str(date_of_birth).strip()
    else:
        dob_str = ""

    raw_parts = [str(p).strip() for p in (name, surname, dob_str) if p and str(p).strip()]
    if not raw_parts:
        return default

    joined = "-".join(raw_parts).lower()
    ascii_norm = unicodedata.normalize("NFKD", joined).encode("ASCII", "ignore").decode("utf-8")
    slug = ascii_norm.replace(" ", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or default


def sanitize_csv_cell(value: Any) -> Any:
    """Neutralizes spreadsheet formula injection prefixes (`=`, `+`, `-`, `@`) while keeping numeric negatives."""
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if not stripped:
        return value
    if stripped[0] in ("=", "+", "-", "@"):
        # Preserve valid signed numbers (e.g. "-12" or "-3.5") for REDCap numeric import compatibility
        if re.fullmatch(r"-\d+(?:\.\d+)?", stripped):
            return value
        return f"'{value}"
    return value


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
