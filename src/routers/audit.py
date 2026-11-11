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
    settings: Settings = Depends(get_settings)
) -> RedCapAuditReport:
    """Esegue l'audit completo di qualità dati su REDCap, rilevando incongruenze e mancanze cliniche."""
    auditor = RedCapDataAuditor(settings=settings)
    try:
        report = auditor.run_audit(patient_id=patient_id)
        auditor.export_reports(report)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore generazione report audit: {e}")


@router.get("/api/audit/anomalies", dependencies=[Depends(verify_api_key)])
def get_audit_anomalies(
    severity: Optional[str] = Query(None, pattern="^(critical|warning|info)$", description="Filtro gravità anomalia"),
    category: Optional[str] = Query(None, description="Filtro categoria (es. date_inconsistency, missing_pathology)"),
    settings: Settings = Depends(get_settings)
) -> List[Dict[str, Any]]:
    """Restituisce l'elenco delle anomalie cliniche rilevate nei record REDCap, filtrabili per gravità."""
    auditor = RedCapDataAuditor(settings=settings)
    try:
        report = auditor.run_audit()
        anomalies = [a.model_dump() for a in report.anomalies]

        if severity:
            anomalies = [a for a in anomalies if a.get("severity") == severity]

        if category:
            anomalies = [a for a in anomalies if a.get("category") == category]

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
        report = auditor.run_audit()
        matrix = [m.model_dump() for m in report.matrix]

        # Calcolo percentuali di completamento complessive per form
        form_stats: Dict[str, Dict[str, int]] = {}
        for row in matrix:
            statuses = row.get("form_statuses", {})
            for form_name, status_code in statuses.items():
                if form_name not in form_stats:
                    form_stats[form_name] = {"complete": 0, "unverified": 0, "incomplete": 0, "not_started": 0}
                if status_code == "2":
                    form_stats[form_name]["complete"] += 1
                elif status_code == "1":
                    form_stats[form_name]["unverified"] += 1
                elif status_code == "0":
                    form_stats[form_name]["incomplete"] += 1
                else:
                    form_stats[form_name]["not_started"] += 1

        return {
            "total_patients": len(matrix),
            "form_statistics": form_stats,
            "patient_matrix": matrix
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore generazione matrice completamento: {e}")
