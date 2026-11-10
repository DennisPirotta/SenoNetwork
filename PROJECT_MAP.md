# PROJECT MAP: SenoNetwork

Pipeline clinica per estrazione dati da referti medici PDF (senologia) tramite IA locale vincolata a metadata REDCap, con ingestione da HIS .NET (Priamo) ed export automatico via API.

```
[Priamo .NET / PDF] -> [PDFExtractor] -> [FormClassifier] -> [SchemaBuilder (REDCap)]
                                                                    |
[REDCap API / Webhook] <- [REDCapNormalizer] <- [llama.cpp RPC / Ollama (AIExtractor)]
```

---

## 1. Architettura File e Responsabilità

| Layer | File | Ruolo Principale |
|---|---|---|
| **Config & Models** | `src/config.py` | Settings Pydantic da `.env` (REDCap, Priamo/Cloudflare, llama.cpp RPC, Ollama, path, SSRF security). |
| | `src/models.py` | Modelli dati Pydantic (`REDCapField`, `ExtractionResult`). |
| **Ingestion (.NET/PDF)**| `src/priamo_client.py` | Client ASP.NET MVC per HIS Priamo (Cloudflare Access, auth token, download PDF). Configurato da `Settings`. |
| | `src/dotnet_client.py` | Facade unificata .NET (Priamo, Excel coorte, mock locale con path configurabili). |
| | `src/pdf_extractor.py` | Estrazione testo da PDF con PyMuPDF (fitz) o pypdf. |
| **REDCap Core** | `src/redcap_service.py` | Client API REDCap (export metadata, import record) e parser CSV/JSON. |
| | `src/schema_builder.py` | Traduzione dinamica campi REDCap -> JSON Schema vincolato (enum, types) e prompt clinico. |
| | `src/normalizer.py` | Mappatura output LLM -> standard REDCap (checkbox flat `___code`, date ISO, difflib/token match sicuro). |
| **AI Inference** | `src/ai_extractor.py` | `AIExtractor` Protocol + `BaseAIExtractor` (DRY prompt/schema, retry tenacity) + `LlamaCppExtractor` + `OllamaExtractor`. |
| | `src/form_classifier.py` | Router automatico referto -> form REDCap (keywords, esame Priamo, fallback LLM basato su `Settings`). |
| **Orchestration** | `src/patient_pipeline.py` | Pipeline paziente: Download .NET -> Classifica -> Estrai IA -> Carica REDCap -> Webhook SSRF-safe + State Tracking. |
| | `src/sync_service.py` | Audit & Sync periodico: ispezione REDCap, rilevamento form mancanti (es. post-op histology), rilettura referti, calcolo diff e aggiornamento arricchito. |
| | `src/audit_service.py` | Data Quality Auditor: matrice di avanzamento form, incongruenze cliniche/temporali, pTNM/grado, generazione report Markdown e JSON. |
| | `src/pipeline.py` | Pipeline generica batch su cartelle/file PDF locali con type hints unificati. |
| **Interfaces** | `src/api_server.py` | FastAPI REST microservice con Dependency Injection, health-check completo, background tasks asincroni, sync e report audit `/api/audit/*`. |
| | `src/cli.py` / `main.py` | CLI avanzata per esecuzione paziente, batch Excel, batch PDF, server mode, audit (`--audit-redcap`) o sync (`--sync-redcap`). |
| **Scripts** | `scripts/delete_redcap_records.py` | CLI per eliminazione sicura di record da REDCap (singolo per record_id o totale con conferma di sicurezza). |
| | `scripts/list_redcap_records.py` | CLI per elencare tutti i record REDCap con visualizzazione tabelle Rich, export JSON/CSV e filtri. |
| | `scripts/audit_redcap_data.py` | CLI dashboard e generatore report su incongruenze, mancanze e panoramica generale dati REDCap. |
| | `scripts/run_periodic_redcap_sync.py` | CLI runner / daemon schedulabile per audit periodico e aggiornamento dati clinici su REDCap. |
| | `scripts/test_remote_llamacpp.py` | Diagnostica e benchmarking server llama.cpp remoto/RPC (latenza, GBNF schema, estrazione clinica). |
| | `scripts/start_llama_rpc_cluster.sh` | Bash script per avvio cluster distribuito llama.cpp su 2 GPU via RPC. |
| | `scripts/create_sample_data.py` | Generatore referti sintetici PDF A4 per test. |
| | `scripts/inspect_redcap_project.py` | Utility di test e dump metadata live da REDCap API. |
| | `scripts/generate_redcap_summary.py`| Generatore documentazione markdown struttura REDCap (`docs/REDCAP_STRUCTURE.md`). |

