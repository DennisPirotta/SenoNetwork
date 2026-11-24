#!/usr/bin/env python3
"""
Test e Diagnostica Autenticazione Priamo .NET (HIS Valduce) tramite Cloudflare Tunnel
====================================================================================
Questo script esegue una diagnosi completa del flusso di autenticazione e delle API di Priamo,
particolarmente utile quando il servizio viene esposto tramite cloudflared su dominio personalizzato
(es. priamo.miodominio.com).

Identifica ed evidenzia automaticamente i problemi comuni dei tunnel/reverse-proxy:
1. Mancata corrispondenza del dominio del cookie (.PRIAMOAUTH con Domain=valduce.it rifiutato dai browser).
2. Header Host e binding IIS errati.
3. Blocchi o credenziali mancanti di Cloudflare Zero Trust (CF-Access).
4. Redirect ad indirizzi IP o host interni non raggiungibili dall'esterno.
5. Verifica funzionamento degli endpoint API (/PriamoCES/api/Anagrafica, /PriamoCES/api/Eventi).

Utilizzo:
    python3 scripts/test_priamo_auth.py
    python3 scripts/test_priamo_auth.py --url https://priamo.miodominio.com -u TUO_USERNAME -p 'TuaPass'
    python3 scripts/test_priamo_auth.py --verbose
"""

import argparse
import http.cookies
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

try:
    import requests
    from requests import Response, Session
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("[ERRORE] Il pacchetto 'requests' non è installato. Esegui: pip install requests")
    sys.exit(1)

# Parsing opzionale con BeautifulSoup (con fallback su regex nativo)
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


# ==============================================================================
# Helper ANSI per output a colori
# ==============================================================================
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    GRAY = "\033[90m"

    @classmethod
    def disable(cls):
        cls.RESET = ""
        cls.BOLD = ""
        cls.GREEN = ""
        cls.RED = ""
        cls.YELLOW = ""
        cls.BLUE = ""
        cls.CYAN = ""
        cls.MAGENTA = ""
        cls.GRAY = ""


def print_title(title: str) -> None:
    sep = "=" * 80
    print(f"\n{Colors.BOLD}{Colors.CYAN}{sep}")
    print(f" {title}")
    print(f"{sep}{Colors.RESET}\n")


def print_step(step_num: int, title: str) -> None:
    print(f"\n{Colors.BOLD}{Colors.BLUE}▶ [FASE {step_num}] {title}{Colors.RESET}")
    print(f"{Colors.GRAY}{'-' * 70}{Colors.RESET}")


def print_ok(msg: str) -> None:
    print(f"  {Colors.GREEN}[✓ OK]{Colors.RESET} {msg}")


def print_fail(msg: str) -> None:
    print(f"  {Colors.RED}[✗ ERRORE]{Colors.RESET} {msg}")


def print_warn(msg: str) -> None:
    print(f"  {Colors.YELLOW}[⚠ ATTENZIONE]{Colors.RESET} {msg}")


def print_info(msg: str) -> None:
    print(f"  {Colors.CYAN}[ℹ INFO]{Colors.RESET} {msg}")


# ==============================================================================
# Parsing .env file leggero (senza dipendenze esterne)
# ==============================================================================
def load_env_file(filepath: str = ".env") -> Dict[str, str]:
    env_vars: Dict[str, str] = {}
    path = Path(filepath)
    if not path.is_file():
        return env_vars

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                env_vars[k] = v
    return env_vars


