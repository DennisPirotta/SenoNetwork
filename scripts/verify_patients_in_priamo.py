#!/usr/bin/env python3
"""Script per la verifica anagrafica dei pazienti su Priamo (HIS Ospedale Valduce).

Controlla che i pazienti estratti dal registro multidisciplinare (positivi_2026.xls)
o da file CSV esistano effettivamente in Priamo, arricchendo l'anagrafica con:
  - ID progressivo paziente in Priamo (progressivoPaz1 / idPaziente)
  - Codice Fiscale ufficiale dal MEF
  - Comune di nascita, sesso e recapito telefonico
  - Indicazione dei pazienti non censiti o con dati incongruenti

Esempi di utilizzo:
  # Verifica rapida sui primi 10 pazienti di positivi_2026.xls
  uv run python scripts/verify_patients_in_priamo.py --limit 10

  # Verifica di tutti i pazienti del file positivi_2026.xls con 6 thread paralleli
  uv run python scripts/verify_patients_in_priamo.py --workers 6

  # Mostra ed esporta solo i pazienti NON trovati in Priamo
  uv run python scripts/verify_patients_in_priamo.py --only-missing --output data/output/pazienti_non_trovati_priamo.csv

  # Verifica un singolo paziente da terminale
  uv run python scripts/verify_patients_in_priamo.py --name Loredana --surname Crapella --dob 1965-10-22
"""

import argparse
from datetime import date
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List
from dotenv import load_dotenv
import pandas as pd

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings
from src.priamo_client import PriamoClient, PriamoPatient
from scripts.extract_patients_from_shared_file import estrai_pazienti_univoci, Paziente

load_dotenv()

# Rich formatting
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("PriamoPatientVerifier")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Verifica l'esistenza dei pazienti in Priamo (HIS Valduce) tramite API REST GetAnagrafica."
    )
    parser.add_argument(
        "--input", "-i",
        default="data/positivi_2026.xls",
        help="Percorso file Excel (.xls/.xlsx) o CSV con l'anagrafica da verificare (default: data/positivi_2026.xls)."
    )
    parser.add_argument(
        "--output", "-o",
        default="data/output/priamo_verification_report.csv",
        help="Percorso in cui salvare il report di verifica (.csv o .json)."
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Numero massimo di pazienti da verificare (utile per test veloci)."
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=4,
        help="Numero di worker concorrenti per le interrogazioni HTTP (default: 4)."
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Mostra ed esporta solo i pazienti NON trovati in Priamo."
    )
    parser.add_argument(
        "--only-found",
        action="store_true",
        help="Mostra ed esporta solo i pazienti trovati e verificati con successo."
    )
    # Parametri per verifica paziente singolo
    parser.add_argument("--name", default=None, help="Nome per verifica singolo paziente.")
    parser.add_argument("--surname", default=None, help="Cognome per verifica singolo paziente.")
    parser.add_argument("--dob", default=None, help="Data di nascita (YYYY-MM-DD o DD/MM/YYYY) per singolo paziente.")
    return parser.parse_args()


def display_results_dashboard(results: List[Dict[str, Any]], title: str = "Verifica Anagrafica Pazienti su Priamo"):
    """Visualizza a terminale una dashboard dettagliata con Rich."""
    console = Console()
    total = len(results)
    found_count = sum(1 for r in results if r.get("status") == "found")
    not_found_count = sum(1 for r in results if r.get("status") == "not_found")
    incomplete_count = sum(1 for r in results if r.get("status") == "incomplete")
    error_count = sum(1 for r in results if r.get("status") == "error")

    found_pct = (found_count / total * 100) if total > 0 else 0.0

    console.print(Panel.fit(
        f"[bold cyan]{title}[/bold cyan]\n\n"
        f"Totale pazienti esaminati: [bold white]{total}[/bold white]\n"
        f"  • [bold green]Trovati su Priamo:[/bold green]   [bold white]{found_count}[/bold white] ([bold green]{found_pct:.1f}%[/bold green])\n"
        f"  • [bold red]Non trovati:[/bold red]           [bold white]{not_found_count}[/bold white]\n"
        f"  • [bold yellow]Dati incompleti:[/bold yellow]       [bold white]{incomplete_count}[/bold white]\n"
        f"  • [bold magenta]Errori connessione:[/bold magenta]    [bold white]{error_count}[/bold white]",
        border_style="cyan"
    ))

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Nominativo (Registro)", style="bold white")
    table.add_column("Data Nascita", justify="center")
    table.add_column("Stato Priamo", justify="center")
    table.add_column("Priamo ID", style="bold green", justify="center")
    table.add_column("Codice Fiscale", style="cyan")
    table.add_column("Comune Nascita", style="dim")
    table.add_column("Righe Excel", justify="center", style="dim")

    for idx, r in enumerate(results, 1):
        status = r.get("status", "")
        if status == "found":
            status_badge = "[bold green]TROVATO[/bold green]"
        elif status == "not_found":
            status_badge = "[bold red]NON TROVATO[/bold red]"
        elif status == "incomplete":
            status_badge = "[bold yellow]INCOMPLETO[/bold yellow]"
        else:
            status_badge = f"[bold magenta]{status.upper()}[/bold magenta]"

        nom = f"{r.get('surname', '')} {r.get('name', '')}".strip()
        rows_str = ", ".join(map(str, r.get("righe_excel", []))) if r.get("righe_excel") else "-"

        table.add_row(
            str(idx),
            nom,
            str(r.get("date_of_birth") or "N.D."),
            status_badge,
            str(r.get("priamo_id") or "-"),
            str(r.get("codice_fiscale") or "-"),
            str(r.get("comune_nascita") or "-"),
            rows_str
        )

    console.print(table)