---

## 2. Dettaglio Classi, Metodi e Firme

### `src/config.py`
- **`Settings(BaseSettings)`**:
  - `redcap_api_url: str`, `redcap_api_token: str`
  - `ai_backend: str = "llama_cpp"` ("llama_cpp" | "ollama")
  - `llama_cpp_base_url: str = "http://localhost:8080"` (master RPC)
  - `llama_cpp_api_mode: str = "chat"` ("chat" | "completion")
  - `llama_cpp_temperature: float = 0.0`, `llama_cpp_timeout_seconds: int = 180`
  - `ollama_base_url: str = "http://localhost:11434"`, `ollama_model: str = "llama3.2:3b"`
  - `priamo_api_url`, `priamo_username`, `priamo_password`, `priamo_cloudflared_client_id`, `priamo_cloudflared_client_secret`, `ai_cloudflared_client_id`, `ai_cloudflared_client_secret`
  - `dotnet_api_base_url`, `dotnet_api_key`
  - `allowed_webhook_hosts: Optional[List[str]] = None`, `allow_local_webhooks: bool = False` (protezione SSRF)
  - `pdf_input_dir`, `output_dir`, `downloads_dir`, `state_dir`, `redcap_cache_file`, `patients_excel_path`, `priamo_files_dir`
- **`get_settings() -> Settings`**: Factory singleton-like helper.

### `src/models.py`
- **`REDCapField(BaseModel)`**: Singolo campo del Data Dictionary REDCap.
  - Attributi: `field_name`, `form_name`, `field_type`, `field_label`, `select_choices_or_calculations`, `text_validation_type_or_show_slider_number`, `required_field`.
  - `is_extractable -> bool`: Esclude `descriptive`, `calc`, `file`, e `*_complete`.
  - `parse_choices() -> Dict[str, str]`: Converte `"1, A | 2, B"` o `yesno` in `{"1": "A", "2": "B"}`.
- **`ExtractionResult(BaseModel)`**: Risultato estrazione per singolo PDF.
  - Attributi: `pdf_path`, `record_id`, `raw_text`, `extracted_data: dict`, `redcap_record: dict`, `status: str`, `extraction_duration_sec: float`.

