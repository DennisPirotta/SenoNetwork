"""PriamoCUP Worksheets and Patients API Router."""

from datetime import date, datetime
import io
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
import pandas as pd
import requests

from src.config import Settings, get_settings
from src.priamo_cup_client import (
    AGENDE_SENOLOGIA,
    UNITA_SENOLOGIA,
    PriamoCupClient,
)
from scripts.extract_patients_from_shared_file import (
    Paziente,
    estrai_pazienti_da_fogli_cup,
    estrai_pazienti_univoci,
)
from src.routers.security import verify_api_key

router = APIRouter(tags=["Priamo CUP"])

# Cache in-memory per il conteggio dei pazienti nei file CUP: (file_name, mtime) -> count
_CUP_FILE_CACHE: Dict[Tuple[str, float], int] = {}


class CupDownloadRequest(BaseModel):
    ytd: bool = Field(default=False, description="Scarica da 1° Gennaio fino a ieri (YTD)")
    year: Optional[int] = Field(default=None, description="Anno intero da scaricare (es. 2025)")
    month: Optional[str] = Field(default=None, description="Mese da scaricare (YYYY-MM o MM, es. '2026-10')")
    months: Optional[int] = Field(default=None, description="Ultimi N mesi da scaricare")
    start_date: Optional[str] = Field(default=None, description="Data inizio (YYYY-MM-DD)")
    end_date: Optional[str] = Field(default=None, description="Data fine (YYYY-MM-DD)")
    weeks: Optional[int] = Field(default=None, description="Numero di settimane")
    unit: str = Field(default=UNITA_SENOLOGIA, description="Codice unità operativa (default: 37002 Senologia)")
    agendas: Optional[List[str]] = Field(default=None, description="Elenco agende (default: 3000, 3003, 3004, 3035, 3036)")


def _find_target_cup_file(file_name: Optional[str] = None) -> Path:
    cup_dir = Path("data/cup")
    if not cup_dir.exists():
        raise HTTPException(status_code=404, detail="Nessun foglio di lavoro CUP scaricato trovato (cartella data/cup vuota).")

    if file_name and file_name.strip():
        clean_name = Path(file_name.strip()).name
        target = cup_dir / clean_name
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"File CUP non trovato: {clean_name}")
        return target

    files = sorted(
        list(cup_dir.glob("*.xlsx")) + list(cup_dir.glob("*.xls")),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )
    if not files:
        raise HTTPException(status_code=404, detail="Nessun file Excel CUP trovato in data/cup.")
    return files[0]


def _get_cup_patient_count(f: Path) -> int:
    """Restituisce il numero di pazienti univoci memorizzato in cache per il file specificato."""
    try:
        mtime = f.stat().st_mtime
        key = (f.name, mtime)
        if key in _CUP_FILE_CACHE:
            return _CUP_FILE_CACHE[key]
        pts = estrai_pazienti_univoci(f)
        count = len(pts)
        _CUP_FILE_CACHE[key] = count
        return count
    except Exception:
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
            output_dir="data/cup",
        )

        # Estrai pazienti per contare i record univoci e aggiorna la cache
        pazienti = estrai_pazienti_univoci(downloaded_file)
        _CUP_FILE_CACHE[(downloaded_file.name, downloaded_file.stat().st_mtime)] = len(pazienti)

        size_kb = downloaded_file.stat().st_size / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{(size_kb / 1024):.2f} MB"

        return {
            "status": "success",
            "file_name": downloaded_file.name,
            "file_path": str(downloaded_file),
            "size_bytes": downloaded_file.stat().st_size,
            "size_formatted": size_str,
            "total_unique_patients": len(pazienti),
            "unit": req.unit,
            "agendas": agendas,
            "message": f"Foglio CUP scaricato con successo ({len(pazienti)} pazienti univoci)."
        }
    except HTTPException:
        raise
    except requests.exceptions.ConnectionError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossibile connettersi a Priamo CUP ({settings.priamo_cup_url}): rete non raggiungibile. Verificare che la VPN Valduce sia attiva."
        )
    except requests.exceptions.Timeout as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Timeout durante la connessione a Priamo CUP ({settings.priamo_cup_url})."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore download PriamoCUP: {e}")


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
def list_cup_files() -> List[Dict[str, Any]]:
    """Elenca tutti i file Excel di foglio lavoro scaricati e archiviati in data/cup/."""
    cup_dir = Path("data/cup")
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
        size_kb = f.stat().st_size / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{(size_kb / 1024):.2f} MB"
        mtime = f.stat().st_mtime
        items.append({
            "name": f.name,
            "path": str(f),
            "size_bytes": f.stat().st_size,
            "size_formatted": size_str,
            "modified_timestamp": mtime,
            "modified_at": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
            "total_patients": count,
            "records_estimate": count,
        })

    return items


@router.get("/api/cup/files/{file_name}/download", dependencies=[Depends(verify_api_key)])
def download_cup_file_raw(file_name: str):
    """Permette di scaricare direttamente il file Excel originale archiviato in data/cup/."""
    target = _find_target_cup_file(file_name)
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@router.get("/api/cup/patients", dependencies=[Depends(verify_api_key)])
def get_cup_patients(
    file_name: Optional[str] = Query(None, description="Nome file specifico in data/cup (se omesso usa il più recente)"),
    cup_file: Optional[str] = Query(None, description="Alias per file_name"),
    search: Optional[str] = Query(None, description="Filtro di ricerca testuale (nome, cognome, codice fiscale, slug)"),
    agenda: Optional[str] = Query(None, description="Filtra per codice agenda (es. 3004)"),
    only_complete: bool = Query(False, description="Filtra solo pazienti con data nascita e nome presenti"),
    page: int = Query(1, ge=1, description="Numero pagina (1-based)"),
    page_size: int = Query(50, ge=1, le=500, description="Dimensione pagina")
) -> Dict[str, Any]:
    """Restituisce l'elenco dei pazienti univoci estratti dal foglio CUP con filtri e paginazione."""
    target_name = file_name or cup_file
    target_file = _find_target_cup_file(target_name)
    pazienti = estrai_pazienti_univoci(target_file, only_complete=only_complete)

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

    # Verifica presenza file di stato locale (già elaborato dalla pipeline)
    state_dir = Path("data/state")
    items = []
    for p in page_items:
        d = p.to_export_dict()
        d["has_local_state"] = (state_dir / f"{p.patient_id}_state.json").exists()
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
    only_complete: bool = Query(False)
):
    """Esporta l'anagrafica dei pazienti univoci in streaming per il download diretto dal browser."""
    target_name = file_name or cup_file
    target_file = _find_target_cup_file(target_name)
    pazienti = estrai_pazienti_univoci(target_file, only_complete=only_complete)
    rows = [p.to_export_dict() for p in pazienti]
    df = pd.DataFrame(rows)

    base_name = f"anagrafica_{target_file.stem}"

    if format == "json":
        buf = io.BytesIO()
        df.to_json(buf, orient="records", indent=2, force_ascii=False)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={base_name}.json"}
        )
    elif format == "xlsx":
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="calamine") as writer:
            df.to_excel(writer, index=False, sheet_name="Pazienti")
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={base_name}.xlsx"}
        )
    else:
        buf = io.StringIO()
        df.to_csv(buf, index=False, encoding="utf-8")
        buf.seek(0)
        return StreamingResponse(
            io.BytesIO(buf.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={base_name}.csv"}
        )
