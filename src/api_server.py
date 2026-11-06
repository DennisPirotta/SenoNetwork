from functools import lru_cache
import logging
from pathlib import Path
import shutil
import time
from typing import Any, Dict, List, Optional
import uuid
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.ai_extractor import LlamaCppExtractor, OllamaExtractor
from src.config import Settings, get_settings
from src.dotnet_client import DotNetApiClient
from src.audit_service import RedCapAuditReport, RedCapDataAuditor
from src.patient_pipeline import PatientPipeline, PatientPipelineResult
from src.redcap_service import REDCapService
from src.sync_service import PatientSyncResult, RedCapSyncService, SyncSummaryReport

logger = logging.getLogger(__name__)

app = FastAPI(
    title="SenoNetwork Medical PDF Extraction Service",
    description="Microservizio per la pipeline: Estrazione PDF (.NET) -> Elaborazione IA Locale -> Caricamento REDCap",
    version="1.1.0"
)

# In-memory store for async background tasks
_tasks_store: Dict[str, Dict[str, Any]] = {}


@lru_cache
def get_shared_settings() -> Settings:
    return get_settings()


def get_pipeline(settings: Settings = Depends(get_shared_settings)) -> PatientPipeline:
    """Dependency provider for PatientPipeline."""
    return PatientPipeline(settings=settings)


def get_sync_service(settings: Settings = Depends(get_shared_settings)) -> RedCapSyncService:
    """Dependency provider for RedCapSyncService."""
    return RedCapSyncService(settings=settings)


class SyncTriggerRequest(BaseModel):
    patient_id: Optional[str] = None
    name: Optional[str] = None
    surname: Optional[str] = None
    date_of_birth: Optional[str] = None
    target_forms: Optional[List[str]] = None
    auto_upload: bool = True
    dry_run: bool = False
    force_reprocess: bool = False
    limit: Optional[int] = None
    only_incomplete: bool = True


class PatientTriggerRequest(BaseModel):
    patient_id: Optional[str] = None
    name: Optional[str] = None
    surname: Optional[str] = None
    date_of_birth: Optional[str] = None
    auto_upload_to_redcap: bool = True
    webhook_url: Optional[str] = None


class AsyncTaskResponse(BaseModel):
    task_id: str
    status: str
    patient_id: str
    message: str


@app.get("/health")
def health_check(settings: Settings = Depends(get_shared_settings)):
    """Checks the status of the local LLM servers (llama.cpp RPC and Ollama) and REDCap connection."""
    ollama = OllamaExtractor(base_url=settings.ollama_base_url)
    llamacpp = LlamaCppExtractor(
        base_url=settings.llama_cpp_base_url,
        cf_client_id=settings.ai_cloudflared_client_id,
        cf_client_secret=settings.ai_cloudflared_client_secret
    )
    dotnet = DotNetApiClient(settings=settings)

    return {
        "status": "online",
        "ai_backend_configured": settings.ai_backend,
        "llama_cpp_rpc_available": llamacpp.is_available(),
        "llama_cpp_base_url": settings.llama_cpp_base_url,
        "ollama_available": ollama.is_available(),
        "ollama_model": settings.ollama_model,
        "redcap_configured": bool(settings.redcap_api_url and settings.redcap_api_token),
        "priamo_dotnet_configured": dotnet.is_priamo_configured
    }


def _run_background_patient_pipeline(
    task_id: str,
    pipeline: PatientPipeline,
    patient_id: str,
    name: Optional[str],
    surname: Optional[str],
    dob: Optional[str],
    auto_upload: bool,
    webhook: Optional[str]
):
    """Executes the patient pipeline in background worker and records results in task store."""
    _tasks_store[task_id]["status"] = "processing"
    _tasks_store[task_id]["started_at"] = time.time()
    try:
        result = pipeline.process_patient(
            patient_id=patient_id,
            name=name,
            surname=surname,
            date_of_birth=dob,
            auto_upload=auto_upload,
            webhook_url=webhook
        )
        _tasks_store[task_id]["status"] = "completed"
        _tasks_store[task_id]["result"] = result.model_dump()
    except Exception as e:
        logger.error(f"Errore task background {task_id} per paziente {patient_id}: {e}")
        _tasks_store[task_id]["status"] = "failed"
        _tasks_store[task_id]["error"] = str(e)
    finally:
        _tasks_store[task_id]["finished_at"] = time.time()


@app.post("/api/patient/{patient_id}/process", response_model=PatientPipelineResult)
def process_patient_from_dotnet(
    patient_id: str,
    request: Optional[PatientTriggerRequest] = None,
    pipeline: PatientPipeline = Depends(get_pipeline)
):
    """Synchronous trigger: Downloads patient's PDFs from Priamo/.NET,
    processes each with local AI, and loads structured data into REDCap.
    """
    auto_upload = request.auto_upload_to_redcap if request else True
    name = request.name if request else None
    surname = request.surname if request else None
    dob = request.date_of_birth if request else None
    webhook = request.webhook_url if request else None

    try:
        return pipeline.process_patient(
            patient_id=patient_id,
            name=name,
            surname=surname,
            date_of_birth=dob,
            auto_upload=auto_upload,
            webhook_url=webhook
        )
    except Exception as e:
        logger.error(f"Errore esecuzione pipeline paziente {patient_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/patient/{patient_id}/process-async", response_model=AsyncTaskResponse)
