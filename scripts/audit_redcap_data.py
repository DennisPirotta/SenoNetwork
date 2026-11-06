#!/usr/bin/env python3
"""Script per il controllo dello stato di inserimento dei record su REDCap,
rilevamento di incongruenze/mancanze cliniche e generazione di un report generale di qualità dati.

Esempi di utilizzo:
  uv run python scripts/audit_redcap_data.py
  uv run python scripts/audit_redcap_data.py --min-severity critical
  uv run python scripts/audit_redcap_data.py --patient-id mario-rossi-1960-01-01
  uv run python scripts/audit_redcap_data.py --summary-only
"""

import argparse
import json
import logging
from pathlib import Path
import sys
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audit_service import (
    IssueSeverity,
    RedCapAuditReport,
    RedCapDataAuditor
)
from src.config import Settings
from src.redcap_service import REDCapService

load_dotenv()

# Rich formatting
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import print as rprint
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("REDCapAuditor")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Audit qualità dati REDCap, rilevamento incongruenze cliniche e report generale."
    )
    parser.add_argument(
        "--patient-id", "-p",
        default=None,
        help="ID Paziente specifico da controllare anziché l'intera coorte."
    )
    parser.add_argument(
        "--min-severity", "-s",
        choices=["critical", "warning", "info"],
        default="info",
        help="Livello minimo di gravità delle incongruenze da visualizzare (default: 'info')."
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="data/output/reports",
        help="Cartella in cui salvare i report Markdown e JSON (default: data/output/reports)."
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="Non salvare i file di report su disco (mostra solo l'output a terminale)."
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Mostra solo la panoramica statistica aggregata senza l'elenco delle singole anomalie."
    )
    parser.add_argument(
        "--local-records",
        default=None,
        help="Percorso a un file JSON locale contenente i record esportati da REDCap (per test/audit offline)."
    )
    return parser.parse_args()


def display_rich_dashboard(report: RedCapAuditReport, min_severity: str, summary_only: bool):
    """Renders a beautiful clinical data quality dashboard in terminal."""
    console = Console()

    # 1. Header Banner
    score_color = "green" if report.global_completeness_score >= 80 else (
        "yellow" if report.global_completeness_score >= 50 else "red"
    )
    banner_text = (
        f"[bold white]REDCap Data Quality & Inconsistency Audit Dashboard[/bold white]\n"
        f"[dim]Progetto: SenoNetwork (ID: 20) | Generato: {report.timestamp}[/dim]\n\n"
        f"Pazienti Analizzati: [bold cyan]{report.total_records}[/bold cyan] | "
        f"Indice di Completezza Globale: [bold {score_color}]{report.global_completeness_score}%[/bold {score_color}] | "
        f"Incongruenze Rilevate: [bold red]{report.critical_issues_count} Critiche[/bold red], "
        f"[bold yellow]{report.warning_issues_count} Avvisi[/bold yellow], "
        f"[bold cyan]{report.info_issues_count} Info[/bold cyan]"
    )
    console.print(Panel(banner_text, border_style="cyan", title="SenoNetwork Clinical Auditor"))

    # 2. Form Completeness Table
    table_forms = Table(title="1. Stato di Completamento Strumenti / Form REDCap")
    table_forms.add_column("Form REDCap", style="cyan")
    table_forms.add_column("Completati ('2')", justify="right", style="green")
    table_forms.add_column("Incompleti ('0'/'1')", justify="right", style="yellow")
    table_forms.add_column("Non Iniziati", justify="right", style="dim")
    table_forms.add_column("Tasso Completezza", justify="right", style="bold")

    for f in report.forms_stats:
        bar = "🟢" if f.completeness_pct >= 80 else ("🟡" if f.completeness_pct >= 50 else "🔴")
        non_comp = f.incomplete_count + f.unverified_count
        table_forms.add_row(
            f.form_name,
            str(f.completed_count),
            str(non_comp),
            str(f.empty_count),
            f"{bar} {f.completeness_pct:.1f}%"
        )
    console.print(table_forms)

    # 3. Clinical Profile Panel
    cp = report.clinical_profile
    clin_text = (
        f"• Pazienti Operati: [bold]{cp.patients_with_surgery}[/bold] / {cp.total_patients}\n"
        f"• Pazienti con Istologico Post-Operatorio: [bold]{cp.patients_with_postop_histology}[/bold] / {cp.total_patients}\n"
    )
    if cp.mean_age_at_diagnosis:
        clin_text += f"• Età media alla diagnosi: [bold]{cp.mean_age_at_diagnosis} anni[/bold]\n"
    if cp.pt_distribution:
        clin_text += f"• Distribuzione pT: {', '.join(f'pT{k}: {v}' for k, v in sorted(cp.pt_distribution.items()))}\n"
    if cp.pn_distribution:
        clin_text += f"• Distribuzione pN: {', '.join(f'pN{k}: {v}' for k, v in sorted(cp.pn_distribution.items()))}\n"
    if cp.grade_distribution:
        clin_text += f"• Grado istologico: {', '.join(f'G{k}: {v}' for k, v in sorted(cp.grade_distribution.items()))}\n"

    console.print(Panel(clin_text, title="2. Panoramica Clinica Coorte", border_style="blue"))

    # 4. Inconsistencies & Issues Table
    if not summary_only:
        sev_rank = {"critical": 3, "warning": 2, "info": 1}
        min_rank = sev_rank.get(min_severity.lower(), 1)

        filtered_issues = [
            i for i in report.issues
            if sev_rank.get(i.severity.value.lower(), 1) >= min_rank
        ]

        table_issues = Table(title=f"3. Dettaglio Incongruenze e Mancanze (Filtro >= {min_severity.upper()})")
        table_issues.add_column("Paziente ID", style="cyan")
        table_issues.add_column("Gravità", style="bold")
        table_issues.add_column("Categoria", style="magenta")
        table_issues.add_column("Descrizione Clinica / Errore")
        table_issues.add_column("Azione Suggerita", style="green")

        for iss in filtered_issues:
            if iss.severity == IssueSeverity.CRITICAL:
                sev_styled = "[bold red]CRITICAL[/bold red]"
            elif iss.severity == IssueSeverity.WARNING:
                sev_styled = "[bold yellow]WARNING[/bold yellow]"
            else:
                sev_styled = "[dim cyan]INFO[/dim cyan]"

            table_issues.add_row(
                iss.patient_id,
                sev_styled,
                iss.category.value,
                iss.description,
                iss.suggested_action
            )

        if filtered_issues:
            console.print(table_issues)
        else:
            console.print("\n[green]Nessuna anomalia rilevata con il filtro di gravità specificato.[/green]\n")


