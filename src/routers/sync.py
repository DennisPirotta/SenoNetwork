"""REDCap Reconciliation & Sync API Router."""

import json
import time
from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.config import Settings, get_settings
from src.sync_service import RedCapSyncService
from src.routers.pipeline import (
    _get_llm_semaphore,
    _get_patient_lock,
    append_task_event,
    register_task,
    update_task,
)
from src.routers.security import verify_api_key

router = APIRouter(tags=["REDCap Sync"])


def get_sync_service(settings: Settings = Depends(get_settings)) -> RedCapSyncService:
    return RedCapSyncService(settings=settings)


class SyncTriggerRequest(BaseModel):
    patient_id: Optional[str] = Field(default=None, description="Paziente specifico da riconciliare")
    name: Optional[str] = None
    surname: Optional[str] = None
    date_of_birth: Optional[str] = None
    target_forms: Optional[List[str]] = Field(default=None, description="Elenco form specifici da verificare")
    auto_upload: bool = Field(default=True, description="Carica automaticamente aggiornamenti su REDCap")
    dry_run: bool = Field(default=False, description="Simulazione senza scritture")
    force_reprocess: bool = Field(default=False, description="Forza rielaborazione dei PDF già registrati")
    limit: Optional[int] = Field(default=None, ge=1, le=5000, description="Limite massimo pazienti")
    only_incomplete: bool = Field(default=True, description="Esamina solo pazienti con form incompleti")
    overwrite_mode: Optional[str] = Field(
        default=None,
        pattern="^(fill_missing|overwrite)$",
        description="Modalità aggiornamento REDCap ('fill_missing' o 'overwrite')"
    )


class AsyncTaskResponse(BaseModel):
    task_id: str
    status: str
    patient_id: str
    message: str


def _run_background_sync(
    task_id: str,
    sync_service: RedCapSyncService,
    req: SyncTriggerRequest
):
    update_task(
        task_id,
        status="processing",
        started_at=time.time(),
        progress_pct=10,
        current_step=f"Avvio riconciliazione REDCap per {req.patient_id or 'coorte incompleta'}...",
        events_log=[],
    )
    append_task_event(
        task_id,
        10,
        f"Sync REDCap avviato ({'dry-run' if req.dry_run else 'live'})...",
        {
            "time": time.strftime("%H:%M:%S"),
            "message": f"Sync REDCap avviato per {req.patient_id or 'tutti i pazienti incompleti'}",
            "pct": 10,
            "extra": None,
        },
    )
    try:
        if req.patient_id:
            with _get_patient_lock(req.patient_id), _get_llm_semaphore(getattr(sync_service, "settings", None)):
                res = sync_service.check_and_update_patient(
                    patient_id=req.patient_id,
                    name=req.name,
                    surname=req.surname,
                    dob=req.date_of_birth,
                    target_forms=req.target_forms,
                    auto_upload=req.auto_upload and not req.dry_run,
                    dry_run=req.dry_run,
                    force_reprocess=req.force_reprocess,
                    overwrite_mode=req.overwrite_mode,
                )
            update_task(
                task_id,
                status="completed",
                progress_pct=100,
                current_step=f"Sync completato per {req.patient_id}: {res.status} ({res.field_updates_count} campi).",
                result=res.model_dump(),
                items_done=1,
            )
        else:
            with _get_llm_semaphore(getattr(sync_service, "settings", None)):
                report = sync_service.sync_all(
                    target_forms=req.target_forms,
                    auto_upload=req.auto_upload and not req.dry_run,
                    dry_run=req.dry_run,
                    force_reprocess=req.force_reprocess,
                    limit=req.limit,
                    only_incomplete=req.only_incomplete,
                    overwrite_mode=req.overwrite_mode,
                )
            update_task(
                task_id,
                status="completed",
                progress_pct=100,
                current_step=(
                    f"Sync globale completato: {report.patients_updated}/{report.patients_inspected} pazienti aggiornati."
                ),
                result=report.model_dump(),
                items_done=report.patients_inspected,
                items_total=max(1, report.patients_inspected),
            )
    except Exception as e:
        update_task(task_id, status="failed", error=str(e), current_step=f"Errore sync: {e}")
    finally:
        update_task(task_id, finished_at=time.time())


