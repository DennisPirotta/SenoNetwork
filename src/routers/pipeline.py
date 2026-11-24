"""Clinical AI Extraction Pipeline & Task Monitoring Router (with SSE support)."""

import asyncio
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.config import Settings, get_settings
from src.dotnet_client import DotNetApiClient
from src.patient_pipeline import PatientPipeline, PatientPipelineResult
from scripts.extract_patients_from_shared_file import estrai_pazienti_univoci
from src.routers.cup import _find_target_cup_file
from src.routers.security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Clinical Pipeline"])

# Shared in-memory task store for tracking progress across worker threads
_tasks_store: Dict[str, Dict[str, Any]] = {}


def get_pipeline(settings: Settings = Depends(get_settings)) -> PatientPipeline:
    return PatientPipeline(settings=settings)


class SinglePatientRequest(BaseModel):
    patient_id: Optional[str] = Field(default=None, description="ID o slug del paziente")
    name: Optional[str] = Field(default=None, description="Nome del paziente")
    surname: Optional[str] = Field(default=None, description="Cognome del paziente")
    date_of_birth: Optional[str] = Field(default=None, description="Data di nascita (YYYY-MM-DD)")
    auto_upload: bool = Field(default=True, description="Se True, carica direttamente i record su REDCap")
    dry_run: bool = Field(default=False, description="Se True, simula l'elaborazione senza modificare REDCap")
    webhook_url: Optional[str] = Field(default=None, description="URL callback opzionale")


class BatchProcessRequest(BaseModel):
    patient_ids: Optional[List[str]] = Field(
        default=None,
        description="Elenco di patient_id da elaborare. Se omesso, carica i pazienti dall'ultimo foglio CUP."
    )
    cup_file_name: Optional[str] = Field(default=None, description="Nome file CUP alternativo")
    limit: Optional[int] = Field(default=None, description="Numero massimo di pazienti da elaborare")
    auto_upload: bool = Field(default=True, description="Carica su REDCap al termine")
    dry_run: bool = Field(default=False, description="Simulazione senza scritture")


class AsyncTaskCreatedResponse(BaseModel):
    task_id: str
    status: str
    message: str
    patient_id: Optional[str] = None
    total_items: int = 1


def _background_process_single(
    task_id: str,
    pipeline: PatientPipeline,
    patient_id: str,
    name: Optional[str],
    surname: Optional[str],
    dob: Optional[str],
    auto_upload: bool,
    dry_run: bool,
    webhook: Optional[str]
):
    _tasks_store[task_id]["status"] = "processing"
    _tasks_store[task_id]["started_at"] = time.time()
    _tasks_store[task_id]["progress_pct"] = 5
    _tasks_store[task_id]["current_step"] = f"Inizializzazione elaborazione per {patient_id}..."
    _tasks_store[task_id]["events_log"] = []
    _tasks_store[task_id]["processed_documents"] = []

    def on_progress(step_msg: str, pct: int, extra: Optional[Dict[str, Any]] = None):
        _tasks_store[task_id]["progress_pct"] = pct
        _tasks_store[task_id]["current_step"] = step_msg
        event_entry = {
            "time": time.strftime("%H:%M:%S"),
            "message": step_msg,
            "pct": pct,
            "extra": extra
        }
        _tasks_store[task_id].setdefault("events_log", []).append(event_entry)
        if extra and "filename" in extra:
            _tasks_store[task_id].setdefault("processed_documents", []).append(extra)

    try:
        res = pipeline.process_patient(
            patient_id=patient_id,
            name=name,
            surname=surname,
            date_of_birth=dob,
            auto_upload=auto_upload and not dry_run,
            webhook_url=webhook,
            progress_callback=on_progress
        )
        _tasks_store[task_id]["status"] = "completed"
        _tasks_store[task_id]["progress_pct"] = 100
        _tasks_store[task_id]["current_step"] = "Elaborazione completata con successo."
        _tasks_store[task_id]["result"] = res.model_dump()
        _tasks_store[task_id]["items_done"] = 1
    except Exception as e:
        logger.error(f"Errore task {task_id}: {e}")
        _tasks_store[task_id]["status"] = "failed"
        _tasks_store[task_id]["error"] = str(e)
    finally:
        _tasks_store[task_id]["finished_at"] = time.time()