def process_patient_async(
    patient_id: str,
    background_tasks: BackgroundTasks,
    request: Optional[PatientTriggerRequest] = None,
    pipeline: PatientPipeline = Depends(get_pipeline)
):
    """Asynchronous trigger: Enqueues the patient processing task in the background,
    immediately returning a task_id to avoid HTTP timeout on long clinical LLM runs.
    """
    task_id = str(uuid.uuid4())
    auto_upload = request.auto_upload_to_redcap if request else True
    name = request.name if request else None
    surname = request.surname if request else None
    dob = request.date_of_birth if request else None
    webhook = request.webhook_url if request else None

    _tasks_store[task_id] = {
        "task_id": task_id,
        "patient_id": patient_id,
        "status": "queued",
        "created_at": time.time(),
        "result": None,
        "error": None
    }

    background_tasks.add_task(
        _run_background_patient_pipeline,
        task_id=task_id,
        pipeline=pipeline,
        patient_id=patient_id,
        name=name,
        surname=surname,
        dob=dob,
        auto_upload=auto_upload,
        webhook=webhook
    )

    return AsyncTaskResponse(
        task_id=task_id,
        status="queued",
        patient_id=patient_id,
        message=f"Elaborazione paziente {patient_id} accodata in background."
    )


@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str):
    """Retrieves current status or finished result of a background processing task."""
    if task_id not in _tasks_store:
        raise HTTPException(status_code=404, detail="Task non trovato.")
    return _tasks_store[task_id]


@app.post("/api/batch/excel", response_model=List[PatientPipelineResult])
def process_excel_batch(
    excel_path: Optional[str] = None,
    limit: Optional[int] = None,
    auto_upload: bool = True,
    settings: Settings = Depends(get_shared_settings),
    pipeline: PatientPipeline = Depends(get_pipeline)
):
    """Processes a cohort of surgical patients from an Excel register (e.g. interventi.xls),
    extracting reports from Priamo, processing with AI, and importing into REDCap.
    """
    target_excel = excel_path or settings.patients_excel_path
    if not target_excel:
        raise HTTPException(
            status_code=400,
            detail="Nessun percorso Excel specificato e 'patients_excel_path' non configurato nelle impostazioni."
        )

    try:
        cohort = DotNetApiClient.load_patients_from_excel(target_excel, settings=settings)
        if limit:
            cohort = cohort[:limit]

        results = []
        for p in cohort:
            res = pipeline.process_patient(
                patient_id=p["patient_id"],
                name=p["name"],
                surname=p["surname"],
                date_of_birth=p["date_of_birth"],
                auto_upload=auto_upload
            )
            results.append(res)
        return results
    except Exception as e:
        logger.error(f"Errore elaborazione batch Excel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/patient/{patient_id}/upload-pdfs", response_model=PatientPipelineResult)
async def process_patient_uploaded_pdfs(
    patient_id: str,
    files: List[UploadFile] = File(...),
    auto_upload: bool = True,
    pipeline: PatientPipeline = Depends(get_pipeline)
):
    """Allows .NET API to directly upload multiple PDF files for a patient,
    running local AI extraction and uploading structured records to REDCap.
    """
    temp_dir = Path("data/uploads") / patient_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: List[Path] = []

    try:
        for uploaded in files:
            file_path = temp_dir / uploaded.filename
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(uploaded.file, buffer)
            saved_paths.append(file_path)

        result = pipeline.process_patient(
            patient_id=patient_id,
            pdf_paths=saved_paths,
            auto_upload=auto_upload
        )
        return result
    except Exception as e:
        logger.error(f"Errore elaborazione PDF caricati per {patient_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _run_background_sync(
    task_id: str,
    sync_service: RedCapSyncService,
    req: SyncTriggerRequest
):
    """Executes the audit & sync pipeline in background worker."""
    _tasks_store[task_id]["status"] = "processing"
    _tasks_store[task_id]["started_at"] = time.time()
    try:
        if req.patient_id:
            res = sync_service.check_and_update_patient(
                patient_id=req.patient_id,
                name=req.name,
                surname=req.surname,
                dob=req.date_of_birth,
                target_forms=req.target_forms,
                auto_upload=req.auto_upload,
                dry_run=req.dry_run,
                force_reprocess=req.force_reprocess
            )
            _tasks_store[task_id]["status"] = "completed"
            _tasks_store[task_id]["result"] = res.model_dump()
        else:
            report = sync_service.sync_all(
                target_forms=req.target_forms,
                auto_upload=req.auto_upload,
                dry_run=req.dry_run,
                force_reprocess=req.force_reprocess,
                limit=req.limit,
                only_incomplete=req.only_incomplete
            )
            _tasks_store[task_id]["status"] = "completed"
            _tasks_store[task_id]["result"] = report.model_dump()
    except Exception as e:
        logger.error(f"Errore task background sync {task_id}: {e}")
        _tasks_store[task_id]["status"] = "failed"
        _tasks_store[task_id]["error"] = str(e)
    finally:
        _tasks_store[task_id]["finished_at"] = time.time()


