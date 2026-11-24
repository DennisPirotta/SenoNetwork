# Revisione del codice SenoNetwork

Analisi di falle, errori logici e miglioramenti, 24 novembre 2026.

## Ambito e metodo

Analizzato il repository **SenoNetwork** (zip caricato). HemaConnect non è stato analizzato perché il repo non era raggiungibile (probabilmente privato). Letti circa 500 KB di sorgenti ed eseguiti i test (124 passano, 2 saltati) con script di riproduzione. Dove scrivo «verificato» ho visto il comportamento eseguendo il codice; altrimenti viene dalla lettura. `pyflakes` conferma i tre nomi non definiti.

## Critico: integrità dei dati paziente

### 1. Associazione al paziente sbagliata (5 percorsi)

- Se manca la data di nascita, `download_all_patient_pdfs` usa il `record_id` REDCap (es. "42") come ID interno Priamo. Verificato con mock: scarica i referti di un altro paziente.
- Il fallback locale `_get_local_documents_mock` cerca `cid in p.stem`. Verificato: `record_id` "1" associa entrambi i PDF di esempio, "42" associa `…SENO1142.pdf`. Scatta anche in silenzio se Priamo va in errore.
- `search_patient` prende `data[0]` senza controllare i risultati multipli, e i fallback allentano il match sul nome.
- `find_record_id_by_demographics` accetta nome+cognome da soli se manca una delle due date di nascita, quindi gli omonimi vengono fusi.
- `generate_next_record_name` ripiega su `1` se la chiamata fallisce. Inoltre `_allocated_record_ids` vive per istanza, ma `PatientPipeline` è creata a ogni richiesta, quindi due richieste concorrenti possono ricevere lo stesso ID.

**Fix:** usare il codice fiscale (già nei dati CUP) come chiave, rifiutare se i match sono più di uno, fallire senza ripiegare su `1`. Aggiungere un controllo dell'identità nel referto (ID, iniziali, data di nascita) rispetto al paziente atteso. La fixture `SENO-2024-TEST` contiene già due pazienti diversi (0891 e 1142) fusi in un record.

### 2. `re` non importato in `patient_pipeline.py:189` (verificato)

Il `NameError` viene inghiottito da `except Exception → logger.debug`. Lo slug `nome-cognome-data_nascita` resta come `record_id` REDCap invece del progressivo numerico. Così i dati anagrafici finiscono in ID, nomi di file e file di stato.

### 3. Dati estratti incompleti e inventati, ma segnati «completi»

- Nel referto istologico di esempio ci sono G2, ER 90%, PgR 65%, HER2 1+, Ki-67 22%, cN0, 18 mm. Il record salvato contiene **solo due date e `_complete=2`**. Ha anche `data_of_fna` (nessuna FNA nel referto) e, nel referto mammografico, `date_of_mr_imaging` (nessuna risonanza).
- Non so quale modello abbia prodotto quell'output, perché non viene registrato. Il difetto è comunque nel codice: nessun valore viene confrontato col testo, e il normalizzatore imposta `_complete=2` se c'è un solo valore non vuoto.
- Il sync poi considera «fatto» ciò che ha `_complete==2` (`detect_incomplete_forms`), quindi quel form non viene mai rielaborato.

**Fix:** da macchina imposta `_complete=1` (Unverified), mai `2`. Fai restituire al modello una citazione per ogni campo valorizzato e scarta i valori la cui citazione non è nel testo. Per le date verifica che compaiano nel testo. Calcola la completezza dalla copertura dei campi.

### 4. Normalizzazione (tutto verificato)

