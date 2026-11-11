#!/usr/bin/env python3
"""Script per l'estrazione e la deduplicazione dell'anagrafica pazienti da PriamoCUP.

Scarica i fogli di lavoro ambulatoriali (worksheets) per l'Unità '37002' (Senologia)
e le agende 3000, 3003, 3004, 3035, 3036, ed estrae l'elenco univoco dei pazienti
con le colonne richieste:
  - PAZ_COGNOME
  - PAZ_NOME
  - PAZ_DATA_NASCITA
  - PAZ_CODICE_FISCALE
  - PAZ_PROGRESSIVO_PAZ1 (ID Priamo)
  - PAZ_SESSO
  - PAZ_COMUNE_NASCITA

Esempi di utilizzo:
  # 1. Scarica la settimana corrente da PriamoCUP ed estrae l'anagrafica a terminale
  uv run python scripts/extract_cup_worksheets.py

  # 2. Scarica le prossime 2 settimane ed esporta in formato CSV ed Excel
  uv run python scripts/extract_cup_worksheets.py --weeks 2 --output data/output/anagrafica_cup_senologia.csv

  # 3. Analizza un file report Excel PriamoCUP già scaricato in precedenza (senza chiamare il server)
  uv run python scripts/extract_cup_worksheets.py --input data/cup/fogli_lavoro_unita_37002_20261109_20261115.xlsx

  # 4. Scarica un periodo specifico
  uv run python scripts/extract_cup_worksheets.py --start-date 2026-11-09 --end-date 2026-11-15 --format xlsx --output data/output/anagrafica_senologia.xlsx
"""

import argparse
from datetime import date, datetime, timedelta
import logging
from pathlib import Path
import sys
from typing import List, Optional

from dotenv import load_dotenv
import pandas as pd

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings
from src.priamo_cup_client import (
    AGENDE_SENOLOGIA,
    CODICE_STRUTTURA_VALDUCE,
    UNITA_SENOLOGIA,
    PriamoCupClient,
)
from scripts.extract_patients_from_shared_file import (
    Paziente,
    display_rich_table,
    estrai_pazienti_da_fogli_cup,
    estrai_pazienti_univoci,
)

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
logger = logging.getLogger("CupWorksheetExtractor")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Estrazione fogli di lavoro e anagrafica pazienti da PriamoCUP (Unità 37002 Senologia)."
    )
    parser.add_argument(
        "--input", "-i",
        default=None,
        help="Percorso di un file report Excel PriamoCUP già scaricato (se omesso, scarica direttamente da PriamoCUP)."
    )
    parser.add_argument(
        "--output", "-o",
        default="data/output/anagrafica_cup_senologia.csv",
        help="Percorso di esportazione dell'anagrafica (.csv, .xlsx o .json). Default: data/output/anagrafica_cup_senologia.csv."
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Data inizio periodo (YYYY-MM-DD). Default: lunedì della settimana corrente."
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Data fine periodo (YYYY-MM-DD). Default: domenica della settimana corrente."
    )
    parser.add_argument(
        "--weeks", "-w",
        type=int,
        default=1,
        help="Numero di settimane da esportare a partire da --start-date (default: 1)."
    )
    parser.add_argument(
        "--unit", "-u",
        default=UNITA_SENOLOGIA,
        help=f"Codice unità operativa per PriamoCUP (default: '{UNITA_SENOLOGIA}' Senologia)."
    )
    parser.add_argument(
        "--agendas", "-a",
        default=",".join(AGENDE_SENOLOGIA),
        help=f"Codici agende/erogatori separati da virgola (default: {','.join(AGENDE_SENOLOGIA)})."
    )
    parser.add_argument(
        "--format", "-f",
        choices=["table", "csv", "xlsx", "json"],
        default="table",
        help="Formato visualizzazione/esportazione principale (default: table)."
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limite massimo di pazienti da visualizzare o esportare."
    )
    parser.add_argument(
        "--only-complete",
        action="store_true",
        help="Include solo i pazienti completi di nome e data di nascita."
    )
    return parser.parse_args()


