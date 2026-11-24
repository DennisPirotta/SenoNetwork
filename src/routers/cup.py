"""PriamoCUP Worksheets and Patients API Router."""

from datetime import datetime
import hashlib
import io
import json
import logging
import math
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
import pandas as pd
import requests

from src.config import Settings, get_settings
from src.redcap_service import REDCapService
from src.priamo_cup_client import (
    AGENDE_SENOLOGIA,
    UNITA_SENOLOGIA,
    PriamoCupClient,
)
from scripts.extract_patients_from_shared_file import estrai_pazienti_univoci
from src.routers.security import raise_internal_error, verify_api_key
from src.utils import normalize_person_name, sanitize_csv_cell, sanitize_identifier

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Priamo CUP"])

_CACHE_LOCK = threading.Lock()
MAX_CUP_CACHE_ENTRIES = 32

# Cache in-memory dei pazienti estratti dai file CUP: (resolved_path, mtime_ns, only_complete) -> List[Paziente]
_CUP_PARSED_CACHE: Dict[Tuple[str, int, bool], List[Any]] = {}

# Cache in-memory per la corrispondenza dei record REDCap partizionata per progetto: TTL 60s
_REDCAP_LOOKUP_BY_PROJECT: Dict[str, Dict[str, Any]] = {}


def _redcap_project_cache_key(settings: Settings) -> str:
    url = (settings.redcap_api_url or "").strip()
    tok = (settings.redcap_api_token or "").strip()
    tok_hash = hashlib.sha256(tok.encode("utf-8")).hexdigest()[:12] if tok else "anon"
    return f"{url}::{tok_hash}"


def invalidate_redcap_lookup_cache(settings: Optional[Settings] = None) -> None:
    """Invalidates cached REDCap lookup indices after mutations (imports or deletions)."""
    with _CACHE_LOCK:
        if settings is not None:
            _REDCAP_LOOKUP_BY_PROJECT.pop(_redcap_project_cache_key(settings), None)
        else:
            _REDCAP_LOOKUP_BY_PROJECT.clear()


def _normalize_dob(dob: Any) -> str:
    """Normalizza una data di nascita in formato ISO 'YYYY-MM-DD'."""
    if not dob:
        return ""
    if hasattr(dob, "isoformat"):
        return dob.isoformat()[:10]
    s = str(dob).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s


def _empty_lookup_entry() -> Dict[str, Any]:
    return {
        "timestamp": 0.0,
        "by_cf": {},
        "by_demographics": {},
        "ambiguous_demographics": set(),
        "by_id": {},
    }


