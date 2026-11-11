#!/usr/bin/env python3
"""Client per l'interfacciamento con PriamoCUP (Ospedale Valduce).

Consente l'autenticazione tramite PriamoHOME e l'esportazione automatica
dei fogli di lavoro ambulatoriali (worksheets) in formato Excel (.xlsx) per:
  - Presidio Ospedaliero: '2037' (Valduce Como)
  - Unità Operativa: '37002' (Senologia / Chirurgia Senologica / Breast Unit)
  - Agende Ambulatoriali: 3000, 3003, 3004, 3035, 3036
"""

from datetime import date, datetime, timedelta
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import urllib3
from bs4 import BeautifulSoup
import requests
from requests import Session, RequestException

from src.config import Settings, get_settings

urllib3.disable_warnings()

logger = logging.getLogger("PriamoCupClient")

# ==============================================================================
# COSTANTI PREDEFINITE OSPEDALE VALDUCE
# ==============================================================================
CODICE_STRUTTURA_VALDUCE = "2037"

# Unità Senologia / Breast Unit
UNITA_SENOLOGIA = "37002"
AGENDE_SENOLOGIA = ["3000", "3003", "3004", "3035", "3036"]

_MODALITA_SESSIONE_DEFAULT = [
    {"codice": "N", "descrizione": "Normale"},
    {"codice": "E", "descrizione": "Extra"},
    {"codice": "I", "descrizione": "Interna"},
    {"codice": "C", "descrizione": "PazienteCronico"},
]


