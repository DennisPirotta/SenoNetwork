#!/usr/bin/env python3
"""Client per l'interfacciamento con PriamoCUP (Ospedale Valduce).

Consente l'autenticazione tramite PriamoHOME e l'esportazione automatica
dei fogli di lavoro ambulatoriali (worksheets) in formato Excel (.xlsx) per:
  - Presidio Ospedaliero: '2037' (Valduce Como)
  - Unità Operativa: '37002' (Senologia / Chirurgia Senologica / Breast Unit)
  - Agende Ambulatoriali: 3000, 3003, 3004, 3035, 3036
"""

import calendar
from datetime import date, datetime, timedelta
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse
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

        # Risoluzione URL con supporto tunnel / reverse proxy (es. priamo.dennispirotta.com)
        base_api = (self.settings.priamo_api_url or os.getenv("PRIAMO_API_URL", "")).rstrip("/")
        if base_api:
            clean_base = re.sub(r"/(PriamoHOME|PriamoCES|PriamoCUP)$", "", base_api, flags=re.IGNORECASE)
            fallback_home = f"{clean_base}/PriamoHOME"
            fallback_cup = f"{clean_base}/PriamoCUP"
        else:
            fallback_home = "https://priamo-home.valduce.it:8062/PriamoHOME"
            fallback_cup = "http://srv-priamo-fe1.valduce.it:8082/PriamoCUP"

        cfg_home = getattr(self.settings, "priamo_home_url", "")
        if not home_url and (not cfg_home or ("valduce.it" in cfg_home and base_api and "valduce.it" not in base_api)):
            self.home_url = fallback_home
        else:
            self.home_url = (home_url or cfg_home or fallback_home).rstrip("/")

        cfg_cup = getattr(self.settings, "priamo_cup_url", "")
        if not cup_url and (not cfg_cup or ("valduce.it" in cfg_cup and base_api and "valduce.it" not in base_api)):
            self.cup_url = fallback_cup
        else:
            self.cup_url = (cup_url or cfg_cup or fallback_cup).rstrip("/")

        self.verify_ssl = verify_ssl

        # Supporto Cloudflare Access Client Headers
        self.cf_client_id = (
            getattr(self.settings, "priamo_cloudflared_client_id", None)
            or getattr(self.settings, "cf_client_id", None)
            or os.getenv("PRIAMO_CLOUDFLARED_CLIENT_ID", "")
            or os.getenv("CLOUDFLARED_CLIENT_ID", "")
        )
        self.cf_client_secret = (
            getattr(self.settings, "priamo_cloudflared_client_secret", None)
            or getattr(self.settings, "cf_client_secret", None)
            or os.getenv("PRIAMO_CLOUDFLARED_CLIENT_SECRET", "")
            or os.getenv("CLOUDFLARED_CLIENT_SECRET", "")
        )

        self.session = Session()
        if self.cf_client_id and self.cf_client_secret:
            self.session.headers.update({
                "CF-Access-Client-Id": self.cf_client_id,
                "CF-Access-Client-Secret": self.cf_client_secret,
            })
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

        # Estrazione manuale del cookie .PRIAMOAUTH da Set-Cookie per gestire mismatch di dominio
        # attraverso tunnel Cloudflare o reverse proxy (es. backend invia dominio .valduce.it)
        all_responses = list(resp_post.history) + [resp_post]
        for resp in all_responses:
            raw_headers = getattr(resp.raw, "headers", None)
            set_cookies = []
            if raw_headers and hasattr(raw_headers, "getlist"):
                set_cookies = raw_headers.getlist("Set-Cookie")
            elif raw_headers and hasattr(raw_headers, "get_all"):
                set_cookies = raw_headers.get_all("Set-Cookie")
            elif "Set-Cookie" in resp.headers:
                set_cookies = [resp.headers["Set-Cookie"]]

            for sc in set_cookies:
                match = re.search(r"(?i)\.PRIAMOAUTH=([^;]+)", sc)
                if match:
                    priamo_auth_val = match.group(1).strip()
                    target_host = urlparse(self.home_url).hostname
                    if target_host:
                        self.session.cookies.set(".PRIAMOAUTH", priamo_auth_val, domain=target_host, path="/")
                    break

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

    def export_ytd(
        self,
        ref_date: Optional[Union[date, str]] = None,
        **kwargs
    ) -> Path:
        """Esporta tutti i fogli di lavoro dall'inizio dell'anno (1° Gennaio) fino a Oggi - 1 giorno (Ieri).

        Parametri:
          ref_date: Data di riferimento (default: data odierna).
          **kwargs: Parametri aggiuntivi per export_worksheets (es. output_dir, codice_unita, agende).
        """
        if ref_date is None:
            base_date = date.today()
        elif isinstance(ref_date, str):
            base_date = date.fromisoformat(ref_date[:10])
        else:
            base_date = ref_date

        start_date = date(base_date.year, 1, 1)
        end_date = base_date - timedelta(days=1)

        # Se oggi fosse il 1° gennaio, copri il singolo giorno
        if end_date < start_date:
            end_date = start_date

        codice_unita = kwargs.get("codice_unita", UNITA_SENOLOGIA)
        default_fn = f"fogli_lavoro_unita_{codice_unita}_YTD_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx"
        custom_fn = kwargs.pop("custom_filename", None) or default_fn

        logger.info(f"Esportazione YTD (1° Gennaio -> Ieri): {start_date.strftime('%d/%m/%Y')} -> {end_date.strftime('%d/%m/%Y')}")
        return self.export_worksheets(
            start_date=start_date,
            end_date=end_date,
            custom_filename=custom_fn,
            **kwargs
        )

    def export_year(
        self,
        year: int,
        up_to_yesterday_if_current: bool = True,
        **kwargs
    ) -> Path:
        """Esporta il foglio di lavoro per un intero anno solare (es. 2025, 2024, ecc.).

        Se l'anno richiesto è quello corrente e up_to_yesterday_if_current=True,
        la data di fine viene impostata a Oggi - 1 giorno.
        """
        start_date = date(year, 1, 1)
        today = date.today()

        if year == today.year and up_to_yesterday_if_current:
            end_date = max(start_date, today - timedelta(days=1))
        else:
            end_date = date(year, 12, 31)

        codice_unita = kwargs.get("codice_unita", UNITA_SENOLOGIA)
        default_fn = f"fogli_lavoro_unita_{codice_unita}_anno_{year}.xlsx"
        custom_fn = kwargs.pop("custom_filename", None) or default_fn

        logger.info(f"Esportazione anno {year}: {start_date.strftime('%d/%m/%Y')} -> {end_date.strftime('%d/%m/%Y')}")
        return self.export_worksheets(
            start_date=start_date,
            end_date=end_date,
            custom_filename=custom_fn,
            **kwargs
        )

    def export_month(
        self,
        year: int,
        month: int,
        **kwargs
    ) -> Path:
        """Esporta il foglio di lavoro per uno specifico mese di un anno (es. anno 2025, mese 10)."""
        start_date = date(year, month, 1)
        _, last_day = calendar.monthrange(year, month)
        end_date = date(year, month, last_day)

        codice_unita = kwargs.get("codice_unita", UNITA_SENOLOGIA)
        default_fn = f"fogli_lavoro_unita_{codice_unita}_mese_{year}{month:02d}.xlsx"
        custom_fn = kwargs.pop("custom_filename", None) or default_fn

        logger.info(f"Esportazione mese {month:02d}/{year}: {start_date.strftime('%d/%m/%Y')} -> {end_date.strftime('%d/%m/%Y')}")
        return self.export_worksheets(
            start_date=start_date,
            end_date=end_date,
            custom_filename=custom_fn,
            **kwargs
        )

    def export_last_months(
        self,
        num_months: int = 1,
        ref_date: Optional[Union[date, str]] = None,
        **kwargs
    ) -> Path:
        """Esporta il foglio di lavoro per gli ultimi N mesi fino a Oggi - 1 giorno."""
        if ref_date is None:
            base_date = date.today()
        elif isinstance(ref_date, str):
            base_date = date.fromisoformat(ref_date[:10])
        else:
            base_date = ref_date

        end_date = base_date - timedelta(days=1)

        # Calcolo mese di inizio
        year = base_date.year
        month = base_date.month - num_months
        while month <= 0:
            month += 12
            year -= 1
        start_date = date(year, month, 1)

        codice_unita = kwargs.get("codice_unita", UNITA_SENOLOGIA)
        default_fn = f"fogli_lavoro_unita_{codice_unita}_ultimi_{num_months}_mesi_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx"
        custom_fn = kwargs.pop("custom_filename", None) or default_fn

        logger.info(f"Esportazione ultimi {num_months} mesi: {start_date.strftime('%d/%m/%Y')} -> {end_date.strftime('%d/%m/%Y')}")
        return self.export_worksheets(
            start_date=start_date,
            end_date=end_date,
            custom_filename=custom_fn,
            **kwargs
        )

    def download_updated_worksheets(
        self,
        ytd: bool = False,
        year: Optional[int] = None,
        month: Optional[Union[str, int]] = None,
        months: Optional[int] = None,
        start_date: Optional[Union[str, date]] = None,
        end_date: Optional[Union[str, date]] = None,
        weeks: Optional[int] = None,
        codice_unita: str = UNITA_SENOLOGIA,
        agende: Optional[List[str]] = None,
        output_dir: Union[str, Path] = "data/cup",
        ref_date: Optional[Union[date, str]] = None,
    ) -> Path:
        """Scarica il foglio di lavoro aggiornato da PriamoCUP in base alle opzioni fornite.

        Se nessun intervallo temporale specifico è indicato (oppure se ytd=True),
        scarica di default tutti i fogli di lavoro da 1° Gennaio fino a ieri (YTD).
        """
        agendas = agende or AGENDE_SENOLOGIA

        if year is not None:
            logger.info(f"Download fogli lavoro PriamoCUP per l'anno {year}...")
            return self.export_year(
                year=year,
                codice_unita=codice_unita,
                agende=agendas,
                output_dir=output_dir,
            )

        if month is not None:
            month_str = str(month).strip()
            if "-" in month_str:
                parts = month_str.split("-")
                target_year = int(parts[0])
                target_month = int(parts[1])
            else:
                base_year = (ref_date.year if isinstance(ref_date, date) else date.today().year)
                target_year = base_year
                target_month = int(month_str)

            logger.info(f"Download fogli lavoro PriamoCUP per il mese {target_month:02d}/{target_year}...")
            return self.export_month(
                year=target_year,
                month=target_month,
                codice_unita=codice_unita,
                agende=agendas,
                output_dir=output_dir,
            )

        if months is not None:
            logger.info(f"Download fogli lavoro PriamoCUP per gli ultimi {months} mesi...")
            return self.export_last_months(
                num_months=months,
                ref_date=ref_date,
                codice_unita=codice_unita,
                agende=agendas,
                output_dir=output_dir,
            )

        if start_date is not None and end_date is not None:
            logger.info(f"Download fogli lavoro PriamoCUP dal {start_date} al {end_date}...")
            return self.export_worksheets(
                start_date=start_date,
                end_date=end_date,
                codice_unita=codice_unita,
                agende=agendas,
                output_dir=output_dir,
            )

        if weeks is not None:
            base_ref = ref_date or start_date or date.today()
            logger.info(f"Download fogli lavoro PriamoCUP per {weeks} settimana/e da {base_ref}...")
            if weeks > 1:
                files = self.export_weeks(
                    start_date=base_ref,
                    num_weeks=weeks,
                    codice_unita=codice_unita,
                    agende=agendas,
                    output_dir=output_dir,
                )
                return files[0]
            return self.export_week(
                ref_date=base_ref,
                codice_unita=codice_unita,
                agende=agendas,
                output_dir=output_dir,
            )

        # Default: YTD (da 1° Gennaio fino a ieri)
        logger.info("Download fogli lavoro PriamoCUP: YTD (1° Gennaio -> Ieri)...")
        return self.export_ytd(
            ref_date=ref_date,
            codice_unita=codice_unita,
            agende=agendas,
            output_dir=output_dir,
        )
