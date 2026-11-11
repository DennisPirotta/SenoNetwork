"""Dashboard Overview & KPI endpoint."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends
from src.config import Settings, get_settings
from src.ai_extractor import LlamaCppExtractor, OllamaExtractor
from src.dotnet_client import DotNetApiClient
from src.redcap_service import REDCapService
from src.routers.security import verify_api_key

router = APIRouter(tags=["Dashboard"])


@router.get("/api/dashboard/overview", dependencies=[Depends(verify_api_key)])
def get_dashboard_overview(
    settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Provides high-level KPI metrics, health checks, and recent activity for the web dashboard."""

    # 1. Health check of external services
    ollama = OllamaExtractor(base_url=settings.ollama_base_url)
    llamacpp = LlamaCppExtractor(
        base_url=settings.llama_cpp_base_url,
        cf_client_id=settings.ai_cloudflared_client_id,
        cf_client_secret=settings.ai_cloudflared_client_secret
    )
    dotnet = DotNetApiClient(settings=settings)

    health = {
        "status": "online",
        "ai_backend_configured": settings.ai_backend,
        "llama_cpp_rpc_available": llamacpp.is_available(),
        "llama_cpp_base_url": settings.llama_cpp_base_url,
        "ollama_available": ollama.is_available(),
        "ollama_model": settings.ollama_model,
        "redcap_configured": bool(settings.redcap_api_url and settings.redcap_api_token),
        "priamo_dotnet_configured": dotnet.is_priamo_configured,
        "priamo_cup_url": settings.priamo_cup_url,
    }

    # 2. PriamoCUP files and patients KPI
    cup_dir = Path("data/cup")
    cup_files = []
    latest_cup_file = None
    cup_patients_count = 0

    if cup_dir.exists():
        cup_files = sorted(list(cup_dir.glob("*.xlsx")) + list(cup_dir.glob("*.xls")), key=lambda f: f.stat().st_mtime, reverse=True)
        if cup_files:
            latest = cup_files[0]
            latest_cup_file = {
                "name": latest.name,
                "size_bytes": latest.stat().st_size,
                "modified_at": latest.stat().st_mtime,
            }

    # 3. REDCap records summary KPI
    redcap_summary = {"records_count": 0, "status": "unconfigured"}
    if settings.redcap_api_url and settings.redcap_api_token:
        try:
            redcap_svc = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)
            summary_data = redcap_svc.get_records_summary()
            redcap_summary = {
                "records_count": len(summary_data),
                "fully_completed_records": sum(1 for s in summary_data if s.get("forms_completed_count", 0) >= 8),
                "status": "connected"
            }
        except Exception as e:
            redcap_summary = {"records_count": 0, "status": "error", "error": str(e)}

    # 4. Local files count
    downloads_count = len(list(Path("data/downloads").rglob("*.pdf"))) if Path("data/downloads").exists() else 0
    state_files_count = len(list(Path("data/state").glob("*_state.json"))) if Path("data/state").exists() else 0

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
