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
    """Capitalizza correttamente le parole gestendo trattini, apostrofi interni o finali

    (es. D'Elia, Dell'Anna, Spano', Al-Hassan) e caratteri accentati (es. Salvadé).
    """
    if not s or not str(s).strip():
        return ""

    def _cap_word(w: str) -> str:
        if not w:
            return ""
        # Gestione trattino interno (es. Al-Hassan, Smith-Jones, Jean-Pierre)
        if "-" in w:
            return "-".join(_cap_word(p) for p in w.split("-"))
        # Gestione apostrofo interno o finale (es. d'elia -> D'Elia, spano' -> Spano')
        if "'" in w:
            parts = w.split("'")
            return "'".join(p.capitalize() if p else "" for p in parts)
        return w.capitalize()

    return " ".join(_cap_word(w) for w in str(s).strip().split())


def split_nominativo(raw_name: str) -> Tuple[str, Optional[str]]:
    """Separa una stringa nominativo 'COGNOME NOME' tipica dei registri clinici italiani ed esteri.

    Riconosce e gestisce:
      - Prefissi patronimici/nobiliari italiani ed esteri singoli o doppi (De, Di, Del, Della, La, San, De La, Van Der, Al, El, ecc.)
      - Particelle intermedie in cognomi composti ispanici/portoghesi/francesi (es. 'Herrera de la rosa Rosaura', 'Mateo De Los Santos Arelis', 'Lopez del Rio Juan')
      - Doppi cognomi composti o asindetici (es. 'Cantali Carnesano Francesca', 'Jaime Rojas Leonor')
      - Nomi composti multipli tradizionali (es. 'Ciceri Luigia Maria', 'Balducchi Maria Clelia')
      - Patronimici arabi e convenzioni internazionali (es. 'Ismail Sohair Farouk Abdelmeguid', 'Mekhaeel Mariam Fayez Camel')
      - Nominativi composti da un solo termine (mononimi, es. 'Ndiaye', 'Volpi')
    """
    if not raw_name or not str(raw_name).strip():
        return "", None

    # Normalizza spazi interni multipli
    parts = str(raw_name).strip().split()
    if len(parts) == 1:
        return clean_title(parts[0]), None

    prefissi_singoli = {
        "de", "di", "da", "del", "della", "delle", "dello", "degli",
        "la", "le", "lo", "li", "san", "santa",
        "al", "el", "ben", "ibn", "von", "van", "ter"
    }
    particelle_intermedie = {
        "de", "di", "da", "del", "della", "delle", "dello", "degli",
        "la", "le", "lo", "li", "los", "las", "el", "al", "san", "santa",
        "dos", "das", "do", "von", "van", "der", "den", "ter", "y", "e"
    }
    known_compound_surnames = {
        "cantali carnesano",
        "jaime rojas"
    }

    # 1. Prefisso all'inizio del nominativo
    if parts[0].lower() in prefissi_singoli:
        # 1a. Prefisso composto all'inizio (es. 'De La Cruz Maria', 'Van Der Bellen Alexander')
        if len(parts) >= 3 and parts[1].lower() in particelle_intermedie:
            if len(parts) == 3:
                return clean_title(f"{parts[0]} {parts[1]} {parts[2]}"), None
            cognome = clean_title(f"{parts[0]} {parts[1]} {parts[2]}")
            nome = clean_title(" ".join(parts[3:]))
            return cognome, nome

        # 1b. Prefisso singolo all'inizio (es. 'Di Leo Maria Grazia', 'La Delfa Rita', 'El Sayed Fatima')
        if len(parts) == 2:
            return clean_title(f"{parts[0]} {parts[1]}"), None
        cognome = clean_title(f"{parts[0]} {parts[1]}")
        nome = clean_title(" ".join(parts[2:]))
        return cognome, nome

    # 2. Preposizione o particella intermedia nel cognome
    # 2a. Posizione 1 (es. 'Herrera de la rosa Rosaura', 'Mateo De Los Santos Arelis', 'Lopez del Rio Juan')
    if len(parts) >= 3 and parts[1].lower() in particelle_intermedie:
        if len(parts) >= 4 and parts[2].lower() in particelle_intermedie:
            # Due particelle (es. 'Herrera de la rosa Rosaura', 'Mateo De Los Santos Arelis')
            if len(parts) == 4:
                return clean_title(" ".join(parts[:4])), None
            cognome = clean_title(" ".join(parts[:4]))
            nome = clean_title(" ".join(parts[4:]))
            return cognome, nome
        else:
            # Una sola particella (es. 'Lopez del Rio Juan', 'Garcia y Vega Carmen')
            if len(parts) == 3:
                return clean_title(" ".join(parts[:3])), None
            cognome = clean_title(" ".join(parts[:3]))
            nome = clean_title(" ".join(parts[3:]))
            return cognome, nome

    # 2b. Posizione 2 per cognomi composti complessi (es. 'Gomez Perez de la Rosa Ana')
    if len(parts) >= 5 and parts[2].lower() in particelle_intermedie:
        if len(parts) >= 6 and parts[3].lower() in particelle_intermedie:
            cognome = clean_title(" ".join(parts[:5]))
            nome = clean_title(" ".join(parts[5:]))
            return cognome, nome
        else:
            cognome = clean_title(" ".join(parts[:4]))
            nome = clean_title(" ".join(parts[4:]))
            return cognome, nome

    # 3. Doppi cognomi noti o composti
    first_two = f"{parts[0]} {parts[1]}".lower()
    if first_two in known_compound_surnames:
        if len(parts) == 2:
            return clean_title(f"{parts[0]} {parts[1]}"), None
        cognome = clean_title(f"{parts[0]} {parts[1]}")
        nome = clean_title(" ".join(parts[2:]))
        return cognome, nome

    # 4. Pattern standard dei registri clinici: Primo elemento = Cognome, Restante = Nome/i
    cognome = clean_title(parts[0])
    nome = clean_title(" ".join(parts[1:]))
    return cognome, nome