# ==============================================================================
# Runner di Diagnostica Priamo
# ==============================================================================
class PriamoDiagnosticRunner:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        cf_client_id: Optional[str] = None,
        cf_client_secret: Optional[str] = None,
        host_header: Optional[str] = None,
        verify_ssl: bool = True,
        verbose: bool = False,
        timeout: int = 15,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.cf_client_id = cf_client_id
        self.cf_client_secret = cf_client_secret
        self.host_header = host_header
        self.verify_ssl = verify_ssl
        self.verbose = verbose
        self.timeout = timeout

        self.parsed_url = urlparse(self.base_url)
        self.target_host = self.parsed_url.hostname or ""

        # Endpoint Priamo
        self.login_url = f"{self.base_url}/PriamoHOME/Account/Login"
        self.home_url = f"{self.base_url}/PriamoHOME"
        self.ces_url = f"{self.base_url}/PriamoCES"
        self.api_anagrafica_url = f"{self.ces_url}/api/Anagrafica/GetAnagrafica"
        self.api_evento_url = f"{self.ces_url}/api/Eventi/GetEvento"

        # Stato diagnostico raccolto
        self.diag_results: Dict[str, Any] = {
            "reachability": False,
            "cf_access_blocked": False,
            "csrf_token_found": False,
            "login_post_status": None,
            "priamo_auth_received": False,
            "priamo_auth_raw_domain": None,
            "domain_mismatch_detected": False,
            "redirect_location": None,
            "browser_loop_confirmed": False,
            "session_valid_with_fix": False,
            "api_anagrafica_ok": False,
        }

        # Sessione HTTP
        self.session = Session()
        if not self.verify_ssl:
            self.session.verify = False

        # Configura header Cloudflare Access se forniti
        self.custom_headers: Dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        }
        if self.cf_client_id and self.cf_client_secret:
            self.custom_headers["CF-Access-Client-Id"] = self.cf_client_id
            self.custom_headers["CF-Access-Client-Secret"] = self.cf_client_secret

        if self.host_header:
            self.custom_headers["Host"] = self.host_header

        self.session.headers.update(self.custom_headers)

        # Variabili estratte
        self.csrf_token: Optional[str] = None
        self.priamo_auth_cookie_val: Optional[str] = None
        self.aspnet_session_id: Optional[str] = None

    def _log_debug(self, msg: str) -> None:
        if self.verbose:
            print(f"{Colors.GRAY}    [DEBUG] {msg}{Colors.RESET}")

    def _extract_all_set_cookies(self, response: Response) -> List[str]:
        """Estrae tutte le righe Set-Cookie dalla risposta grezza HTTP."""
        raw_headers = getattr(response.raw, "headers", None)
        if raw_headers:
            if hasattr(raw_headers, "getlist"):
                return raw_headers.getlist("Set-Cookie")
            if hasattr(raw_headers, "get_all"):
                return raw_headers.get_all("Set-Cookie")

        # Fallback se non esposto come lista
        sc = response.headers.get("Set-Cookie")
        return [sc] if sc else []

    # ==========================================================================
    # FASE 1: Connettività & Cloudflare Access
    # ==========================================================================
    def test_connectivity(self) -> bool:
        print_step(1, "Verifica Connettività di Rete & Cloudflare Tunnel")
        print_info(f"Target URL: {self.base_url}")
        print_info(f"Host target: {self.target_host}")
        if self.host_header:
            print_info(f"Custom Host Header: {self.host_header}")

        try:
            start_t = time.time()
            resp = self.session.get(
                self.login_url,
                timeout=self.timeout,
                allow_redirects=False
            )
            elapsed = (time.time() - start_t) * 1000
            self.diag_results["reachability"] = True

            print_ok(f"Connessione riuscita in {elapsed:.1f} ms (HTTP Status: {resp.status_code})")

            # Header Cloudflare
            cf_ray = resp.headers.get("cf-ray")
            server_hdr = resp.headers.get("server", "")
            if cf_ray:
                print_ok(f"Cloudflare Tunnel attivo: CF-Ray = {cf_ray}, Server = {server_hdr}")
            else:
                print_info(f"Server header: {server_hdr} (nessun header CF-Ray diretto)")

            # Controllo blocco Cloudflare Access
            if resp.status_code == 403 or "cloudflareaccess.com" in resp.headers.get("location", "").lower():
                print_fail("Cloudflare Zero Trust (Access) ha BLOCCATO la richiesta o richiede login!")
                print_warn("Assicurati di passare CF-Access-Client-Id e CF-Access-Client-Secret validi.")
                self.diag_results["cf_access_blocked"] = True
                return False

            if self.cf_client_id:
                print_ok("Header Cloudflare Access (Service Token) inviati correttamente.")

            return True

        except requests.exceptions.SSLError as e:
            print_fail(f"Errore SSL/TLS: {e}")
            print_warn("Se usi un certificato self-signed locale, prova ad eseguire con l'opzione --insecure (-k).")
            return False
        except requests.exceptions.ConnectionError as e:
            print_fail(f"Impossibile raggiungere il server: {e}")
            print_warn(f"Verifica che il tunnel cloudflared sia avviato e che il dominio {self.target_host} sia attivo.")
            return False
        except Exception as e:
            print_fail(f"Errore generico di connessione: {e}")
            return False

    # ==========================================================================
    # FASE 2: GET Pagina di Login & Token Anti-CSRF
    # ==========================================================================
    def test_login_page(self) -> bool:
        print_step(2, f"GET Pagina di Login ({self.login_url})")

        try:
            resp = self.session.get(self.login_url, timeout=self.timeout, allow_redirects=True)
            self._log_debug(f"Status: {resp.status_code}, Final URL: {resp.url}")

            if resp.status_code != 200:
                print_fail(f"La pagina di login ha restituito HTTP {resp.status_code} invece di 200 OK")
                return False

            html_text = resp.text

            # Cerca __RequestVerificationToken
            token = None
            if HAS_BS4:
                soup = BeautifulSoup(html_text, "html.parser")
                inp = soup.find("input", {"name": "__RequestVerificationToken"})
                if inp:
                    token = inp.get("value")
            else:
                match = re.search(r'name=["\']__RequestVerificationToken["\'][^>]*value=["\']([^"\']+)["\']', html_text)
                if not match:
                    match = re.search(r'value=["\']([^"\']+)["\'][^>]*name=["\']__RequestVerificationToken["\']', html_text)
                if match:
                    token = match.group(1)

            if not token:
                print_fail("Impossibile trovare il token Anti-CSRF '__RequestVerificationToken' nella pagina!")
                self._log_debug(f"Primi 500 caratteri HTML: {html_text[:500]}")
                return False

            self.csrf_token = token
            self.diag_results["csrf_token_found"] = True
            masked_token = token[:10] + "..." + token[-6:] if len(token) > 16 else token
            print_ok(f"Token __RequestVerificationToken estratto: {masked_token}")

            # Controlla cookie di sessione iniziali
            cookies_dict = self.session.cookies.get_dict()
            self._log_debug(f"Cookie iniziali ricevuti: {list(cookies_dict.keys())}")
            if "ASP.NET_SessionId" in cookies_dict:
                self.aspnet_session_id = cookies_dict["ASP.NET_SessionId"]
                print_ok(f"Cookie ASP.NET_SessionId rilevato: {self.aspnet_session_id}")

            return True

        except Exception as e:
            print_fail(f"Errore durante l'ispezione della pagina di login: {e}")
            return False

    # ==========================================================================
    # FASE 3: POST Autenticazione & Analisi dei Cookie (.PRIAMOAUTH)
    # ==========================================================================
    def test_login_post(self) -> bool:
        print_step(3, "POST Credenziali & Analisi Risposta IIS/ASP.NET")

        if not self.csrf_token:
            print_fail("Token Anti-CSRF non presente. Impossibile tentare il login.")
            return False

        payload = {
            "UserName": self.username,
            "Password": self.password,
            "__RequestVerificationToken": self.csrf_token,
        }

        masked_user = self.username
        print_info(f"Invio POST credenziali per utente: '{masked_user}'")

        try:
            # Inviamo con allow_redirects=False per intercettare il 302 grezzo e i Set-Cookie!
            resp = self.session.post(
                self.login_url,
                data=payload,
                timeout=self.timeout,
                allow_redirects=False,
            )

            self.diag_results["login_post_status"] = resp.status_code
            print_info(f"Risposta HTTP ricevuta: {resp.status_code} {resp.reason}")

            # Ispezione header Set-Cookie grezzi
            set_cookies = self._extract_all_set_cookies(resp)
            self._log_debug(f"Set-Cookie grezzi ricevuti: {set_cookies}")

            priamo_cookie_raw = None
            for sc_line in set_cookies:
                if ".PRIAMOAUTH" in sc_line:
                    priamo_cookie_raw = sc_line
                    break

            # Se non trovato nella singola risposta, cerchiamo nella sessione
            if not priamo_cookie_raw and ".PRIAMOAUTH" in self.session.cookies.get_dict():
                self.priamo_auth_cookie_val = self.session.cookies.get_dict()[".PRIAMOAUTH"]
                self.diag_results["priamo_auth_received"] = True

            location = resp.headers.get("Location")
            self.diag_results["redirect_location"] = location

            if resp.status_code == 302:
                print_ok(f"Autenticazione riuscita su IIS (HTTP 302 Redirect verso: '{location}')")
            elif resp.status_code == 200:
                print_warn("Il server ha restituito HTTP 200 OK invece di 302 Redirect.")
                # Estraiamo eventuale errore da HTML
                err_text = self._extract_html_error(resp.text)
                if err_text:
                    print_fail(f"Messaggio di errore dal form Priamo: {err_text}")
                else:
                    print_warn("La pagina di login è stata ricaricata senza redirect esplicito.")

            # Diagnosi del cookie .PRIAMOAUTH
            if priamo_cookie_raw:
                self.diag_results["priamo_auth_received"] = True
                parsed_cookie = self._parse_cookie_line(priamo_cookie_raw)
                cookie_val = parsed_cookie.get("value", "")
                cookie_domain = parsed_cookie.get("domain", "")
                self.priamo_auth_cookie_val = cookie_val
                self.diag_results["priamo_auth_raw_domain"] = cookie_domain

                val_preview = (cookie_val[:8] + "..." + cookie_val[-6:]) if len(cookie_val) > 14 else cookie_val
                print_ok(f"Cookie '.PRIAMOAUTH' emesso dal server! Valore: {val_preview}")
                print_info(f"Attributi cookie: Domain='{cookie_domain}', Path='{parsed_cookie.get('path')}', "
                           f"HttpOnly={parsed_cookie.get('httponly')}, Secure={parsed_cookie.get('secure')}")

                # ==============================================================
                # VERIFICA CRUCIALE: DOMAIN MISMATCH (RFC 6265)
                # ==============================================================
                if cookie_domain:
                    clean_domain = cookie_domain.lstrip(".")
                    if clean_domain.lower() != self.target_host.lower() and not self.target_host.lower().endswith("." + clean_domain.lower()):
                        self.diag_results["domain_mismatch_detected"] = True
                        print("\n" + f"{Colors.BOLD}{Colors.RED}" + "!" * 75)
                        print(" [!] PROBLEMA CRITICO RILEVATO: COOKIE DOMAIN MISMATCH (RFC 6265)")
                        print("!" * 75 + f"{Colors.RESET}")
                        print(f" {Colors.RED}• Il server Priamo/IIS ha emesso il cookie con: Domain='{cookie_domain}'")
                        print(f" • Ma il browser accede tramite il dominio del tunnel: '{self.target_host}'{Colors.RESET}")
                        print(f"\n {Colors.YELLOW}Spiegazione del comportamento:{Colors.RESET}")
                        print(" I browser web (Chrome, Edge, Firefox) applicano rigidamente la specifica RFC 6265:")
                        print(f" Un sito su '{self.target_host}' NON può impostare un cookie per '{cookie_domain}'.")
                        print(f" Di conseguenza, il browser {Colors.BOLD}RIFIUTA E CANCELLA IMMEDIATAMENTE{Colors.RESET} il cookie .PRIAMOAUTH!")
                        print(f" Quando il browser segue il redirect a {location or '/PriamoHOME'},")
                        print(f" NON invia alcun cookie di autenticazione. Priamo lo vede non loggato e")
                        print(f" lo {Colors.BOLD}RIMANDA SUBITO ALLA PAGINA DI LOGIN{Colors.RESET}!")
                        print(f"{Colors.RED}" + "!" * 75 + f"{Colors.RESET}\n")
                    else:
                        print_ok(f"Il dominio del cookie ({cookie_domain}) è compatibile con {self.target_host}.")
                else:
                    print_ok("Il cookie .PRIAMOAUTH non ha attributo Domain esplicito (Host-only cookie, ottimo per reverse-proxy).")

            elif self.priamo_auth_cookie_val:
                print_ok(f"Cookie '.PRIAMOAUTH' acquisito nella sessione: {self.priamo_auth_cookie_val[:8]}...")
            else:
                print_fail("Il cookie '.PRIAMOAUTH' NON è stato ricevuto negli header di risposta!")
                return False

            # Ispezione header Location per redirect assoluto su host interno
            if location:
                loc_parsed = urlparse(location)
                if loc_parsed.hostname and loc_parsed.hostname.lower() != self.target_host.lower():
                    print_warn(f"Redirect Location punta ad un host interno/diverso: '{loc_parsed.hostname}'")
                    print_warn("Se questo host non è risolvibile dal browser esterno, causerà fallimento nel browser.")

            return True

        except Exception as e:
            print_fail(f"Errore durante l'invio del form di login: {e}")
            return False

    # ==========================================================================
    # FASE 4: Simulazione Loop Browser vs Risoluzione con Sessione
    # ==========================================================================
    def test_session_redirect(self) -> bool:
        print_step(4, "Verifica del Reindirizzamento Post-Login & Test Sessione")

        target_url = self.diag_results.get("redirect_location") or self.home_url
        if not target_url.startswith("http"):
            target_url = urljoin(self.base_url, target_url)

        # --- Test 4A: Comportamento Browser Standard (senza cookie domain-rewrite) ---
        print_info("Test 4A: Simulazione del comportamento del Browser (cookie rifiutato se domain errato)...")
        if self.diag_results["domain_mismatch_detected"]:
            # Creiamo una sessione vergine senza il cookie rifiutato
            browser_sim = Session()
            if not self.verify_ssl:
                browser_sim.verify = False
            browser_sim.headers.update(self.custom_headers)
            try:
                b_resp = browser_sim.get(target_url, timeout=self.timeout, allow_redirects=False)
                if b_resp.status_code in (301, 302) and "Login" in b_resp.headers.get("Location", ""):
                    print_warn(f"Confermato: senza cookie .PRIAMOAUTH, il server rimanda a '{b_resp.headers.get('Location')}'!")
                    self.diag_results["browser_loop_confirmed"] = True
            except Exception as e:
                self._log_debug(f"Errore test browser_sim: {e}")

        # --- Test 4B: Comportamento con Cookie Adattato al Target Host ---
        print_info("Test 4B: Applicazione del Cookie .PRIAMOAUTH direttamente al dominio target...")
        if self.priamo_auth_cookie_val:
            # Iniettiamo esplicitamente il cookie per self.target_host
            self.session.cookies.set(
                ".PRIAMOAUTH",
                self.priamo_auth_cookie_val,
                domain=self.target_host,
                path="/"
            )
            print_ok(f"Cookie .PRIAMOAUTH associato esplicitamente all'host '{self.target_host}'")

        try:
            # Navighiamo verso la pagina protetta
            resp = self.session.get(target_url, timeout=self.timeout, allow_redirects=True)
            self._log_debug(f"Status: {resp.status_code}, Final URL: {resp.url}")

            if "Login" in resp.url and resp.url != target_url:
                print_fail(f"La sessione è stata comunque reindirizzata alla pagina di login: {resp.url}")
                return False

            if resp.status_code == 200:
                print_ok(f"Accesso riuscito all'area riservata! (URL finale: {resp.url})")
                self.diag_results["session_valid_with_fix"] = True
                return True
            else:
                print_warn(f"Risposta inattesa dopo il redirect: HTTP {resp.status_code}")
                return False

        except Exception as e:
            print_fail(f"Errore durante la navigazione post-login: {e}")
            return False

    # ==========================================================================
    # FASE 5: Test Endpoint API (/PriamoCES/api)
    # ==========================================================================
    def test_api_endpoints(
        self,
        test_name: Optional[str] = None,
        test_surname: Optional[str] = None,
        test_dob: Optional[str] = None
    ) -> bool:
        print_step(5, "Verifica Funzionamento API REST PriamoCES")

        # Test GetAnagrafica
        anagrafica_ok = False
        sample_name = test_name or "MARIA"
        sample_surname = test_surname or "ROSSI"
        sample_dob = test_dob or "1970-01-01"

        print_info(f"Test endpoint: {self.api_anagrafica_url}")
        print_info(f"Query parametri test: Nome='{sample_name}', Cognome='{sample_surname}', DataNascita='{sample_dob}'")

        try:
            params = {
                "Nome": sample_name,
                "Cognome": sample_surname,
                "DataNascita": sample_dob,
            }
            resp = self.session.get(self.api_anagrafica_url, params=params, timeout=self.timeout)
            self._log_debug(f"API Anagrafica Status: {resp.status_code}")

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    print_ok("API /Anagrafica ha risposto HTTP 200 con formato JSON valido!")
                    self.diag_results["api_anagrafica_ok"] = True
                    anagrafica_ok = True
                    if isinstance(data, list):
                        print_info(f"Risultati restituiti dalla ricerca: {len(data)} record")
                        if data:
                            self._log_debug(f"Primo record: {data[0]}")
                    elif isinstance(data, dict):
                        print_info(f"Risposta JSON dizionario: {list(data.keys())}")
                except Exception:
                    print_warn("Risposta HTTP 200 ma il contenuto non è un JSON valido.")
                    self._log_debug(f"Contenuto risposta API: {resp.text[:300]}")
            elif resp.status_code in (401, 403):
                print_fail(f"Accesso API non autorizzato: HTTP {resp.status_code}. Il cookie .PRIAMOAUTH non è valido o è scaduto.")
            elif resp.status_code == 302:
                print_fail(f"L'API ha risposto con Redirect 302 verso '{resp.headers.get('Location')}', sessione non riconosciuta.")
            else:
                print_warn(f"L'API ha restituito HTTP {resp.status_code}: {resp.text[:200]}")

        except Exception as e:
            print_fail(f"Errore durante la chiamata API Anagrafica: {e}")

        # Test GetEvento
        print_info(f"\nTest endpoint: {self.api_evento_url}")
        try:
            ev_params = {
                "idPaziente": "0",
                "DataInizio": "2024-01-01",
            }
            ev_resp = self.session.get(self.api_evento_url, params=ev_params, timeout=self.timeout)
            if ev_resp.status_code == 200:
                print_ok("API /Eventi ha risposto con successo HTTP 200!")
            elif ev_resp.status_code in (401, 403, 302):
                print_fail(f"API /Eventi non autorizzata (HTTP {ev_resp.status_code})")
            else:
                self._log_debug(f"API Eventi HTTP {ev_resp.status_code}")
        except Exception as e:
            self._log_debug(f"Errore test API eventi: {e}")

        return anagrafica_ok

    # ==========================================================================
    # Helper di Parsing
    # ==========================================================================
    @staticmethod
    def _parse_cookie_line(cookie_str: str) -> Dict[str, Any]:
        """Estrae nome, valore e attributi da una riga Set-Cookie."""
        res: Dict[str, Any] = {
            "name": "",
            "value": "",
            "domain": "",
            "path": "",
            "httponly": False,
            "secure": False,
            "samesite": ""
        }
        parts = cookie_str.split(";")
        if parts:
            first = parts[0].strip()
            if "=" in first:
                k, v = first.split("=", 1)
                res["name"] = k.strip()
                res["value"] = v.strip()

        for attr in parts[1:]:
            attr = attr.strip()
            if "=" in attr:
                ak, av = attr.split("=", 1)
                ak_lower = ak.strip().lower()
                av_clean = av.strip()
                if ak_lower == "domain":
                    res["domain"] = av_clean
                elif ak_lower == "path":
                    res["path"] = av_clean
                elif ak_lower == "samesite":
                    res["samesite"] = av_clean
            else:
                if attr.lower() == "httponly":
                    res["httponly"] = True
                elif attr.lower() == "secure":
                    res["secure"] = True

        return res

    @staticmethod
    def _extract_html_error(html: str) -> str:
        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")
            for cl in ("validation-summary-errors", "field-validation-error", "alert", "alert-danger"):
                el = soup.find(class_=lambda c: c and cl in c)
                if el:
                    return el.get_text(separator=" ", strip=True)
        else:
            m = re.search(r'class=["\'][^"\']*(?:validation|alert|error)[^"\']*["\'][^>]*>([^<]+)<', html)
            if m:
                return m.group(1).strip()
        return ""

    # ==========================================================================
    # FASE 6: Report Finale & Istruzioni di Risoluzione
    # ==========================================================================
    def print_summary(self) -> None:
        print_title("Riepilogo Diagnostico & Istruzioni per Risolvere il Problema")

        reach = self.diag_results["reachability"]
        cf_blocked = self.diag_results["cf_access_blocked"]
        csrf = self.diag_results["csrf_token_found"]
        priamo_auth = self.diag_results["priamo_auth_received"]
        mismatch = self.diag_results["domain_mismatch_detected"]
        sess_ok = self.diag_results["session_valid_with_fix"]
        api_ok = self.diag_results["api_anagrafica_ok"]

        def fmt(status: bool) -> str:
            return f"{Colors.GREEN}PASS (OK){Colors.RESET}" if status else f"{Colors.RED}FAIL{Colors.RESET}"

        print(f"  • Raggiungibilità Tunnel Cloudflared:    {fmt(reach)}")
        print(f"  • Cloudflare Zero Trust (Access):        {fmt(not cf_blocked)}")
        print(f"  • Estrazione Token Anti-CSRF:            {fmt(csrf)}")
        print(f"  • Ricezione Cookie '.PRIAMOAUTH':        {fmt(priamo_auth)}")
        print(f"  • Compatibilità Dominio Cookie (RFC6265):{fmt(not mismatch)}")
        print(f"  • Sessione Valida per API (con fix):     {fmt(sess_ok)}")
        print(f"  • Endpoint API /PriamoCES Funzionanti:   {fmt(api_ok)}")

        print("\n" + f"{Colors.BOLD}{Colors.CYAN}{'=' * 80}")
        print("  DIAGNOSI DEL TUO PROBLEMA: PERCHÉ IL PANNELLO WEB RIMBALZA AL LOGIN?")
        print(f"{'=' * 80}{Colors.RESET}\n")

        if mismatch:
            raw_dom = self.diag_results.get("priamo_auth_raw_domain")
            print(f"  {Colors.BOLD}Causa Principale:{Colors.RESET}")
            print(f"  Il server IIS/Priamo invia l'header di autenticazione con un dominio hardcoded:")
            print(f"    {Colors.RED}Set-Cookie: .PRIAMOAUTH=...; domain={raw_dom}; path=/{Colors.RESET}")
            print(f"  Poiché il tuo browser sta visitando {Colors.CYAN}https://{self.target_host}{Colors.RESET},")
            print(f"  la specifica di sicurezza RFC 6265 dei browser impedisce tassativamente di salvare")
            print(f"  un cookie con Domain='{raw_dom}'. Il browser lo scarta all'istante.")
            print(f"  Dopo il login (POST), il browser esegue una GET verso l'area riservata,")
            print(f"  ma non ha il cookie .PRIAMOAUTH in memoria. IIS riceve una richiesta anonima")
            print(f"  e {Colors.YELLOW}reindirizza immediatamente l'utente alla pagina di login!{Colors.RESET}\n")

            print(f"  {Colors.BOLD}COME RISOLVERE IL PROBLEMA SUL PANNELLO WEB (3 SOLUZIONI):{Colors.RESET}\n")

            print(f"  {Colors.BOLD}{Colors.GREEN}[SOLUZIONE 1 - CONSIGLIATA (Zero modifiche al server)]{Colors.RESET}")
            print("  Rimuovi o riscrivi l'attributo 'Domain' del cookie direttamente in Cloudflare:")
            print("  1. Accedi alla dashboard di Cloudflare sul tuo dominio.")
            print("  2. Vai su: Rules (Regole) ➔ Transform Rules ➔ Modify Response Header.")
            print("  3. Crea una regola:")
            print(f"     • When incoming requests match: (http.host eq \"{self.target_host}\")")
            print("     • Modify response header: 'Set-Cookie'")
            print("     • In alternativa, usa una Cloudflare Worker lightweight che intercetta la risposta:")
            print(f"       {Colors.GRAY}response.headers.set('Set-Cookie', sc.replace(/domain=[^;]+;?/i, ''));{Colors.RESET}")
            print("     Senza l'attributo Domain, il cookie diventa Host-Only e il browser lo accetta subito!\n")

            print(f"  {Colors.BOLD}{Colors.GREEN}[SOLUZIONE 2 - Configurazione Cloudflared sul PC]{Colors.RESET}")
            print("  Nel file ~/.cloudflared/config.yml sul PC che fa da tunnel, assicurati di aver impostato:")
            print(f"    {Colors.CYAN}ingress:")
            print(f"      - hostname: {self.target_host}")
            print(f"        service: https://<IP_PRIAMO>:<PORTA>  # o http://")
            print(f"        originRequest:")
            print(f"          noTLSVerify: true")
            print(f"          httpHostHeader: priamo-home.valduce.it:8062{Colors.RESET}\n")

            print(f"  {Colors.BOLD}{Colors.GREEN}[SOLUZIONE 3 - Su web.config di IIS (Se hai accesso alla macchina)]{Colors.RESET}")
            print("  Nel file web.config dell'applicazione Priamo:")
            print("  Trova la sezione <forms> e rimuovi l'attributo domain:")
            print(f"    {Colors.GRAY}<forms name=\".PRIAMOAUTH\" loginUrl=\"~/Account/Login\" requireSSL=\"false\" />{Colors.RESET}\n")

        print(f"  {Colors.BOLD}{Colors.CYAN}COME USARE L'API IN PYTHON / SENONETWORK:{Colors.RESET}")
        print("  La nostra pipeline SenoNetwork (src/priamo_client.py) estrae direttamente il valore")
        print("  del token .PRIAMOAUTH dall'header Set-Cookie e lo associa al dominio del tunnel,")
        print("  aggirando automaticamente il blocco del browser. L'API REST è pronta per essere utilizzata!")
        print("\n" + "=" * 80 + "\n")


