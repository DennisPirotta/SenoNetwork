#!/usr/bin/env python3
"""Script per l'estrazione, normalizzazione e deduplicazione dell'anagrafica pazienti

dal file condiviso multidisciplinare della Breast Unit (es. positivi_2026.xls).

Esempi di utilizzo:
  # Visualizza a terminale la tabella anagrafica dei pazienti univoci
  uv run python scripts/extract_patients_from_shared_file.py

  # Esporta in formato CSV
  uv run python scripts/extract_patients_from_shared_file.py --output data/output/anagrafica_pazienti_univoci.csv

  # Esporta in formato JSON includendo solo pazienti completi (con nome e data di nascita)
  uv run python scripts/extract_patients_from_shared_file.py --format json --only-complete --output data/output/pazienti.json
"""

import argparse
from datetime import date, datetime
import logging
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple, Union
from dotenv import load_dotenv
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
logger = logging.getLogger("SharedPatientExtractor")


def clean_title(s: str) -> str:
    """Capitalizza correttamente le parole gestendo apostrofi interni o finali

    (es. D'Elia, Dell'Anna, Spano') e caratteri accentati (es. Salvadé).
    """
    if not s or not str(s).strip():
        return ""

    def _cap_word(w: str) -> str:
        if not w:
            return ""
        # Gestione apostrofo interno o finale (es. d'elia -> D'Elia, spano' -> Spano')
        if "'" in w:
            parts = w.split("'")
            return "'".join(p.capitalize() if p else "" for p in parts)
        return w.capitalize()

    return " ".join(_cap_word(w) for w in str(s).strip().split())


def split_nominativo(raw_name: str) -> Tuple[str, Optional[str]]:
    """Separa una stringa nominativo 'COGNOME NOME' tipica dei registri clinici italiani.

    Riconosce e gestisce:
      - Prefissi patronimici/nobiliari italiani (De, Di, Del, Della, La, San, ecc.)
      - Particelle intermedie in cognomi composti (es. 'Herrera della rosa Rosaura', 'Mateo De Los Santos Arelis')
      - Doppi cognomi noti o composti (es. 'Cantali Carnesano Francesca', 'Jaime Rojas Leonor')
      - Nomi composti multipli (es. 'Ciceri Luigia Maria', 'Balducchi Maria Clelia')
      - Nominativi composti da un solo termine (es. 'Ndiaye', 'Volpi')
    """
    if not raw_name or not str(raw_name).strip():
        return "", None

    # Normalizza spazi interni multipli
    parts = str(raw_name).strip().split()
    if len(parts) == 1:
        return clean_title(parts[0]), None

    prefissi = {
        "de", "di", "da", "del", "della", "delle", "dello", "degli",
        "la", "le", "lo", "li", "san", "santa"
    }
    particelle_intermedie = {
        "de", "di", "da", "del", "della", "delle", "dello", "degli",
        "los", "las", "el", "al", "san", "santa", "von", "van"
    }
    known_compound_surnames = {
        "cantali carnesano",
        "jaime rojas"
    }

    # 1. Prefisso all'inizio del nominativo (es. 'Di Leo Maria Grazia', 'De Martinis Annunziata', 'La Delfa Rita')
    if parts[0].lower() in prefissi:
        if len(parts) == 2:
            # Caso in cui è presente solo il cognome con prefisso (es. 'Di Grigoli')
            return clean_title(f"{parts[0]} {parts[1]}"), None
        cognome = clean_title(f"{parts[0]} {parts[1]}")
        nome = clean_title(" ".join(parts[2:]))
        return cognome, nome

    # 2. Preposizione o particella intermedia nel cognome
    # Es: 'Herrera della rosa Rosaura' (4 parti) -> Cognome: 'Herrera Della Rosa', Nome: 'Rosaura'
    # Es: 'Mateo De Los Santos Arelis' (5 parti) -> Cognome: 'Mateo De Los Santos', Nome: 'Arelis'
    if len(parts) >= 4 and parts[1].lower() in particelle_intermedie:
        if parts[2].lower() in particelle_intermedie and len(parts) >= 5:
            cognome = clean_title(" ".join(parts[:4]))
            nome = clean_title(" ".join(parts[4:]))
            return cognome, nome
        else:
            cognome = clean_title(" ".join(parts[:3]))
            nome = clean_title(" ".join(parts[3:]))
            return cognome, nome

    # 3. Doppi cognomi noti o composti
    first_two = f"{parts[0]} {parts[1]}".lower()
    if first_two in known_compound_surnames and len(parts) >= 3:
        cognome = clean_title(f"{parts[0]} {parts[1]}")
        nome = clean_title(" ".join(parts[2:]))
        return cognome, nome

    # 4. Pattern standard dei registri clinici: Primo elemento = Cognome, Restante = Nome/i
    cognome = clean_title(parts[0])
    nome = clean_title(" ".join(parts[1:]))
    return cognome, nome