@app.post("/api/sync/check-updates")
def sync_check_updates_sync(
    req: Optional[SyncTriggerRequest] = None,
    sync_service: RedCapSyncService = Depends(get_sync_service)
):
    """Synchronous audit and sync:
    Checks REDCap records, discovers new reports or incomplete forms (e.g. post-op histopathology),
    runs local AI extraction and updates REDCap.
    """
    params = req or SyncTriggerRequest()
    try:
        if params.patient_id:
            return sync_service.check_and_update_patient(
                patient_id=params.patient_id,
                name=params.name,
                surname=params.surname,
                dob=params.date_of_birth,
                target_forms=params.target_forms,
                auto_upload=params.auto_upload,
                dry_run=params.dry_run,
                force_reprocess=params.force_reprocess
            )
        else:
            return sync_service.sync_all(
                target_forms=params.target_forms,
                auto_upload=params.auto_upload,
                dry_run=params.dry_run,
                force_reprocess=params.force_reprocess,
                limit=params.limit,
                only_incomplete=params.only_incomplete
            )
    except Exception as e:
        logger.error(f"Errore esecuzione sync REDCap: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sync/check-updates-async", response_model=AsyncTaskResponse)
def sync_check_updates_async(
    background_tasks: BackgroundTasks,
    req: Optional[SyncTriggerRequest] = None,
    sync_service: RedCapSyncService = Depends(get_sync_service)
):
    """Asynchronous audit and sync:
    Queues sync in background and returns task_id immediately.
    """
    params = req or SyncTriggerRequest()
    task_id = str(uuid.uuid4())
    p_id = params.patient_id or "all_patients"

    _tasks_store[task_id] = {
        "task_id": task_id,
        "patient_id": p_id,
        "status": "queued",
        "created_at": time.time(),
        "result": None,
        "error": None
    }

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


@app.get("/api/sync/incomplete-patients")
def get_incomplete_patients(
    form: Optional[str] = None,
    sync_service: RedCapSyncService = Depends(get_sync_service)
):
    """Returns patients currently in REDCap who have incomplete forms (e.g. form=anatomia_patologica)."""
    filter_forms = [form] if form else None
    try:
        return sync_service.get_patients_from_redcap(filter_incomplete_forms=filter_forms)
    except Exception as e:
        logger.error(f"Errore recupero pazienti incompleti: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sync/latest-report")
def get_latest_sync_report(settings: Settings = Depends(get_shared_settings)):
    """Returns the most recent sync audit report JSON from disk."""
    audit_dir = settings.sync_audit_dir
    if not audit_dir.exists():
        raise HTTPException(status_code=404, detail="Nessun report di audit trovato.")

    reports = sorted(list(audit_dir.glob("sync_report_*.json")), reverse=True)
    if not reports:
        raise HTTPException(status_code=404, detail="Nessun report di audit trovato.")

    try:
        with open(reports[0], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore lettura report: {e}")


@app.get("/api/audit/report", response_model=RedCapAuditReport)
def get_audit_data_quality_report(
    patient_id: Optional[str] = None,
    settings: Settings = Depends(get_shared_settings)
):
    """Executes a full live data quality audit of REDCap records,
    detecting clinical inconsistencies, missing post-operative pathology,
    temporal date anomalies, and calculating completion rates.
    """
    auditor = RedCapDataAuditor(settings=settings)
    try:
        report = auditor.run_audit(patient_id=patient_id)
        auditor.export_reports(report)
        return report
    except Exception as e:
        logger.error(f"Errore generazione audit report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/redcap/records")
def get_all_redcap_records(
    limit: Optional[int] = None,
    filter_logic: Optional[str] = None,
    form: Optional[str] = None,
    settings: Settings = Depends(get_shared_settings)
):
    """Returns list of all records in the REDCap project with demographics and form status flags."""
    svc = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)
    forms_list = [form] if form else None
    try:
        records = svc.list_all_records(
            forms=forms_list,
            include_demographics=True,
            include_completion_status=True,
            filter_logic=filter_logic
        )
        if limit:
            records = records[:limit]
        return records
    except Exception as e:
        logger.error(f"Errore recupero lista record REDCap: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/redcap/records/summary")
def get_redcap_records_summary(
    settings: Settings = Depends(get_shared_settings)
):
    """Returns high-level summary overview of all records on REDCap."""
    svc = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)
    try:
        return svc.get_records_summary()
    except Exception as e:
        logger.error(f"Errore generazione riassunto record REDCap: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def run_server(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    uvicorn.run("src.api_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run_server()
