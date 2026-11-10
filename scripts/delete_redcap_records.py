#!/usr/bin/env python3
"""Script per eliminare record dal progetto REDCap (singolo record o pulizia totale).

Esempi di utilizzo:
  # Elimina un singolo paziente specificando il record_id
  uv run python scripts/delete_redcap_records.py --record-id 101

  # Elimina un singolo paziente e rimuove anche il file di stato locale
  uv run python scripts/delete_redcap_records.py --record-id 101 --clean-state

  # Simula l'eliminazione di tutti i record senza cancellare nulla
  uv run python scripts/delete_redcap_records.py --all --dry-run

  # Elimina TUTTI i record attualmente presenti su REDCap (richiede conferma interattiva o flag --confirm)
  uv run python scripts/delete_redcap_records.py --all --confirm
"""

import argparse
import logging
from pathlib import Path
import sys
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Settings, get_settings
from src.redcap_service import REDCapService, delete_redcap_records

load_dotenv()

# Rich formatting
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
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
logger = logging.getLogger("REDCapRecordDeleter")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Elimina record dal database REDCap (eliminazione selettiva per record_id o totale di tutti i record)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--record-id", "-r",
        default=None,
        help="Identificativo record_id del singolo paziente da eliminare."
    )
    group.add_argument(
        "--all", "-a",
        action="store_true",
        help="Elimina TUTTI i record attualmente registrati nel progetto REDCap."
    )
    parser.add_argument(
        "--confirm", "-y",
        action="store_true",
        help="Salta la richiesta interattiva di conferma per l'eliminazione massiva."
    )
    parser.add_argument(
        "--clean-state",
        action="store_true",
        help="Elimina anche i corrispondenti file di stato locale (data/state/{record_id}_state.json)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Modalità simulazione: elenca i record che verrebbero eliminati senza invocare l'API di cancellazione."
    )
    parser.add_argument(
        "--arm",
        default=None,
        help="Numero del braccio (arm) REDCap (per progetti longitudinali)."
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=100,
        help="Numero di record per singola richiesta API batch di eliminazione (default: 100)."
    )
    return parser.parse_args()


def main():
    args = parse_args()
    settings = get_settings()

    if not settings.redcap_api_url or not settings.redcap_api_token:
        print("[ERRORE] REDCAP_API_URL e REDCAP_API_TOKEN devono essere configurati in .env", file=sys.stderr)
        sys.exit(1)

    console = Console() if HAS_RICH else None

    # 1. Eliminazione singolo record
    if args.record_id:
        target_id = str(args.record_id).strip()
        if HAS_RICH:
            console.print(Panel(
                f"[bold yellow]Richiesta eliminazione record REDCap[/bold yellow]\n"
                f"Record ID: [bold cyan]{target_id}[/bold cyan]\n"
                f"Pulizia stato locale: {'[green]Sì[/green]' if args.clean_state else '[dim]No[/dim]'}",
                title="REDCap Delete",
                border_style="yellow"
            ))
        else:
            print(f"Richiesta eliminazione record REDCap: {target_id} (clean_state={args.clean_state})")

        if args.dry_run:
            print(f"[DRY-RUN] Il record '{target_id}' verrebbe eliminato da REDCap. Nessuna modifica effettuata.")
            return

        service = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)
        try:
            success = service.delete_record(record_id=target_id, arm=args.arm)
            if success:
                if args.clean_state:
                    state_file = settings.state_dir / f"{target_id}_state.json"
                    if state_file.exists():
                        state_file.unlink(missing_ok=True)
                        print(f"  • Rimosso file di stato locale: {state_file}")

                if HAS_RICH:
                    console.print(f"[bold green]✔ Record '{target_id}' eliminato con successo da REDCap.[/bold green]")
                else:
                    print(f"[OK] Record '{target_id}' eliminato con successo da REDCap.")
            else:
                if HAS_RICH:
                    console.print(f"[bold red]✖ Impossibile eliminare il record '{target_id}' (non presente o errore API).[/bold red]")
                else:
                    print(f"[ERRORE] Impossibile eliminare il record '{target_id}'.")
        except Exception as e:
            logger.error(f"Errore durante l'eliminazione: {e}")
            sys.exit(1)
        return

    # 2. Eliminazione di TUTTI i record
    if args.all:
        service = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)
        all_ids = service.get_all_patient_ids()

        if not all_ids:
            if HAS_RICH:
                console.print("[bold yellow]Nessun record attualmente presente nel progetto REDCap. Operazione completata.[/bold yellow]")
            else:
                print("Nessun record attualmente presente nel progetto REDCap.")
            return

        if HAS_RICH:
            console.print(Panel(
                f"[bold red]ATTENZIONE: ELIMINAZIONE MASSIVA DATABASE REDCAP[/bold red]\n\n"
                f"Totale record da eliminare: [bold white]{len(all_ids)}[/bold white]\n"
                f"Primi ID: {', '.join(all_ids[:10])}{' ...' if len(all_ids) > 10 else ''}\n"
                f"Pulizia stato locale: {'[green]Sì[/green]' if args.clean_state else '[dim]No[/dim]'}\n"
                f"Progetto API: [cyan]{settings.redcap_api_url}[/cyan]\n\n"
                f"[bold red]Questa operazione è irreversibile e cancellerà definitivamente tutti i record clinici dal progetto REDCap.[/bold red]",
                title="⚠️  PERICOLO CANCELLAZIONE TOTALE ⚠️",
                border_style="red"
            ))
        else:
            print("=" * 70)
            print(" ATTENZIONE: ELIMINAZIONE MASSIVA DI TUTTI I RECORD REDCAP")
            print(f" Totale record trovati: {len(all_ids)}")
            print("=" * 70)

        if args.dry_run:
            print(f"\n[DRY-RUN] Simulazione completata. Sarebbero stati eliminati {len(all_ids)} record da REDCap.")
            return

        # Verifica di sicurezza interattiva se non fornito --confirm
        if not args.confirm:
            if HAS_RICH:
                typed = Prompt.ask(
                    "\n[bold yellow]Per confermare l'eliminazione di TUTTI i record, digita esattamente[/bold yellow] [bold red]ELIMINA TUTTO[/bold red]"
                )
            else:
                typed = input("\nPer confermare l'eliminazione di TUTTI i record, digita esattamente 'ELIMINA TUTTO': ")

            if typed.strip() != "ELIMINA TUTTO":
                print("\nOperazione annullata dall'utente. Nessun dato è stato cancellato.")
                return

        # Procedi con l'eliminazione
        try:
            deleted_count = service.delete_all_records(
                confirm=True,
                arm=args.arm,
                chunk_size=args.chunk_size,
                clean_local_state=args.clean_state
            )
            if HAS_RICH:
                console.print(f"\n[bold green]✔ Operazione completata con successo: eliminati {deleted_count} record da REDCap.[/bold green]")
            else:
                print(f"\n[OK] Operazione completata con successo: eliminati {deleted_count} record da REDCap.")
        except Exception as e:
            logger.error(f"Errore durante l'eliminazione massiva: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
