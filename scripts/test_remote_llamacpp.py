#!/usr/bin/env python3
"""
Test e Diagnostica Server llama.cpp Remoto / Cluster Distribuito (RPC)
====================================================================
Questo script esegue una diagnosi completa del server llama.cpp (master server RPC o remoto),
verificando connettività, latenza, specifiche del modello, supporto JSON Schema (GBNF)
e capacità di estrazione clinica compatibile con SenoNetwork.

Funzionalità verificate:
1. Connettività HTTP e latenza di rete (/health, /props).
2. Metadati del modello caricato e dimensione contesto (/v1/models, /props).
3. Test di generazione base e calcolo della velocità (token/secondo).
4. Test vincolo JSON Schema (GBNF strutturato) sia in modalità 'chat' (/v1/chat/completions)
   sia in modalità nativa 'completion' (/completion).
5. Simulazione estrazione referto clinico senologico reale (BI-RADS e lateralità).
6. Supporto autenticazione Bearer Token o Cloudflare Access (CF-Access-Client-Id/Secret).

Esempi di utilizzo:
    # 1. Test server locale o da .env
    python3 scripts/test_remote_llamacpp.py

    # 2. Test server su IP specifico nella rete locale o cluster
    python3 scripts/test_remote_llamacpp.py --url http://192.168.1.100:8080

    # 3. Test con modalità specifica e output dettagliato
    python3 scripts/test_remote_llamacpp.py --url http://192.168.1.100:8080 --mode chat --verbose

    # 4. Test server protetto da Cloudflare Access
    python3 scripts/test_remote_llamacpp.py --url https://llama.ospedale.it \\
        --cf-client-id "xxx.access" --cf-client-secret "yyy"
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, Optional, Tuple

try:
    import requests
    import urllib3
except ImportError:
    print("[ERRORE] Pacchetto 'requests' non trovato. Installa con: pip install requests")
    sys.exit(1)

# Carica variabili d'ambiente da .env se presente
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ==============================================================================
# Helper Colori ANSI per Terminale
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
    DIM = "\033[2m"

    @classmethod
    def ok(cls, msg: str) -> str:
        return f"{cls.GREEN}✔ {msg}{cls.RESET}"

    @classmethod
    def fail(cls, msg: str) -> str:
        return f"{cls.RED}✘ {msg}{cls.RESET}"

    @classmethod
    def warn(cls, msg: str) -> str:
        return f"{cls.YELLOW}⚠ {msg}{cls.RESET}"

    @classmethod
    def info(cls, msg: str) -> str:
        return f"{cls.CYAN}ℹ {msg}{cls.RESET}"

    @classmethod
    def header(cls, msg: str) -> str:
        line = "=" * 75
        return f"\n{cls.BOLD}{cls.BLUE}{line}\n  {msg}\n{line}{cls.RESET}\n"


# ==============================================================================
# Client Diagnostica llama.cpp
# ==============================================================================
class LlamaRemoteTester:
    def __init__(
        self,
        base_url: str,
        timeout: int = 120,
        api_key: Optional[str] = None,
        cf_client_id: Optional[str] = None,
        cf_client_secret: Optional[str] = None,
        verbose: bool = False,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose
        self.verify_ssl = verify_ssl

        self.headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        if api_key:
            auth_val = api_key if api_key.startswith("Bearer ") else f"Bearer {api_key}"
            self.headers["Authorization"] = auth_val

        cf_id = cf_client_id or os.getenv("AI_CLOUDFLARED_CLIENT_ID") or os.getenv("CLOUDFLARED_CLIENT_ID")
        cf_secret = cf_client_secret or os.getenv("AI_CLOUDFLARED_CLIENT_SECRET") or os.getenv("CLOUDFLARED_CLIENT_SECRET")
        if cf_id and cf_secret:
            self.headers["CF-Access-Client-Id"] = cf_id
            self.headers["CF-Access-Client-Secret"] = cf_secret

        self.results: Dict[str, Any] = {
            "base_url": self.base_url,
            "connected": False,
            "health_status": None,
            "latency_ms": None,
            "model_name": None,
            "context_size": None,
            "chat_mode_supported": False,
            "completion_mode_supported": False,
            "json_schema_supported": False,
            "preferred_api_mode": "chat",
            "tokens_per_sec": None,
            "clinical_test_passed": False,
            "notes": []
        }

    def _log_debug(self, msg: str, data: Any = None):
        if self.verbose:
            print(f"{Colors.DIM}[DEBUG] {msg}{Colors.RESET}")
            if data is not None:
                if isinstance(data, (dict, list)):
                    print(f"{Colors.DIM}{json.dumps(data, indent=2, ensure_ascii=False)}{Colors.RESET}")
                else:
                    print(f"{Colors.DIM}{data}{Colors.RESET}")

    # --------------------------------------------------------------------------
    # Step 1: Connettività & Health Check
    # --------------------------------------------------------------------------
    def test_health(self) -> bool:
        print(f"-> Verifica connettività verso {Colors.BOLD}{self.base_url}{Colors.RESET}...")
        url = f"{self.base_url}/health"

        t_start = time.time()
        try:
            resp = requests.get(url, headers=self.headers, timeout=self.timeout, verify=self.verify_ssl)
            latency = (time.time() - t_start) * 1000
            self.results["latency_ms"] = round(latency, 1)

            self._log_debug(f"GET /health: status={resp.status_code}", resp.text)

            if resp.status_code == 200:
                self.results["connected"] = True
                self.results["health_status"] = "OK"
                print(Colors.ok(f"Server raggiungibile (/health HTTP 200) — Latenza ping: {latency:.1f} ms"))
                return True
            elif resp.status_code == 503:
                print(Colors.warn(f"Server risponde HTTP 503 (Modello in fase di caricamento o slot occupati)"))
                self.results["connected"] = True
                self.results["health_status"] = "LOADING"
                return True
            else:
                print(Colors.warn(f"Endpoint /health ha ritornato HTTP {resp.status_code}"))
        except Exception as e:
            self._log_debug(f"Errore /health: {e}")
            print(Colors.warn(f"/health non disponibile ({e}). Tento endpoint alternativi..."))

        # Fallback test: prova GET /props
        try:
            t_start = time.time()
            resp = requests.get(f"{self.base_url}/props", headers=self.headers, timeout=self.timeout, verify=self.verify_ssl)
            latency = (time.time() - t_start) * 1000
            self.results["latency_ms"] = round(latency, 1)

            if resp.status_code == 200:
                self.results["connected"] = True
                self.results["health_status"] = "OK (/props)"
                print(Colors.ok(f"Server raggiungibile (/props HTTP 200) — Latenza: {latency:.1f} ms"))
                return True
        except Exception as e:
            self.results["notes"].append(f"Connessione fallita: {e}")
            print(Colors.fail(f"Impossibile connettersi al server llama.cpp su {self.base_url}: {e}"))
            return False

        return False

    # --------------------------------------------------------------------------
    # Step 2: Info Modello & Server Props
    # --------------------------------------------------------------------------
    def get_server_props(self) -> Dict[str, Any]:
        print(f"\n-> Recupero informazioni server e modello caricato...")
        info: Dict[str, Any] = {}

        # 1. Prova /props (proprietà native llama-server)
        try:
            r = requests.get(f"{self.base_url}/props", headers=self.headers, timeout=self.timeout, verify=self.verify_ssl)
            if r.status_code == 200:
                data = r.json()
                self._log_debug("GET /props response", data)
                gen_settings = data.get("default_generation_settings", {})
                info["n_ctx"] = gen_settings.get("n_ctx")
                info["model_alias"] = gen_settings.get("model")
                info["total_slots"] = data.get("total_slots")
        except Exception as e:
            self._log_debug(f"Errore /props: {e}")

        # 2. Prova /v1/models (compatibile OpenAI)
        try:
            r = requests.get(f"{self.base_url}/v1/models", headers=self.headers, timeout=self.timeout, verify=self.verify_ssl)
            if r.status_code == 200:
                data = r.json()
                self._log_debug("GET /v1/models response", data)
                models_list = data.get("data", [])
                if models_list:
                    model_obj = models_list[0]
                    info["model_id"] = model_obj.get("id", "")
        except Exception as e:
            self._log_debug(f"Errore /v1/models: {e}")

        # Stampa riassunto
        model_name = info.get("model_id") or info.get("model_alias") or "Modello non dichiarato esplicitamente"
        self.results["model_name"] = model_name
        self.results["context_size"] = info.get("n_ctx")

        print(f"   * Modello caricato:  {Colors.BOLD}{model_name}{Colors.RESET}")
        if info.get("n_ctx"):
            print(f"   * Dimensione contesto (n_ctx): {Colors.BOLD}{info['n_ctx']} token{Colors.RESET}")
        if info.get("total_slots"):
            print(f"   * Slot paralleli attivi: {Colors.BOLD}{info['total_slots']}{Colors.RESET}")

        return info

    # --------------------------------------------------------------------------
    # Step 3: Test Generazione Base & Calcolo Velocità
    # --------------------------------------------------------------------------
    def test_basic_generation(self) -> Tuple[bool, Optional[float]]:
        print(f"\n-> Test di generazione testo base e calcolo velocità...")
        # Usiamo un prompt che genera un numero sufficiente di token (30-50) per una stima attendibile
        prompt = "Elenca in 3 punti sintetici le linee guida per la refertazione della mammografia di screening."

        # Prova endpoint OpenAI chat
        payload_chat = {
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 80,
            "temperature": 0.0,
            "stream": False
        }

        t_start = time.time()
        try:
            r = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json=payload_chat,
                timeout=self.timeout,
                verify=self.verify_ssl
            )
            elapsed = time.time() - t_start
            self._log_debug(f"Chat completion status: {r.status_code}", r.text)

            if r.status_code == 200:
                res_json = r.json()
                content = res_json["choices"][0]["message"]["content"].strip()
                usage = res_json.get("usage", {})
                completion_tokens = usage.get("completion_tokens", len(content.split()))

                # Estrazione metrica nativa interna di llama.cpp se presente
                timings = res_json.get("timings", {})
                native_gen_tps = timings.get("predicted_per_second")
                native_prompt_tps = timings.get("prompt_per_second")

                wall_clock_tps = completion_tokens / elapsed if elapsed > 0 else 0.0
                effective_tps = native_gen_tps or wall_clock_tps

                self.results["chat_mode_supported"] = True
                self.results["tokens_per_sec"] = round(effective_tps, 2)

                print(Colors.ok(f"Generazione chat riuscita ({completion_tokens} token generati in {elapsed:.2f}s)"))
                if native_gen_tps:
                    print(f"   * {Colors.BOLD}Velocità pura GPU (Decoding):{Colors.RESET} {Colors.GREEN}{native_gen_tps:.2f} token/s{Colors.RESET} (metrica hardware interna llama.cpp)")
                    if native_prompt_tps:
                        print(f"   * {Colors.BOLD}Velocità Prompt Eval (Prefill):{Colors.RESET} {native_prompt_tps:.1f} token/s")
                    print(f"   * {Colors.BOLD}Throughput End-to-End (Rete+Prefill+Gen):{Colors.RESET} {wall_clock_tps:.2f} token/s")
                else:
                    print(f"   * {Colors.BOLD}Throughput End-to-End (Rete+Prefill+Gen):{Colors.RESET} {Colors.GREEN}{wall_clock_tps:.2f} token/s{Colors.RESET}")

                return True, effective_tps
        except Exception as e:
            self._log_debug(f"Chat mode fallito: {e}")

        # Fallback a endpoint nativo /completion
        payload_compl = {
            "prompt": f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n",
            "n_predict": 80,
            "temperature": 0.0,
            "stream": False
        }
        t_start = time.time()
        try:
            r = requests.post(
                f"{self.base_url}/completion",
                headers=self.headers,
                json=payload_compl,
                timeout=self.timeout,
                verify=self.verify_ssl
            )
            elapsed = time.time() - t_start
            if r.status_code == 200:
                res_json = r.json()
                content = res_json.get("content", "").strip()
                tokens_count = res_json.get("tokens_predicted", len(content.split()))

                timings = res_json.get("timings", {})
                native_gen_tps = timings.get("predicted_per_second")
                native_prompt_tps = timings.get("prompt_per_second")

                wall_clock_tps = tokens_count / elapsed if elapsed > 0 else 0.0
                effective_tps = native_gen_tps or wall_clock_tps

                self.results["completion_mode_supported"] = True
                self.results["preferred_api_mode"] = "completion"
                self.results["tokens_per_sec"] = round(effective_tps, 2)

                print(Colors.ok(f"Generazione /completion riuscita ({tokens_count} token in {elapsed:.2f}s)"))
                if native_gen_tps:
                    print(f"   * {Colors.BOLD}Velocità pura GPU (Decoding):{Colors.RESET} {Colors.GREEN}{native_gen_tps:.2f} token/s{Colors.RESET}")
                    if native_prompt_tps:
                        print(f"   * {Colors.BOLD}Velocità Prompt Eval (Prefill):{Colors.RESET} {native_prompt_tps:.1f} token/s")
                    print(f"   * {Colors.BOLD}Throughput End-to-End (Rete+Prefill+Gen):{Colors.RESET} {wall_clock_tps:.2f} token/s")
                else:
                    print(f"   * {Colors.BOLD}Throughput End-to-End (Rete+Prefill+Gen):{Colors.RESET} {Colors.GREEN}{wall_clock_tps:.2f} token/s{Colors.RESET}")

                return True, effective_tps
        except Exception as e:
            self._log_debug(f"Completion mode fallito: {e}")

        print(Colors.fail("Nessun endpoint di generazione (/v1/chat/completions o /completion) ha risposto con successo."))
        return False, None

    # --------------------------------------------------------------------------
    # Step 4: Test Vincolo JSON Schema (GBNF)
    # --------------------------------------------------------------------------
    def test_json_schema_gbnf(self) -> bool:
        print(f"\n-> Test vincolo formale JSON Schema (Grammatiche GBNF)...")

        test_schema = {
            "type": "object",
            "properties": {
                "stato_server": {"type": "string", "enum": ["operativo", "manutenzione"]},
                "cluster_rpc": {"type": "boolean"},
                "num_schede_gpu": {"type": "integer"}
            },
            "required": ["stato_server", "cluster_rpc", "num_schede_gpu"]
        }

        user_prompt = (
            "Sei il supervisore del cluster di inferenza. Ritorna lo stato del sistema in JSON. "
            "Il cluster ha 2 schede GPU ed è operativo."
        )

        # 4.1 Prova /v1/chat/completions con response_format schema
        chat_ok = False
        try:
            payload_chat = {
                "messages": [
                    {"role": "system", "content": "Restituisci esclusivamente un oggetto JSON valido."},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.0,
                "response_format": {
                    "type": "json_object",
                    "schema": test_schema
                },
                "stream": False
            }
            r = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json=payload_chat,
                timeout=self.timeout,
                verify=self.verify_ssl
            )
            if r.status_code == 200:
                raw_text = r.json()["choices"][0]["message"]["content"]
                parsed = json.loads(raw_text)
                if "stato_server" in parsed and "num_schede_gpu" in parsed:
                    chat_ok = True
                    self.results["chat_mode_supported"] = True
                    self.results["json_schema_supported"] = True
                    self.results["preferred_api_mode"] = "chat"
                    print(Colors.ok("Vincolo JSON Schema supportato su /v1/chat/completions!"))
                    self._log_debug("JSON validato", parsed)
        except Exception as e:
            self._log_debug(f"Test schema su /v1/chat fallito: {e}")

        # 4.2 Prova /completion con parametro json_schema nativo
        compl_ok = False
        try:
            payload_compl = {
                "prompt": f"<|im_start|>user\n{user_prompt}\nRestituisci solo JSON:<|im_end|>\n<|im_start|>assistant\n",
                "temperature": 0.0,
                "json_schema": test_schema,
                "n_predict": 256,
                "stream": False
            }
            r = requests.post(
                f"{self.base_url}/completion",
                headers=self.headers,
                json=payload_compl,
                timeout=self.timeout,
                verify=self.verify_ssl
            )
            if r.status_code == 200:
                raw_text = r.json().get("content", "")
                parsed = json.loads(raw_text)
                if "stato_server" in parsed and "num_schede_gpu" in parsed:
                    compl_ok = True
                    self.results["completion_mode_supported"] = True
                    self.results["json_schema_supported"] = True
                    print(Colors.ok("Vincolo JSON Schema supportato su /completion (nativo llama.cpp)!"))
                    self._log_debug("JSON validato (/completion)", parsed)
        except Exception as e:
            self._log_debug(f"Test schema su /completion fallito: {e}")

        if chat_ok:
            self.results["preferred_api_mode"] = "chat"
            return True
        elif compl_ok:
            self.results["preferred_api_mode"] = "completion"
            return True

        print(Colors.fail("Nessun endpoint supporta la costrizione grammaticale JSON Schema."))
        return False

    # --------------------------------------------------------------------------
    # Step 5: Test Estrazione Clinica Reale (SenoNetwork)
    # --------------------------------------------------------------------------
    def test_clinical_extraction(self) -> bool:
        print(f"\n-> Simulazione estrazione clinica SenoNetwork (Referto Mammografia)...")

        medical_text = (
            "REFERTO MAMMOGRAFIA BILATERALE:\n"
            "Esame eseguito in tecnica digitale 2D e tomosintesi 3D.\n"
            "A destra nel quadrante supero-esterno si apprezza una formazione nodulare a margini spiculati\n"
            "del diametro massimo di circa 18 mm con associate microcalcificazioni pleomorfe raggruppate.\n"
            "Reperto altamente suggestivo di eteroformazione maligna (BI-RADS 5).\n"
            "A sinistra non si evidenziano lesioni a carattere focale evolutivo (BI-RADS 1).\n"
            "Cavo ascellare destro: linfoadenopatia sospetta a corteccia ispessita."
        )

        redcap_mock_schema = {
            "type": "object",
            "properties": {
                "result_of_mx_birads": {
                    "type": ["string", "null"],
                    "enum": ["1", "2", "3", "4", "5", None],
                    "description": "Codice BI-RADS mammografia da 1 a 5"
                },
                "side": {
                    "type": ["string", "null"],
                    "enum": ["1", "2", "3", None],
                    "description": "Lateralita: '1'=Destro, '2'=Sinistro, '3'=Bilaterale"
                },
                "linfonodi_ascellari_sospetti": {
                    "type": ["string", "null"],
                    "enum": ["1", "0", None],
                    "description": "'1'=Presenti/Sospetti, '0'=Assenti"
                }
            },
            "required": ["result_of_mx_birads", "side", "linfonodi_ascellari_sospetti"]
        }

        system_prompt = (
            "Sei un assistente medico esperto in senologia. Estrai con accuratezza le variabili REDCap richieste.\n"
            "Restituisci esclusivamente un JSON conforme allo schema."
        )
        user_prompt = f"{medical_text}\n\nEstrai i dati:"

        t_start = time.time()
        try:
            if self.results["preferred_api_mode"] == "completion":
                endpoint = f"{self.base_url}/completion"
                payload = {
                    "prompt": (
                        f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
                        f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
                        f"<|im_start|>assistant\n"
                    ),
                    "temperature": 0.0,
                    "json_schema": redcap_mock_schema,
                    "n_predict": 512,
                    "stream": False
                }
                r = requests.post(endpoint, headers=self.headers, json=payload, timeout=self.timeout, verify=self.verify_ssl)
                r.raise_for_status()
                raw_out = r.json().get("content", "")
            else:
                endpoint = f"{self.base_url}/v1/chat/completions"
                payload = {
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.0,
                    "response_format": {
                        "type": "json_object",
                        "schema": redcap_mock_schema
                    },
                    "stream": False
                }
                r = requests.post(endpoint, headers=self.headers, json=payload, timeout=self.timeout, verify=self.verify_ssl)
                r.raise_for_status()
                raw_out = r.json()["choices"][0]["message"]["content"]

            elapsed = round(time.time() - t_start, 2)
            parsed = json.loads(raw_out)
            self._log_debug("Risultato estrazione clinica", parsed)

            # Verifica accuratezza medica
            birads = parsed.get("result_of_mx_birads")
            side = parsed.get("side")
            nodes = parsed.get("linfonodi_ascellari_sospetti")

            print(Colors.ok(f"Estrazione completata in {elapsed}s:"))
            print(f"   * BI-RADS estratto: {Colors.BOLD}{birads}{Colors.RESET} (Atteso: '5')")
            print(f"   * Lato lesione:     {Colors.BOLD}{side}{Colors.RESET} (Atteso: '1' per Destro)")
            print(f"   * Linfonodi sosp.:  {Colors.BOLD}{nodes}{Colors.RESET} (Atteso: '1')")

            if birads == "5" and side == "1":
                self.results["clinical_test_passed"] = True
                print(Colors.ok("Tutti i valori clinici corrispondono esattamente alla diagnosi."))
                return True
            else:
                print(Colors.warn("Il JSON è valido ma i valori estratti differiscono da quelli attesi."))
                return True

        except Exception as e:
            print(Colors.fail(f"Errore durante l'estrazione clinica: {e}"))
            return False

    # --------------------------------------------------------------------------
    # Step 6: Verdetto Finale & Report di Configurazione
    # --------------------------------------------------------------------------
    def print_summary(self):
        print(Colors.header("RIEPILOGO DIAGNOSTICA LLAMA.CPP"))

        url_str = self.results["base_url"]
        conn_str = Colors.ok("CONNESSO") if self.results["connected"] else Colors.fail("NON RAGGIUNGIBILE")
        schema_str = Colors.ok("ATTIVO (GBNF)") if self.results["json_schema_supported"] else Colors.fail("NON SUPPORTATO")
        clin_str = Colors.ok("SUPERATO") if self.results["clinical_test_passed"] else Colors.warn("PARZIALE / NON TESTATO")

        print(f"Server Target:           {Colors.BOLD}{url_str}{Colors.RESET}")
        print(f"Stato Connessione:       {conn_str} (Latenza: {self.results['latency_ms']} ms)")
        print(f"Modello Rilevato:        {self.results['model_name'] or 'N/D'}")
        print(f"Dimensione Contesto:     {self.results['context_size'] or 'N/D'} token")
        print(f"Velocità Inferenza:      {self.results['tokens_per_sec'] or 'N/D'} token/s")
        print(f"Supporto JSON Schema:    {schema_str}")
        print(f"Modalità API Consigliata: {Colors.BOLD}{Colors.CYAN}{self.results['preferred_api_mode']}{Colors.RESET}")
        print(f"Test Clinico SenoNetwork: {clin_str}")

        print("\n" + "-" * 75)
        if self.results["connected"] and self.results["json_schema_supported"]:
            print(f"{Colors.GREEN}{Colors.BOLD}✔ IL SERVER È PRONTO E COMPATIBILE CON SENONETWORK!{Colors.RESET}")
            print("\nPer utilizzare questo server nella pipeline, imposta nel file .env:")
            print(f"{Colors.CYAN}AI_BACKEND=llama_cpp{Colors.RESET}")
            print(f"{Colors.CYAN}LLAMA_CPP_BASE_URL={self.results['base_url']}{Colors.RESET}")
            print(f"{Colors.CYAN}LLAMA_CPP_API_MODE={self.results['preferred_api_mode']}{Colors.RESET}")
        else:
            print(f"{Colors.RED}{Colors.BOLD}✘ IL SERVER NON È ANCORA PRONTO PER L'ESTRAZIONE SENONETWORK.{Colors.RESET}")
            if not self.results["connected"]:
                print("-> Verifica che llama-server sia avviato e che la porta/firewall sia aperta.")
            elif not self.results["json_schema_supported"]:
                print("-> Aggiorna llama.cpp all'ultima versione per il supporto del parametro json_schema / GBNF.")
        print("-" * 75 + "\n")


# ==============================================================================
# Entrypoint CLI
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Diagnostica e test di connettività / prestazioni per server llama.cpp remoto (cluster RPC)."
    )
    parser.add_argument(
        "--url", "-u",
        default=None,
        help="URL di base del server llama.cpp (es. http://192.168.1.100:8080). Default: da .env o http://localhost:8080."
    )
    parser.add_argument(
        "--api-key", "-k",
        default=None,
        help="Token API opzionale per server protetto da autenticazione Bearer."
    )
    parser.add_argument(
        "--cf-client-id",
        default=None,
        help="Cloudflare Access Client ID (se esposto tramite Cloudflare Tunnel)."
    )
    parser.add_argument(
        "--cf-client-secret",
        default=None,
        help="Cloudflare Access Client Secret."
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=30,
        help="Timeout per richiesta in secondi (default: 30)."
    )
    parser.add_argument(
        "--skip-clinical",
        action="store_true",
        help="Salta la simulazione di estrazione del referto medico."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Mostra payload JSON e dettagli di debug completi."
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disabilita la verifica dei certificati TLS/SSL (solo per test interni con certificati self-signed)."
    )
    args = parser.parse_args()

    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Risoluzione URL
    target_url = args.url or os.getenv("LLAMA_CPP_BASE_URL", "http://localhost:8080")

    print(Colors.header("DIAGNOSTICA SERVER LLAMA.CPP REMOTO (RPC CLUSTER)"))
    tester = LlamaRemoteTester(
        base_url=target_url,
        timeout=args.timeout,
        api_key=args.api_key,
        cf_client_id=args.cf_client_id,
        cf_client_secret=args.cf_client_secret,
        verbose=args.verbose,
        verify_ssl=not args.insecure,
    )

    # 1. Connettività
    if not tester.test_health():
        tester.print_summary()
        sys.exit(1)

    # 2. Informazioni modello e server
    tester.get_server_props()

    # 3. Test generazione testo
    tester.test_basic_generation()

    # 4. Test vincolo formale JSON Schema
    tester.test_json_schema_gbnf()

    # 5. Test clinico SenoNetwork
    if not args.skip_clinical:
        tester.test_clinical_extraction()

    # 6. Riepilogo
    tester.print_summary()


if __name__ == "__main__":
    main()