class Paziente(BaseModel):
    """Rappresentazione di un paziente univoco estratto dal registro o foglio lavoro."""
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    patient_id: str = Field(default="", description="ID o slug normalizzato per REDCap/SenoNetwork")
    cognome: str = Field(..., description="Cognome del paziente")
    nome: Optional[str] = Field(None, description="Nome del paziente (se presente)")
    data_nascita: Optional[date] = Field(None, description="Data di nascita (YYYY-MM-DD)")
    righe_excel: List[int] = Field(default_factory=list, description="Indici di riga nel file Excel originale (1-based)")
    note: Optional[str] = Field(None, description="Note cliniche, prestazioni o orari registrati")
    codice_fiscale: Optional[str] = Field(None, description="Codice Fiscale ufficiale MEF")
    sesso: Optional[str] = Field(None, description="Sesso del paziente ('M' o 'F')")
    comune_nascita: Optional[str] = Field(None, description="Comune o stato estero di nascita")
    priamo_id: Optional[str] = Field(None, description="ID o progressivo paziente in Priamo (progressivoPaz1)")
    telefono: Optional[str] = Field(None, description="Recapito telefonico o cellulare")
    agende: List[str] = Field(default_factory=list, description="Agende/erogatori Priamo CUP associati")
    num_appuntamenti: int = Field(default=1, description="Numero di appuntamenti registrati")

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
        res = {
            "record_id": self.patient_id,
            "name": self.nome or "",
            "surname": self.cognome,
            "date_of_birth": self.data_nascita.isoformat() if self.data_nascita else ""
        }
        if self.codice_fiscale:
            res["codice_fiscale"] = self.codice_fiscale
        if self.priamo_id:
            res["priamo_id"] = self.priamo_id
        return res

    def to_export_dict(self) -> Dict[str, Any]:
        """Restituisce un dizionario completo per esportazione tabellare con colonne PAZ_..."""
        return {
            "patient_id": self.patient_id,
            "PAZ_COGNOME": self.cognome,
            "PAZ_NOME": self.nome or "",
            "PAZ_DATA_NASCITA": self.data_nascita.isoformat() if self.data_nascita else "",
            "cognome": self.cognome,
            "nome": self.nome or "",
            "data_nascita": self.data_nascita.isoformat() if self.data_nascita else "",
            "codice_fiscale": self.codice_fiscale or "",
            "priamo_id": self.priamo_id or "",
            "sesso": self.sesso or "",
            "comune_nascita": self.comune_nascita or "",
            "telefono": self.telefono or "",
            "agende": ", ".join(self.agende),
            "num_appuntamenti": self.num_appuntamenti,
            "righe_excel": ", ".join(map(str, self.righe_excel)),
            "is_complete": self.is_complete,
            "note": self.note or "",
        }


