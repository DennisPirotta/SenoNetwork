#!/usr/bin/env python3
"""Script per elencare tutti i record presenti nel progetto REDCap.

Esempi di utilizzo:
  # Visualizza tabella di tutti i pazienti con anagrafica e avanzamento form
  uv run python scripts/list_redcap_records.py

  # Esporta tutti i record in formato JSON o CSV
  uv run python scripts/list_redcap_records.py --format json --output data/output/records_redcap.json
  uv run python scripts/list_redcap_records.py --format csv --output data/output/records_redcap.csv

  # Filtra solo i record con istologico post-operatorio completato
  uv run python scripts/list_redcap_records.py --filter-logic "[anatomia_patologica_complete] = '2'"
"""

import argparse
import csv
import json
import logging
from pathlib import Path
import sys
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Settings
from src.redcap_service import REDCapService, list_redcap_records

load_dotenv()

# Rich formatting
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("REDCapRecordLister")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Elenca tutti i record presenti su REDCap con anagrafica e stato di avanzamento."
    )
    parser.add_argument(
        "--format", "-f",
        choices=["table", "json", "csv"],
        default="table",
        help="Formato di visualizzazione / esportazione dei dati (default: table)."
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Percorso file in cui esportare i dati (.json o .csv)."
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Numero massimo di record da mostrare o esportare."
    )
    parser.add_argument(
        "--fields",
        default=None,
        help="Campi specifici separati da virgola da estrarre (es. 'record_id,name,surname,pt,pn,grade')."
    )
    parser.add_argument(
        "--filter-logic",
        default=None,
        help="Espressione di filtro REDCap (es. \"[anatomia_patologica_complete] = '2'\")."
    )
    parser.add_argument(
        "--form",
        default=None,
        help="Nome dello strumento/form specifico da esportare (es. 'anatomia_patologica')."
    )
    return parser.parse_args()


def display_rich_table(records: list):
    """Prints a beautiful table with patient details and completion badges."""
    console = Console()
    total = len(records)

    console.print(
        Panel.fit(
            f"[bold cyan]Elenco Record REDCap - SenoNetwork[/bold cyan]\n"
            f"[dim]Totale record trovati: [bold white]{total}[/bold white][/dim]",
            border_style="cyan"
        )
    )

    table = Table(title="Pazienti Registrati in REDCap")
    table.add_column("Record ID", style="bold cyan", no_wrap=True)
    table.add_column("Nominativo", style="white")
    table.add_column("Data Nascita", style="dim")
    table.add_column("Dati Paz.", justify="center")
    table.add_column("Radio.", justify="center")
    table.add_column("Biopsia", justify="center")
    table.add_column("Chirurgia", justify="center")
    table.add_column("Istol. Post-Op", justify="center")
    table.add_column("Chemio", justify="center")
    table.add_column("Radio", justify="center")
    table.add_column("Recidiva", justify="center")
    table.add_column("Form OK", justify="right", style="bold green")

    def _badge(val: str) -> str:
        s = str(val or "").strip()
        if s == "2":
            return "[green]●[/green]"
        elif s == "1":
            return "[yellow]◐[/yellow]"
        elif s == "0":
            return "[red]○[/red]"
        return "[dim]-[/dim]"

    forms_keys = [
        "dati_del_paziente_complete",
        "radiologia_complete",
        "anatomia_patologica_preoperatoria_complete",
        "struttura_della_lesione_complete",
        "anatomia_patologica_complete",
        "chemioterapia_complete",
        "radioterapia_complete",
        "recidiva_complete"
    ]

    for r in records:
        pid = str(r.get("record_id", ""))
        name = str(r.get("name", "")).strip()
        surname = str(r.get("surname", "")).strip()
        fullname = f"{name} {surname}".strip() or "[dim]N/D[/dim]"
        dob = str(r.get("date_of_birth", "")).strip() or "-"

        badges = [_badge(r.get(k)) for k in forms_keys]
        completed_count = sum(1 for k in forms_keys if str(r.get(k) or "").strip() == "2")

        table.add_row(
            pid,
            fullname,
            dob,
            *badges,
            f"{completed_count}/8"
        )

    console.print(table)
    console.print("[dim]Legenda: [green]●[/green] Completato ('2') | [yellow]◐[/yellow] Non verificato ('1') | [red]○[/red] Incompleto ('0') | - Non iniziato[/dim]\n")


def display_plain_table(records: list):
    """Plain text fallback."""
    print("=" * 80)
    print(f" RECORD REDCAP ({len(records)} TOTALI) ")
    print("=" * 80)
    print(f"{'RECORD ID':<25} | {'NOMINATIVO':<30} | {'DATA NASCITA':<12} | {'FORM OK'}")
    print("-" * 80)
    for r in records:
        pid = str(r.get("record_id", ""))
        name = str(r.get("name", "")).strip()
        surname = str(r.get("surname", "")).strip()
        fullname = f"{name} {surname}".strip() or "N/D"
        dob = str(r.get("date_of_birth", "")).strip() or "-"
        comp = sum(1 for k in r if k.endswith("_complete") and str(r.get(k) or "").strip() == "2")
        print(f"{pid:<25} | {fullname:<30} | {dob:<12} | {comp}/8")
    print("-" * 80)


def main():
    args = parse_args()
    settings = Settings()

    if not settings.redcap_api_url or not settings.redcap_api_token:
        print("[ERRORE] REDCAP_API_URL o REDCAP_API_TOKEN non impostati in .env!", file=sys.stderr)
        sys.exit(1)

    fields_list = [f.strip() for f in args.fields.split(",") if f.strip()] if args.fields else None
    forms_list = [args.form] if args.form else None

    service = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)

    # Recupera i record da REDCap
    records = service.list_all_records(
        fields=fields_list,
        forms=forms_list,
        include_demographics=True,
        include_completion_status=True,
        filter_logic=args.filter_logic
    )

    if args.limit:
        records = records[:args.limit]

    # Output format
    if args.format == "json":
        out_content = json.dumps(records, indent=2, ensure_ascii=False)
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(out_content, encoding="utf-8")
            print(f"[OK] Esportati {len(records)} record in JSON: {out_path}")
        else:
            print(out_content)
        return

    if args.format == "csv":
        if not records:
            print("[AVVISO] Nessun record trovato da esportare in CSV.")
            return

        all_keys = []
        for r in records:
            for k in r.keys():
                if k not in all_keys:
                    all_keys.append(k)

        out_dest = Path(args.output) if args.output else Path("data/output/redcap_records_list.csv")
        out_dest.parent.mkdir(parents=True, exist_ok=True)
        with open(out_dest, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys)
            writer.writeheader()
            for r in records:
                writer.writerow(r)
        print(f"[OK] Esportati {len(records)} record in CSV: {out_dest}")
        return

    # Default: Rich Table
    if HAS_RICH:
        display_rich_table(records)
    else:
        display_plain_table(records)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        print(f"[OK] Copia JSON salvata in: {out_path}")


if __name__ == "__main__":
    main()