@router.post("/api/sync/check-updates", dependencies=[Depends(verify_api_key)])
@router.post("/api/sync/trigger", dependencies=[Depends(verify_api_key)])
def sync_check_updates_sync(
    req: Optional[SyncTriggerRequest] = None,
    sync_service: RedCapSyncService = Depends(get_sync_service)
) -> Any:
    """Esegue la scansione e riconciliazione dei referti mancanti su REDCap in modalità sincrona."""
    params = req or SyncTriggerRequest()
    try:
        if params.patient_id:
            with _get_patient_lock(params.patient_id), _get_llm_semaphore(getattr(sync_service, "settings", None)):
                return sync_service.check_and_update_patient(
                    patient_id=params.patient_id,
                    name=params.name,
                    surname=params.surname,
                    dob=params.date_of_birth,
                    target_forms=params.target_forms,
                    auto_upload=params.auto_upload and not params.dry_run,
                    dry_run=params.dry_run,
                    force_reprocess=params.force_reprocess,
                    overwrite_mode=params.overwrite_mode,
                )
        else:
            with _get_llm_semaphore(getattr(sync_service, "settings", None)):
                return sync_service.sync_all(
                    target_forms=params.target_forms,
                    auto_upload=params.auto_upload and not params.dry_run,
                    dry_run=params.dry_run,
                    force_reprocess=params.force_reprocess,
                    limit=params.limit,
                    only_incomplete=params.only_incomplete,
                    overwrite_mode=params.overwrite_mode,
                )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore esecuzione sync: {e}")


@router.post("/api/sync/check-updates-async", dependencies=[Depends(verify_api_key)], response_model=AsyncTaskResponse)
def sync_check_updates_async(
    background_tasks: BackgroundTasks,
    req: Optional[SyncTriggerRequest] = None,
    sync_service: RedCapSyncService = Depends(get_sync_service)
) -> AsyncTaskResponse:
    """Accoda il sync in background e ritorna immediatamente un task_id."""
    params = req or SyncTriggerRequest()
    task_id = str(uuid.uuid4())
    p_id = params.patient_id or "all_patients"

    register_task(
        task_id,
        {
            "task_id": task_id,
            "patient_id": p_id,
            "type": "sync",
            "status": "queued",
            "progress_pct": 0,
            "current_step": "Sync in coda...",
            "items_total": 1,
            "items_done": 0,
            "events_log": [],
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None
        }
    )

    background_tasks.add_task(
        _run_background_sync,
        task_id=task_id,
        sync_service=sync_service,
        req=params
    )

    return AsyncTaskResponse(
        task_id=task_id,
        status="queued",
        patient_id=p_id,
        message=f"Sync REDCap accodato in background per {p_id}."
    )


@router.get("/api/sync/incomplete-patients", dependencies=[Depends(verify_api_key)])
def get_incomplete_patients(
    form: Optional[str] = Query(None, description="Filtra per form specifico (es. anatomia_patologica)"),
    sync_service: RedCapSyncService = Depends(get_sync_service)
) -> List[Dict[str, Any]]:
    """Restituisce l'elenco dei pazienti presenti in REDCap che hanno ancora form clinici incompleti."""
    filter_forms = [form] if form else None
    try:
        return sync_service.get_patients_from_redcap(filter_incomplete_forms=filter_forms)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore recupero pazienti incompleti: {e}")


@router.get("/api/sync/latest-report", dependencies=[Depends(verify_api_key)])
def get_latest_sync_report(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    """Restituisce il report JSON dell'ultima sincronizzazione periodica archiviata su disco."""
    audit_dir = settings.sync_audit_dir
    if not audit_dir.exists():
        raise HTTPException(status_code=404, detail="Nessun report di sincronizzazione trovato.")

    reports = sorted(list(audit_dir.glob("sync_report_*.json")), reverse=True)
    if not reports:
        raise HTTPException(status_code=404, detail="Nessun report di sincronizzazione archiviato.")

    try:
        with open(reports[0], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore lettura report: {e}")
