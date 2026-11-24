"""Clinical AI Extraction Pipeline & Task Monitoring Router (with SSE support)."""

import asyncio
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.config import Settings, get_settings
from src.patient_pipeline import PatientPipeline, PatientPipelineResult
from scripts.extract_patients_from_shared_file import estrai_pazienti_univoci
from src.routers.cup import _find_target_cup_file, invalidate_redcap_lookup_cache
from src.routers.security import raise_internal_error, verify_api_key, verify_sse_or_api_key
from src.utils import build_patient_slug, get_llm_semaphore, get_patient_lock, sanitize_identifier

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Clinical Pipeline"])

# Shared in-memory task store for tracking progress across worker threads
_tasks_store: Dict[str, Dict[str, Any]] = {}
_tasks_lock = threading.Lock()
TASK_TTL_SECONDS = 3600.0
STALE_ACTIVE_TTL_SECONDS = 14400.0
MAX_TASKS_IN_STORE = 200


def _get_patient_lock(patient_id: str) -> threading.Lock:
    """Backward-compatible wrapper around shared patient lock registry."""
    return get_patient_lock(patient_id)


def _get_llm_semaphore(settings: Optional[Settings] = None) -> threading.Semaphore:
    """Backward-compatible wrapper around shared LLM concurrency semaphore registry."""
    raw_limit = getattr(settings, "ai_max_concurrent_requests", 1) if settings is not None else 1
    limit = max(1, int(raw_limit if isinstance(raw_limit, int) else 1))
    return get_llm_semaphore(limit)


def _cleanup_old_tasks_locked() -> None:
    """Removes expired completed/failed tasks, stale stuck tasks, and bounds store size."""
    now = time.time()
    expired = [
        tid for tid, t in _tasks_store.items()
        if (
            t.get("status") in ("completed", "failed")
            and (t.get("finished_at") or t.get("created_at") or now) < (now - TASK_TTL_SECONDS)
        )
        or (
            t.get("status") in ("queued", "processing")
            and (t.get("started_at") or t.get("created_at") or now) < (now - STALE_ACTIVE_TTL_SECONDS)
        )
    ]
    for tid in expired:
        _tasks_store.pop(tid, None)

    if len(_tasks_store) > MAX_TASKS_IN_STORE:
        removable = sorted(
            (
                (
                    0 if t.get("status") in ("completed", "failed") else 1,
                    t.get("finished_at") or t.get("created_at") or 0.0,
                    tid,
                )
                for tid, t in _tasks_store.items()
            )
        )
        excess = len(_tasks_store) - MAX_TASKS_IN_STORE
        for _, _, tid in removable[:excess]:
            _tasks_store.pop(tid, None)


def register_task(task_id: str, payload: Dict[str, Any]) -> None:
    """Thread-safely registers a new async task while pruning stale completed tasks."""
    with _tasks_lock:
        _cleanup_old_tasks_locked()
        _tasks_store[task_id] = payload


def update_task(task_id: str, **fields: Any) -> None:
    """Thread-safely updates scalar fields on a registered task."""
    with _tasks_lock:
        task = _tasks_store.get(task_id)
        if task is not None:
            task.update(fields)


def append_task_event(
    task_id: str,
    pct: int,
    step_msg: str,
    event_entry: Dict[str, Any],
    extra_doc: Optional[Dict[str, Any]] = None,
) -> None:
    """Thread-safely appends a progress event (and optional processed document) to a task."""
    with _tasks_lock:
        task = _tasks_store.get(task_id)
        if task is None:
            return
        task["progress_pct"] = pct
        task["current_step"] = step_msg
        task.setdefault("events_log", []).append(event_entry)
        if extra_doc is not None:
            task.setdefault("processed_documents", []).append(extra_doc)


def get_task_snapshot(task_id: str) -> Optional[Dict[str, Any]]:
    """Returns a thread-safe shallow snapshot of a task entry."""
    with _tasks_lock:
        task = _tasks_store.get(task_id)
        if task is None:
            return None
        snap = dict(task)
        if "events_log" in snap and isinstance(snap["events_log"], list):
            snap["events_log"] = list(snap["events_log"])
        if "processed_documents" in snap and isinstance(snap["processed_documents"], list):
            snap["processed_documents"] = list(snap["processed_documents"])
        return snap


def get_pipeline(settings: Settings = Depends(get_settings)) -> PatientPipeline:
    return PatientPipeline(settings=settings)