### `src/priamo_client.py`
- **`PriamoDocument(BaseModel)`**: `document_id`, `event_id`, `repository_id`, `exam_name`, `local_path`.
- **`PriamoPatient(BaseModel)`**: `patient_id` (idAnagPaz1), `name`, `surname`, `date_of_birth`, `slug`.
- **`PriamoClient(settings=None, ...)`**: Client HTTP stateful con retry (`tenacity`) e Cloudflare Access headers caricati da `Settings`.
  - `authenticate(force: bool = False) -> None`: GET form login -> estrae `__RequestVerificationToken` -> POST credenziali -> estrae e adatta cookie `.PRIAMOAUTH` anche in caso di domain mismatch su Cloudflare Tunnel. Re-autentica su 401/403.
  - `search_patient(name, surname, dob) -> Optional[PriamoPatient]`: GET `/PriamoCES/api/Anagrafica/GetAnagrafica`.
  - `get_patient_events(patient_id, start_date) -> List[dict]`: GET `/PriamoCES/api/Eventi/GetEvento`.
  - `get_event_documents(event_id) -> List[dict]`: GET `/PriamoCES/api/Documenti/GetDocumento`.
  - `download_document_pdf(doc_id, event_id, repo_id, dest_path, event_type, preferred_stato, versione) -> Path`: GET `/PriamoCES/Documento/GetReportTextToPdfToView`. Supporta sia `stato=FIRMATO` (documenti repository) sia `stato=REFERTATO` (testo ambulatoriale on-the-fly con `tipoEvento`, `versione=0`, `repository=N`) con fallback automatico tra le due modalità.
  - `fetch_all_patient_reports(patient, dest_dir, max_workers) -> List[PriamoDocument]`: Cerca eventi in parallelo, scarica concorrentemente tutti i PDF tramite `ThreadPoolExecutor` con riuso delle connessioni HTTP persistenti, gestendo sia documenti di archivio sia referti ambulatoriali (`referto='S'`).

### `scripts/test_priamo_auth.py`
- CLI di diagnosi e collaudo per l'autenticazione su Priamo esposto tramite Cloudflare Tunnel (`cloudflared`):
  - Verifica connettività, header Cloudflare e Cloudflare Access Service Tokens.
  - Ispeziona il form login e il token Anti-CSRF (`__RequestVerificationToken`).
  - Analizza gli attributi del cookie `.PRIAMOAUTH` (evidenzia Cookie Domain Mismatch RFC 6265 responsabile del redirect loop su browser).
  - Collauda gli endpoint REST `/PriamoCES/api/Anagrafica/GetAnagrafica` e `/PriamoCES/api/Eventi/GetEvento`.

### `src/dotnet_client.py`
- **`DotNetApiClient(settings=None, ...)`**: Facade di accesso ai dati ospedalieri configurata con `Settings`.
  - `download_all_patient_pdfs(patient_id, dest_dir, name, surname, dob) -> List[Path]`: Prova `PriamoClient`, poi generic REST API, poi fallback cartelle configurate (`priamo_files_dir`, `downloads_dir`).
  - `get_patient_documents(patient_id) -> List[dict]`: Elenco metadati documenti disponibili.
  - `load_patients_from_excel(excel_path=None, header_row=4, settings=None) -> List[dict]`: Legge registro chirurgico, deduplica per `(nome, cognome, dob)` e ritorna lista pazienti.

### `src/pdf_extractor.py`
- **`PDFExtractor`**:
  - `extract_text(pdf_path: str|Path) -> str`: Estrae e concatena testo da tutte le pagine con PyMuPDF (`pymupdf.open`) o fallback su `pypdf`. Pulisce righe vuote.
  - `find_pdf_files(target_path: str|Path) -> List[Path]`: Ritorna file PDF singolo o ricorsivo da directory.