class Paziente(BaseModel):
    """Rappresentazione di un paziente univoco estratto dal registro."""
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    patient_id: str = Field(default="", description="ID o slug normalizzato per REDCap/SenoNetwork")
    cognome: str = Field(..., description="Cognome del paziente")
    nome: Optional[str] = Field(None, description="Nome del paziente (se presente)")
    data_nascita: Optional[date] = Field(None, description="Data di nascita (YYYY-MM-DD)")
    righe_excel: List[int] = Field(default_factory=list, description="Indici di riga nel file Excel originale (1-based)")
    note: Optional[str] = Field(None, description="Note cliniche o di follow-up registrate")

    @model_validator(mode="before")
    @classmethod
    def compute_patient_id(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("patient_id"):
                name = str(data.get("nome") or "").strip()
                surname = str(data.get("cognome") or "").strip()
                dob = data.get("data_nascita")
                dob_str = ""
                if dob:
                    if hasattr(dob, "isoformat"):
                        dob_str = dob.isoformat()
                    else:
                        dob_str = str(dob)[:10]

                parts = [p for p in [name, surname, dob_str] if p]
                raw_slug = "-".join(parts).lower().replace(" ", "-")
                clean_slug = re.sub(r"[^a-z0-9\-]", "", raw_slug).strip("-")
                data["patient_id"] = clean_slug or "paziente-anonimo"
        return data

    @property
    def nominativo(self) -> str:
        """Restituisce 'COGNOME Nome' o solo 'COGNOME' se il nome è assente."""
        return f"{self.cognome} {self.nome}".strip() if self.nome else self.cognome

    @property
    def is_complete(self) -> bool:
        """Verifica se l'anagrafica include sia nome che data di nascita."""
        return bool(self.nome and self.data_nascita)

    def to_redcap_dict(self) -> Dict[str, Any]:
        """Converte l'anagrafica nel dizionario standard compatibile con REDCap."""
        return {
            "record_id": self.patient_id,
            "name": self.nome or "",
            "surname": self.cognome,
            "date_of_birth": self.data_nascita.isoformat() if self.data_nascita else ""
        }


def estrai_pazienti_univoci(
    filepath: Union[str, Path],
    sheet_name: Optional[str] = None,
    only_complete: bool = False
) -> List[Paziente]:
    """Legge il file Excel condiviso (es. positivi_2026.xls), individua l'intestazione

    e la colonna pazienti/data nascita, filtra i placeholder, deduplica le occorrenze
    multiple e restituisce l'elenco ordinato di Pazienti univoci.

    Args:
        filepath: Percorso del file Excel (.xls o .xlsx).
        sheet_name: Nome del foglio da leggere (default: 'Foglio1' o primo foglio con dati).
        only_complete: Se True, esclude i record privi di data di nascita o nome.

    Returns:
        Lista ordinata di oggetti Paziente deduplicati.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File non trovato: {path}")

    # Carica il file Excel per determinare il foglio corretto
    xl = pd.ExcelFile(path)
    target_sheet = sheet_name or ("Foglio1" if "Foglio1" in xl.sheet_names else xl.sheet_names[0])
    df = pd.read_excel(path, sheet_name=target_sheet, header=None)

    if df.empty:
        logger.warning(f"Il foglio '{target_sheet}' in {path.name} è vuoto.")
        return []

    # 1. Rileva dinamicamente la riga di intestazione e le colonne PZ e Data Nascita
    header_idx = None
    col_pz = 2   # default storico
    col_dob = 3  # default storico

    for r_idx in range(min(10, len(df))):
        row_vals = [str(v).strip().lower() for v in df.iloc[r_idx].tolist() if pd.notna(v)]
        if any(h in row_vals for h in ["pz", "paziente", "pazienti", "nominativo"]):
            header_idx = r_idx
            # Identifica le colonne specifiche
            for c_idx in range(df.shape[1]):
                val = str(df.iloc[r_idx, c_idx]).strip().lower()
                if val in ["pz", "paziente", "pazienti", "nominativo"]:
                    col_pz = c_idx
                elif any(d in val for d in ["gg/m/aa", "nascita", "data_nascita", "dob", "nato il", "data nascita"]):
                    col_dob = c_idx
            break

    # Se l'intestazione è stata trovata alla riga header_idx, i dati partono dalla riga successiva
    data_start = (header_idx + 1) if header_idx is not None else 2
    df_data = df.iloc[data_start:].copy()

    # Dizionario di deduplicazione: chiave normalizzata -> dict dati paziente
    pazienti_map: Dict[Tuple[str, str, Optional[date]], Dict[str, Any]] = {}

    for row_idx, row in df_data.iterrows():
        # Excel riga 1-based reale
        excel_row_num = int(row_idx) + 1

        raw_pz = row[col_pz] if col_pz < len(row) else None
        if pd.isna(raw_pz):
            continue

        str_pz = str(raw_pz).strip()
        # Filtra placeholder numerici (es. '0', '0.0'), vuoti o simboli di sessione vuota
        if str_pz in ["0", "0.0", "", "nan", "None", "-", "N.D.", "nd"]:
            continue

        cognome, nome = split_nominativo(str_pz)
        if not cognome:
            continue

        # Parsing data di nascita
        raw_dob = row[col_dob] if col_dob < len(row) else None
        dob: Optional[date] = None

        if pd.notna(raw_dob):
            try:
                if hasattr(raw_dob, "date"):
                    dob = raw_dob.date()
                else:
                    # In Italia la notazione è rigorosamente gg/mm/aaaa
                    dt = pd.to_datetime(raw_dob, dayfirst=True, errors="coerce")
                    if pd.notna(dt):
                        dob = dt.date()
            except Exception:
                dob = None

        # Raccogli eventuali note cliniche presenti nelle colonne successive (es. col 4, 5, 6)
        notes = []
        for c in range(4, min(7, len(row))):
            val = row[c]
            if pd.notna(val):
                v_str = str(val).strip()
                if v_str and v_str not in ["nan", "None", "0"]:
                    notes.append(v_str)
        note_str = " | ".join(notes) if notes else None

        # Chiave di deduplicazione: (cognome_lower, nome_lower, dob)
        dedup_key = (
            cognome.lower(),
            (nome or "").lower(),
            dob
        )

        if dedup_key in pazienti_map:
            # Paziente già presente: registra l'occorrenza multipla (duplicato nel registro)
            pazienti_map[dedup_key]["righe_excel"].append(excel_row_num)
            if note_str and not pazienti_map[dedup_key]["note"]:
                pazienti_map[dedup_key]["note"] = note_str
            elif note_str and note_str not in str(pazienti_map[dedup_key]["note"]):
                pazienti_map[dedup_key]["note"] += f" ; {note_str}"
        else:
            pazienti_map[dedup_key] = {
                "cognome": cognome,
                "nome": nome,
                "data_nascita": dob,
                "righe_excel": [excel_row_num],
                "note": note_str
            }

    # Creazione istanze Paziente
    pazienti: List[Paziente] = []
    for data in pazienti_map.values():
        p = Paziente(**data)
        if only_complete and not p.is_complete:
            continue
        pazienti.append(p)

    # Ordinamento deterministico per Cognome, Nome e Data di nascita
    pazienti.sort(key=lambda p: (
        p.cognome,
        p.nome or "",
        p.data_nascita or date(1900, 1, 1)
    ))

    return pazienti


def display_rich_table(pazienti: List[Paziente], title: str = "Anagrafica Pazienti Univoci - SenoNetwork"):
    """Visualizza a terminale una tabella formattata con Rich."""
    console = Console()
    total = len(pazienti)
    complete_count = sum(1 for p in pazienti if p.is_complete)
    incomplete_count = total - complete_count
    multi_row_count = sum(1 for p in pazienti if len(p.righe_excel) > 1)

    console.print(Panel.fit(
        f"[bold cyan]{title}[/bold cyan]\n"
        f"Totale pazienti univoci: [bold white]{total}[/bold white] | "
        f"Completi: [bold green]{complete_count}[/bold green] | "
        f"Incompleti (privi di data nascita/nome): [bold yellow]{incomplete_count}[/bold yellow] | "
        f"Con occorrenze multiple deduplicate: [bold magenta]{multi_row_count}[/bold magenta]",
        border_style="cyan"
    ))

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Patient ID (Slug)", style="dim cyan")
    table.add_column("Cognome", style="bold white")
    table.add_column("Nome", style="white")
    table.add_column("Data Nascita", justify="center")
    table.add_column("Stato", justify="center")
    table.add_column("Righe Excel", justify="center", style="dim")

    for idx, p in enumerate(pazienti, 1):
        dob_str = p.data_nascita.strftime("%d/%m/%Y") if p.data_nascita else "[yellow]N.D.[/yellow]"
        status_badge = "[green]COMPLETO[/green]" if p.is_complete else "[yellow]INCOMPLETO[/yellow]"
        rows_str = ", ".join(map(str, p.righe_excel)) if p.righe_excel else "-"
        if len(p.righe_excel) > 1:
            rows_str = f"[magenta]{rows_str}[/magenta]"

        table.add_row(
            str(idx),
            p.patient_id,
            p.cognome,
            p.nome or "[yellow]N.D.[/yellow]",
            dob_str,
            status_badge,
            rows_str
        )

    console.print(table)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Estrae, normalizza e deduplica l'anagrafica pazienti dal file condiviso (es. positivi_2026.xls)."
    )
    parser.add_argument(
        "--input", "-i",
        default="data/positivi_2026.xls",
        help="Percorso del file Excel di input (default: data/positivi_2026.xls)."
    )
    parser.add_argument(
        "--output", "-o",
        default="data/output/anagrafica_pazienti_univoci.csv",
        help="Percorso in cui salvare il file di esportazione (.csv o .json)."
    )
    parser.add_argument(
        "--sheet", "-s",
        default="Foglio1",
        help="Nome del foglio Excel da analizzare (default: Foglio1)."
    )
    parser.add_argument(
        "--format", "-f",
        choices=["table", "csv", "json"],
        default="table",
        help="Formato principale di output o visualizzazione (default: table)."
    )
    parser.add_argument(
        "--only-complete",
        action="store_true",
        help="Estrae solo i pazienti completi (che dispongono sia di nome che di data di nascita)."
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Numero massimo di pazienti da visualizzare o esportare."
    )
    return parser.parse_args()


def main():
    args = parse_args()
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"[ERRORE] File di input non trovato: {input_path}", file=sys.stderr)
        sys.exit(1)

    pazienti = estrai_pazienti_univoci(
        filepath=input_path,
        sheet_name=args.sheet,
        only_complete=args.only_complete
    )

    if args.limit:
        pazienti = pazienti[:args.limit]

    # Visualizzazione tabella Rich se richiesta o di default
    if args.format == "table":
        if HAS_RICH:
            display_rich_table(pazienti, title=f"Anagrafica Pazienti Univoci ({input_path.name})")
        else:
            print(f"Estratti {len(pazienti)} pazienti univoci da {input_path.name}:")
            print(f"{'#':<4} {'COGNOME':<22} {'NOME':<25} {'DATA NASCITA':<14} {'RIGHE EXCEL'}")
            print("-" * 75)
            for idx, p in enumerate(pazienti, 1):
                dob_str = p.data_nascita.strftime("%d/%m/%Y") if p.data_nascita else "N.D."
                rows_str = ", ".join(map(str, p.righe_excel)) if p.righe_excel else "-"
                print(f"{idx:<4} {p.cognome:<22} {p.nome or 'N.D.':<25} {dob_str:<14} {rows_str}")

    # Esportazione su file se specificato output
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        is_json = out_path.suffix.lower() == ".json" or args.format == "json"
        if is_json:
            import json
            data = [p.model_dump(mode="json") for p in pazienti]
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"\n[OK] Esportati {len(pazienti)} record in formato JSON: {out_path}")
        else:
            # CSV con colonne pulite
            df_out = pd.DataFrame([{
                "patient_id": p.patient_id,
                "cognome": p.cognome,
                "nome": p.nome or "",
                "data_nascita": p.data_nascita.isoformat() if p.data_nascita else "",
                "righe_excel": ",".join(map(str, p.righe_excel)),
                "note": p.note or ""
            } for p in pazienti])
            df_out.to_csv(out_path, index=False)
            print(f"\n[OK] Esportati {len(pazienti)} record in formato CSV: {out_path}")


if __name__ == "__main__":
    main()