def _pipeline_with_backend(base_pipeline: PatientPipeline, ai_backend: Optional[str]) -> PatientPipeline:
    """Returns `base_pipeline` or a clone overriding `settings.ai_backend` when explicitly requested."""
    if not ai_backend or not ai_backend.strip():
        return base_pipeline
    normalized = ai_backend.strip().lower()
    if normalized not in {"llama_cpp", "llamacpp", "ollama"}:
        raise HTTPException(
            status_code=400,
            detail=f"Backend IA non valido: '{ai_backend}'. Valori supportati: 'llama_cpp', 'ollama'."
        )
    canonical = "llama_cpp" if normalized == "llamacpp" else normalized
    if (base_pipeline.settings.ai_backend or "").strip().lower() == canonical:
        return base_pipeline
    cloned_settings = base_pipeline.settings.model_copy(update={"ai_backend": canonical})
    return PatientPipeline(
        settings=cloned_settings,
        redcap_service=base_pipeline.redcap_service,
        dotnet_client=base_pipeline.dotnet_client,
    )


class SinglePatientRequest(BaseModel):
    patient_id: Optional[str] = Field(default=None, description="ID o slug del paziente")
    name: Optional[str] = Field(default=None, description="Nome del paziente")
    surname: Optional[str] = Field(default=None, description="Cognome del paziente")
    date_of_birth: Optional[str] = Field(default=None, description="Data di nascita (YYYY-MM-DD)")
    codice_fiscale: Optional[str] = Field(default=None, description="Codice fiscale del paziente (opzionale)")
    auto_upload: bool = Field(default=True, description="Se True, carica direttamente i record su REDCap")
    dry_run: bool = Field(default=False, description="Se True, simula l'elaborazione senza modificare REDCap")
    overwrite: bool = Field(default=False, description="Se True, sovrascrive i campi REDCap già valorizzati")
    ai_backend: Optional[str] = Field(default=None, description="Override backend IA ('llama_cpp' o 'ollama')")
    webhook_url: Optional[str] = Field(default=None, description="URL callback opzionale")


class BatchProcessRequest(BaseModel):
    patient_ids: Optional[List[str]] = Field(
        default=None,
        description="Elenco di patient_id da elaborare. Se omesso, carica i pazienti dall'ultimo foglio CUP."
    )
    cup_file_name: Optional[str] = Field(default=None, description="Nome file CUP alternativo")
    limit: Optional[int] = Field(default=None, ge=1, le=500, description="Numero massimo di pazienti da elaborare")
    auto_upload: bool = Field(default=True, description="Carica su REDCap al termine")
    dry_run: bool = Field(default=False, description="Simulazione senza scritture")
    overwrite: bool = Field(default=False, description="Se True, sovrascrive i campi REDCap già valorizzati")
    ai_backend: Optional[str] = Field(default=None, description="Override backend IA ('llama_cpp' o 'ollama')")


class AsyncTaskCreatedResponse(BaseModel):
    task_id: str
    status: str
    message: str
    patient_id: Optional[str] = None
    total_items: int = 1


def _resolve_request_patient_id(req: Optional[SinglePatientRequest], path_patient_id: Optional[str] = None) -> str:
    """Resolves a validated patient_id or deterministic slug; raises 400 if empty."""
    explicit = (path_patient_id or (req.patient_id if req else None) or "").strip()
    if explicit:
        return explicit
    if req and (req.name or "").strip() and (req.surname or "").strip():
        return build_patient_slug(req.name, req.surname, req.date_of_birth)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Specificare 'patient_id' oppure 'name' e 'surname'."
    )


def _background_process_single(
    task_id: str,
    pipeline: PatientPipeline,
    patient_id: str,
    name: Optional[str],
    surname: Optional[str],
    dob: Optional[str],
    auto_upload: bool,
    dry_run: bool,
    webhook: Optional[str],
    overwrite: bool = False,
    codice_fiscale: Optional[str] = None,
):
    update_task(
        task_id,
        status="processing",
        started_at=time.time(),
        progress_pct=5,
        current_step=f"Inizializzazione elaborazione per {patient_id}...",
        events_log=[],
        processed_documents=[],
    )

    def on_progress(step_msg: str, pct: int, extra: Optional[Dict[str, Any]] = None):
        event_entry = {
            "time": time.strftime("%H:%M:%S"),
            "message": step_msg,
            "pct": pct,
            "extra": extra
        }
        extra_doc = extra if (extra and "filename" in extra) else None
        append_task_event(task_id, pct, step_msg, event_entry, extra_doc=extra_doc)

    try:
        with get_patient_lock(patient_id):
            res = pipeline.process_patient(
                patient_id=patient_id,
                name=name,
                surname=surname,
                date_of_birth=dob,
                auto_upload=auto_upload and not dry_run,
                webhook_url=webhook,
                progress_callback=on_progress,
                overwrite=overwrite,
                codice_fiscale=codice_fiscale,
            )
        if res.redcap_upload_status == "uploaded":
            invalidate_redcap_lookup_cache(pipeline.settings)
        update_task(
            task_id,
            status="completed",
            progress_pct=100,
            current_step="Elaborazione completata con successo.",
            result=res.model_dump(),
            items_done=1,
        )
    except Exception as e:
        logger.exception("Errore task %s: %s", task_id, e)
        update_task(task_id, status="failed", error=str(e))
    finally:
        update_task(task_id, finished_at=time.time())


