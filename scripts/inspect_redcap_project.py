"""Inspects the live REDCap project configured in .env and summarizes all forms and fields."""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import requests

# Add root directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import REDCapField

load_dotenv()

raw_url = os.getenv("REDCAP_API_URL", "").strip()
API_TOKEN = os.getenv("REDCAP_API_TOKEN", "").strip()

if not raw_url or not API_TOKEN:
    print("ERRORE: REDCAP_API_URL o REDCAP_API_TOKEN non impostati in .env!")
    exit(1)

clean_url = raw_url.rstrip("/")
if not clean_url.endswith("/api"):
    API_URL = clean_url + "/api/"
else:
    API_URL = clean_url + "/"


def api_call(content: str, extra_params: dict = None) -> dict | list:
    payload = {
        "token": API_TOKEN,
        "content": content,
        "format": "json",
        "returnFormat": "json"
    }
    if extra_params:
        payload.update(extra_params)
    
    response = requests.post(API_URL, data=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def main():
    print(f"=== CONNESSIONE A REDCAP ({API_URL}) ===")
    
    # 1. Project info
    try:
        project_info = api_call("project")
        print("\n--- INFORMAZIONI PROGETTO ---")
        print(f"Titolo: {project_info.get('project_title')}")
        print(f"ID Progetto: {project_info.get('project_id')}")
        print(f"Longitudinale: {'Sì' if project_info.get('is_longitudinal') == 1 else 'No'}")
        print(f"Record Count: {project_info.get('record_count', 'N/D')}")
    except Exception as e:
        print(f"Info progetto non disponibili o permessi limitati: {e}")

    # 2. Instruments / Forms
    try:
        instruments = api_call("instrument")
        print(f"\n--- FORMS / STRUMENTI TROVATI ({len(instruments)}) ---")
        for inst in instruments:
            print(f"- {inst.get('instrument_name')}: \"{inst.get('instrument_label')}\"")
    except Exception as e:
        print(f"Impossibile recuperare lista strumenti: {e}")
        instruments = []

    # 3. Metadata
    print("\n--- RECUPERO DATA DICTIONARY (METADATA) ---")
    raw_metadata = api_call("metadata")
    print(f"Totale campi nel progetto: {len(raw_metadata)}")

    # Save to local file for reference
    out_dir = Path("data")
    out_dir.mkdir(parents=True, exist_ok=True)
    live_meta_path = out_dir / "redcap_metadata_live.json"
    with open(live_meta_path, "w", encoding="utf-8") as f:
        json.dump(raw_metadata, f, ensure_ascii=False, indent=2)
    print(f"Salvato metadata completo in: {live_meta_path}")

    # Parse fields
    fields = [REDCapField(**item) for item in raw_metadata]
    
    # Group fields by form
    forms_dict = {}
    for f in fields:
        forms_dict.setdefault(f.form_name, []).append(f)

    print("\n=== STRUTTURA DETTAGLIATA DEI FORM DA POPOLARE ===")
    for form_name, form_fields in forms_dict.items():
        extractable = [f for f in form_fields if f.is_extractable]
        print(f"\n=======================================================")
        print(f"FORM: '{form_name}' (Totale campi: {len(form_fields)}, Estraibili: {len(extractable)})")
        print(f"=======================================================")
        for f in form_fields:
            if not f.is_extractable:
                print(f"  [SKIP {f.field_type.upper()}] {f.field_name}: {f.field_label[:60]}")
                continue
            
            choices = f.parse_choices()
            choices_summary = ""
            if choices:
                choices_summary = f" -> Opzioni: {list(choices.items())[:5]}"
                if len(choices) > 5:
                    choices_summary += f"... (+{len(choices)-5} opzioni)"
            
            validation = f" (Validazione: {f.text_validation_type_or_show_slider_number})" if f.text_validation_type_or_show_slider_number else ""
            required = " [OBBLIGATORIO]" if f.required_field == "y" else ""
            print(f"  * {f.field_name} ({f.field_type}){validation}{required}: \"{f.field_label.strip()[:70]}\"{choices_summary}")


if __name__ == "__main__":
    main()