def _get_redcap_lookup(settings: Settings, force_refresh: bool = False) -> Dict[str, Any]:
    """Recupera l'elenco dei record REDCap per il matching con i pazienti CUP, con cache a 60 secondi per progetto."""
    proj_key = _redcap_project_cache_key(settings)
    now = time.time()
    with _CACHE_LOCK:
        cached = _REDCAP_LOOKUP_BY_PROJECT.get(proj_key)
        if not force_refresh and cached and cached["timestamp"] > 0 and (now - cached["timestamp"]) < 60:
            return cached

    if not settings.redcap_api_url or not settings.redcap_api_token:
        with _CACHE_LOCK:
            return _REDCAP_LOOKUP_BY_PROJECT.get(proj_key) or _empty_lookup_entry()

    try:
        svc = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)
        summary = svc.get_records_summary()
        by_cf: Dict[str, Dict[str, Any]] = {}
        by_demo: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        ambiguous_demo: set = set()
        by_id: Dict[str, Dict[str, Any]] = {}

        for rec in summary:
            rid = str(rec.get("record_id", "")).strip()
            name_norm = normalize_person_name(rec.get("name"))
            sur_norm = normalize_person_name(rec.get("surname"))
            dob_norm = _normalize_dob(rec.get("date_of_birth"))
            raw_rec = rec.get("raw_record") or {}
            cf_norm = str(rec.get("codice_fiscale") or raw_rec.get("codice_fiscale") or "").strip().upper()
            forms_completed = rec.get("forms_completed_count", 0)

            rec_info = {
                "record_id": rid,
                "forms_completed_count": forms_completed,
                "forms_status": rec.get("forms_status", {}),
            }

            if rid:
                by_id[rid.lower()] = rec_info

            if cf_norm:
                by_cf[cf_norm] = rec_info

            if sur_norm and name_norm and dob_norm:
                demo_key = (sur_norm, name_norm, dob_norm)
                if demo_key in by_demo:
                    ambiguous_demo.add(demo_key)
                else:
                    by_demo[demo_key] = rec_info

        for key in ambiguous_demo:
            by_demo.pop(key, None)

        new_cache = {
            "timestamp": time.time(),
            "by_cf": by_cf,
            "by_demographics": by_demo,
            "ambiguous_demographics": ambiguous_demo,
            "by_id": by_id,
        }
        with _CACHE_LOCK:
            if len(_REDCAP_LOOKUP_BY_PROJECT) >= 16:
                oldest_proj = next(iter(_REDCAP_LOOKUP_BY_PROJECT))
                _REDCAP_LOOKUP_BY_PROJECT.pop(oldest_proj, None)
            _REDCAP_LOOKUP_BY_PROJECT[proj_key] = new_cache
            return new_cache
    except Exception as e:
        logger.debug("Impossibile aggiornare cache record REDCap per CUP: %s", e)
        with _CACHE_LOCK:
            return _REDCAP_LOOKUP_BY_PROJECT.get(proj_key) or _empty_lookup_entry()


def _check_local_state(patient_id: str, state_dir: Path) -> Tuple[bool, bool]:
    """Verifica l'esistenza del file di stato locale e lo status dell'upload su REDCap."""
    safe_id = sanitize_identifier(patient_id, default="")
    if not safe_id:
        return False, False
    p_path = Path(state_dir) / f"{safe_id}_state.json"
    if not p_path.exists():
        return False, False
    try:
        with open(p_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            uploaded = data.get("redcap_upload_status") == "uploaded"
            return True, uploaded
    except Exception as e:
        logger.debug("Impossibile leggere state file %s: %s", p_path, e)
        return True, False


class CupDownloadRequest(BaseModel):
    ytd: bool = Field(default=False, description="Scarica da 1° Gennaio fino a ieri (YTD)")
    year: Optional[int] = Field(default=None, ge=2000, le=2100, description="Anno intero da scaricare (es. 2025)")
    month: Optional[str] = Field(default=None, description="Mese da scaricare (YYYY-MM o MM, es. '2026-10')")
    months: Optional[int] = Field(default=None, ge=1, le=60, description="Ultimi N mesi da scaricare")
    start_date: Optional[str] = Field(default=None, description="Data inizio (YYYY-MM-DD)")
    end_date: Optional[str] = Field(default=None, description="Data fine (YYYY-MM-DD)")
    weeks: Optional[int] = Field(default=None, ge=1, le=104, description="Numero di settimane")
    unit: str = Field(default=UNITA_SENOLOGIA, pattern=r"^[A-Za-z0-9_\-]{1,20}$", description="Codice unità operativa (default: 37002 Senologia)")
    agendas: Optional[List[str]] = Field(default=None, description="Elenco agende (default: 3000, 3003, 3004, 3035, 3036)")


def _find_target_cup_file(file_name: Optional[str] = None, cup_dir: Optional[Path] = None) -> Path:
    resolved_dir = Path(cup_dir or "data/cup")
    if not resolved_dir.exists():
        raise HTTPException(status_code=404, detail="Nessun foglio di lavoro CUP scaricato trovato (cartella CUP vuota).")

    if file_name and file_name.strip():
        clean_name = Path(file_name.strip()).name
        target = resolved_dir / clean_name
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"File CUP non trovato: {clean_name}")
        return target

    files = sorted(
        list(resolved_dir.glob("*.xlsx")) + list(resolved_dir.glob("*.xls")),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )
    if not files:
        raise HTTPException(status_code=404, detail="Nessun file Excel CUP trovato nella cartella configurata.")
    return files[0]