def main():
    args = parse_args()
    settings = get_settings()

    if not settings.priamo_api_url or not settings.priamo_username or not settings.priamo_password:
        print("[ERRORE] Credenziali Priamo non configurate in .env (PRIAMO_API_URL, PRIAMO_USERNAME, PRIAMO_PASSWORD).", file=sys.stderr)
        sys.exit(1)

    client = PriamoClient(settings=settings)

    # 1. Caso singolo paziente da CLI
    if args.name and args.surname:
        if not args.dob:
            print("[ERRORE] Per verificare un paziente singolo è necessario specificare --dob (es. --dob 1965-05-12).", file=sys.stderr)
            sys.exit(1)
        print(f"-> Verifica singolo paziente: {args.name} {args.surname} ({args.dob})...")
        p = client.search_patient(
            name=args.name,
            surname=args.surname,
            date_of_birth=args.dob
        )
        if p:
            if HAS_RICH:
                rprint(f"[bold green]✔ Paziente TROVATO in Priamo![/bold green]")
                rprint(f"  • Priamo ID:       [bold cyan]{p.patient_id}[/bold cyan]")
                rprint(f"  • Codice Fiscale:  [bold white]{p.codice_fiscale or 'N.D.'}[/bold white]")
                rprint(f"  • Comune nascita:  {p.comune_nascita or 'N.D.'}")
                rprint(f"  • Sesso:           {p.sesso or 'N.D.'}")
                rprint(f"  • Telefono:        {p.telefono or 'N.D.'}")
            else:
                print(f"[OK] Paziente TROVATO: ID={p.patient_id}, CF={p.codice_fiscale}")
        else:
            if HAS_RICH:
                rprint(f"[bold red]✖ Paziente NON trovato in Priamo.[/bold red]")
            else:
                print(f"[WARN] Paziente non trovato su Priamo.")
        return

    # 2. Caricamento coorte da file di input
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERRORE] File di input non trovato: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"-> Caricamento anagrafica pazienti da: {input_path}...")
    if input_path.suffix.lower() in [".xls", ".xlsx"]:
        pazienti_objs = estrai_pazienti_univoci(input_path)
        cohort = [p.model_dump() for p in pazienti_objs]
    else:
        # File CSV
        df = pd.read_csv(input_path)
        cohort = df.to_dict(orient="records")

    if args.limit:
        cohort = cohort[:args.limit]
        print(f"-> Limitata verifica ai primi {args.limit} pazienti.")

    print(f"-> Avvio verifica di {len(cohort)} pazienti su Priamo (concorrenza: {args.workers} workers)...")

    # Esecuzione verifica concorrente
    results = client.verify_patient_cohort(patients=cohort, max_workers=args.workers)

    # Filtri opzionali per visualizzazione ed export
    filtered_results = results
    if args.only_missing:
        filtered_results = [r for r in results if r.get("status") == "not_found"]
    elif args.only_found:
        filtered_results = [r for r in results if r.get("status") == "found"]

    # Dashboard visuale
    if HAS_RICH:
        display_results_dashboard(filtered_results, title=f"Esito Controllo Anagrafica Priamo ({input_path.name})")
    else:
        print(f"\n--- RIEPILOGO VERIFICA PRIAMO ({len(filtered_results)} record) ---")
        for r in filtered_results:
            st = r.get("status")
            nom = f"{r.get('surname', '')} {r.get('name', '')}".strip()
            print(f"- {nom:<30} | {r.get('date_of_birth', '')} | {st:<10} | ID Priamo: {r.get('priamo_id') or '-'} | CF: {r.get('codice_fiscale') or '-'}")

    # Esportazione report su file
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if out_path.suffix.lower() == ".json":
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(filtered_results, f, indent=2, ensure_ascii=False)
            print(f"\n[OK] Report salvato in formato JSON: {out_path}")
        else:
            # Esportazione CSV
            rows_for_csv = []
            for r in filtered_results:
                rows_for_csv.append({
                    "patient_id": r.get("patient_id", ""),
                    "cognome": r.get("surname", ""),
                    "nome": r.get("name", ""),
                    "data_nascita": r.get("date_of_birth", ""),
                    "esiste_in_priamo": "SI" if r.get("exists_in_priamo") else "NO",
                    "stato_priamo": r.get("status", ""),
                    "priamo_id": r.get("priamo_id") or "",
                    "codice_fiscale": r.get("codice_fiscale") or "",
                    "comune_nascita": r.get("comune_nascita") or "",
                    "sesso": r.get("sesso") or "",
                    "righe_excel": ",".join(map(str, r.get("righe_excel") or [])),
                    "messaggio": r.get("message", "")
                })
            df_out = pd.DataFrame(rows_for_csv)
            df_out.to_csv(out_path, index=False)
            print(f"\n[OK] Report salvato in formato CSV: {out_path}")


if __name__ == "__main__":
    main()
