"""Data Quality Audit & Clinical Inconsistencies API Router."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from src.config import Settings, get_settings
from src.audit_service import RedCapAuditReport, RedCapDataAuditor
from src.routers.security import verify_api_key

router = APIRouter(tags=["Data Quality & Audit"])


@router.get("/api/audit/report", dependencies=[Depends(verify_api_key)], response_model=RedCapAuditReport)
def get_audit_report(
    patient_id: Optional[str] = Query(None, description="Filtra audit su un singolo patient_id"),
    export: bool = Query(False, description="Se True, salva i report Markdown/JSON su disco"),
    settings: Settings = Depends(get_settings)
) -> RedCapAuditReport:
    """Esegue l'audit completo di qualità dati su REDCap, rilevando incongruenze e mancanze cliniche."""
    auditor = RedCapDataAuditor(settings=settings)
    try:
        report = auditor.run_audit(patient_id=patient_id)
        if export is True:
            auditor.export_reports(report)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore generazione report audit: {e}")


@router.get("/api/audit/anomalies", dependencies=[Depends(verify_api_key)])
def get_audit_anomalies(
    severity: Optional[str] = Query(None, pattern="(?i)^(critical|warning|info)$", description="Filtro gravità anomalia"),
    category: Optional[str] = Query(None, description="Filtro categoria (es. date_inconsistency, missing_pathology)"),
    settings: Settings = Depends(get_settings)
) -> List[Dict[str, Any]]:
    """Restituisce l'elenco delle anomalie cliniche rilevate nei record REDCap, filtrabili per gravità."""
    auditor = RedCapDataAuditor(settings=settings)
    try:
        report = auditor.run_audit()
        anomalies = [a.model_dump() for a in report.issues]

        if severity:
            sev_target = severity.strip().lower()
            anomalies = [
                a for a in anomalies
                if str(getattr(a.get("severity"), "value", a.get("severity") or "")).strip().lower() == sev_target
            ]

        if category:
            cat_target = category.strip().lower()
            anomalies = [
                a for a in anomalies
                if (
                    str(getattr(a.get("category"), "value", a.get("category") or "")).strip().lower() == cat_target
                    or str(getattr(a.get("category"), "name", "")).strip().lower() == cat_target
                )
            ]

        return anomalies
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore recupero anomalie: {e}")


@router.get("/api/audit/completion-matrix", dependencies=[Depends(verify_api_key)])
def get_completion_matrix(
    settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Restituisce la matrice di completamento dei form per ciascun paziente presente su REDCap."""
    auditor = RedCapDataAuditor(settings=settings)
    try:
        return auditor.build_completion_matrix()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore generazione matrice completamento: {e}")