def _get_cup_patients_cached(f: Path, only_complete: bool = False) -> List[Any]:
    """Parses and caches unique patients extracted from a CUP Excel worksheet keyed by `(path, mtime_ns, only_complete)`."""
    if not f.exists():
        return estrai_pazienti_univoci(f, only_complete=only_complete)
    mtime_ns = f.stat().st_mtime_ns
    key = (str(f.resolve()), mtime_ns, bool(only_complete))
    with _CACHE_LOCK:
        if key in _CUP_PARSED_CACHE:
            return list(_CUP_PARSED_CACHE[key])

    pts = estrai_pazienti_univoci(f, only_complete=only_complete)
    with _CACHE_LOCK:
        if len(_CUP_PARSED_CACHE) >= MAX_CUP_CACHE_ENTRIES:
            oldest = next(iter(_CUP_PARSED_CACHE))
            _CUP_PARSED_CACHE.pop(oldest, None)
        _CUP_PARSED_CACHE[key] = list(pts)
    return pts


def _get_cup_patient_count(f: Path) -> int:
    """Restituisce il numero di pazienti univoci memorizzato in cache per il file specificato."""
    try:
        return len(_get_cup_patients_cached(f, only_complete=False))
    except Exception as e:
        logger.warning("Impossibile estrarre conteggio pazienti da %s: %s", f.name, e)
        return 0