def main():
    args = parse_args()
    settings = get_settings()

    agende_list = [a.strip() for a in args.agendas.split(",") if a.strip()]

    # 1. Determinazione file sorgente (locale o download live da PriamoCUP)
    if args.input:
        input_file = Path(args.input)
        if not input_file.exists():
            print(f"[ERRORE] File di input non trovato: {input_file}", file=sys.stderr)
            sys.exit(1)
        print(f"-> Analisi anagrafica dal file locale: {input_file}...")
        report_files = [input_file]
    else:
        # Download live da PriamoCUP
        if not settings.priamo_username or not settings.priamo_password:
            print("[ERRORE] Credenziali Priamo non configurate in .env (PRIAMO_USERNAME, PRIAMO_PASSWORD).", file=sys.stderr)
            sys.exit(1)

        cup_client = PriamoCupClient(settings=settings)

        if args.start_date and args.end_date:
            print(f"-> Download fogli lavoro PriamoCUP: Unità {args.unit}, Agende {agende_list}, {args.start_date} -> {args.end_date}...")
            f_path = cup_client.export_worksheets(
                start_date=args.start_date,
                end_date=args.end_date,
                codice_unita=args.unit,
                agende=agende_list,
                output_dir="data/cup",
            )
            report_files = [f_path]
        else:
            ref_date = args.start_date or date.today()
            print(f"-> Download fogli lavoro PriamoCUP per {args.weeks} settimana/e da {ref_date} (Unità {args.unit}, Agende {agende_list})...")
            if args.weeks > 1:
                report_files = cup_client.export_weeks(
                    start_date=ref_date,
                    num_weeks=args.weeks,
                    codice_unita=args.unit,
                    agende=agende_list,
                    output_dir="data/cup",
                )
            else:
                f_path = cup_client.export_week(
                    ref_date=ref_date,
                    codice_unita=args.unit,
                    agende=agende_list,
                    output_dir="data/cup",
                )
                report_files = [f_path]

    if not report_files:
        print("[ERRORE] Nessun foglio lavoro disponibile per l'estrazione.", file=sys.stderr)
        sys.exit(1)

    # 2. Consolidamento ed estrazione anagrafica univoca
    all_pazienti_dict = {}

    for rep in report_files:
        pazienti_rep = estrai_pazienti_da_fogli_cup(rep, only_complete=args.only_complete)
        for p in pazienti_rep:
            dedup_key = (p.cognome.lower(), (p.nome or "").lower(), p.data_nascita.isoformat() if p.data_nascita else "")
            if dedup_key not in all_pazienti_dict:
                all_pazienti_dict[dedup_key] = p
            else:
                # Merge agende e righe se su più file
                existing = all_pazienti_dict[dedup_key]
                merged_rows = sorted(list(set(existing.righe_excel + p.righe_excel)))
                merged_agende = sorted(list(set(existing.agende + p.agende)))
                all_pazienti_dict[dedup_key] = Paziente(
                    patient_id=existing.patient_id,
                    cognome=existing.cognome,
                    nome=existing.nome,
                    data_nascita=existing.data_nascita,
                    righe_excel=merged_rows,
                    codice_fiscale=existing.codice_fiscale or p.codice_fiscale,
                    sesso=existing.sesso or p.sesso,
                    comune_nascita=existing.comune_nascita or p.comune_nascita,
                    priamo_id=existing.priamo_id or p.priamo_id,
                    telefono=existing.telefono or p.telefono,
                    agende=merged_agende,
                    num_appuntamenti=existing.num_appuntamenti + p.num_appuntamenti,
                    note=f"{existing.note}; {p.note}" if existing.note and p.note and existing.note != p.note else (existing.note or p.note)
                )

    pazienti = sorted(list(all_pazienti_dict.values()), key=lambda p: (p.cognome, p.nome or "", p.data_nascita or date(1900, 1, 1)))

    if args.limit:
        pazienti = pazienti[:args.limit]

    # 3. Visualizzazione Tabella
    source_label = ", ".join(p.name for p in report_files)
    if args.format == "table":
        if HAS_RICH:
            display_rich_table(pazienti, title=f"Anagrafica Pazienti PriamoCUP (Unità {args.unit} - {source_label})")
        else:
            print(f"\n--- Anagrafica Pazienti Univoci PriamoCUP ({len(pazienti)} pazienti) ---")
            print(f"{'#':<4} {'PAZ_COGNOME':<22} {'PAZ_NOME':<22} {'PAZ_DATA_NASCITA':<16} {'CODICE FISCALE':<18} {'AGENDE'}")
            print("-" * 95)
            for idx, p in enumerate(pazienti, 1):
                dob_str = p.data_nascita.strftime("%d/%m/%Y") if p.data_nascita else "N.D."
                ag_str = ", ".join(p.agende) if p.agende else "-"
                print(f"{idx:<4} {p.cognome:<22} {p.nome or 'N.D.':<22} {dob_str:<16} {p.codice_fiscale or '-':<18} {ag_str}")

    # 4. Esportazione File (con colonne PAZ_COGNOME, PAZ_NOME, PAZ_DATA_NASCITA)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        is_json = out_path.suffix.lower() == ".json" or args.format == "json"
        is_xlsx = out_path.suffix.lower() == ".xlsx" or args.format == "xlsx"

        records_data = [p.to_export_dict() for p in pazienti]

        if is_json:
            import json
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(records_data, f, indent=2, ensure_ascii=False)
            print(f"[OK] Esportati {len(pazienti)} record in formato JSON: {out_path}")
        elif is_xlsx:
            df_out = pd.DataFrame(records_data)
            df_out.to_excel(out_path, index=False)
            print(f"[OK] Esportati {len(pazienti)} record in formato Excel (.xlsx): {out_path}")
        else:
            df_out = pd.DataFrame(records_data)
            df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"[OK] Esportati {len(pazienti)} record in formato CSV: {out_path}")


if __name__ == "__main__":
    main()