### `src/redcap_service.py`
- **`REDCapService(api_url, api_token)`**: Normalizza URL terminando con `/api/`.
  - `fetch_metadata_from_api(forms=None) -> List[REDCapField]`: POST `content=metadata`.
  - `load_metadata_from_file(path) -> List[REDCapField]`: Carica da CSV Data Dictionary o JSON esportato.
  - `filter_fields_by_form(fields, form_name) -> List[REDCapField]`: Filtra per strumento includendo sempre la primary key (`record_id`).
  - `import_records_to_api(records: List[dict]) -> dict`: POST `content=record, type=flat, overwriteBehavior=normal`.
  - `export_records_from_api(records, fields, forms, events, filter_logic, ...) -> List[dict]`: POST `content=record, type=flat` per interrogazione e audit stato pazienti.
  - `get_record(record_id, fields, forms) -> Optional[dict]`: Recupera singolo paziente da REDCap.
  - `get_all_patient_ids() -> List[str]`: Elenco completo di tutti i `record_id` registrati nel progetto.
  - `list_all_records(fields, forms, include_demographics, include_completion_status, filter_logic) -> List[dict]`: Elenca tutti i record presenti su REDCap con filtri e campi anagrafici/status.
  - `get_records_summary() -> List[dict]`: Panoramica aggregata di tutti i record con avanzamento form e conteggio form completati.
  - `find_record_id_by_demographics(name, surname, dob) -> Optional[str]`: Cerca e preserva il `record_id` nativo di REDCap per anagrafica.
  - `delete_records(record_ids: List[str], arm, chunk_size) -> int`: Eliminazione a lotti (chunked POST con `action=delete`) di record su REDCap.
  - `delete_record(record_id: str, arm) -> bool`: Eliminazione di un singolo record da REDCap.
  - `delete_all_records(confirm=False, arm, chunk_size, clean_local_state) -> int`: Eliminazione massiva protetta da flag `confirm=True` di tutti i record presenti su REDCap con pulizia facoltativa dello stato locale.
- **Funzioni di modulo**:
  - `list_redcap_records(...) -> List[dict]`: Helper top-level per elencare tutti i record.
  - `delete_redcap_records(record_id, all_records, confirm, clean_local_state, ...) -> int`: Helper top-level unificato per eliminazione singola o totale da REDCap.

### `src/sync_service.py`
- **`RedCapSyncService(settings, redcap_service, dotnet_client, ai_extractor, ...)`**:
  - `get_patients_from_redcap(filter_incomplete_forms) -> List[dict]`: Filtra pazienti con form incompleti (`status != '2'`).
  - `detect_incomplete_forms(redcap_record, candidate_forms) -> List[str]`: Rileva form mancanti/incompleti.
  - `check_and_update_patient(patient_id, target_forms, auto_upload, dry_run, ...) -> PatientSyncResult`: Ispezione paziente, download e analisi nuovi referti (es. istologico post-operatorio), estrazione IA, diff per campo e upload.
  - `sync_all(target_forms, auto_upload, dry_run, limit, ...) -> SyncSummaryReport`: Ciclo completo su coorte con salvataggio log in `data/output/sync/`.
  - `run_periodic(interval_seconds, target_forms, ...) -> None`: Daemon loop per esecuzione schedulata continua.

### `src/audit_service.py`
- **`RedCapDataAuditor(settings, redcap_service)`**:
  - `run_audit(records=None, patient_id=None) -> RedCapAuditReport`: Analisi completa di avanzamento form, incongruenze cliniche, date e pTNM.
  - `generate_markdown_report(report) -> str`: Genera report formattato Markdown con alert e tabelle.
  - `export_reports(report, output_dir) -> Dict[str, Path]`: Esporta report Markdown e JSON in `data/output/reports/`.

### `src/schema_builder.py`
- **`SchemaBuilder`**:
  - `build_json_schema(fields: List[REDCapField]) -> dict`: Genera JSON Schema formale per LLM.
    - `dropdown`/`radio`: `"type": ["string", "null"]`, `"enum": [codici..., None]`.
    - `yesno`/`truefalse`: `"enum": ["1", "0", None]`.
    - `checkbox`: `"type": ["array", "null"]`, `"items": {"type": "string", "enum": [codici...]}`.
    - `date`: `"description": "...YYYY-MM-DD..."`.
  - `build_prompt_guidelines(fields: List[REDCapField]) -> str`: Testo di istruzioni cliniche in italiano con codici ammessi e regole zero-allucinazioni.

### `src/form_classifier.py`
- **`FormClassifier`**:
  - `classify_report(text: str, hint=None, settings=None, ...) -> str`: Smista referto su uno degli 8 form REDCap.
    1. Verifica hint/nome file (`mammo`, `biops`, `istolog`, `chirurg`, ecc.).
    2. Heuristics regex/testo clinico (es. "frustoli"/"core biopsy" -> `anatomia_patologica_preoperatoria`, "birads" -> `radiologia`, "quadrantectomia"/"margini" -> `anatomia_patologica`).
    3. Fallback LLM zero-shot: legge configurazioni da `Settings` (llama.cpp su master RPC o Ollama).