def estrai_pazienti_da_fogli_cup(
    filepath_or_df: Union[str, Path, pd.DataFrame],
    sheet_name: Optional[Union[str, int]] = 0,
    only_complete: bool = False,
) -> List[Paziente]:
    """Estrae l'anagrafica univoca dei pazienti (PAZ_NOME, PAZ_COGNOME, PAZ_DATA_NASCITA)

    da un file report di foglio di lavoro esportato da PriamoCUP.
    Deduplica i pazienti univoci, normalizza i nominativi e include metadati essenziali
    (Codice Fiscale, Sesso, Comune Nascita, ID Priamo, Agende, Note appuntamento).
    """
    if isinstance(filepath_or_df, pd.DataFrame):
        df = filepath_or_df.copy()
    else:
        p = Path(filepath_or_df)
        if not p.exists():
            raise FileNotFoundError(f"File foglio lavoro non trovato: {p.resolve()}")
        df = pd.read_excel(p, sheet_name=sheet_name or 0)

    if df.empty:
        return []

    # Normalizza colonne (stripped, maiuscolo)
    col_map = {c: str(c).strip().upper() for c in df.columns}
    df = df.rename(columns=col_map)

    req_cols = ["PAZ_COGNOME", "PAZ_NOME"]
    for col in req_cols:
        if col not in df.columns:
            raise ValueError(f"Colonna obbligatoria '{col}' assente nel foglio lavoro PriamoCUP.")

    pazienti_map: Dict[Tuple[str, str, Optional[str]], Dict[str, Any]] = {}

    for idx, row in df.iterrows():
        excel_row_num = int(idx) + 2  # 1-based, riga 1 intestazione

        raw_cognome = row.get("PAZ_COGNOME")
        raw_nome = row.get("PAZ_NOME")
        raw_dob = row.get("PAZ_DATA_NASCITA")

        if pd.isna(raw_cognome) or not str(raw_cognome).strip():
            continue

        cognome = clean_title(raw_cognome)
        nome = clean_title(raw_nome) if pd.notna(raw_nome) and str(raw_nome).strip() else None

        dob: Optional[date] = None
        dob_iso: Optional[str] = None
        if pd.notna(raw_dob):
            try:
                ts = pd.to_datetime(raw_dob, errors="coerce")
                if not pd.isna(ts):
                    dob = ts.date()
                    dob_iso = dob.isoformat()
            except Exception:
                pass

        cf = str(row.get("PAZ_CODICE_FISCALE") or "").strip() or None
        sesso = str(row.get("PAZ_SESSO") or "").strip().upper() or None
        comune = clean_title(row.get("PAZ_COMUNE_NASCITA")) or None
        priamo_id = str(row.get("PAZ_PROGRESSIVO_PAZ1") or "").strip() or None
        telefono = str(row.get("PRENO_CELLULARE") or row.get("PRENO_TELEFONO") or "").strip() or None

        agenda_codice = str(row.get("EROG_CODICE") or "").strip()
        slot_data = str(row.get("SLOT_DATA"))[:10] if pd.notna(row.get("SLOT_DATA")) else ""
        slot_ora = str(row.get("SLOT_ORA") or "").strip()
        prestazione = str(row.get("PREST_DESCRIZIONE") or "").strip()

        app_info = f"{slot_data} {slot_ora} - Agenda {agenda_codice} ({prestazione})".strip()

        dedup_key = (cognome.lower(), (nome or "").lower(), dob_iso)

        if dedup_key in pazienti_map:
            p = pazienti_map[dedup_key]
            p["righe_excel"].append(excel_row_num)
            p["num_appuntamenti"] += 1
            if agenda_codice and agenda_codice not in p["agende"]:
                p["agende"].append(agenda_codice)
            if app_info and app_info not in p["note_list"]:
                p["note_list"].append(app_info)
            if not p["codice_fiscale"] and cf:
                p["codice_fiscale"] = cf
            if not p["priamo_id"] and priamo_id:
                p["priamo_id"] = priamo_id
            if not p["telefono"] and telefono:
                p["telefono"] = telefono
        else:
            pazienti_map[dedup_key] = {
                "cognome": cognome,
                "nome": nome,
                "data_nascita": dob,
                "righe_excel": [excel_row_num],
                "codice_fiscale": cf,
                "sesso": sesso,
                "comune_nascita": comune,
                "priamo_id": priamo_id,
                "telefono": telefono,
                "agende": [agenda_codice] if agenda_codice else [],
                "num_appuntamenti": 1,
                "note_list": [app_info] if app_info else [],
            }

    pazienti: List[Paziente] = []
    for data in pazienti_map.values():
        note_str = "; ".join(data.pop("note_list", [])) or None
        p = Paziente(**data, note=note_str)
        if only_complete and not p.is_complete:
            continue
        pazienti.append(p)

    pazienti.sort(key=lambda p: (
        p.cognome,
        p.nome or "",
        p.data_nascita or date(1900, 1, 1)
    ))
    return pazienti


