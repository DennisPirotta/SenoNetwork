"""Priamo HIS (Ospedale Valduce) Verification API Router."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.config import Settings, get_settings
from src.dotnet_client import DotNetApiClient
from scripts.extract_patients_from_shared_file import estrai_pazienti_univoci
from src.routers.cup import _find_target_cup_file
from src.routers.security import verify_api_key

router = APIRouter(tags=["Priamo HIS"])


class VerifySinglePatientRequest(BaseModel):
    name: str = Field(..., description="Nome del paziente")
    surname: str = Field(..., description="Cognome del paziente")
    date_of_birth: Optional[str] = Field(default=None, description="Data di nascita (YYYY-MM-DD)")


class VerifyCohortRequest(BaseModel):
    patients: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Lista di pazienti da verificare. Se omessa, carica automaticamente i pazienti dall'ultimo foglio CUP."
    )
    cup_file_name: Optional[str] = Field(default=None, description="Nome file CUP alternativo da cui caricare i pazienti")
    limit: Optional[int] = Field(default=None, ge=1, le=5000, description="Numero massimo di pazienti da verificare")
    max_workers: int = Field(default=4, ge=1, le=16, description="Numero di thread concorrenti per la verifica")


@router.post("/api/priamo/verify-patient", dependencies=[Depends(verify_api_key)])
def verify_patient(
    req: VerifySinglePatientRequest,
    settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Verifica l'esistenza e recupera l'anagrafica certificata di un paziente da Priamo HIS."""
    dotnet = DotNetApiClient(settings=settings)
    if not dotnet.is_priamo_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Priamo non configurato. Impostare PRIAMO_API_URL, PRIAMO_USERNAME e PRIAMO_PASSWORD in .env."
        )

    try:
        p = dotnet.verify_patient_in_priamo(
            name=req.name,
            surname=req.surname,
            date_of_birth=req.date_of_birth
        )

        if not p:
            return {
                "found": False,
                "name": req.name,
                "surname": req.surname,
                "date_of_birth": req.date_of_birth,
                "message": "Paziente non trovato su Priamo HIS."
            }

        return {
            "found": True,
            "patient_id": p.patient_id,
            "name": p.name,
            "surname": p.surname,
            "date_of_birth": str(p.date_of_birth) if p.date_of_birth else None,
            "codice_fiscale": p.codice_fiscale,
            "comune_nascita": p.comune_nascita,
            "sesso": p.sesso,
            "telefono": p.telefono,
            "tessera_sanitaria": p.tessera_sanitaria,
            "slug": p.slug
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore verifica Priamo: {e}")


@router.post("/api/priamo/verify-cohort", dependencies=[Depends(verify_api_key)])
def verify_cohort(
    req: VerifyCohortRequest,
    settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Verifica in parallelo una coorte di pazienti su Priamo HIS."""
    dotnet = DotNetApiClient(settings=settings)
    if not dotnet.is_priamo_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Priamo non configurato. Impostare PRIAMO_API_URL, PRIAMO_USERNAME e PRIAMO_PASSWORD in .env."
        )

    cohort = req.patients
    source_desc = "payload"

    if not cohort:
        target_file = _find_target_cup_file(req.cup_file_name, cup_dir=settings.cup_dir)
        pzs = estrai_pazienti_univoci(target_file, only_complete=True)
        cohort = [
            {
                "patient_id": p.patient_id,
                "name": p.nome or "",
                "surname": p.cognome,
                "date_of_birth": p.data_nascita,
                "codice_fiscale": p.codice_fiscale,
                "priamo_id": p.priamo_id
            }
            for p in pzs
        ]
        source_desc = target_file.name

    if req.limit:
        cohort = cohort[:req.limit]

    try:
        results = dotnet.verify_patients_cohort(cohort, max_workers=req.max_workers)
        found_count = sum(1 for r in results if r.get("status") == "found")
        not_found_count = sum(1 for r in results if r.get("status") == "not_found")
        incomplete_count = sum(1 for r in results if r.get("status") == "incomplete")
        error_count = sum(1 for r in results if r.get("status") == "error")

        return {
            "source": source_desc,
            "total_tested": len(results),
            "found_count": found_count,
            "not_found_count": not_found_count,
            "incomplete_count": incomplete_count,
            "error_count": error_count,
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore verifica coorte Priamo: {e}")
