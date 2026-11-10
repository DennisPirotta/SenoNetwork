import argparse
from datetime import datetime
import logging
import sys
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv

from src.ai_extractor import (
    LlamaCppExtractor,
    OllamaExtractor,
    get_ai_extractor
)
from src.config import Settings
from src.models import ExtractionResult
from src.pdf_extractor import PDFExtractor
from src.pipeline import ExtractionPipeline
from src.redcap_service import REDCapService
from src.sync_service import PatientSyncResult, RedCapSyncService, SyncSummaryReport

load_dotenv()

# Optional Rich formatting if available
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import print as rprint

    HAS_RICH = True
except ImportError:
    HAS_RICH = False


def setup_logger(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )


def print_banner():
    if HAS_RICH:
        console = Console()
        console.print(
            Panel.fit(
                "[bold cyan]SenoNetwork - Estrattore Referti Medici con IA Locale & REDCap[/bold cyan]\n"
                "[dim]Estrazione strutturata di dati clinici guidata da metadata REDCap[/dim]",
                border_style="cyan"
            )
        )
    else:
        print("=" * 70)
        print(" SenoNetwork - Estrattore Referti Medici con IA Locale & REDCap")
        print("=" * 70)


def display_results_summary(results: List[ExtractionResult]):
    """Prints a clear summary table of extraction results."""
    success_count = sum(1 for r in results if r.status == "success")
    error_count = sum(1 for r in results if r.status != "success")

    if HAS_RICH:
        console = Console()
        table = Table(title="Riepilogo Estrazione Dati Referti Medici")
        table.add_column("PDF / Referto", style="cyan")
        table.add_column("Record ID", style="magenta")
        table.add_column("Stato", style="bold")
        table.add_column("Campi Estratti", justify="right", style="green")
        table.add_column("Tempo (s)", justify="right")

        for r in results:
            status_style = "[green]OK[/green]" if r.status == "success" else f"[red]ERRORE: {r.error_message}[/red]"
            fields_count = len(
                [k for k, v in r.redcap_record.items() if v not in (None, "", "0")]) if r.redcap_record else 0
            table.add_row(
                Path(r.pdf_path).name,
                r.record_id or "-",
                status_style,
                str(fields_count),
                f"{r.extraction_duration_sec:.2f}"
            )
        console.print(table)
        console.print(
            f"\nTotale elaborati: [bold]{len(results)}[/bold] (Successi: [green]{success_count}[/green], Errori: [red]{error_count}[/red])\n")
    else:
        print("\n--- RIEPILOGO ESTRAZIONE ---")
        for r in results:
            fields_count = len(
                [k for k, v in r.redcap_record.items() if v not in (None, "", "0")]) if r.redcap_record else 0
            status = "OK" if r.status == "success" else f"ERRORE ({r.error_message})"
            print(
                f"- {Path(r.pdf_path).name} | ID: {r.record_id} | {status} | Campi: {fields_count} | {r.extraction_duration_sec}s")
        print(f"Totale: {len(results)} (Successi: {success_count}, Errori: {error_count})\n")