def to_form_pairs(data: Any, parent_key: str = "") -> List[Tuple[str, str]]:
    """Serializza ricorsivamente un dizionario/lista Python in coppie form-urlencoded

    compatibili con i model binder di ASP.NET MVC / Kendo UI.
    Esempio:
      model['filtri']['riga1']['colonna1']['daData'] = "..."
      model['filtri']['riga4']['strutturaErogatrice'][0]['codice'] = "3000"
    """
    pairs: List[Tuple[str, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            new_key = f"{parent_key}[{key}]" if parent_key else str(key)
            pairs.extend(to_form_pairs(value, new_key))
    elif isinstance(data, (list, tuple)):
        for index, item in enumerate(data):
            sub_key = f"{parent_key}[{index}]" if isinstance(item, (dict, list, tuple)) else f"{parent_key}[]"
            pairs.extend(to_form_pairs(item, sub_key))
    elif isinstance(data, bool):
        pairs.append((parent_key, "true" if data else "false"))
    elif data is None:
        pairs.append((parent_key, ""))
    else:
        pairs.append((parent_key, str(data)))
    return pairs


class PriamoCupAuthError(Exception):
    """Eccezione sollevata quando l'autenticazione su Priamo non va a buon fine."""


class PriamoCupClient:
    """Client per l'autenticazione e il download dei fogli di lavoro da PriamoCUP."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        cup_url: Optional[str] = None,
        home_url: Optional[str] = None,
        verify_ssl: bool = False,
    ):
        self.settings = settings or get_settings()
        self.username = username or self.settings.priamo_username
        self.password = password or self.settings.priamo_password
        self.cup_url = (cup_url or getattr(self.settings, "priamo_cup_url", "") or "http://srv-priamo-fe1.valduce.it:8082/PriamoCUP").rstrip("/")
        self.home_url = (home_url or getattr(self.settings, "priamo_home_url", "") or "https://priamo-home.valduce.it:8062/PriamoHOME").rstrip("/")
        self.verify_ssl = verify_ssl

        self.session = Session()
        self._is_authenticated = False

    def authenticate(self, force: bool = False) -> None:
        """Esegue il login su PriamoHOME acquisendo il cookie di sessione .PRIAMOAUTH.

        Il cookie è valido per l'intero dominio .valduce.it e garantisce l'accesso a PriamoCUP.
        """
        if self._is_authenticated and not force:
            return

        if not self.username or not self.password:
            raise PriamoCupAuthError("Credenziali Priamo mancanti (PRIAMO_USERNAME / PRIAMO_PASSWORD).")

        login_url = f"{self.home_url}/Account/Login"
        logger.info(f"Autenticazione su PriamoHOME ({login_url}) come utente '{self.username}'...")

        try:
            resp_get = self.session.get(login_url, timeout=(5, 30), verify=self.verify_ssl)
            resp_get.raise_for_status()
        except RequestException as e:
            raise PriamoCupAuthError(f"Impossibile raggiungere l'endpoint di login PriamoHOME ({login_url}): {e}") from e

        soup = BeautifulSoup(resp_get.text, "html.parser")
        token_input = soup.find("input", {"name": "__RequestVerificationToken"})
        if token_input is None:
            raise PriamoCupAuthError(f"Token di verifica non trovato nel form di login su {login_url}")

        payload = {
            "UserName": self.username,
            "Password": self.password,
            "__RequestVerificationToken": str(token_input.get("value", "")),
        }

        try:
            resp_post = self.session.post(
                login_url,
                data=payload,
                allow_redirects=True,
                timeout=(5, 30),
                verify=self.verify_ssl,
            )
            resp_post.raise_for_status()
        except RequestException as e:
            raise PriamoCupAuthError(f"Errore durante l'invio delle credenziali di login: {e}") from e

        # Verifica cookie .PRIAMOAUTH
        cookies = self.session.cookies.get_dict()
        if ".PRIAMOAUTH" not in cookies:
            # Verifica se siamo rimasti sul form di login
            if "/Account/Login" in resp_post.url:
                raise PriamoCupAuthError(f"Login fallito su Priamo: credenziali non valide o sessione non autorizzata ({resp_post.url}).")
            raise PriamoCupAuthError(f"Login fallito: cookie '.PRIAMOAUTH' non acquisito dopo la chiamata a {resp_post.url}.")

        self._is_authenticated = True
        logger.info("Autenticazione su PriamoHOME avvenuta con successo (.PRIAMOAUTH acquisito).")

    def get_csrf_token(self) -> str:
        """Recupera il token CSRF (requestVerificationToken) per le chiamate API di PriamoCUP."""
        self.authenticate()
        token_page_url = f"{self.cup_url}/Riepilogo/FoglioLavoro"

        try:
            resp = self.session.get(token_page_url, timeout=(5, 30), verify=self.verify_ssl)
            resp.raise_for_status()
        except RequestException as e:
            raise PriamoCupAuthError(f"Impossibile accedere alla pagina Riepilogo FoglioLavoro ({token_page_url}): {e}") from e

        regex_pattern = r"priamoMasterPage\.site\.requestVerificationToken\s*=\s*'([^']+)'"
        match = re.search(regex_pattern, resp.text)
        if not match:
            raise ValueError(f"requestVerificationToken non individuato nel contenuto HTML/JS di {token_page_url}")

        token = match.group(1).strip()
        return token

    def export_worksheets(
        self,
        start_date: Union[date, str],
        end_date: Union[date, str],
        codice_unita: str = UNITA_SENOLOGIA,
        agende: Optional[List[Union[str, int]]] = None,
        codice_struttura: str = CODICE_STRUTTURA_VALDUCE,
        output_dir: Union[str, Path] = "data/cup",
        custom_filename: Optional[str] = None,
    ) -> Path:
        """Esporta e scarica il file Excel dei fogli di lavoro da PriamoCUP.

        Parametri:
          start_date: Data inizio periodo (date o stringa 'YYYY-MM-DD')
          end_date: Data fine periodo (date o stringa 'YYYY-MM-DD')
          codice_unita: Codice unità operativa (default: '37002' Senologia)
          agende: Lista codici agende/erogatori (default: ['3000', '3003', '3004', '3035', '3036'])
          codice_struttura: Presidio (default: '2037')
          output_dir: Directory di destinazione del file scaricato
          custom_filename: Nome file personalizzato opzionale

        Ritorna:
          Path: Percorso assoluto o relativo del file Excel scaricato con successo.
        """
        self.authenticate()

        # Normalizzazione date
        if isinstance(start_date, str):
            start_dt = date.fromisoformat(start_date[:10])
        else:
            start_dt = start_date

        if isinstance(end_date, str):
            end_dt = date.fromisoformat(end_date[:10])
        else:
            end_dt = end_date

        if agende is None:
            if codice_unita == UNITA_SENOLOGIA:
                agende_list = [str(a) for a in AGENDE_SENOLOGIA]
            else:
                agende_list = []
        else:
            agende_list = [str(a) for a in agende]

        logger.info(
            f"Avvio esportazione fogli lavoro PriamoCUP: "
            f"Unità={codice_unita}, Agende={agende_list}, "
            f"Periodo={start_dt.isoformat()} -> {end_dt.isoformat()}"
        )

        csrf_token = self.get_csrf_token()

        model = {
            "filtri": {
                "riga1": {
                    "colonna1": {"daData": start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                    "colonna2": {"aData": end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                    "colonna3": {"tipoErogatore": {"selezionato": ""}},
                    "colonna4": {
                        "modalitaSessione": {
                            "selezionati": _MODALITA_SESSIONE_DEFAULT,
                            "selezionato": "",
                        }
                    },
                },
                "riga2": {
                    "colonna1": {"soloAttivi": True},
                    "colonna2": {"orariLiberi": False},
                    "colonna3": {"escludiOrariLiberiExtra": True},
                    "colonna4": {"postiBloccati": False},
                },
                "riga3": {
                    "strutture": {"selezionati": [{"codice": codice_struttura}], "selezionato": ""},
                    "aree": {
                        "selezionati": [{"codice": "AMBULATORIO", "codiceStruttura": [codice_struttura]}],
                        "selezionato": "",
                    },
                    "unita": {
                        "selezionati": [
                            {
                                "codice": f"{codice_struttura}|AMBULATORIO|{codice_unita}",
                                "codiceUnita": codice_unita,
                                "codiceStruttura": codice_struttura,
                                "area": "AMBULATORIO",
                            }
                        ],
                        "selezionato": "",
                    },
                },
                "riga4": {
                    "gruppiStruttureErogatrici": {"selezionato": ""},
                    "strutturaErogatrice": {
                        "selezionati": [{"codice": c} for c in agende_list],
                        "selezionato": "",
                    },
                },
            },
            "filtriStampeControllo": {
                "riga1": {"soloPostiBloccati": False, "soloCalendariCancellati": False},
            },
            "opzioni": {
                "riga1": {
                    "modalitaStampa": {"selezionato": {"codice": "Completa", "descrizione": "COMPLETA"}},
                    "raggruppata": False,
                    "dividiSessione": False,
                    "bloccoCalendario": False,
                    "anonimizzaDatiPazienti": False,
                    "anonimizzaDatiTelefonici": False,
                    "stampaBarCode": False,
                }
            },
            "configurazioni": {
                "attivaPulsanteAggiungiStruttureKendo": False,
                "attivaStampaBarcode": True,
            },
        }

        structured_payload = {"tipo": "xlsx", "model": model}
        payload = to_form_pairs(structured_payload)
        headers = {"RequestVerificationToken": csrf_token}

        esporta_url = f"{self.cup_url}/api/RiepilogoFoglioLavoro/Esporta"
        try:
            resp_export = self.session.post(esporta_url, headers=headers, data=payload, timeout=(10, 60))
            resp_export.raise_for_status()
        except RequestException as e:
            raise RuntimeError(f"Errore durante la richiesta di generazione report PriamoCUP: {e}") from e

        # Il server restituisce il nome del file temporaneo tra virgolette (es. "71061a8b...xlsx")
        remote_filename = resp_export.text.strip('"\'')
        if not remote_filename or not remote_filename.endswith(".xlsx"):
            raise RuntimeError(f"Risposta imprevista dall'endpoint di esportazione PriamoCUP: {resp_export.text}")

        # Download del file generato
        preleva_url = f"{self.cup_url}/api/RiepilogoFoglioLavoro/Preleva"
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        if custom_filename:
            local_name = custom_filename if custom_filename.endswith(".xlsx") else f"{custom_filename}.xlsx"
        else:
            local_name = f"fogli_lavoro_unita_{codice_unita}_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.xlsx"

        dest_path = out_dir / local_name

        try:
            with self.session.get(preleva_url, params={"fileName": remote_filename}, stream=True, timeout=(10, 60)) as resp_down:
                resp_down.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in resp_down.iter_content(chunk_size=8192):
                        f.write(chunk)
        except RequestException as e:
            raise RuntimeError(f"Errore durante il prelievo del file esportato da PriamoCUP ({remote_filename}): {e}") from e

        logger.info(f"Foglio di lavoro scaricato con successo ({dest_path.stat().st_size} bytes): {dest_path}")
        return dest_path

    def export_week(self, ref_date: Optional[Union[date, str]] = None, **kwargs) -> Path:
        """Esporta il foglio di lavoro per l'intera settimana (da lunedì a domenica)

        in cui ricade `ref_date` (default: data odierna).
        """
        if ref_date is None:
            base_date = date.today()
        elif isinstance(ref_date, str):
            base_date = date.fromisoformat(ref_date[:10])
        else:
            base_date = ref_date

        lunedi = base_date - timedelta(days=base_date.weekday())
        domenica = lunedi + timedelta(days=6)
        logger.info(f"Esportazione settimana: {lunedi.strftime('%d/%m/%Y')} -> {domenica.strftime('%d/%m/%Y')}")
        return self.export_worksheets(start_date=lunedi, end_date=domenica, **kwargs)

    def export_weeks(
        self,
        start_date: Optional[Union[date, str]] = None,
        num_weeks: int = 1,
        **kwargs
    ) -> List[Path]:
        """Esporta settimana per settimana per `num_weeks` settimane a partire da `start_date` (default: oggi).

        Restituisce la lista dei percorsi dei file Excel scaricati.
        """
        if start_date is None:
            base_date = date.today()
        elif isinstance(start_date, str):
            base_date = date.fromisoformat(start_date[:10])
        else:
            base_date = start_date

        curr_lunedi = base_date - timedelta(days=base_date.weekday())
        saved_paths: List[Path] = []

        for i in range(num_weeks):
            curr_domenica = curr_lunedi + timedelta(days=6)
            logger.info(f"[Settimana {i + 1}/{num_weeks}] Esportazione da {curr_lunedi.strftime('%d/%m/%Y')} a {curr_domenica.strftime('%d/%m/%Y')}")
            file_path = self.export_worksheets(start_date=curr_lunedi, end_date=curr_domenica, **kwargs)
            saved_paths.append(file_path)
            curr_lunedi += timedelta(days=7)

        return saved_paths