def display_plain_summary(report: RedCapAuditReport, min_severity: str, summary_only: bool):
    """Plain-text fallback display."""
    print("=" * 70)
    print(" SENONETWORK - AUDIT QUALITA E INCONGRUENZE DATI REDCAP ")
    print("=" * 70)
    print(f"Pazienti analizzati: {report.total_records}")
    print(f"Indice di completezza globale: {report.global_completeness_score}%")
    print(f"Anomalie totali: {report.total_issues_count} (Critiche: {report.critical_issues_count}, Warning: {report.warning_issues_count})\n")

    print("--- STATO DEI FORM REDCAP ---")
    for f in report.forms_stats:
        print(f"- {f.form_name}: {f.completeness_pct}% (Completati: {f.completed_count}/{f.total_records})")

    if not summary_only:
        print("\n--- DETTAGLIO ANOMALIE E INCONGRUENZE ---")
        for iss in report.issues:
            print(f"[{iss.severity.value}] {iss.patient_id} | {iss.category.value}: {iss.description}")
            print(f"  -> Azione: {iss.suggested_action}")


def main():
    args = parse_args()
    settings = Settings()

    auditor = RedCapDataAuditor(settings=settings)

    # Optional local records
    local_records = None
    if args.local_records:
        local_path = Path(args.local_records)
        if not local_path.exists():
            logger.error(f"File record locale non trovato: {local_path}")
            sys.exit(1)
        with open(local_path, "r", encoding="utf-8") as f:
            local_records = json.load(f)
            logger.info(f"Caricati {len(local_records)} record dal file locale: {local_path.name}")
    elif not settings.redcap_api_url or not settings.redcap_api_token:
        logger.error("REDCAP_API_URL o REDCAP_API_TOKEN non configurati in .env!")
        sys.exit(1)

    # Run audit
    report = auditor.run_audit(records=local_records, patient_id=args.patient_id)

    # Display results
    if HAS_RICH:
        display_rich_dashboard(report, min_severity=args.min_severity, summary_only=args.summary_only)
    else:
        display_plain_summary(report, min_severity=args.min_severity, summary_only=args.summary_only)

    # Export reports
    if not args.no_export:
        paths = auditor.export_reports(report, output_dir=args.output_dir)
        print(f"\n[OK] Report salvati:")
        print(f"  • Markdown: {paths['markdown']}")
        print(f"  • JSON:     {paths['json']}\n")


if __name__ == "__main__":
    main()