def _background_process_batch(
    task_id: str,
    pipeline: PatientPipeline,
    cohort: List[Dict[str, Any]],
    auto_upload: bool,
    dry_run: bool,
    overwrite: bool = False,
):
    total = len(cohort)
    update_task(
        task_id,
        status="processing",
        started_at=time.time(),
        items_total=total,
        events_log=[],
        processed_documents=[],
    )
    results = []
    any_uploaded = False

    for idx, p in enumerate(cohort, 1):
        target_id = p.get("patient_id") or f"patient_{idx}"
        p_name = p.get("name")
        p_surname = p.get("surname")
        p_dob = p.get("date_of_birth")
        p_cf = p.get("codice_fiscale")

        pct_start = int(((idx - 1) / total) * 100)
        update_task(
            task_id,
            progress_pct=pct_start,
            current_step=f"[{idx}/{total}] Elaborazione paziente ({target_id})",
        )

        def on_batch_progress(step_msg: str, pct: int, extra: Optional[Dict[str, Any]] = None):
            scaled_pct = pct_start + int(pct / 100 * (100 / total))
            clamped = min(scaled_pct, 99)
            prefixed_msg = f"[{idx}/{total}] {step_msg}"
            event_entry = {
                "time": time.strftime("%H:%M:%S"),
                "message": prefixed_msg,
                "pct": clamped,
                "extra": extra
            }
            extra_doc = extra if (extra and "filename" in extra) else None
            append_task_event(task_id, clamped, prefixed_msg, event_entry, extra_doc=extra_doc)

        try:
            with get_patient_lock(target_id):
                res = pipeline.process_patient(
                    patient_id=target_id,
                    name=p_name,
                    surname=p_surname,
                    date_of_birth=p_dob,
                    auto_upload=auto_upload and not dry_run,
                    progress_callback=on_batch_progress,
                    overwrite=overwrite,
                    codice_fiscale=p_cf,
                )
            if res.redcap_upload_status == "uploaded":
                any_uploaded = True
            results.append(res.model_dump())
        except Exception as e:
            logger.exception("Errore elaborazione paziente %s: %s", target_id, e)
            results.append({
                "patient_id": target_id,
                "status": "error",
                "error": str(e)
            })

        update_task(task_id, items_done=idx)

    if any_uploaded:
        invalidate_redcap_lookup_cache(pipeline.settings)

    update_task(
        task_id,
        status="completed",
        progress_pct=100,
        current_step=f"Elaborazione batch terminata ({len(results)}/{total} pazienti elaborati).",
        result=results,
        finished_at=time.time(),
    )