def _background_process_batch(
    task_id: str,
    pipeline: PatientPipeline,
    cohort: List[Dict[str, Any]],
    auto_upload: bool,
    dry_run: bool
):
    _tasks_store[task_id]["status"] = "processing"
    _tasks_store[task_id]["started_at"] = time.time()
    total = len(cohort)
    _tasks_store[task_id]["items_total"] = total
    _tasks_store[task_id]["events_log"] = []
    _tasks_store[task_id]["processed_documents"] = []
    results = []

    for idx, p in enumerate(cohort, 1):
        target_id = p.get("patient_id")
        p_name = p.get("name")
        p_surname = p.get("surname")
        p_dob = p.get("date_of_birth")

        pct_start = int(((idx - 1) / total) * 100)
        _tasks_store[task_id]["progress_pct"] = pct_start
        _tasks_store[task_id]["current_step"] = f"[{idx}/{total}] Elaborazione: {p_name or ''} {p_surname or ''} ({target_id})"

        def on_batch_progress(step_msg: str, pct: int, extra: Optional[Dict[str, Any]] = None):
            scaled_pct = pct_start + int(pct / 100 * (100 / total))
            _tasks_store[task_id]["progress_pct"] = min(scaled_pct, 99)
            _tasks_store[task_id]["current_step"] = f"[{idx}/{total}] {step_msg}"
            event_entry = {
                "time": time.strftime("%H:%M:%S"),
                "message": f"[{idx}/{total}] {step_msg}",
                "pct": min(scaled_pct, 99),
                "extra": extra
            }
            _tasks_store[task_id].setdefault("events_log", []).append(event_entry)
            if extra and "filename" in extra:
                _tasks_store[task_id].setdefault("processed_documents", []).append(extra)

        try:
            res = pipeline.process_patient(
                patient_id=target_id,
                name=p_name,
                surname=p_surname,
                date_of_birth=p_dob,
                auto_upload=auto_upload and not dry_run,
                progress_callback=on_batch_progress
            )
            results.append(res.model_dump())
        except Exception as e:
            logger.error(f"Errore elaborazione paziente {target_id}: {e}")
            results.append({
                "patient_id": target_id,
                "status": "error",
                "error": str(e)
            })

        _tasks_store[task_id]["items_done"] = idx

    _tasks_store[task_id]["status"] = "completed"
    _tasks_store[task_id]["progress_pct"] = 100
    _tasks_store[task_id]["current_step"] = f"Elaborazione batch terminata ({len(results)}/{total} pazienti elaborati)."
    _tasks_store[task_id]["result"] = results
    _tasks_store[task_id]["finished_at"] = time.time()