# ==============================================================================
# Entry point CLI
# ==============================================================================
def main():
    # Abilita colori ANSI su terminale
    if not sys.stdout.isatty():
        Colors.disable()

    # Carica default da .env se presente
    env_cfg = load_env_file(".env")

    parser = argparse.ArgumentParser(
        description="Test e Diagnostica Autenticazione Priamo .NET HIS su Cloudflare Tunnel"
    )
    parser.add_argument(
        "--url",
        default=env_cfg.get("PRIAMO_API_URL", "https://priamo-home.valduce.it:8062"),
        help="URL base del servizio Priamo (es. https://priamo.miodominio.com)"
    )
    parser.add_argument(
        "--username", "-u",
        default=env_cfg.get("PRIAMO_USERNAME", ""),
        help="Nome utente Priamo .NET"
    )
    parser.add_argument(
        "--password", "-p",
        default=env_cfg.get("PRIAMO_PASSWORD", ""),
        help="Password Priamo .NET"
    )
    parser.add_argument(
        "--cf-id",
        default=(
            env_cfg.get("PRIAMO_CLOUDFLARED_CLIENT_ID")
            or env_cfg.get("CLOUDFLARED_CLIENT_ID")
            or env_cfg.get("CF_CLIENT_ID", "")
        ),
        help="Cloudflare Access Client ID (CF-Access-Client-Id)"
    )
    parser.add_argument(
        "--cf-secret",
        default=(
            env_cfg.get("PRIAMO_CLOUDFLARED_CLIENT_SECRET")
            or env_cfg.get("CLOUDFLARED_CLIENT_SECRET")
            or env_cfg.get("CF_CLIENT_SECRET", "")
        ),
        help="Cloudflare Access Client Secret (CF-Access-Client-Secret)"
    )
    parser.add_argument(
        "--host-header",
        default=None,
        help="Header Host personalizzato da inviare al server (opzionale)"
    )
    parser.add_argument(
        "--insecure", "-k",
        action="store_true",
        help="Disabilita la verifica del certificato SSL/TLS"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Mostra output dettagliato per il debug di header e cookie"
    )
    parser.add_argument(
        "--patient-name",
        default=None,
        help="Nome paziente da testare nella chiamata API Anagrafica"
    )
    parser.add_argument(
        "--patient-surname",
        default=None,
        help="Cognome paziente da testare nella chiamata API Anagrafica"
    )
    parser.add_argument(
        "--patient-dob",
        default=None,
        help="Data di nascita paziente (YYYY-MM-DD)"
    )

    args = parser.parse_args()

    if not args.url or not args.username or not args.password:
        print(f"{Colors.RED}[ERRORE] Credenziali o URL mancanti!{Colors.RESET}")
        print("Specificali tramite argomenti CLI (--url, -l, -p) oppure nel file .env:")
        print("  PRIAMO_API_URL=https://priamo.miodominio.com")
        print("  PRIAMO_USERNAME=TUO_USERNAME")
        print("  PRIAMO_PASSWORD=...")
        sys.exit(1)

    runner = PriamoDiagnosticRunner(
        base_url=args.url,
        username=args.username,
        password=args.password,
        cf_client_id=args.cf_id,
        cf_client_secret=args.cf_secret,
        host_header=args.host_header,
        verify_ssl=not args.insecure,
        verbose=args.verbose,
    )

    print_title("AVVIO DIAGNOSTICA AUTENTICAZIONE PRIAMO .NET VIA CLOUDFLARE")

    # Step 1
    if not runner.test_connectivity():
        runner.print_summary()
        sys.exit(1)

    # Step 2
    if not runner.test_login_page():
        runner.print_summary()
        sys.exit(1)

    # Step 3
    if not runner.test_login_post():
        runner.print_summary()
        sys.exit(1)

    # Step 4
    runner.test_session_redirect()

    # Step 5
    runner.test_api_endpoints(
        test_name=args.patient_name,
        test_surname=args.patient_surname,
        test_dob=args.patient_dob
    )

    # Step 6
    runner.print_summary()


if __name__ == "__main__":
    main()
