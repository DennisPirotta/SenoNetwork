"""REDCap Records Management API Router."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.config import Settings, get_settings
import src.redcap_service as redcap_service
from src.redcap_service import REDCapService
from src.routers.security import verify_api_key

router = APIRouter(tags=["REDCap Records"])


def get_redcap_service(settings: Settings = Depends(get_settings)) -> REDCapService:
    if not settings.redcap_api_url or not settings.redcap_api_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configurazione REDCap mancante (REDCAP_API_URL o REDCAP_API_TOKEN non impostati)."
        )
    return REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)


@router.get("/api/redcap/records", dependencies=[Depends(verify_api_key)])
def get_all_records(
    limit: Optional[int] = Query(None, ge=1, description="Numero massimo di record da restituire"),
    filter_logic: Optional[str] = Query(None, description="Espressione logica REDCap (es. [age] > 50)"),
    form: Optional[str] = Query(None, description="Filtra campi appartenenti a un singolo form"),
    redcap_svc: REDCapService = Depends(get_redcap_service)
) -> List[Dict[str, Any]]:
    """Restituisce l'elenco di tutti i record presenti in REDCap con anagrafica e stato di avanzamento form."""
    forms_list = [form] if form else None
    try:
        records = redcap_svc.list_all_records(
            forms=forms_list,
            include_demographics=True,
            include_completion_status=True,
            filter_logic=filter_logic
        )
        if limit:
            records = records[:limit]
        return records
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore recupero record REDCap: {e}")


@router.get("/api/redcap/records/summary", dependencies=[Depends(verify_api_key)])
def get_records_summary(
    redcap_svc: REDCapService = Depends(get_redcap_service)
) -> Dict[str, Any]:
    """Restituisce un riepilogo aggregato dei record e dei tassi di completamento dei form su REDCap."""
    try:
        return redcap_svc.get_records_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore generazione riassunto REDCap: {e}")


@router.get("/api/redcap/records/{record_id}", dependencies=[Depends(verify_api_key)])
def get_single_record(
    record_id: str,
    redcap_svc: REDCapService = Depends(get_redcap_service)
) -> Dict[str, Any]:
    """Recupera tutti i dati clinici e i campi registrati per un singolo record_id su REDCap."""
    try:
        records = redcap_svc.list_all_records(include_demographics=True, include_completion_status=True)
        matched = [r for r in records if str(r.get("record_id")) == str(record_id)]
        if not matched:
            raise HTTPException(status_code=404, detail=f"Record '{record_id}' non trovato su REDCap.")
        return matched[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore recupero record '{record_id}': {e}")


@router.delete("/api/redcap/records/{record_id}", dependencies=[Depends(verify_api_key)])
def delete_single_record(
    record_id: str,
    clean_local_state: bool = Query(False, description="Rimuove anche il file di tracking locale in data/state"),
    settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Elimina un singolo record da REDCap specificando il suo record_id."""
    try:
        count = redcap_service.delete_redcap_records(
            record_id=record_id,
            clean_local_state=clean_local_state,
            api_url=settings.redcap_api_url,
            api_token=settings.redcap_api_token
        )
        if count == 0:
            raise HTTPException(status_code=404, detail=f"Record '{record_id}' non trovato o non eliminato da REDCap.")

        return {
            "status": "deleted",
            "record_id": record_id,
            "deleted_count": count,
            "message": f"Record '{record_id}' eliminato con successo da REDCap."
        }
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore eliminazione record '{record_id}': {e}")


@router.delete("/api/redcap/records", dependencies=[Depends(verify_api_key)])
def delete_all_records(
    confirm: bool = Query(False, description="Conferma esplicita richiesta per operazione irreversibile"),
    clean_local_state: bool = Query(False, description="Rimuove anche i file di tracking locale in data/state"),
    settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Elimina TUTTI i record attualmente presenti nel progetto REDCap (richiede ?confirm=true)."""
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Operazione ad alto rischio: per eliminare tutti i record è obbligatorio specificare il parametro ?confirm=true."
        )

    try:
        count = redcap_service.delete_redcap_records(
            all_records=True,
            confirm=True,
            clean_local_state=clean_local_state,
            api_url=settings.redcap_api_url,
            api_token=settings.redcap_api_token
        )
        return {
            "status": "deleted",
            "deleted_count": count,
            "message": f"Eliminati con successo {count} record da REDCap."
        }
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore eliminazione massiva record REDCap: {e}")