@router.post("/api/cup/download", dependencies=[Depends(verify_api_key)])
def download_cup_worksheets(
    req: CupDownloadRequest,
    settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Scarica il foglio di lavoro aggiornato da PriamoCUP e ne estrae il riepilogo anagrafico."""
    if not settings.priamo_username or not settings.priamo_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credenziali Priamo non configurate in .env (PRIAMO_USERNAME, PRIAMO_PASSWORD)."
        )

    try:
        cup_client = PriamoCupClient(settings=settings)
        agendas = req.agendas or AGENDE_SENOLOGIA

        downloaded_file = cup_client.download_updated_worksheets(
            ytd=req.ytd,
            year=req.year,
            month=req.month,
            months=req.months,
            start_date=req.start_date,
            end_date=req.end_date,
            weeks=req.weeks,
            codice_unita=req.unit,
            agende=agendas,
            output_dir=str(settings.cup_dir),
        )

        # Estrai pazienti per contare i record univoci e popola la cache
        pazienti = estrai_pazienti_univoci(downloaded_file)
        if downloaded_file.exists():
            key = (str(downloaded_file.resolve()), downloaded_file.stat().st_mtime_ns, False)
            with _CACHE_LOCK:
                if len(_CUP_PARSED_CACHE) >= MAX_CUP_CACHE_ENTRIES:
                    oldest = next(iter(_CUP_PARSED_CACHE))
                    _CUP_PARSED_CACHE.pop(oldest, None)
                _CUP_PARSED_CACHE[key] = list(pazienti)

        size_bytes = downloaded_file.stat().st_size
        size_kb = size_bytes / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{(size_kb / 1024):.2f} MB"

        return {
            "status": "success",
            "file_name": downloaded_file.name,
            "file_path": str(downloaded_file),
            "size_bytes": size_bytes,
            "size_formatted": size_str,
            "total_unique_patients": len(pazienti),
            "unit": req.unit,
            "agendas": agendas,
            "message": f"Foglio CUP scaricato con successo ({len(pazienti)} pazienti univoci)."
        }
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossibile connettersi a Priamo CUP ({settings.priamo_cup_url}): rete non raggiungibile. Verificare che la VPN Valduce sia attiva."
        )
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Timeout durante la connessione a Priamo CUP ({settings.priamo_cup_url})."
        )
    except Exception as e:
        raise_internal_error(logger, "Errore download PriamoCUP", e)


@router.post("/api/cup/download-latest", dependencies=[Depends(verify_api_key)])
def download_latest_cup_worksheet(
    req: Optional[CupDownloadRequest] = Body(default=None),
    settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Scarica il foglio di lavoro più recente da PriamoCUP (default: YTD da 1° Gennaio fino a ieri)."""
    if req is None:
        req = CupDownloadRequest(ytd=True)
    elif not req.ytd and not req.year and not req.month and not req.months and not req.start_date and not req.weeks:
        req.ytd = True

    return download_cup_worksheets(req=req, settings=settings)


@router.get("/api/cup/files", dependencies=[Depends(verify_api_key)])
def list_cup_files(settings: Settings = Depends(get_settings)) -> List[Dict[str, Any]]:
    """Elenca tutti i file Excel di foglio lavoro scaricati e archiviati nella cartella CUP."""
    cup_dir = Path(settings.cup_dir)
    if not cup_dir.exists():
        return []

    files = sorted(
        list(cup_dir.glob("*.xlsx")) + list(cup_dir.glob("*.xls")),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )

    items = []
    for f in files:
        count = _get_cup_patient_count(f)
        st = f.stat()
        size_kb = st.st_size / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{(size_kb / 1024):.2f} MB"
        mtime = st.st_mtime
        items.append({
            "name": f.name,
            "path": str(f),
            "size_bytes": st.st_size,
            "size_formatted": size_str,
            "modified_timestamp": mtime,
            "modified_at": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
            "total_patients": count,
            "records_estimate": count,
        })

    return items


@router.get("/api/cup/files/{file_name}/download", dependencies=[Depends(verify_api_key)])
def download_cup_file_raw(file_name: str, settings: Settings = Depends(get_settings)):
    """Permette di scaricare direttamente il file Excel originale archiviato nella cartella CUP."""
    target = _find_target_cup_file(file_name, cup_dir=settings.cup_dir)
    media_type = (
        "application/vnd.ms-excel"
        if target.suffix.lower() == ".xls"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type=media_type
    )


@router.get("/api/cup/patients", dependencies=[Depends(verify_api_key)])
def get_cup_patients(
    file_name: Optional[str] = Query(None, description="Nome file specifico nella cartella CUP (se omesso usa il più recente)"),
    cup_file: Optional[str] = Query(None, description="Alias per file_name"),
    search: Optional[str] = Query(None, description="Filtro di ricerca testuale (nome, cognome, codice fiscale, slug)"),
    agenda: Optional[str] = Query(None, description="Filtra per codice agenda (es. 3004)"),
    only_complete: bool = Query(False, description="Filtra solo pazienti con data nascita e nome presenti"),
    page: int = Query(1, ge=1, description="Numero pagina (1-based)"),
    page_size: int = Query(50, ge=1, le=500, description="Dimensione pagina"),
    refresh_redcap: bool = Query(False, description="Forza l'aggiornamento della cache dei record REDCap"),
    settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Restituisce l'elenco dei pazienti univoci estratti dal foglio CUP con filtri, paginazione e verifica stato REDCap."""
    target_name = file_name or cup_file
    target_file = _find_target_cup_file(target_name, cup_dir=settings.cup_dir)
    pazienti = _get_cup_patients_cached(target_file, only_complete=only_complete)

    # Filtraggio per ricerca testuale
    if search and search.strip():
        q = search.strip().lower()
        filtered = []
        for p in pazienti:
            text_corpus = f"{p.cognome} {p.nome or ''} {p.codice_fiscale or ''} {p.patient_id} {p.priamo_id or ''}".lower()
            if q in text_corpus:
                filtered.append(p)
        pazienti = filtered

    # Filtraggio per agenda
    if agenda and agenda.strip():
        ag_q = agenda.strip()
        pazienti = [p for p in pazienti if ag_q in p.agende]

    total_items = len(pazienti)
    total_pages = max(1, math.ceil(total_items / page_size))
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_items = pazienti[start_idx:end_idx]

    # Verifica corrispondenza con REDCap e file di stato locale
    redcap_lookup = _get_redcap_lookup(settings, force_refresh=refresh_redcap)
    state_dir = Path(settings.state_dir)
    items = []

    for p in page_items:
        sur_norm = normalize_person_name(p.cognome)
        name_norm = normalize_person_name(p.nome)
        dob_norm = _normalize_dob(p.data_nascita)
        cf_norm = str(p.codice_fiscale or "").strip().upper()

        redcap_match = None
        # 1. Matching su Codice Fiscale esatto
        if cf_norm and cf_norm in redcap_lookup.get("by_cf", {}):
            redcap_match = redcap_lookup["by_cf"][cf_norm]
        # 2. Matching anagrafico completo e univoco (cognome, nome, data di nascita)
        elif (
            sur_norm
            and name_norm
            and dob_norm
            and (sur_norm, name_norm, dob_norm) in redcap_lookup.get("by_demographics", {})
        ):
            redcap_match = redcap_lookup["by_demographics"][(sur_norm, name_norm, dob_norm)]
        # 3. Matching esatto su patient_id (solo slug/id applicativo usato direttamente come record_id)
        elif p.patient_id and p.patient_id.lower() in redcap_lookup.get("by_id", {}):
            redcap_match = redcap_lookup["by_id"][p.patient_id.lower()]

        # 4. Verifica stato locale (su slug del paziente o record_id REDCap)
        has_local, is_local_uploaded = _check_local_state(p.patient_id, state_dir)
        if not has_local and redcap_match:
            has_local, is_local_uploaded = _check_local_state(redcap_match["record_id"], state_dir)

        in_redcap = (redcap_match is not None) or is_local_uploaded

        d = p.to_export_dict()
        d["has_local_state"] = has_local
        d["in_redcap"] = in_redcap
        d["redcap_record_id"] = redcap_match["record_id"] if redcap_match else None
        d["redcap_forms_completed"] = redcap_match["forms_completed_count"] if redcap_match else None
        items.append(d)

    return {
        "file_name": target_file.name,
        "total_items": total_items,
        "total": total_items,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "items": items
    }


@router.get("/api/cup/export", dependencies=[Depends(verify_api_key)])
def export_cup_patients(
    file_name: Optional[str] = Query(None, description="Nome file specifico"),
    cup_file: Optional[str] = Query(None, description="Alias per file_name"),
    format: str = Query("csv", pattern="^(csv|xlsx|json)$", description="Formato esportazione: csv, xlsx, json"),
    only_complete: bool = Query(False),
    settings: Settings = Depends(get_settings),
):
    """Esporta l'anagrafica dei pazienti univoci in streaming per il download diretto dal browser."""
    target_name = file_name or cup_file
    target_file = _find_target_cup_file(target_name, cup_dir=settings.cup_dir)
    pazienti = _get_cup_patients_cached(target_file, only_complete=only_complete)
    rows = [p.to_export_dict() for p in pazienti]
    df = pd.DataFrame(rows)

    safe_stem = sanitize_identifier(target_file.stem, default="cup")
    base_name = f"anagrafica_{safe_stem}"

    if format == "json":
        buf = io.BytesIO()
        df.to_json(buf, orient="records", indent=2, force_ascii=False)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.json"'}
        )
    elif format == "xlsx":
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Pazienti")
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.xlsx"'}
        )
    else:
        safe_df = df.map(sanitize_csv_cell)
        buf = io.StringIO()
        safe_df.to_csv(buf, index=False, encoding="utf-8")
        buf.seek(0)
        return StreamingResponse(
            io.BytesIO(buf.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.csv"'}
        )