@router.post("/api/pipeline/process-patient", dependencies=[Depends(verify_api_key)])
def process_patient_sync(
    req: SinglePatientRequest,
    pipeline: PatientPipeline = Depends(get_pipeline)
) -> PatientPipelineResult:
    """Elaborazione sincrona di un singolo paziente (download referti -> IA -> REDCap)."""
    p_id = req.patient_id or f"{req.name}-{req.surname}-{req.date_of_birth or 'unk'}".lower().replace(" ", "-")
    try:
        return pipeline.process_patient(
            patient_id=p_id,
            name=req.name,
            surname=req.surname,
            date_of_birth=req.date_of_birth,
            auto_upload=req.auto_upload and not req.dry_run,
            webhook_url=req.webhook_url
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore elaborazione paziente: {e}")


@router.post("/api/patient/{patient_id}/process", dependencies=[Depends(verify_api_key)])
def process_patient_from_dotnet(
    patient_id: str,
    request: Optional[SinglePatientRequest] = None,
    pipeline: PatientPipeline = Depends(get_pipeline)
) -> PatientPipelineResult:
    auto_upload = request.auto_upload if request else True
    dry_run = request.dry_run if request else False
    name = request.name if request else None
    surname = request.surname if request else None
    dob = request.date_of_birth if request else None
    webhook = request.webhook_url if request else None
    return pipeline.process_patient(
        patient_id=patient_id,
        name=name,
        surname=surname,
        date_of_birth=dob,
        auto_upload=auto_upload and not dry_run,
        webhook_url=webhook
    )


@router.post("/api/pipeline/process-patient-async", dependencies=[Depends(verify_api_key)], response_model=AsyncTaskCreatedResponse)
@router.post("/api/patient/{patient_id}/process-async", dependencies=[Depends(verify_api_key)], response_model=AsyncTaskCreatedResponse)
def process_patient_async(
    patient_id: Optional[str] = None,
    background_tasks: BackgroundTasks = None,
    request: Optional[SinglePatientRequest] = Body(None),
    pipeline: PatientPipeline = Depends(get_pipeline)
) -> AsyncTaskCreatedResponse:
    """Accoda l'elaborazione clinica di un singolo paziente in background e ritorna task_id."""
    effective_req = request
    p_id = patient_id or (effective_req.patient_id if effective_req else None)
    if not p_id and effective_req and effective_req.name and effective_req.surname:
        p_id = f"{effective_req.name}-{effective_req.surname}-{effective_req.date_of_birth or 'unk'}".lower().replace(" ", "-")
    if not p_id:
        p_id = "unknown-patient"

    task_id = str(uuid.uuid4())
    auto_upload = effective_req.auto_upload if effective_req else True
    dry_run = effective_req.dry_run if effective_req else False
    name = effective_req.name if effective_req else None
    surname = effective_req.surname if effective_req else None
    dob = effective_req.date_of_birth if effective_req else None
    webhook = effective_req.webhook_url if effective_req else None

    _tasks_store[task_id] = {
        "task_id": task_id,
        "type": "single_patient",
        "patient_id": p_id,
        "status": "queued",
        "progress_pct": 0,
        "current_step": "Task in coda...",
        "items_total": 1,
        "items_done": 0,
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None
    }

    if background_tasks:
        background_tasks.add_task(
            _background_process_single,
            task_id=task_id,
            pipeline=pipeline,
            patient_id=p_id,
            name=name,
            surname=surname,
            dob=dob,
            auto_upload=auto_upload,
            dry_run=dry_run,
            webhook=webhook
        )

    return AsyncTaskCreatedResponse(
        task_id=task_id,
        status="queued",
        patient_id=p_id,
        message=f"Elaborazione paziente '{p_id}' accodata in background.",
        total_items=1
    )


@router.post("/api/pipeline/process-batch-async", dependencies=[Depends(verify_api_key)], response_model=AsyncTaskCreatedResponse)
def process_batch_async(
    req: BatchProcessRequest,
    background_tasks: BackgroundTasks,
    pipeline: PatientPipeline = Depends(get_pipeline)
) -> AsyncTaskCreatedResponse:
    """Accoda l'elaborazione clinica batch di più pazienti selezionati in background."""
    # Determinazione coorte
    target_file = _find_target_cup_file(req.cup_file_name)
    pzs = estrai_pazienti_univoci(target_file, only_complete=True)

    if req.patient_ids:
        # Filtra solo i pazienti selezionati tramite ID/Slug
        wanted = set(req.patient_ids)
        cohort = [
            {
                "patient_id": p.patient_id,
                "name": p.nome or "",
                "surname": p.cognome,
                "date_of_birth": p.data_nascita,
            }
            for p in pzs if p.patient_id in wanted or (p.priamo_id and p.priamo_id in wanted)
        ]
    else:
        cohort = [
            {
                "patient_id": p.patient_id,
                "name": p.nome or "",
                "surname": p.cognome,
                "date_of_birth": p.data_nascita,
            }
            for p in pzs
        ]

    if req.limit:
        cohort = cohort[:req.limit]

    if not cohort:
        raise HTTPException(status_code=400, detail="Nessun paziente da elaborare trovato.")

    task_id = str(uuid.uuid4())
    _tasks_store[task_id] = {
        "task_id": task_id,
        "type": "batch_cohort",
        "status": "queued",
        "progress_pct": 0,
        "current_step": f"Batch di {len(cohort)} pazienti in coda...",
        "items_total": len(cohort),
        "items_done": 0,
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None
    }

    background_tasks.add_task(
        _background_process_batch,
        task_id=task_id,
        pipeline=pipeline,
        cohort=cohort,
        auto_upload=req.auto_upload,
        dry_run=req.dry_run
    )

    return AsyncTaskCreatedResponse(
        task_id=task_id,
        status="queued",
        message=f"Elaborazione batch di {len(cohort)} pazienti accodata in background.",
        total_items=len(cohort)
    )


@router.get("/api/tasks/{task_id}", dependencies=[Depends(verify_api_key)])
def get_task_status(task_id: str) -> Dict[str, Any]:
    """Restituisce lo stato dettagliato e la percentuale di avanzamento di un task asincrono."""
    if task_id not in _tasks_store:
        raise HTTPException(status_code=404, detail="Task non trovato.")
    return _tasks_store[task_id]


@router.get("/api/tasks/{task_id}/events", dependencies=[Depends(verify_api_key)])
async def stream_task_events(task_id: str):
    """Stream Server-Sent Events (SSE) per monitorare l'avanzamento in tempo reale sulla dashboard."""
    if task_id not in _tasks_store:
        raise HTTPException(status_code=404, detail="Task non trovato.")

    async def event_generator():
        last_pct = -1
        while True:
            task = _tasks_store.get(task_id)
            if not task:
                break

            current_pct = task.get("progress_pct", 0)
            status_val = task.get("status")

            # Invia payload arricchito con eventi e schede documenti
            payload = {
                "task_id": task_id,
                "status": status_val,
                "progress_pct": current_pct,
                "current_step": task.get("current_step", ""),
                "items_done": task.get("items_done", 0),
                "items_total": task.get("items_total", 1),
                "events_log": task.get("events_log", []),
                "processed_documents": task.get("processed_documents", []),
                "result": task.get("result") if status_val in ("completed", "failed") else None,
                "error": task.get("error")
            }
            yield f"data: {json.dumps(payload)}\n\n"

            if status_val in ("completed", "failed"):
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