### `src/ai_extractor.py`
- **`AIExtractor(Protocol)`**: Contratto runtime per i backend di estrazione (`is_available`, `extract_from_text`).
- **`BaseAIExtractor(ABC)`**: Classe base con prompt unico consolidato, schema injection, retry con `tenacity`, filtro anti-allucinazione chiavi sconosciute e normalizzazione REDCap.
- **`LlamaCppExtractor(BaseAIExtractor)`**: Supporta `/v1/chat/completions` (OpenAI format con JSON schema) e `/completion` (nativo llama.cpp GBNF su cluster RPC 2 GPU).
- **`OllamaExtractor(BaseAIExtractor)`**: Supporta `/api/chat` con `format: json_schema`.
- **`parse_json_response(text: str) -> dict`**: Strippa blocchi markdown ````json ```` ed isola token `{ ... }`.
- **`get_ai_extractor(settings=None) -> AIExtractor`**: Factory con fallback trasparente (se llama.cpp RPC è offline, commuta su Ollama locale).

### `src/normalizer.py`
- **`REDCapNormalizer`**:
  - `normalize_record(extracted_data, fields, record_id, form_name) -> dict`:
    - `_normalize_choice`: Exact code -> case-insensitive -> exact label -> token-level matching -> difflib similarità (>=0.80). Previene falsi match parziali pericolosi in medicina.
    - Espande checkbox nel formato flat REDCap (`campo___codice = "1"` se selezionato, `"0"` altrimenti).
    - Converte date in `YYYY-MM-DD`.
    - Pulisce numerici (virgola -> punto).
    - Imposta `{form_name}_complete = "2"` (Completato).

### `src/patient_pipeline.py`
- **`PatientPipeline(settings=None, ...)`**:
  - `process_patient(patient_id, name, surname, dob, pdf_paths, auto_upload, webhook_url) -> PatientPipelineResult`:
    1. Scarica referti PDF da Priamo/.NET (o usa `pdf_paths`).
    2. Calcola SHA-256 dei PDF per tracciamento idempotenza.
    3. Popola `dati_del_paziente` se disponibili dati anagrafici.
    4. Per ogni PDF: estrae testo -> `FormClassifier` -> carica schema REDCap del form -> estrazione con `ai_extractor` -> `REDCapNormalizer`.
    5. Aggrega tutti i record per `record_id = patient_id`.
    6. Se `auto_upload=True`: `REDCapService.import_records_to_api()`.
    7. Memorizza lo stato in `data/state/{patient_id}_state.json`.
    8. Valida `webhook_url` con `is_safe_webhook_url()` (prevenzione SSRF) e invia callback.
    9. Salva log JSON in `data/output/patients/`.

### `src/pipeline.py`
- **`ExtractionPipeline`**: Orchestratore batch generico per cartelle o liste di file PDF. Tipizzato con `AIExtractor`. Esporta in JSON (`export_to_json`) e CSV REDCap (`export_to_redcap_csv`).

### `src/api_server.py`
- Microservizio FastAPI (`main.py --serve --port 8000`):
  - Dependency Injection con `Depends(get_pipeline)` e `Depends(get_shared_settings)`.
  - `GET /health`: Stato di llama.cpp RPC, Ollama, REDCap e Priamo .NET.
  - `POST /api/patient/{id}/process`: Trigger sincrono da applicazione .NET.
  - `POST /api/patient/{id}/process-async`: Trigger asincrono (`BackgroundTasks`) con restituzione immediata di `task_id`.
  - `GET /api/tasks/{task_id}`: Polling stato ed esito elaborazione background.
  - `POST /api/patient/{id}/upload-pdfs`: Upload multipart di PDF direttamente da applicazione .NET.
  - `POST /api/batch/excel`: Elaborazione batch di coorte da file Excel configurato in `Settings`.
  - `GET /api/redcap/records`: Elenco completo dei record presenti su REDCap con filtri.
  - `GET /api/redcap/records/summary`: Panoramica aggregata di avanzamento e form completati.
  - `DELETE /api/redcap/records/{record_id}`: Eliminazione singolo record paziente con opzione `clean_local_state`.
  - `DELETE /api/redcap/records?confirm=true`: Eliminazione massiva di tutti i record da REDCap protetta da `confirm=true`.

