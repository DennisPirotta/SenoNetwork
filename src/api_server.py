"""SenoNetwork FastAPI Microservice & Backend API.

Exposes REST endpoints and SSE streams for:
- PriamoCUP Worksheets Download & Unique Patient Extraction
- Priamo HIS Valduce Patient & Cohort Verification
- Clinical AI Extraction Pipeline (llama.cpp RPC / Ollama)
- Data Quality & Inconsistency Audit on REDCap
- Periodic Reconciliation & Sync
- REDCap Records & Forms Management
- System Health & Dashboard Overview KPIs
"""

import argparse
from functools import lru_cache
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.ai_extractor import LlamaCppExtractor, OllamaExtractor
from src.audit_service import RedCapAuditReport, RedCapDataAuditor
from src.config import Settings, get_settings
from src.dotnet_client import DotNetApiClient

# Import modular routers
from src.routers.security import router as security_router, generate_api_key
from src.routers.dashboard import router as dashboard_router
from src.routers.cup import router as cup_router
from src.routers.priamo import router as priamo_router
from src.routers.pipeline import (
    router as pipeline_router,
    _tasks_store,
    process_patient_async,
    get_task_status,
)
from src.routers.audit import router as audit_router, get_audit_report as get_audit_data_quality_report
from src.routers.sync import (
    router as sync_router,
    SyncTriggerRequest,
    get_incomplete_patients,
    sync_check_updates_async,
    sync_check_updates_sync,
)
from src.routers.redcap import (
    router as redcap_router,
    delete_single_record as delete_single_redcap_record,
    delete_all_records as delete_all_redcap_records,
)

logger = logging.getLogger(__name__)

settings = get_settings()

openapi_tags = [
    {"name": "Dashboard", "description": "KPI riassuntivi e statistiche generali per la schermata iniziale della dashboard."},
    {"name": "Priamo CUP", "description": "Download fogli di lavoro ambulatoriali (Unità 37002 Senologia) ed estrazione anagrafica pazienti."},
    {"name": "Priamo HIS", "description": "Verifica anagrafica certificata e ricerca eventi su HIS Ospedale Valduce."},
    {"name": "Clinical Pipeline", "description": "Pipeline di estrazione clinica IA locale (llama.cpp RPC / Ollama) e tracking task con SSE."},
    {"name": "Data Quality & Audit", "description": "Audit automatico di conformità dati, rilevamento anomalie e incongruenze su REDCap."},
    {"name": "REDCap Sync", "description": "Riconciliazione e aggiornamento periodico dei referti mancanti su REDCap."},
    {"name": "REDCap Records", "description": "Consultazione, esportazione ed eliminazione record dal database REDCap."},
    {"name": "Security & Auth", "description": "Gestione e generazione delle API Key per l'accesso protetto via header."},
    {"name": "System", "description": "Verifica dello stato di salute dei sottosistemi e connettività."},
]

app = FastAPI(
    title="SenoNetwork Medical Report Extractor & Quality API",
    description=(
        "Backend REST API per SenoNetwork. Consente l'integrazione completa con dashboard web esterne "
        "(Laravel/FilamentPHP, SvelteKit, React, Vue) per l'ingestione da PriamoCUP, estrazione IA e audit REDCap."
    ),
    version="2.0.0",
    openapi_tags=openapi_tags,
)

# CORS Middleware per consentire chiamate dalla dashboard web esterna
origins = settings.cors_origins
if "*" in origins:
    allow_origins = ["*"]
else:
    allow_origins = origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache
def get_shared_settings() -> Settings:
    return get_settings()


@app.get("/health", tags=["System"])
def health_check(settings: Settings = Depends(get_shared_settings)):
    """Verifica lo stato dei server IA (llama.cpp RPC e Ollama), REDCap e Priamo .NET."""
    ollama = OllamaExtractor(base_url=settings.ollama_base_url)
    llamacpp = LlamaCppExtractor(
        base_url=settings.llama_cpp_base_url,
        cf_client_id=settings.ai_cloudflared_client_id,
        cf_client_secret=settings.ai_cloudflared_client_secret
    )
    dotnet = DotNetApiClient(settings=settings)

    return {
        "status": "online",
        "version": "2.0.0",
        "ai_backend_configured": settings.ai_backend,
        "llama_cpp_rpc_available": llamacpp.is_available(),
        "llama_cpp_base_url": settings.llama_cpp_base_url,
        "ollama_available": ollama.is_available(),
        "ollama_model": settings.ollama_model,
        "redcap_configured": bool(settings.redcap_api_url and settings.redcap_api_token),
        "priamo_dotnet_configured": dotnet.is_priamo_configured,
        "priamo_cup_url": settings.priamo_cup_url,
        "api_key_required": bool(settings.api_key and settings.api_key.strip())
    }


static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
def root():
    """Serve la Dashboard Web di SenoNetwork."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return RedirectResponse(url="/docs")


@app.get("/dashboard", include_in_schema=False)
def dashboard_view():
    """Endpoint esplicito per la Dashboard Web di SenoNetwork."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return RedirectResponse(url="/docs")


# Registrazione dei router modulari
app.include_router(dashboard_router)
app.include_router(cup_router)
app.include_router(priamo_router)
app.include_router(pipeline_router)
app.include_router(audit_router)
app.include_router(sync_router)
app.include_router(redcap_router)
app.include_router(security_router)


def run_server(host: Optional[str] = None, port: Optional[int] = None):
    cfg = get_settings()
    target_host = host or cfg.api_host
    target_port = port or cfg.api_port
    print(f"\n-> Avvio SenoNetwork API Server su http://{target_host}:{target_port}...")
    print(f"-> Documentazione Swagger interattiva disponibile su: http://{target_host}:{target_port}/docs\n")
    uvicorn.run("src.api_server:app", host=target_host, port=target_port, reload=False)


def main(argv: Optional[List[str]] = None) -> None:
    args_list = argv if argv is not None else sys.argv[1:]
    if "--generate-key" in args_list:
        key = generate_api_key()
        print("\n" + "=" * 65)
        print("  CHIAVE API GENERATA PER SENONETWORK DASHBOARD")
        print("=" * 65)
        print(f"API Key: {key}")
        print("-" * 65)
        print("Per utilizzarla, inserisci nel file .env:")
        print(f"SENONETWORK_API_KEY={key}")
        print("\nNelle richieste dalla dashboard (Laravel/SvelteKit), invia l'header:")
        print(f"X-API-Key: {key}\n")
        sys.exit(0)

    parser = argparse.ArgumentParser(description="Avvia il server API di SenoNetwork")
    parser.add_argument("--host", default=None, help="Host di ascolto (default da .env o 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Porta di ascolto (default da .env o 8000)")
    parser.add_argument("--generate-key", action="store_true", help="Genera una nuova API key crittograficamente sicura ed esci")
    args = parser.parse_args(args_list)

    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