def estrai_pazienti_univoci(
    filepath: Union[str, Path, pd.DataFrame],
    sheet_name: Optional[Union[str, int]] = None,
    only_complete: bool = False
) -> List[Paziente]:
    """Legge il file Excel (file condiviso multidisciplinare es. positivi_2026.xls oppure

    foglio di lavoro PriamoCUP), individua il formato, deduplica le occorrenze multiple
    e restituisce l'elenco ordinato di Pazienti univoci.

    Args:
        filepath: Percorso del file Excel (.xls o .xlsx) o DataFrame pandas.
        sheet_name: Nome o indice del foglio da leggere.
        only_complete: Se True, esclude i record privi di data di nascita o nome.

    Returns:
        Lista ordinata di oggetti Paziente deduplicati.
    """
    if isinstance(filepath, pd.DataFrame):
        cols_upper = [str(c).strip().upper() for c in filepath.columns]
        if "PAZ_COGNOME" in cols_upper and "PAZ_NOME" in cols_upper:
            return estrai_pazienti_da_fogli_cup(filepath, only_complete=only_complete)
        df = filepath
        path_name = "DataFrame"
    else:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"File non trovato: {path}")

        path_name = path.name
        xl = pd.ExcelFile(path)
        target_sheet = sheet_name if sheet_name is not None else ("Foglio1" if "Foglio1" in xl.sheet_names else xl.sheet_names[0])

        # Verifica preliminare delle intestazioni per rilevamento automatico formato PriamoCUP
        df_sample = pd.read_excel(path, sheet_name=target_sheet, nrows=5)
        cols_upper = [str(c).strip().upper() for c in df_sample.columns]

        if "PAZ_COGNOME" in cols_upper and "PAZ_NOME" in cols_upper:
            # È un report esportato da PriamoCUP!
            df_cup = pd.read_excel(path, sheet_name=target_sheet)
            return estrai_pazienti_da_fogli_cup(df_cup, only_complete=only_complete)

        df = pd.read_excel(path, sheet_name=target_sheet, header=None)

    if df.empty:
        logger.warning(f"Il foglio in {path_name} è vuoto.")
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
    has_cf = any(bool(p.codice_fiscale) for p in pazienti)
    has_agende = any(bool(p.agende) for p in pazienti)

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
    table.add_column("Cognome (PAZ_COGNOME)", style="bold white")
    table.add_column("Nome (PAZ_NOME)", style="white")
    table.add_column("Data Nascita (PAZ_DATA_NASCITA)", justify="center")
    if has_cf:
        table.add_column("Codice Fiscale", style="cyan")
    if has_agende:
        table.add_column("Agende", justify="center", style="yellow")
    table.add_column("Stato", justify="center")
    table.add_column("Righe Excel", justify="center", style="dim")

    for idx, p in enumerate(pazienti, 1):
        dob_str = p.data_nascita.strftime("%d/%m/%Y") if p.data_nascita else "[yellow]N.D.[/yellow]"
        status_badge = "[green]COMPLETO[/green]" if p.is_complete else "[yellow]INCOMPLETO[/yellow]"
        rows_str = ", ".join(map(str, p.righe_excel)) if p.righe_excel else "-"
        if len(p.righe_excel) > 1:
            rows_str = f"[magenta]{rows_str}[/magenta]"

        row_cells = [
            str(idx),
            p.patient_id,
            p.cognome,
            p.nome or "[yellow]N.D.[/yellow]",
            dob_str,
        ]
        if has_cf:
            row_cells.append(p.codice_fiscale or "-")
        if has_agende:
            row_cells.append(", ".join(p.agende) if p.agende else "-")
        row_cells.extend([status_badge, rows_str])

        table.add_row(*row_cells)

    console.print(table)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Estrae, normalizza e deduplica l'anagrafica pazienti dal file condiviso (es. positivi_2026.xls) o da fogli di lavoro PriamoCUP."
    )
    parser.add_argument(
        "--input", "-i",
        default="data/positivi_2026.xls",
        help="Percorso del file Excel di input (default: data/positivi_2026.xls)."
    )
    parser.add_argument(
        "--output", "-o",
        default="data/output/anagrafica_pazienti_univoci.csv",
        help="Percorso in cui salvare il file di esportazione (.csv, .xlsx o .json)."
    )
    parser.add_argument(
        "--sheet", "-s",
        default=None,
        help="Nome o indice del foglio Excel da analizzare (default: Foglio1 o primo foglio)."
    )
    parser.add_argument(
        "--format", "-f",
        choices=["table", "csv", "xlsx", "json"],
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
    # Parametri per integrazione diretta PriamoCUP
    parser.add_argument(
        "--download-cup", "--from-cup",
        action="store_true",
        help="Scarica direttamente i fogli di lavoro da PriamoCUP prima dell'estrazione."
    )
    parser.add_argument(
        "--ytd", "--da-gennaio",
        action="store_true",
        help="Scarica da PriamoCUP da 1° Gennaio fino a Oggi - 1 giorno (Ieri)."
    )
    parser.add_argument(
        "--year", "--anno",
        type=int,
        default=None,
        help="Scarica da PriamoCUP l'intero anno specificato (es. 2025, 2024, ecc.)."
    )
    parser.add_argument(
        "--month", "--mese",
        default=None,
        help="Scarica da PriamoCUP uno specifico mese in formato YYYY-MM (es. 2025-10, 2026-03)."
    )
    parser.add_argument(
        "--months", "--ultimi-mesi",
        type=int,
        default=None,
        help="Scarica da PriamoCUP gli ultimi N mesi fino a Oggi - 1 giorno."
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Data inizio periodo download PriamoCUP (YYYY-MM-DD). Default: inizio settimana corrente."
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Data fine periodo download PriamoCUP (YYYY-MM-DD). Default: fine settimana corrente."
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=1,
        help="Numero di settimane da esportare con PriamoCUP (default: 1)."
    )
    parser.add_argument(
        "--unit",
        default="37002",
        help="Codice unità operativa per PriamoCUP (default: '37002' Senologia)."
    )
    parser.add_argument(
        "--agendas",
        default="3000,3003,3004,3035,3036",
        help="Elenco agende/erogatori PriamoCUP separate da virgola (default: 3000,3003,3004,3035,3036)."
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Se richiesto download diretto da PriamoCUP
    download_requested = args.download_cup or args.ytd or args.year or args.month or args.months
    if download_requested:
        from src.priamo_cup_client import PriamoCupClient
        cup_client = PriamoCupClient()
        agendas_list = [a.strip() for a in args.agendas.split(",") if a.strip()]

        if args.ytd:
            print(f"-> Download fogli lavoro PriamoCUP: YTD (1° Gennaio -> Ieri) per Unità {args.unit}, Agende {agendas_list}...")
            downloaded_file = cup_client.export_ytd(
                codice_unita=args.unit,
                agende=agendas_list,
                output_dir="data/cup",
            )
        elif args.year:
            print(f"-> Download fogli lavoro PriamoCUP per l'anno {args.year} (Unità {args.unit}, Agende {agendas_list})...")
            downloaded_file = cup_client.export_year(
                year=args.year,
                codice_unita=args.unit,
                agende=agendas_list,
                output_dir="data/cup",
            )
        elif args.month:
            month_str = str(args.month).strip()
            if "-" in month_str:
                parts = month_str.split("-")
                target_year = int(parts[0])
                target_month = int(parts[1])
            else:
                target_year = date.today().year
                target_month = int(month_str)

            print(f"-> Download fogli lavoro PriamoCUP per il mese {target_month:02d}/{target_year} (Unità {args.unit}, Agende {agendas_list})...")
            downloaded_file = cup_client.export_month(
                year=target_year,
                month=target_month,
                codice_unita=args.unit,
                agende=agendas_list,
                output_dir="data/cup",
            )
        elif args.months:
            print(f"-> Download fogli lavoro PriamoCUP per gli ultimi {args.months} mesi (Unità {args.unit}, Agende {agendas_list})...")
            downloaded_file = cup_client.export_last_months(
                num_months=args.months,
                codice_unita=args.unit,
                agende=agendas_list,
                output_dir="data/cup",
            )
        elif args.start_date and args.end_date:
            print(f"-> Download fogli lavoro PriamoCUP (Unità {args.unit}, Agende {agendas_list}, Periodo {args.start_date} -> {args.end_date})...")
            downloaded_file = cup_client.export_worksheets(
                start_date=args.start_date,
                end_date=args.end_date,
                codice_unita=args.unit,
                agende=agendas_list,
                output_dir="data/cup",
            )
        else:
            ref_d = args.start_date or date.today()
            print(f"-> Download fogli lavoro PriamoCUP per {args.weeks} settimana/e a partire da {ref_d} (Unità {args.unit}, Agende {agendas_list})...")
            if args.weeks > 1:
                files = cup_client.export_weeks(
                    start_date=ref_d,
                    num_weeks=args.weeks,
                    codice_unita=args.unit,
                    agende=agendas_list,
                    output_dir="data/cup",
                )
                downloaded_file = files[0] if files else None
            else:
                downloaded_file = cup_client.export_week(
                    ref_date=ref_d,
                    codice_unita=args.unit,
                    agende=agendas_list,
                    output_dir="data/cup",
                )

        if not downloaded_file:
            print("[ERRORE] Nessun file scaricato da PriamoCUP.", file=sys.stderr)
            sys.exit(1)

        input_path = Path(downloaded_file)
    else:
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
        is_xlsx = out_path.suffix.lower() == ".xlsx" or args.format == "xlsx"

        if is_json:
            import json
            data = [p.to_export_dict() for p in pazienti]
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"\n[OK] Esportati {len(pazienti)} record in formato JSON: {out_path}")
        elif is_xlsx:
            df_out = pd.DataFrame([p.to_export_dict() for p in pazienti])
            df_out.to_excel(out_path, index=False)
            print(f"\n[OK] Esportati {len(pazienti)} record in formato Excel: {out_path}")
        else:
            # CSV con colonne PAZ_NOME, PAZ_COGNOME, PAZ_DATA_NASCITA e standard
            df_out = pd.DataFrame([p.to_export_dict() for p in pazienti])
            df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"\n[OK] Esportati {len(pazienti)} record in formato CSV: {out_path}")


if __name__ == "__main__":
    main()