@router.post("/api/pipeline/process-patient", dependencies=[Depends(verify_api_key)])
def process_patient_sync(
    req: SinglePatientRequest,
    pipeline: PatientPipeline = Depends(get_pipeline)
) -> PatientPipelineResult:
    """Elaborazione sincrona di un singolo paziente (download referti -> IA -> REDCap)."""
    p_id = _resolve_request_patient_id(req)
    active_pipeline = _pipeline_with_backend(pipeline, req.ai_backend)
    try:
        with get_patient_lock(p_id):
            res = active_pipeline.process_patient(
                patient_id=p_id,
                name=req.name,
                surname=req.surname,
                date_of_birth=req.date_of_birth,
                auto_upload=req.auto_upload and not req.dry_run,
                webhook_url=req.webhook_url,
                overwrite=req.overwrite,
                codice_fiscale=req.codice_fiscale,
            )
        if res.redcap_upload_status == "uploaded":
            invalidate_redcap_lookup_cache(active_pipeline.settings)
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise_internal_error(logger, "Errore elaborazione paziente", e)


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
    overwrite = request.overwrite if request else False
    codice_fiscale = request.codice_fiscale if request else None
    active_pipeline = _pipeline_with_backend(pipeline, request.ai_backend if request else None)
    try:
        with get_patient_lock(patient_id):
            res = active_pipeline.process_patient(
                patient_id=patient_id,
                name=name,
                surname=surname,
                date_of_birth=dob,
                auto_upload=auto_upload and not dry_run,
                webhook_url=webhook,
                overwrite=overwrite,
                codice_fiscale=codice_fiscale,
            )
        if res.redcap_upload_status == "uploaded":
            invalidate_redcap_lookup_cache(active_pipeline.settings)
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise_internal_error(logger, f"Errore elaborazione paziente '{sanitize_identifier(patient_id)}'", e)


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
    p_id = _resolve_request_patient_id(effective_req, path_patient_id=patient_id)

    active_pipeline = _pipeline_with_backend(pipeline, effective_req.ai_backend if effective_req else None)
    task_id = str(uuid.uuid4())
    auto_upload = effective_req.auto_upload if effective_req else True
    dry_run = effective_req.dry_run if effective_req else False
    overwrite = effective_req.overwrite if effective_req else False
    name = effective_req.name if effective_req else None
    surname = effective_req.surname if effective_req else None
    dob = effective_req.date_of_birth if effective_req else None
    webhook = effective_req.webhook_url if effective_req else None
    codice_fiscale = effective_req.codice_fiscale if effective_req else None

    register_task(
        task_id,
        {
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
    )

    if background_tasks:
        background_tasks.add_task(
            _background_process_single,
            task_id=task_id,
            pipeline=active_pipeline,
            patient_id=p_id,
            name=name,
            surname=surname,
            dob=dob,
            auto_upload=auto_upload,
            dry_run=dry_run,
            webhook=webhook,
            overwrite=overwrite,
            codice_fiscale=codice_fiscale,
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
    active_pipeline = _pipeline_with_backend(pipeline, req.ai_backend)
    target_file = _find_target_cup_file(req.cup_file_name, cup_dir=active_pipeline.settings.cup_dir)
    pzs = estrai_pazienti_univoci(target_file, only_complete=True)

    if req.patient_ids:
        wanted = set(req.patient_ids)
        cohort = [
            {
                "patient_id": p.patient_id,
                "name": p.nome or "",
                "surname": p.cognome,
                "date_of_birth": p.data_nascita,
                "codice_fiscale": p.codice_fiscale,
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
                "codice_fiscale": p.codice_fiscale,
            }
            for p in pzs
        ]

    if req.limit:
        cohort = cohort[:req.limit]

    if not cohort:
        raise HTTPException(status_code=400, detail="Nessun paziente da elaborare trovato.")

    task_id = str(uuid.uuid4())
    register_task(
        task_id,
        {
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
    )

    background_tasks.add_task(
        _background_process_batch,
        task_id=task_id,
        pipeline=active_pipeline,
        cohort=cohort,
        auto_upload=req.auto_upload,
        dry_run=req.dry_run,
        overwrite=req.overwrite,
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
    snap = get_task_snapshot(task_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Task non trovato.")
    return snap


@router.get("/api/tasks/{task_id}/events", dependencies=[Depends(verify_sse_or_api_key)])
async def stream_task_events(task_id: str):
    """Stream Server-Sent Events (SSE) incrementale per monitorare l'avanzamento sulla dashboard."""
    if get_task_snapshot(task_id) is None:
        raise HTTPException(status_code=404, detail="Task non trovato.")

    async def event_generator():
        sent_events_idx = 0
        sent_docs_idx = 0

        while True:
            task = get_task_snapshot(task_id)
            if not task:
                break

            status_val = task.get("status")
            all_events = task.get("events_log") or []
            all_docs = task.get("processed_documents") or []

            # Reset cursors if task restarted/cleared events_log
            if len(all_events) < sent_events_idx:
                sent_events_idx = 0
            if len(all_docs) < sent_docs_idx:
                sent_docs_idx = 0

            delta_events = all_events[sent_events_idx:]
            delta_docs = all_docs[sent_docs_idx:]
            is_incremental = sent_events_idx > 0 or sent_docs_idx > 0

            sent_events_idx = len(all_events)
            sent_docs_idx = len(all_docs)

            payload = {
                "task_id": task_id,
                "status": status_val,
                "progress_pct": task.get("progress_pct", 0),
                "current_step": task.get("current_step", ""),
                "items_done": task.get("items_done", 0),
                "items_total": task.get("items_total", 1),
                "incremental": is_incremental,
                "events_log": delta_events,
                "processed_documents": delta_docs,
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
