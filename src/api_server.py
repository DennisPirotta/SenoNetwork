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
from src.patient_pipeline import PatientPipeline, PatientPipelineResult
from src.redcap_service import REDCapService

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


def run_server(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    uvicorn.run("src.api_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run_server()
