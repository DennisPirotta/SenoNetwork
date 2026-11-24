#!/usr/bin/env python3
"""Script per il controllo periodico e l'aggiornamento dei referti su REDCap.

Funzionalità:
1. Ispeziona i pazienti registrati su REDCap.
2. Identifica form mancanti o incompleti (es. anatomia_patologica post-operatoria per pazienti già inseriti che hanno fatto l'intervento).
3. Scarica/rileva nuovi referti da Priamo .NET o da directory locali.
4. Estrae i dati clinici con l'IA locale (llama.cpp RPC / Ollama).
5. Calcola le differenze (diff) e carica gli aggiornamenti su REDCap.
6. Può essere eseguito come one-shot (ad es. da cron o scheduler) o in loop continuo (--loop).
"""

import argparse
import logging
from pathlib import Path
import signal
import sys
import threading
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Settings
from src.sync_service import RedCapSyncService

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("REDCapPeriodicSync")

stop_event = threading.Event()


def handle_sigint(signum, frame):
    logger.info("Interruzione ricevuta (SIGINT/SIGTERM). Arresto ordinato...")
    stop_event.set()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Controllo periodico e riconciliazione referti clinici su REDCap."
    )
    parser.add_argument(
        "--target-form", "-f",
        default="anatomia_patologica",
        help="Form prioritario da verificare (default: 'anatomia_patologica'). Specificare 'all' per verificare tutti i form incompleti."
    )
    parser.add_argument(
        "--patient-id", "-p",
        default=None,
        help="ID Paziente specifico da controllare su REDCap anziché l'intera coorte."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Esegue la scansione in sola visualizzazione (nessuna scrittura su REDCap)."
    )
    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Rielabora tutti i referti PDF anche se già registrati nello stato."
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Esegue il controllo in loop periodico continuo anziché una sola volta."
    )
    parser.add_argument(
        "--interval-minutes", "-i",
        type=int,
        default=60,
        help="Intervallo in minuti tra un controllo e l'altro se --loop è attivo (default: 60)."
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limite massimo di pazienti da elaborare per ciclo."
    )
    parser.add_argument(
        "--all-patients",
        action="store_true",
        help="Verifica tutti i pazienti presenti in REDCap, non solo quelli con form incompleti."
    )
    return parser.parse_args()


def main():
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    args = parse_args()
    settings = Settings()

    if not settings.redcap_api_url or not settings.redcap_api_token:
        logger.error("REDCAP_API_URL o REDCAP_API_TOKEN non configurati!")
        sys.exit(1)

    target_forms = None if args.target_form.lower() == "all" else [args.target_form]
    sync_service = RedCapSyncService(settings=settings)

    logger.info("================================================================")
    logger.info(" SenoNetwork - Controllo Periodico & Aggiornamento Dati REDCap ")
    logger.info(f" Modalità: {'DRY-RUN (Simulazione)' if args.dry_run else 'LIVE UPDATE'}")
    logger.info(f" Form monitorati: {target_forms or 'Tutti i form incompleti'}")
    logger.info("================================================================")

    # Singolo paziente
    if args.patient_id:
        logger.info(f"Scansione mirata sul paziente: {args.patient_id}")
        res = sync_service.check_and_update_patient(
            patient_id=args.patient_id,
            target_forms=target_forms,
            auto_upload=not args.dry_run,
            dry_run=args.dry_run,
            force_reprocess=args.force_reprocess
        )
        logger.info(f"Esito: {res.status.upper()} | Modifiche: {res.field_updates_count} campi in {res.duration_sec}s")
        for u in res.field_updates:
            logger.info(f"  * [{u.form_name}] {u.field_name}: '{u.old_value}' -> '{u.new_value}' ({u.update_type})")
        return

    # Loop continuo o one-shot
    if args.loop:
        interval_sec = args.interval_minutes * 60
        logger.info(f"Avvio loop periodico ogni {args.interval_minutes} minuti ({interval_sec}s)...")
        while not stop_event.is_set():
            try:
                sync_service.sync_all(
                    target_forms=target_forms,
                    auto_upload=not args.dry_run,
                    dry_run=args.dry_run,
                    force_reprocess=args.force_reprocess,
                    limit=args.limit,
                    only_incomplete=not args.all_patients
                )
            except Exception as e:
                logger.error(f"Errore nel ciclo di sync: {e}")

            logger.info(f"In attesa del prossimo ciclo ({args.interval_minutes} min)...")
            if stop_event.wait(timeout=interval_sec):
                break
    else:
        logger.info("Esecuzione scansione one-shot...")
        report = sync_service.sync_all(
            target_forms=target_forms,
            auto_upload=not args.dry_run,
            dry_run=args.dry_run,
            force_reprocess=args.force_reprocess,
            limit=args.limit,
            only_incomplete=not args.all_patients
        )
        logger.info(
            f"Riepilogo: {report.patients_updated}/{report.patients_inspected} pazienti aggiornati "
            f"({report.total_fields_updated} campi arricchiti) in {report.duration_sec}s"
        )


if __name__ == "__main__":
    main()
