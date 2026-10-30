"""Generates a comprehensive markdown documentation of the REDCap forms and fields structure."""

import json
from pathlib import Path

with open("data/redcap_metadata_live.json", "r", encoding="utf-8") as f:
    fields = json.load(f)

forms = {}
for f in fields:
    forms.setdefault(f.get("form_name"), []).append(f)

FORM_DESCRIPTIONS = {
    "dati_del_paziente": "Anagrafica paziente, comorbidità (ipertensione, diabete, cardiopatia, tiroide), familiarità e genetica (BRCA1/2), metastasi a distanza e follow-up.",
    "radiologia": "Indagini diagnostiche senologiche: Mammografia 2D/3D tomosintesi (esito EU R1-R5, BI-RADS 0-6), Ecografia mammaria (U1-U5), Risonanza Magnetica (MR1-MR5), stadio clinico cTNM e indicazioni.",
    "anatomia_patologica_preoperatoria": "Biopsie diagnostiche pre-operatorie: Agoaspirato (FNA C1-C5), Agobiopsia (Core Biopsy B1-B5), istotipo, grado nucleare, recettori ER, PgR, Ki-67 e HER2.",
    "struttura_della_lesione": "Dati chirurgici: sede lesione (quadranti QSE, QSI, QIE, QII), lateralità (destra/sinistra), tipo di chirurgia (conservativa/mastectomia), oncoplastica, ricostruzione e chirurgia ascellare (sentinella/dissezione).",
    "anatomia_patologica": "Esame istologico definitivo post-operatorio: istotipo invasivo e in situ, grado istologico (Elston-Ellis), pTNM patologico, margini chirurgici, valutazione linfonodale (OSNA/congelatore) e profilo molecolare definitivo.",
    "radioterapia": "Dati del trattamento radioterapico: erogazione, sede e volume irradiato (mammella intera, boost, parete toracica, catene linfonodali), date.",
    "chemioterapia": "Terapie oncologiche mediche: chemioterapia neoadiuvante vs adiuvante, farmaci biologici (trastuzumab), ormonoterapia primaria e adiuvante (tamoxifene, inibitori aromatasi, analoghi GnRH).",
    "recidiva": "Monitoraggio recidive di malattia: recidiva locale mammaria, ascellare omolaterale, catena mammaria interna, sovraclaveare e parete toracica."
}

lines = []
lines.append("# Mappatura Struttura Progetto REDCap: SenoNetwork\n")
lines.append(f"**Progetto**: SenoNetwork (ID: 20)  ")
lines.append(f"**Totale Strumenti / Form**: {len(forms)}  ")
lines.append(f"**Totale Campi**: {len(fields)}\n")
lines.append("---\n")

for form_name, form_fields in forms.items():
    desc = FORM_DESCRIPTIONS.get(form_name, "")
    extractable = [f for f in form_fields if f.get("field_type") not in {"descriptive", "calc", "file"}]
    form_label = form_name.replace("_", " ").title()
    
    lines.append(f"## 📋 Form: `{form_name}` ({form_label})")
    lines.append(f"**Scopo Clinico**: {desc}  ")
    lines.append(f"**Campi totali**: {len(form_fields)} | **Campi estraibili per l'IA**: {len(extractable)}\n")
    
    lines.append("| Variabile / Campo | Tipo | Etichetta Clinica | Scelte / Validazione |")
    lines.append("|---|---|---|---|")
    
    for f in form_fields:
        fname = f.get("field_name")
        ftype = f.get("field_type")
        flabel = f.get("field_label", "").replace("\n", " ").replace("|", "/").strip()
        choices = f.get("select_choices_or_calculations") or ""
        val = f.get("text_validation_type_or_show_slider_number") or ""
        
        info = ""
        if val:
            info += f"Val: `{val}` "
        if choices:
            clean_choices = choices.replace("\n", " | ").replace("\r", "")
            if len(clean_choices) > 60:
                clean_choices = clean_choices[:57] + "..."
            info += f"Opzioni: {clean_choices}"
        
        lines.append(f"| `{fname}` | `{ftype}` | {flabel[:65]} | {info} |")
    
    lines.append("\n---\n")

output_path = Path("docs/REDCAP_STRUCTURE.md")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"Creato documento: {output_path}")