### `src/cli.py` & `main.py`
- Entrypoint CLI:
  - Inizializza `Settings(**overrides)` in modo immutabile.
  - Supporta `--patient-id`, `--name`, `--surname`, `--dob`, `--excel`, `--pdf`, `--serve`, `--backend`.
  - Supporta `--audit-redcap`, `--sync-redcap`, `--list-records`.
  - Supporta `--delete-record <ID>` e `--delete-all-records --confirm` (con opzione `--clean-local-state`).

---

## 3. Mappatura Form REDCap Progetto SenoNetwork (ID: 20)

| Form REDCap | Campi | Referto Clinico Associato | Variabili Chiave |
|---|:---:|---|---|
| `dati_del_paziente` | 44 | Anagrafica, anamnesi, familiarità, genetica | `name`, `surname`, `date_of_birth`, `brca1`, `brca2`, `status`, `metastasi` |
| `radiologia` | 36 | Mammografia 2D/3D, Ecografia, RMN | `date_of_mx`, `result_of_mx_birads`, `result_of_mx_eu`, `result_of_us`, `mr_result`, `focality`, cTNM |
| `anatomia_patologica_preoperatoria`| 19 | Core biopsy, VABB, Agoaspirato (FNA) | `date_of_cb`, `result_cb` (B1-B5), `cytological_result_fna` (C1-C5), `istotype`, ER, PgR, HER2, Ki67 |
| `struttura_della_lesione` | 44 | Verbale operatorio chirurgico | `date_of_1st_surgical_procedure`, `type_of_1st_breast_surgery`, `side`, `site`, chirurgia ascellare |
| `anatomia_patologica` | 46 | Istologico definitivo post-operatorio | `grade` (Elston-Ellis), `invasive_histological_type`, pT, pN, margini (mm), OSNA, ER%, PgR%, HER2, Ki67% |
| `chemioterapia` | 66 | Schede oncologiche terapie mediche | Chemioterapia neoadiuvante/adiuvante, Trastuzumab, Tamoxifene, Inibitori aromatasi |
| `radioterapia` | 12 | Piani di trattamento radiante | `radiotherapy_delivery`, `volume_irradiated`, dosi Gy, date |
| `recidiva` | 10 | Follow-up oncologico | `breast_local_recurrence`, recidiva ascellare, parete toracica, date |

---

## 4. Cluster llama.cpp Distribuito (Protocollo RPC)

- **Worker GPU 0**: `CUDA_VISIBLE_DEVICES=0 rpc-server -H 127.0.0.1 -p 50052`
- **Worker GPU 1**: `CUDA_VISIBLE_DEVICES=1 rpc-server -H 127.0.0.1 -p 50053`
- **Master Server**: `llama-server -m <model.gguf> --rpc 127.0.0.1:50052,127.0.0.1:50053 --host 0.0.0.0 --port 8080 -c 8192 --n-gpu-layers 999`
- **Script launcher**: `./scripts/start_llama_rpc_cluster.sh <model.gguf>`
- **Fallback status**: Se offline, `get_ai_extractor()` usa `OllamaExtractor` (`127.0.0.1:11434`). Quando il cluster è online su `:8080`, commuta a `LlamaCppExtractor` senza modifiche al codice.