def display_sync_summary(report: SyncSummaryReport):
    """Prints a clear summary table of audit and sync results."""
    mode_str = "[yellow](SIMULAZIONE / DRY-RUN)[/yellow]" if report.dry_run else "[green](LIVE APPLIED)[/green]"

    if HAS_RICH:
        console = Console()
        console.print(f"\n[bold cyan]=== RIEPILOGO AUDIT & SINCRONIZZAZIONE REDCAP {mode_str} ===[/bold cyan]")
        table = Table(title="Pazienti Verificati su REDCap")
        table.add_column("Paziente ID", style="cyan")
        table.add_column("Nominativo", style="white")
        table.add_column("Stato", style="bold")
        table.add_column("Form Aggiornati", style="green")
        table.add_column("Campi Arricchiti", justify="right", style="magenta")
        table.add_column("Nuovi Referti", justify="right")
        table.add_column("Tempo (s)", justify="right")

        for r in report.results:
            if r.status == "updated":
                status_style = "[green]AGGIORNATO[/green]"
            elif r.status == "up_to_date":
                status_style = "[dim]INVARIATO[/dim]"
            elif r.status == "no_reports":
                status_style = "[yellow]NESSUN REFERTO[/yellow]"
            else:
                status_style = f"[red]ERRORE: {r.error or 'Sconosciuto'}[/red]"

            patient_name = f"{r.name or ''} {r.surname or ''}".strip() or "-"
            forms_str = ", ".join(r.forms_updated) if r.forms_updated else "-"

            table.add_row(
                r.patient_id,
                patient_name,
                status_style,
                forms_str,
                str(r.field_updates_count),
                str(r.new_reports_count),
                f"{r.duration_sec:.2f}"
            )
        console.print(table)
        console.print(
            f"\nTotale ispezionati: [bold]{report.patients_inspected}[/bold] | "
            f"Aggiornati: [green bold]{report.patients_updated}[/green bold] | "
            f"Campi arricchiti: [magenta bold]{report.total_fields_updated}[/magenta bold] | "
            f"Durata: [bold]{report.duration_sec:.2f}s[/bold]\n"
        )
    else:
        print(f"\n--- RIEPILOGO AUDIT & SINCRONIZZAZIONE REDCAP {'(DRY-RUN)' if report.dry_run else '(LIVE)'} ---")
        for r in report.results:
            patient_name = f"{r.name or ''} {r.surname or ''}".strip() or "-"
            forms_str = ", ".join(r.forms_updated) if r.forms_updated else "-"
            print(
                f"- ID: {r.patient_id} ({patient_name}) | Stato: {r.status.upper()} | "
                f"Form: {forms_str} | Campi: {r.field_updates_count} | Durata: {r.duration_sec}s"
            )
        print(
            f"\nTotale ispezionati: {report.patients_inspected} | "
            f"Aggiornati: {report.patients_updated} | Campi arricchiti: {report.total_fields_updated} | "
            f"Durata: {report.duration_sec:.2f}s\n"
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analizza referti medici in PDF con IA locale (Ollama) ed esporta dati strutturati per REDCap."
    )
    parser.add_argument(
        "--pdf", "-p",
        default=None,
        help="Percorso a un singolo file PDF o a una cartella contenente più PDF di referti medici."
    )
    parser.add_argument(
        "--patient-id", "--patient",
        default=None,
        dest="patient_id",
        help="ID Paziente da elaborare end-to-end: estrae PDF da API .NET -> elabora con IA -> carica su REDCap."
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Nome del paziente per ricerca anagrafica su Priamo .NET."
    )
    parser.add_argument(
        "--surname",
        default=None,
        help="Cognome del paziente per ricerca anagrafica su Priamo .NET."
    )
    parser.add_argument(
        "--dob",
        default=None,
        help="Data di nascita (AAAA-MM-GG) per ricerca su Priamo .NET."
    )
    parser.add_argument(
        "--excel",
        default=None,
        help="Percorso al file Excel degli interventi (es. interventi.xls) per elaborare una coorte di pazienti."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Numero massimo di pazienti da elaborare dal file Excel."
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Avvia il server HTTP REST (FastAPI) per integrazione diretta con l'applicazione .NET."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Porta per il server HTTP (default: 8000)."
    )
    parser.add_argument(
        "--metadata-file", "-m",
        default=None,
        help="Percorso a un file locale con i metadata REDCap (.csv o .json Data Dictionary). Se omesso, viene usata l'API REDCap."
    )
    parser.add_argument(
        "--form", "-f",
        default=None,
        help="Nome dello strumento/form REDCap da estrarre (es. 'referto_istologico', 'ecografia'). Se omesso, estrae tutti i campi."
    )
    parser.add_argument(
        "--redcap-url",
        default=None,
        help="URL dell'endpoint API REDCap (es. https://redcap.example.org/api/). Può essere impostato via variabile d'ambiente o file .env."
    )
    parser.add_argument(
        "--redcap-token",
        default=None,
        help="Token API del progetto REDCap. Può essere impostato via variabile d'ambiente o file .env."
    )
    parser.add_argument(
        "--backend",
        choices=["llama_cpp", "ollama"],
        default=None,
        help="Backend IA locale da utilizzare: 'llama_cpp' (default con supporto RPC multi-GPU) o 'ollama'."
    )
    parser.add_argument(
        "--llama-url",
        default=None,
        help="URL del server master llama.cpp distribuito (default: preso da .env o http://localhost:8080)."
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Nome del modello IA locale su Ollama (default: preso da .env o llama3.2:3b)."
    )
    parser.add_argument(
        "--ollama-url",
        default=None,
        help="URL del server Ollama locale (default: preso da .env o http://localhost:11434)."
    )
    parser.add_argument(
        "--output-json", "-o",
        default="./data/output/extracted_records.json",
        help="Percorso del file JSON in cui salvare i risultati dell'estrazione (default: ./data/output/extracted_records.json)."
    )
    parser.add_argument(
        "--output-csv", "-c",
        default="./data/output/redcap_import.csv",
        help="Percorso del file CSV pronto per il Data Import Tool di REDCap (default: ./data/output/redcap_import.csv)."
    )
    parser.add_argument(
        "--import-to-redcap",
        action="store_true",
        help="Importa direttamente i record estratti nel progetto REDCap tramite API al termine dell'elaborazione."
    )
    parser.add_argument(
        "--sync-redcap", "--sync",
        action="store_true",
        help="Controlla i pazienti presenti su REDCap, rilegge i referti (es. Priamo o locali) e aggiorna i dati clinici mancanti o nuovi (es. anatomia patologica post-operatoria)."
    )
    parser.add_argument(
        "--audit-redcap", "--audit",
        action="store_true",
        help="Esegue l'audit completo di qualità dati su REDCap, rilevando incongruenze cliniche, mancanze e producendo un report generale."
    )
    parser.add_argument(
        "--verify-priamo",
        action="store_true",
        help="Verifica l'esistenza dei pazienti su Priamo (HIS Ospedale Valduce) interrogando l'API GetAnagrafica."
    )
    parser.add_argument(
        "--audit-min-severity",
        choices=["critical", "warning", "info"],
        default="info",
        help="Soglia minima di gravità per le anomalie nell'audit (critical, warning, info)."
    )
    parser.add_argument(
        "--list-records",
        action="store_true",
        help="Elenca tutti i record presenti in REDCap mostrando anagrafica e avanzamento form."
    )
    parser.add_argument(
        "--delete-record",
        default=None,
        help="Elimina un singolo record da REDCap specificando il suo record_id (es. --delete-record 101)."
    )
    parser.add_argument(
        "--delete-all-records",
        action="store_true",
        help="Elimina TUTTI i record attualmente presenti nel progetto REDCap (richiede --confirm)."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Conferma esplicita richiesta per operazioni irreversibili (es. --delete-all-records)."
    )
    parser.add_argument(
        "--clean-local-state",
        action="store_true",
        help="Rimuove anche i file di stato locali (data/state/{id}_state.json) dei record eliminati."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Esegue il controllo in modalità simulazione: visualizza i campi che verrebbero aggiornati senza scrivere su REDCap."
    )
    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Forza la rilettura e l'estrazione da tutti i referti PDF anche se già registrati nello stato di idempotenza."
    )
    parser.add_argument(
        "--overwrite-mode",
        choices=["fill_missing", "overwrite"],
        default=None,
        help="Strategia di aggiornamento: 'fill_missing' (default: popola solo campi vuoti/nuovi) o 'overwrite' (sovrascrive anche campi esistenti)."
    )
    parser.add_argument(
        "--sync-interval",
        type=int,
        default=None,
        help="Intervallo in minuti per eseguire il controllo periodico in loop continuo (es. --sync-interval 60)."
    )
    parser.add_argument(
        "--all-patients",
        action="store_true",
        help="Nel sync, esamina tutti i pazienti presenti in REDCap e non solo quelli con form incompleti."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Abilita log dettagliati di debug."
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    setup_logger(args.verbose)
    print_banner()

    # Load configuration
    overrides = {}
    if args.backend:
        overrides["ai_backend"] = args.backend
    if args.llama_url:
        overrides["llama_cpp_base_url"] = args.llama_url
    if args.redcap_url:
        overrides["redcap_api_url"] = args.redcap_url
    if args.redcap_token:
        overrides["redcap_api_token"] = args.redcap_token
    if args.model:
        overrides["ollama_model"] = args.model
    if args.ollama_url:
        overrides["ollama_base_url"] = args.ollama_url
    if args.form:
        overrides["default_form_name"] = args.form

    settings = Settings(**overrides)

    # Validate REDCap configuration source
    if not args.metadata_file and (not settings.redcap_api_url or not settings.redcap_api_token):
        print(
            "\n[ERRORE] È necessario fornire o '--metadata-file <path.csv/.json>' oppure configurare '--redcap-url' e '--redcap-token'.",
            file=sys.stderr)
        print("Puoi anche impostare REDCAP_API_URL e REDCAP_API_TOKEN in un file .env\n", file=sys.stderr)
        sys.exit(1)

    # 1. Mode: Serve FastAPI
    if args.serve:
        print(f"-> Avvio microservizio HTTP FastAPI per integrazione .NET sulla porta {args.port}...")
        from src.api_server import run_server
        run_server(port=args.port)
        return

    # 2. Mode: REDCap Data Quality & Inconsistency Audit
    if args.audit_redcap:
        from src.audit_service import RedCapDataAuditor
        from scripts.audit_redcap_data import display_rich_dashboard, display_plain_summary

        auditor = RedCapDataAuditor(settings=settings)
        print("-> Avvio audit qualità dati e rilevamento incongruenze su REDCap...")
        report = auditor.run_audit(patient_id=args.patient_id)

        if HAS_RICH:
            display_rich_dashboard(report, min_severity=args.audit_min_severity, summary_only=False)
        else:
            display_plain_summary(report, min_severity=args.audit_min_severity, summary_only=False)

        paths = auditor.export_reports(report)
        print(f"\n[OK] Report esportati:")
        print(f"  • Markdown: {paths['markdown']}")
        print(f"  • JSON:     {paths['json']}\n")
        return

    # 3. Mode: List REDCap records
    if args.list_records:
        from scripts.list_redcap_records import display_plain_table, display_rich_table
        redcap_svc = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)
        print("-> Recupero lista completa record da REDCap...")
        records = redcap_svc.list_all_records(include_demographics=True, include_completion_status=True)
        if args.limit:
            records = records[:args.limit]
        if HAS_RICH:
            display_rich_table(records)
        else:
            display_plain_table(records)
        return

    # 4. Mode: Delete single record from REDCap
    if args.delete_record:
        from src.redcap_service import delete_redcap_records
        print(f"-> Richiesta eliminazione record REDCap: {args.delete_record}...")
        count = delete_redcap_records(
            record_id=args.delete_record,
            api_url=settings.redcap_api_url,
            api_token=settings.redcap_api_token,
            clean_local_state=args.clean_local_state
        )
        if count > 0:
            print(f"[OK] Record '{args.delete_record}' eliminato con successo da REDCap.")
        else:
            print(f"[WARN] Nessun record eliminato per ID '{args.delete_record}'.", file=sys.stderr)
        return

    # 5. Mode: Delete ALL records from REDCap
    if args.delete_all_records:
        from src.redcap_service import delete_redcap_records
        if not args.confirm:
            print(
                "\n[ERRORE DI SICUREZZA] L'eliminazione massiva di tutti i record su REDCap richiede conferma esplicita.",
                file=sys.stderr
            )
            print("Per procedere, riesegui il comando aggiungendo il flag '--confirm':", file=sys.stderr)
            print("  uv run python -m src.cli --delete-all-records --confirm\n", file=sys.stderr)
            sys.exit(1)

        print("-> [ATTENZIONE] Avvio eliminazione di TUTTI i record presenti su REDCap...")
        count = delete_redcap_records(
            all_records=True,
            confirm=True,
            api_url=settings.redcap_api_url,
            api_token=settings.redcap_api_token,
            clean_local_state=args.clean_local_state
        )
        print(f"[OK] Eliminazione completata: eliminati con successo {count} record da REDCap.")
        return

    # 6. Mode: Verify patients on Priamo
    if args.verify_priamo:
        from src.dotnet_client import DotNetApiClient
        from scripts.verify_patients_in_priamo import display_results_dashboard

        dotnet = DotNetApiClient(settings=settings)
        if args.patient_id or (args.name and args.surname):
            p_name = args.name or ""
            p_surname = args.surname or ""
            p_dob = args.dob or ""
            print(f"-> Verifica esistenza paziente singolo su Priamo: {p_name} {p_surname} ({p_dob})...")
            res = dotnet.verify_patient_in_priamo(name=p_name, surname=p_surname, date_of_birth=p_dob)
            if res:
                print(f"[OK] Paziente TROVATO su Priamo: ID={res.patient_id}, CF={res.codice_fiscale or 'N.D.'}")
            else:
                print(f"[WARN] Paziente non trovato su Priamo.")
            return

        target_excel = args.excel or settings.patients_excel_path
        if not target_excel or not Path(target_excel).exists():
            candidate_shared = Path("data/positivi_2026.xls")
            if candidate_shared.exists():
                target_excel = candidate_shared
            else:
                print("[ERRORE] Nessun file pazienti specificato (--excel <path>).", file=sys.stderr)
                sys.exit(1)

        print(f"-> Caricamento pazienti da {target_excel} per verifica su Priamo...")
        cohort = DotNetApiClient.load_patients_from_excel(target_excel)
        if args.limit:
            cohort = cohort[:args.limit]

        print(f"-> Verifica di {len(cohort)} pazienti su Priamo...")
        results = dotnet.verify_patients_cohort(cohort, max_workers=4)
        if HAS_RICH:
            display_results_dashboard(results, title="Esito Verifica Priamo")
        else:
            for r in results:
                print(f"- {r.get('surname')} {r.get('name')} -> {r.get('status')} (ID: {r.get('priamo_id') or '-'})")
        return

    # 4. Mode: REDCap Periodic Audit & Sync
    if args.sync_redcap or args.sync_interval:
        sync_svc = RedCapSyncService(settings=settings)
        target_forms = [args.form] if args.form else None

        if args.sync_interval:
            interval_sec = args.sync_interval * 60
            print(f"-> Avvio loop di sincronizzazione periodica REDCap ogni {args.sync_interval} minuti ({interval_sec}s)...")
            sync_svc.run_periodic(
                interval_seconds=interval_sec,
                target_forms=target_forms,
                auto_upload=not args.dry_run,
                dry_run=args.dry_run
            )
            return

        if args.patient_id:
            print(f"-> Esecuzione controllo e aggiornamento referti per paziente: [bold cyan]{args.patient_id}[/bold cyan]")
            res = sync_svc.check_and_update_patient(
                patient_id=args.patient_id,
                name=args.name,
                surname=args.surname,
                dob=args.dob,
                target_forms=target_forms,
                auto_upload=not args.dry_run,
                dry_run=args.dry_run,
                force_reprocess=args.force_reprocess,
                overwrite_mode=args.overwrite_mode
            )
            report = SyncSummaryReport(
                timestamp=datetime.now().isoformat(),
                patients_inspected=1,
                patients_updated=1 if res.status == "updated" else 0,
                total_fields_updated=res.field_updates_count,
                forms_affected=res.forms_updated,
                results=[res],
                duration_sec=res.duration_sec,
                dry_run=args.dry_run
            )
            display_sync_summary(report)
            return

        print(f"-> Avvio audit & sincronizzazione su REDCap (Target forms: {target_forms or 'Tutti i form incompleti'})...")
        report = sync_svc.sync_all(
            target_forms=target_forms,
            auto_upload=not args.dry_run,
            dry_run=args.dry_run,
            force_reprocess=args.force_reprocess,
            limit=args.limit,
            only_incomplete=not args.all_patients
        )
        display_sync_summary(report)
        return

    # 3. Mode: Batch Excel Cohort (.NET Priamo -> IA -> REDCap)
    if args.excel:
        from src.dotnet_client import DotNetApiClient
        from src.patient_pipeline import PatientPipeline
        print(f"-> Caricamento coorte pazienti dal registro Excel: [bold cyan]{args.excel}[/bold cyan]")
        patients = DotNetApiClient.load_patients_from_excel(args.excel)
        if args.limit:
            patients = patients[:args.limit]
            print(f"-> Limitazione elaborazione ai primi {args.limit} pazienti.")

        pipeline = PatientPipeline(settings=settings)
        print(f"-> Avvio pipeline su {len(patients)} pazienti...")

        results = []
        for idx, p in enumerate(patients, 1):
            # Se il paziente esiste già su REDCap, mantieni il record_id nativo di REDCap
            target_id = p["patient_id"]
            if settings.redcap_api_url and settings.redcap_api_token:
                matched_id = pipeline.redcap_service.find_record_id_by_demographics(
                    p["name"], p["surname"], p["date_of_birth"]
                )
                if matched_id:
                    target_id = matched_id
                    print(f"-> Mantenuto record_id nativo di REDCap: '{matched_id}' per {p['name']} {p['surname']}")

            print(
                f"\n[{idx}/{len(patients)}] Elaborazione: {p['name']} {p['surname']} ({p['date_of_birth']}) -> ID: {target_id}")
            try:
                res = pipeline.process_patient(
                    patient_id=target_id,
                    name=p["name"],
                    surname=p["surname"],
                    date_of_birth=p["date_of_birth"],
                    auto_upload=args.import_to_redcap
                )
                results.append(res)
            except Exception as e:
                print(f"[ERRORE] Elaborazione fallita per {target_id}: {e}", file=sys.stderr)

        print("\n=== RIEPILOGO BATCH COORTE EXCEL ===")
        for r in results:
            print(
                f"- Paziente {r.patient_id}: {r.successful_pdfs}/{r.total_pdfs} PDF elaborati | Form: {', '.join(r.forms_populated)} | REDCap: {r.redcap_upload_status}")
        return

    # 4. Mode: End-to-end Patient Pipeline (.NET Priamo -> IA -> REDCap)
    if args.patient_id or (args.name and args.surname):
        from src.patient_pipeline import PatientPipeline
        pipeline = PatientPipeline(settings=settings)

        # Se non è specificato patient_id, cerca se esiste già su REDCap per mantenerne il record_id nativo
        p_id = args.patient_id
        if not p_id and args.name and args.surname and settings.redcap_api_url and settings.redcap_api_token:
            matched_id = pipeline.redcap_service.find_record_id_by_demographics(
                args.name, args.surname, args.dob
            )
            if matched_id:
                p_id = matched_id
                print(f"-> Mantenuto record_id nativo di REDCap: [bold cyan]{p_id}[/bold cyan]")

        if not p_id:
            p_id = f"{args.name}-{args.surname}-{args.dob or 'unknown'}".lower().replace(" ", "-")

        print(f"-> Avvio pipeline end-to-end per paziente: [bold cyan]{p_id}[/bold cyan]")
        # Check if local PDF was also specified
        pdf_paths = None
        if args.pdf:
            pdf_paths = PDFExtractor.find_pdf_files(args.pdf)

        res = pipeline.process_patient(
            patient_id=p_id,
            name=args.name,
            surname=args.surname,
            date_of_birth=args.dob,
            pdf_paths=pdf_paths,
            auto_upload=args.import_to_redcap
        )
        print(f"\n-> Risultato Pipeline Paziente {p_id}:")
        print(f"   * PDF totali elaborati: {res.total_pdfs} (Successo: {res.successful_pdfs})")
        print(f"   * Form REDCap popolati: {', '.join(res.forms_populated) or 'Nessuno'}")
        print(f"   * Stato caricamento REDCap: {res.redcap_upload_status} ({res.redcap_upload_response})")
        print(f"   * Tempo totale: {res.total_duration_sec}s")
        return

    # 4. Mode: Direct PDF Batch
    if not args.pdf:
        print(
            "\n[ERRORE] Specificare '--patient-id <ID>', '--name/--surname', '--excel <file.xls>', '--pdf <percorso>', oppure '--serve'.",
            file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    # Initialize services
    redcap_svc = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)
    ai_extractor = get_ai_extractor(settings)

    # Check AI server connection
    if not ai_extractor.is_available():
        if isinstance(ai_extractor, LlamaCppExtractor):
            print(f"\n[AVVISO] Il server llama.cpp non risponde all'indirizzo {settings.llama_cpp_base_url}.", file=sys.stderr)
            print("Assicurati che llama-server sia avviato (es. con il cluster RPC su 2 schede).\n", file=sys.stderr)
        else:
            print(f"\n[AVVISO] Il server Ollama non risponde all'indirizzo {settings.ollama_base_url}.", file=sys.stderr)
            print("Assicurati che Ollama sia in esecuzione ('ollama serve' o servizio attivo).\n", file=sys.stderr)

    pipeline = ExtractionPipeline(
        settings=settings,
        redcap_service=redcap_svc,
        ai_extractor=ai_extractor,
        metadata_file=args.metadata_file,
        target_form=args.form
    )

    print(f"-> Caricamento metadata REDCap (Sorgente: {args.metadata_file or settings.redcap_api_url})...")
    fields = pipeline.initialize_metadata()
    print(f"-> Trovati {len(fields)} campi da estrarre.")

    # Find PDFs
    pdf_files = PDFExtractor.find_pdf_files(args.pdf)
    print(f"-> Trovati {len(pdf_files)} file PDF da elaborare in '{args.pdf}'.")

    # Run processing
    def progress(idx: int, total: int, current_path: Path):
        print(f"[{idx}/{total}] Elaborazione referto: {current_path.name}...")

    results = pipeline.process_batch(pdf_files, progress_callback=progress)

    # Export
    json_path = pipeline.export_to_json(results, args.output_json)
    csv_path = pipeline.export_to_redcap_csv(results, args.output_csv)
    print(f"\n-> Dati strutturati salvati in JSON: {json_path}")
    print(f"-> File CSV per importazione REDCap salvato in: {csv_path}")

    # Optional direct import to REDCap
    if args.import_to_redcap:
        if not settings.redcap_api_url or not settings.redcap_api_token:
            print("[ERRORE] Impossibile importare su REDCap: URL o Token API mancanti.", file=sys.stderr)
        else:
            print("-> Invio dei record estratti a REDCap tramite API...")
            records_to_upload = [r.redcap_record for r in results if r.status == "success" and r.redcap_record]
            try:
                res = redcap_svc.import_records_to_api(records_to_upload)
                print(f"-> Importazione completata con successo! Risposta REDCap: {res}")
            except Exception as e:
                print(f"[ERRORE] Fallimento importazione REDCap: {e}", file=sys.stderr)

    # Display summary table
    display_results_summary(results)


if __name__ == "__main__":
    main()