- Fuzzy match a 0.80: "Grado 3" diventa codice `1`, "Luminal" diventa Luminal A. Se nulla combacia, il testo grezzo passa nel campo radio (REDCap rifiuta l'intero batch).
- `_normalize_boolean` mappa "non valutabile", "n.d." e "dubbio" a `0` (No).
- Le branching logic confrontano i numeri come stringhe: `[size_mm] > 50` con 100 dà «nascosto», quindi il valore estratto viene cancellato. `datediff` e simili ricadono su «visibile».
- La pipeline conta `v not in ("0", …)` e scarta i "0" legittimi.

### 5. Classificatore (verificato)

`"pt"` combacia con «agobio**pt**ici», presente in ogni core biopsy. Il referto di esempio con nome file neutro finisce in `anatomia_patologica` (post-operatorio); solo «biopsia» nel nome lo salva. La regola post-operatoria viene valutata prima di quella pre-operatoria. Il timeout LLM è 3 s, contro i 114 s misurati per documento. Se scade, un istologico benigno con «mammario» finisce in `radiologia`.

**Fix:** regex con word boundary, regola pre-op prima, fallback `unknown` con revisione umana.

### 6. Data documento

`extract_document_date` prende la prima data dopo «del» o «li». Verificato: «confronto con mammografia del 14/03/2022» batte «Data Referto 20/05/2024». Sul merge conta l'ordine cronologico, quindi la precedenza si inverte. Le date prima del 2020 danno `1970-01-01` (il regex copre solo 202x).

### 7. Sovrascrittura su REDCap

`overwriteBehavior: normal` sovrascrive i valori inseriti a mano con quelli del modello. Per i checkbox il normalizzatore scrive `0` per ogni opzione non citata; per la semantica REDCap questo dovrebbe disattivare opzioni già spuntate (non testato su REDCap). `auto_upload=True` è il default, e `fill_missing` esiste solo nel sync.

**Fix:** leggi il record, calcola il diff, scrivi solo i campi vuoti e usa `dry_run` come default.

## Critico: sicurezza

### 8. Autenticazione fail-open, CORS e delete di massa (verificato)

- Senza `SENONETWORK_API_KEY` tutte le rotte rispondono 200, e il bind predefinito è `0.0.0.0`.
- Il preflight da `https://evil.example` riceve l'origine riflessa con `allow-credentials: true`. La lista `cors_origins` contiene `"*"`, quindi l'elenco di origini consentite non ha effetto.
- `DELETE /api/redcap/records` è protetto solo da `?confirm=true` e usa lo stesso token dell'ingestione. Qualunque pagina aperta da un browser in rete può svuotare il progetto.

**Fix:** rifiuta l'avvio senza API key, usa origini esplicite, due token REDCap (uno senza diritto di delete) e togli il delete di massa dall'API.

### 9. Rotte aperte anche con la chiave impostata (verificato)

- `GET /api/tasks/{id}/events` (SSE) espone `current_step` con nome e cognome.
- `POST /api/security/generate-key` è accessibile senza chiave.
- `/health` espone gli URL interni di llama.cpp e PriamoCUP.

### 10. Lettura di file arbitrari e path traversal

- In `_find_target_cup_file`, `file_name` può essere un percorso assoluto: verificato, `/api/cup/patients?file_name=/tmp/outside/other.xlsx` risponde 200. Il `../` non l'ho provato.
- `patient_id` dal body non è sanitizzato. Verificato: `downloads_dir / "/tmp/pwn"` diventa `/tmp/pwn`. Lo stesso valore finisce in `patient_{id}_result.json` e `{id}_state.json`.

### 11. TLS e rete

- `PriamoCupClient.verify_ssl=False` di default, con `urllib3.disable_warnings()` globale all'import (silenzia gli avvisi anche per REDCap). L'URL CUP predefinito è `http://`, quindi credenziali e fogli di lavoro viaggiano senza verifica.
- `verify-cohort` accetta `max_workers` senza limite massimo: si può sovraccaricare Priamo.

### 12. Webhook

Il `webhook_url` arriva dalla richiesta, e il payload contiene l'intero risultato clinico. Senza whitelist va a qualunque host pubblico. Il controllo `is_safe_webhook_url` è solo sulle stringhe: non resiste a un nome DNS che punta a un IP privato né ai redirect. Accettalo solo da configurazione server.

## Affidabilità

### 13. Funzioni che non possono funzionare (verificate)

- `sync.py`: `background_tasks: Any` rende l'endpoint async sempre 422. Inoltre `time` non è importato, quindi il task in background cade in `NameError` prima del `try` e resta «processing». Nei test ci sono 0 riferimenti.
- `/api/cup/export?format=xlsx` dà 500: `pd.ExcelWriter(engine="calamine")` non esiste (calamine legge soltanto).
- `PriamoClient.authenticate()` va in **deadlock**. Con la pagina di login a 403 (per esempio Cloudflare Access che rifiuta il token), `_request` richiama `authenticate(force=True)` con un `threading.Lock` non rientrante. Resta bloccato invece di dare errore.

### 14. Privacy nei log e nei dati

- Nome, cognome e data di nascita compaiono nei log INFO e WARNING (`priamo_client.py:394, 418, 609…`, `patient_pipeline.py:202`).
- `_tasks_store` tiene in RAM per sempre i risultati clinici completi (`model_dump()`): perdita di memoria e dati sanitari in memoria.
- Le scritture di stato e report non sono atomiche.

### 15. Igiene del repo

- Lo zip è un archivio GitHub e contiene `data/…` (PDF, CSV, JSON) che `.gitignore` esclude, quindi sono tracciati. Sembrano sintetici ma servono `git rm --cached` e una conferma.
- `redcap_metadata_live.json` (200 KB) è il data dictionary di produzione e funge anche da cache, quindi può restare obsoleto.
- Nel README compare `PRIAMO_USERNAME=90092`: controlla che non sia reale. Ci sono percorsi `/home/dennis/…` in README, `.env.example` e test.
- `GEMINI.md` dà all'agente il permesso permanente di fare commit e push da solo; con i file di cui sopra tracciati è un rischio.

## Miglioramenti

- **Prestazioni:** `get_single_record` esporta tutti i record e filtra in Python; `find_record_id_by_demographics` esporta tutti i record per ogni paziente (O(N²) in batch). Con `Depends(get_pipeline)` ogni richiesta fa fino a 3×5 s di sonde di rete (`get_ai_extractor`) e rilegge `.env`. `GET /api/audit/report` scrive file (effetti collaterali su GET) e rifà un audit completo.
- **Concorrenza:** i job LLM da minuti girano nel threadpool sincrono, senza lock per paziente e senza coda. Usa una coda (RQ, Arq o Celery) o almeno un semaforo.
- **Tracciabilità:** registra backend, modello, hash del prompt e versione dello schema. Oggi il fallback a Ollama `llama3.2:3b` è silenzioso e il risultato non lo indica.
- **Contesto:** non c'è un controllo sui token: guideline dell'intero form più testo intero, con `-c 8192` nel README.
- **Qualità:** 73 `except Exception`, 11 con `pass`, 88 import inutilizzati. Aggiungi ruff (la regola F821 avrebbe intercettato `re` e `time`), mypy e una CI.

## Ordine consigliato

1. Auth e CORS, più la separazione dei token REDCap (punto 8).
2. Chiave paziente e fail-closed sull'identità (punti 1 e 2).
3. `_complete=1` e validazione delle citazioni (punto 3), poi la classificazione (punto 5).
4. Fix puntuali: `sync.py`, deadlock, export xlsx, percorsi (punti 13 e 10).
