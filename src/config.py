import re
from typing import List, Optional
from pathlib import Path
from pydantic import Field, AliasChoices, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # REDCap API settings
    redcap_api_url: str = Field(
        default="",
        description="Full URL to the REDCap API endpoint (e.g., https://redcap.example.org/api/)"
    )
    redcap_api_token: str = Field(
        default="",
        description="API Token with Export Metadata (and optionally Import Records) privileges"
    )

    # Local AI Engine Selection: 'llama_cpp' (default) or 'ollama'
    ai_backend: str = Field(
        default="llama_cpp",
        description="Backend IA locale da utilizzare ('llama_cpp' con RPC o 'ollama')"
    )

    # llama.cpp Server settings (Inferenza distribuita multi-GPU con protocollo RPC)
    llama_cpp_base_url: str = Field(
        default="http://localhost:8080",
        description="URL del server master llama.cpp (es. http://localhost:8080)"
    )
    llama_cpp_api_mode: str = Field(
        default="chat",
        description="Modalità API llama.cpp: 'chat' (/v1/chat/completions) o 'completion' (/completion)"
    )
    llama_cpp_model: str = Field(
        default="",
        description="Nome opzionale del modello per l'header OpenAI di llama.cpp"
    )
    llama_cpp_temperature: float = Field(
        default=0.0,
        description="Temperatura di campionamento per llama.cpp (0.0 per estrazione deterministica)"
    )
    llama_cpp_timeout_seconds: int = Field(
        default=180,
        description="Timeout in secondi per llama.cpp (supporta modelli distribuiti su 2 schede RPC)"
    )
    ai_cloudflared_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("ai_cloudflared_client_id", "ai_cf_client_id"),
        description="Cloudflare Access Client ID per server inferenza llama.cpp (CF-Access-Client-Id)"
    )
    ai_cloudflared_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("ai_cloudflared_client_secret", "ai_cf_client_secret"),
        description="Cloudflare Access Client Secret per server inferenza llama.cpp (CF-Access-Client-Secret)"
    )

    # Local Ollama AI settings
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Local Ollama instance URL"
    )
    ollama_model: str = Field(
        default="llama3.2:3b",
        description="Local model name running in Ollama (e.g. llama3.2:3b, qwen2.5:7b)"
    )
    ollama_temperature: float = Field(
        default=0.0,
        description="Sampling temperature (0.0 for deterministic factual extraction)"
    )
    ollama_timeout_seconds: int = Field(
        default=240,
        description="Timeout in seconds for Ollama LLM requests (consente inferenza su CPU per form estesi)"
    )

    # Priamo .NET HIS Settings (Ospedale Valduce)
    priamo_api_url: str = Field(
        default="",
        description="URL base per il portale/servizi Priamo (es. https://priamo-home.valduce.it:8062 o https://priamo.dennispirotta.com)"
    )
    priamo_cup_url: str = Field(
        default="http://srv-priamo-fe1.valduce.it:8082/PriamoCUP",
        description="URL base per il modulo PriamoCUP (es. http://srv-priamo-fe1.valduce.it:8082/PriamoCUP)"
    )
    priamo_home_url: str = Field(
        default="https://priamo-home.valduce.it:8062/PriamoHOME",
        description="URL base per il portale PriamoHOME per autenticazione (es. https://priamo-home.valduce.it:8062/PriamoHOME)"
    )
    priamo_username: str = Field(
        default="",
        description="Nome utente per autenticazione su Priamo .NET"
    )
    priamo_password: str = Field(
        default="",
        description="Password per autenticazione su Priamo .NET"
    )
    priamo_cloudflared_client_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "priamo_cloudflared_client_id",
            "priamo_cf_client_id",
            "cloudflared_client_id",
            "cf_client_id",
        ),
        description="Cloudflare Access Client ID per Priamo (CF-Access-Client-Id)"
    )
    priamo_cloudflared_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "priamo_cloudflared_client_secret",
            "priamo_cf_client_secret",
            "cloudflared_client_secret",
            "cf_client_secret",
        ),
        description="Cloudflare Access Client Secret per Priamo (CF-Access-Client-Secret)"
    )
    priamo_max_workers: int = Field(
        default=8,
        description="Numero massimo di thread concorrenti per il download parallelo dei referti PDF da Priamo"
    )

    # Generic .NET API Settings
    dotnet_api_base_url: str = Field(
        default="",
        description="URL di base per API REST generica .NET dell'ospedale"
    )
    dotnet_api_key: str = Field(
        default="",
        description="Chiave API / Token Bearer per API generica .NET"
    )

    # Extraction settings
    default_form_name: Optional[str] = Field(
        default=None,
        description="Optional REDCap form/instrument name to filter metadata fields"
    )
    webhook_callback_url: Optional[str] = Field(
        default=None,
        description="URL opzionale di un endpoint API esterno a cui inviare via POST i risultati dell'elaborazione"
    )
    allowed_webhook_hosts: Optional[List[str]] = Field(
        default=None,
        description="Whitelist opzionale di hostname autorizzati per le chiamate webhook (prevenzione SSRF)"
    )
    allow_local_webhooks: bool = Field(
        default=False,
        description="Consenti webhook verso localhost o IP privati (usare solo in ambiente di sviluppo locale)"
    )

    # API Server & Security (Web Dashboard Integration)
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("senonetwork_api_key", "api_key"),
        description="API Key opzionale per autenticare le richieste dalla dashboard web via header X-API-Key"
    )
    allow_insecure_local_dev: bool = Field(
        default=False,
        validation_alias=AliasChoices("allow_insecure_local_dev", "allow_unauthenticated_dev"),
        description="Consente esplicitamente l'avvio senza API Key anche su host non-loopback (solo sviluppo)"
    )
    allow_mass_delete: bool = Field(
        default=False,
        validation_alias=AliasChoices("allow_mass_delete", "enable_mass_delete_api"),
        description="Abilita l'endpoint HTTP distruttivo DELETE /api/redcap/records (disabilitato per sicurezza di default)"
    )
    redcap_delete_token: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("redcap_delete_token", "redcap_admin_api_token"),
        description="Token REDCap separato opzionale per operazioni di eliminazione record"
    )
    priamo_verify_ssl: bool = Field(
        default=True,
        description="Verifica i certificati SSL/TLS per connessioni a Priamo / PriamoCUP"
    )
    ai_max_concurrent_requests: int = Field(
        default=1,
        ge=1,
        le=8,
        description="Numero massimo di inferenze LLM concorrenti eseguite contemporaneamente"
    )
    llm_fallback_timeout_seconds: int = Field(
        default=30,
        ge=3,
        le=180,
        description="Timeout in secondi per la classificazione fallback tramite LLM"
    )
    cors_origins: List[str] = Field(
        default=[
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://127.0.0.1:5173",
            "http://localhost:8080",
        ],
        description="Elenco delle origini consentite dal middleware CORS per chiamate da browser"
    )
    api_host: str = Field(
        default="127.0.0.1",
        description="Host predefinito per il server HTTP FastAPI (default sicuro loopback 127.0.0.1)"
    )
    api_port: int = Field(
        default=8000,
        description="Porta predefinita per il server HTTP FastAPI"
    )

    # Sync / Audit Settings (Controllo periodico e aggiornamento dati REDCap)
    sync_interval_minutes: int = Field(
        default=60,
        description="Intervallo in minuti per il controllo periodico di aggiornamento dei referti su REDCap"
    )
    sync_auto_upload: bool = Field(
        default=True,
        description="Se True, carica automaticamente le modifiche e i nuovi dati estratti su REDCap"
    )
    sync_overwrite_mode: str = Field(
        default="fill_missing",
        description="Modalità di aggiornamento: 'fill_missing' (arricchisce solo campi vuoti/nuovi) o 'overwrite' (sovrascrive sempre)"
    )
    sync_target_forms: Optional[List[str]] = Field(
        default=None,
        description="Lista opzionale di form REDCap prioritari da controllare (es. ['anatomia_patologica', 'struttura_della_lesione'])"
    )
    sync_audit_dir: Path = Field(
        default=Path("./data/output/sync"),
        description="Cartella in cui archiviare i report di audit e riconciliazione periodica"
    )

    # Paths and Cohort Directories
    pdf_input_dir: Path = Field(
        default=Path("./data/input_pdf"),
        description="Cartella predefinita con i referti PDF da elaborare"
    )
    output_dir: Path = Field(
        default=Path("./data/output"),
        description="Cartella predefinita per salvare i risultati strutturati JSON/CSV"
    )
    cup_dir: Path = Field(
        default=Path("./data/cup"),
        description="Cartella predefinita in cui salvare i fogli Excel scaricati da PriamoCUP"
    )
    downloads_dir: Path = Field(
        default=Path("./data/downloads"),
        description="Cartella predefinita per salvare i PDF scaricati da Priamo/.NET"
    )
    state_dir: Path = Field(
        default=Path("./data/state"),
        description="Cartella per memorizzare lo stato e tracciare l'idempotenza delle elaborazioni"
    )
    redcap_cache_file: Path = Field(
        default=Path("./data/redcap_metadata_live.json"),
        description="Percorso della copia cache del Data Dictionary REDCap"
    )
    clinical_guidelines_file: Path = Field(
        default=Path("./config/clinical_guidelines.json"),
        description="Percorso del file JSON contenente le linee guida cliniche personalizzabili per form e campo"
    )
    patients_excel_path: Optional[Path] = Field(
        default=None,
        description="Percorso al file Excel degli interventi (es. interventi.xls)"
    )
    priamo_files_dir: Optional[Path] = Field(
        default=None,
        description="Cartella locale contenente file o PDF esportati da PriamoScraper"
    )

    @model_validator(mode="after")
    def sync_priamo_urls(self) -> "Settings":
        """Se priamo_api_url è specificato (es. tunnel Cloudflare), sincronizza i default interni di home e cup."""
        if self.priamo_api_url:
            clean = self.priamo_api_url.rstrip("/")
            base = re.sub(r"/(PriamoHOME|PriamoCES|PriamoCUP)$", "", clean, flags=re.IGNORECASE)
            if self.priamo_home_url == "https://priamo-home.valduce.it:8062/PriamoHOME":
                self.priamo_home_url = f"{base}/PriamoHOME"
            if self.priamo_cup_url == "http://srv-priamo-fe1.valduce.it:8082/PriamoCUP":
                self.priamo_cup_url = f"{base}/PriamoCUP"
        return self

    @property
    def cloudflared_client_id(self) -> str:
        return self.priamo_cloudflared_client_id

    @property
    def cloudflared_client_secret(self) -> str:
        return self.priamo_cloudflared_client_secret

    @property
    def cf_client_id(self) -> str:
        return self.priamo_cloudflared_client_id

    @property
    def cf_client_secret(self) -> str:
        return self.priamo_cloudflared_client_secret

    @property
    def ai_cf_client_id(self) -> str:
        return self.ai_cloudflared_client_id

    @property
    def ai_cf_client_secret(self) -> str:
        return self.ai_cloudflared_client_secret


def get_settings() -> Settings:
    """Helper to instantiate settings."""
    return Settings()
