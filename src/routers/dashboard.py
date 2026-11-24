"""Dashboard Overview & KPI endpoint."""

import logging
from pathlib import Path
from typing import Any, Dict
from fastapi import APIRouter, Depends
from src.ai_extractor import probe_ai_health
from src.config import Settings, get_settings
from src.dotnet_client import DotNetApiClient
from src.form_classifier import AVAILABLE_FORMS
from src.redcap_service import REDCapService
from src.routers.security import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Dashboard"])


@router.get("/api/dashboard/overview", dependencies=[Depends(verify_api_key)])
def get_dashboard_overview(
    settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Provides high-level KPI metrics, health checks, and recent activity for the web dashboard."""
    ai_status = probe_ai_health(settings)
    dotnet = DotNetApiClient(settings=settings)

    health = {
        "status": "online",
        "ai_backend_configured": settings.ai_backend,
        "llama_cpp_rpc_available": ai_status["llama_cpp_rpc_available"],
        "ollama_available": ai_status["ollama_available"],
        "ollama_model": settings.ollama_model,
        "redcap_configured": bool(settings.redcap_api_url and settings.redcap_api_token),
        "priamo_dotnet_configured": dotnet.is_priamo_configured,
    }

    # 2. PriamoCUP files and patients KPI
    cup_dir = Path(settings.cup_dir)
    cup_files = []
    latest_cup_file = None

    if cup_dir.exists():
        cup_files = sorted(
            list(cup_dir.glob("*.xlsx")) + list(cup_dir.glob("*.xls")),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        if cup_files:
            latest = cup_files[0]
            latest_cup_file = {
                "name": latest.name,
                "size_bytes": latest.stat().st_size,
                "modified_at": latest.stat().st_mtime,
            }

    # 3. REDCap records summary KPI
    total_forms = len(AVAILABLE_FORMS)
    redcap_summary: Dict[str, Any] = {"records_count": 0, "total_forms": total_forms, "status": "unconfigured"}
    if settings.redcap_api_url and settings.redcap_api_token:
        try:
            redcap_svc = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)
            summary_data = redcap_svc.get_records_summary()
            redcap_summary = {
                "records_count": len(summary_data),
                "fully_completed_records": sum(
                    1 for s in summary_data if s.get("forms_completed_count", 0) >= total_forms
                ),
                "total_forms": total_forms,
                "status": "connected"
            }
        except Exception as e:
            logger.warning("Errore recupero riepilogo REDCap per dashboard: %s", e)
            redcap_summary = {
                "records_count": 0,
                "total_forms": total_forms,
                "status": "error",
                "error": "Connessione REDCap non riuscita."
            }

    # 4. Local files count
    downloads_dir = Path(settings.downloads_dir)
    state_dir = Path(settings.state_dir)
    downloads_count = len(list(downloads_dir.rglob("*.pdf"))) if downloads_dir.exists() else 0
    state_files_count = len(list(state_dir.glob("*_state.json"))) if state_dir.exists() else 0

    return {
        "health": health,
        "cup_metrics": {
            "total_files": len(cup_files),
            "latest_file": latest_cup_file,
        },
        "redcap_metrics": redcap_summary,
        "storage_metrics": {
            "downloaded_pdfs": downloads_count,
            "processed_patients_state": state_files_count
        }
    }
