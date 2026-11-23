import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Default comprehensive clinical guidelines across all REDCap forms
DEFAULT_CLINICAL_GUIDELINES: Dict[str, Any] = {
    "version": "1.0",
    "description": "Linee guida cliniche per l'estrazione assistita da IA dei referti senologici su REDCap.",
    "forms": {
        "anatomia_patologica_preoperatoria": {
            "title": "Anatomia Patologica Pre-operatoria (Agobiopsie e Citologia)",
            "description": "Biopsie con ago tranciante (Tru-cut, CB), aspirazione con vuoto (VABB/Mammotome) e agoaspirato (FNA).",
            "general_guidelines": [
                "Distingui chiaramente tra esame citologico (agoaspirato/FNA: classificazione C1-C5) e microistologico (core biopsy Tru-cut o VABB: classificazione B1-B5).",
                "Per core biopsy (Tru-cut / VABB), assegna rigorosamente la categoria B (B1=non diagnostico, B2=benigno, B3=dubbio a incerto potenziale di malignità, B4=sospetto di malignità, B5=positivo per carcinoma).",
                "Se result_cb non è '4' o '5' (non è B4/B5), i campi histological_type_b4_b5, istotype, nuclear_grade e i recettori biologici DEVONO essere rigorosamente null."
            ],
            "field_guidelines": {
                "result_cb": "Estrai l'esito della core biopsy (agobiopsia / Tru-cut / VAAB / VABB): 0=Non eseguito, 1=B1 (Inadeguato/Normale), 2=B2 (Benigno: fibroadenoma, mastopatia, adenosi), 3=B3 (Dubbio / lesione a incerto potenziale di malignità: LIN, FEA, papilloma, cicatrice sclerosante, tumore filloide benigno), 4=B4 (Sospetto di malignità), 5=B5 (Positivo per carcinoma: in situ o invasivo). Assegna la sola cifra corrispondente alla classe B.",
                "date_of_cb": "Data di esecuzione del prelievo bioptico (core biopsy Tru-cut o VABB) in formato YYYY-MM-DD.",
                "cytological_result_fna": "Esito esame citologico da agoaspirato: 0=Non eseguito, 1=C1 (inadeguato), 2=C2 (benigno), 3=C3 (atipie probabilmente benigne), 4=C4 (sospetto), 5=C5 (positivo per cancro), 9=Ignoto.",
                "data_of_fna": "Data esecuzione agoaspirato per citologia (FNA) in formato YYYY-MM-DD.",
                "histological_type_b4_b5": "Se result_cb è '4' o '5' (B4/B5): 1=Carcinoma in situ (es. DCIS, DIN), 2=Carcinoma invasivo (es. carcinoma duttale infiltrante NST, lobulare infiltrante). Se result_cb non è B4/B5 imposta null.",
                "istotype": "Istotipo su biopsia B4/B5: 1=Duttale, 2=Lobulare, 3=Tubulare, 4=Altro, 9=Ignoto.",
                "nuclear_grade": "Grado nucleare su biopsia B4/B5: 1=Basso, 2=Medio, 3=Alto, 9=Ignoto.",
                "er_status_at_cb": "Stato recettori estrogeni su biopsia: 0=Negativo (<1%), 1=Positivo (>=1%), 9=Ignoto.",
                "er_positive_cells_at_cb": "Percentuale numerica (0-100) di cellule neoplastiche positive per estrogeni (ER).",
                "pr_status_at_cb": "Stato recettori progesterone su biopsia: 0=Negativo (<1%), 1=Positivo (>=1%), 9=Ignoto.",
                "pr_positive_cells_at_cb": "Percentuale numerica (0-100) di cellule neoplastiche positive per progesterone (PgR).",
                "ki67_positive_cells_at_cb": "Percentuale numerica (0-100) dell'indice di proliferazione Ki-67 su biopsia.",
                "her2_overexpression_immunohistochemistry_at_cb": "Iperespressione HER2 immunoistochimica su biopsia: 0=Non eseguito, 1=Negativo (score 0 o 1+), 2=Dubbio/Equivoco (score 2+), 3=Positivo (score 3+), 9=Ignoto.",
                "fish_hybridization_at_cb": "Stato amplificazione genica HER2 (FISH/SISH/CISH): 2=Non eseguito, 0=Non amplificato (negativo), 1=Amplificato (positivo), 3=Equivoco, 9=Ignoto."
            }
        },
        "anatomia_patologica": {
            "title": "Anatomia Patologica Definitiva (Pezzo Operatorio Chirurgico)",
            "description": "Referto istologico definitivo post-operatorio del pezzo asportato (tumorectomia, quadrantectomia, mastectomia, linfonodo sentinella, dissezione ascellare).",
            "general_guidelines": [
                "Estrai la diagnosi istologica definitiva sul pezzo operatorio, le dimensioni del tumore (mm), la stadiazione pT e pN, il grading istologico (G1, G2, G3) e l'assetto recettoriale definitivo.",
                "Non confondere l'esame istologico definitivo con la biopsia pre-operatoria o con l'esame estemporaneo intraoperatorio al congelatore.",
                "Per il linfonodo sentinella con metodica OSNA, estrai il conteggio copie CK19 e assegna l'esito corretto (negativo, micrometastasi, macrometastasi)."
            ],
            "field_guidelines": {
                "histopathological_diagnosis_of_operative_specimen": "Diagnosi istopatologica pezzo operatorio: 0=Benigno, 1=Lesione intraepiteliale, 2=In situ (DCIS/DIN), 3=Microinvasivo, 4=Invasivo (carcinoma infiltrante), 5=Maligno non epiteliale, 6=Altro, 7=Assenza di tumore residuo (pCR completa risposta patologica post-neoadiuvante).",
                "invasive_histological_type": "Istotipo invasivo definitivo: 1=Duttale NAS/NST, 2=Lobulare, 3=Tubulare, 4=Cribriforme, 5=Simil-midollare, 6=Mucinoso, 7=Misto, 8=Apocrino, 9=Micropapillare, 10=Midollare, 88=Altro.",
                "grade": "Grado istologico del carcinoma invasivo: 1=Grado 1 (ben differenziato / basso), 2=Grado 2 (moderatamente differenziato / intermedio), 3=Grado 3 (scarsamente differenziato / alto), 4=Non valutabile.",
                "pt": "Stadiazione patologica pT: IS=in situ, 1mic=microinvasivo <=1mm, 1a=>1mm e <=5mm, 1b=>5mm e <=10mm, 1c=>10mm e <=20mm, 2=>20mm e <=50mm, 3=>50mm, 4=estensione a parete/cute o infiammatorio, X=non valutabile.",
                "pn": "Stadiazione patologica pN dissezione ascellare: 0=nessuna metastasi, 1mi=micrometastasi (0.2-2mm), 1a=1-3 linfonodi, 2a=4-9 linfonodi, 3a=10 o più linfonodi, X=non valutabile.",
                "positive_post_operative_histology": "Metastasi al definitivo post-operatorio nei linfonodi: 1=Sì, 0=No.",
                "sentinel_lymphnode_method": "Metodica esame linfonodo sentinella: 1=OSNA (biologia molecolare copie mRNA CK19), 2=Congelatore / Istologia tradizionale, 9=Non noto.",
                "osna_results": "Esito analisi molecolare OSNA linfonodo sentinella: 0=Negativo (<160 copie), 1=Micrometastasi (160-2500 copie, indicato con +), 2=Macrometastasi (>2500 copie, indicato con ++).",
                "ck19_copies": "Numero esatto di copie mRNA CK19 indicato nel referto OSNA.",
                "involved_margins_at_1st_surgical_procedure": "Margini chirurgici al 1° intervento: 0=No (indenni/negativi), 1=A ridosso (focale prossimità / <1mm o <2mm), 2=Sì (interessati/positivi per neoplasia invasiva o in situ), 9=Ignoto.",
                "size_of_minimum_free_margin_from_invasive_mm1": "Distanza minima (in mm) del carcinoma invasivo dal margine chirurgico più vicino.",
                "size_of_minimum_free_margin_from_in_situ_mm1": "Distanza minima (in mm) della componente in situ dal margine chirurgico più vicino."
            }
        },
        "struttura_della_lesione": {
            "title": "Chirurgia Senologica e Descrizione della Lesione",
            "description": "Intervento chirurgico mammaria e ascellare (visita chirurgica, cartella operatoria, dimissione).",
            "general_guidelines": [
                "Collega sempre i referti di Chirurgia Senologica a questo form.",
                "Estrai la data dell'intervento, la tipologia di chirurgia conservativa vs mastectomia e gli atti chirurgici ascellari (SLNB vs dissezione)."
            ],
            "field_guidelines": {
                "bi1": "Esecuzione intervento chirurgico sulla mammella: 1=Sì, 0=No.",
                "date_of_bi1": "Data del 1° intervento chirurgico sulla mammella (YYYY-MM-DD).",
                "first_breast_operation": "Tipo del primo intervento: 1=Biopsia incisionale, 2=Duttectomia, 3=Biopsia escissionale/tumorectomia, 4=Resezione ampia, 5=Quadrantectomia/settorectomia, 6=Mastectomia.",
                "type_of_mastectomy_at_bi1": "Se primo intervento è mastectomia ('6'): 1=Totale, 2=Skin Reducing, 3=Skin Sparing, 4=Nipple Sparing.",
                "sentinel_lymph_node_biopsy": "Esecuzione biopsia del linfonodo sentinella: 1=Sì, 0=No.",
                "date_of_sentinel_lymph_node_biopsy": "Data esecuzione biopsia linfonodo sentinella (YYYY-MM-DD).",
                "axillary_dissection": "Esecuzione dissezione ascellare (svuotamento): 1=Sì, 0=No.",
                "date_of_axillary_dissection": "Data esecuzione dissezione ascellare (YYYY-MM-DD).",
                "frozen_section_of_the_lesion_at_bi1": "Esame estemporaneo intraoperatorio della lesione: 0=Non eseguito, 1=Sì negativo, 2=Sì dubbio, 3=Sì positivo Tis, 4=Sì positivo invasivo, 5=Sì esito ignoto.",
                "frozen_section_of_specimen_margins_at_bi1": "Esame estemporaneo intraoperatorio dei margini: 0=Non eseguito, 1=Sì negativo, 2=Sì Tis in prossimità, 3=Sì invasivo in prossimità, 4=Sì positivo Tis, 5=Sì positivo invasivo, 6=Sì esito ignoto."
            }
        },
        "radiologia": {
            "title": "Diagnostica Senologica e Procedure Guidate da Imaging",
            "description": "Mammografia, tomosintesi, ecografia mammaria/ascellare, risonanza magnetica mammaria.",
            "general_guidelines": [
                "Estrai la classificazione BI-RADS per ciascuna metodica diagnostica.",
                "Se è presente un prelievo bioptico eseguito sotto guida ecografica o stereotassica (mammotome/VABB), estrai la tecnica utilizzata in cb_technique."
            ],
            "field_guidelines": {
                "result_of_mx_birads": "Classificazione BI-RADS della mammografia: 0=Incompleta, 1=Negativa, 2=Benigna, 3=Probabilmente benigna, 4A=Basso sospetto, 4B=Intermedio sospetto, 4C=Moderato sospetto, 5=Alto sospetto, 6=Malignità già accertata.",
                "date_of_mx": "Data di esecuzione della mammografia (YYYY-MM-DD).",
                "result_of_us": "Esito ecografia mammaria: 1=U1 Negativo, 2=U2 Benigno, 3=U3 Dubbio, 4=U4 Sospetto, 5=U5 Positivo per cancro, 0=Non eseguita.",
                "date_of_us": "Data di esecuzione dell'ecografia mammaria (YYYY-MM-DD).",
                "cb_technique": "Tecnica della core biopsy eseguita sotto guida radiologica: 1=Agobiopsia / Tru-cut / ago a scatto, 2=Vacuum assisted (VABB / Mammotome), 3=Altro, 9=Ignoto.",
                "mr_result": "Esito risonanza magnetica mammaria: 0=Non eseguita, 7=MR0 Incompleta, 1=MR1 Negativa, 2=MR2 Benigno, 3=MR3 Probabilmente benigno, 4=MR4 Sospetta, 5=MR5 Alto sospetto, 6=MR6 Cancro già accertato da biopsia."
            }
        },
        "dati_del_paziente": {
            "title": "Dati Anagrafici e Prima Visita",
            "description": "Anagrafica paziente, data prima visita senologica o discussione multidisciplinare.",
            "general_guidelines": [
                "Estrai i dati anagrafici e demografici primari.",
                "Estrai la data della prima visita senologica o discussione multidisciplinare."
            ],
            "field_guidelines": {
                "first_visit_date": "Data della prima visita senologica o discussione multidisciplinare in formato YYYY-MM-DD."
            }
        },
        "chemioterapia": {
            "title": "Terapie Mediche Oncologiche",
            "description": "Trattamenti chemioterapici (neoadiuvante / adiuvante), terapia biologica anti-HER2 e ormonoterapia.",
            "general_guidelines": [
                "Distingui chiaramente tra chemioterapia primaria (neoadiuvante, pre-operatoria) e adiuvante (post-operatoria).",
                "Estrai se la terapia biologica con Trastuzumab/Herceptin o l'ormonoterapia sono state prescritte o somministrate."
            ],
            "field_guidelines": {
                "primary_chemotherapy": "Esecuzione chemioterapia primaria (neoadiuvante): 1=Sì, 0=No, 2=No per rifiuto paziente.",
                "adjuvant_chemotherapy_administered": "Esecuzione chemioterapia adiuvante: 1=Sì, 0=No, 2=No per rifiuto paziente.",
                "adjuvant_therapy_with_biological_drugs": "Esecuzione terapia adiuvante con farmaci biologici (es. Trastuzumab/Herceptin): 1=Sì, 0=No, 2=No per rifiuto paziente."
            }
        },
        "radioterapia": {
            "title": "Radioterapia Oncologica",
            "description": "Trattamento radiante post-operatorio su mammella, parete toracica o stazioni linfonodali.",
            "general_guidelines": [
                "Estrai la data di inizio e fine del ciclo di radioterapia e i distretti anatomici irradiati."
            ],
            "field_guidelines": {
                "radiotherapy_performed": "Esecuzione radioterapia: 1=Sì, 0=No, 2=No per rifiuto paziente.",
                "whole_breast": "Radioterapia sull'intera mammella residua (whole breast): 1=Cobalto60, 2=x-MV, 0=No."
            }
        },
        "recidiva": {
            "title": "Follow-up e Recidiva di Malattia",
            "description": "Dati di ripresa di malattia mammaria, regionale o a distanza durante il follow-up.",
            "general_guidelines": [
                "Identifica con esattezza la sede anatomica della recidiva e la data di prima accertamento."
            ],
            "field_guidelines": {
                "breast_local_recurrence": "Recidiva locale nella mammella operata/residua: 1=Sì, 0=No.",
                "date_of_breast_local_recurrence": "Data accertamento della recidiva locale mammaria (YYYY-MM-DD)."
            }
        }
    }
}


class ClinicalGuidelinesManager:
    """Manages form-level and field-level clinical guidelines for AI prompt and schema construction.
    Loads from `config/clinical_guidelines.json` and automatically falls back to rich built-in defaults.
    Allows easy customization, runtime editing, and persistence so clinicians and users can guide
    how the LLM interprets challenging clinical variables.
    """

    _instance: Optional["ClinicalGuidelinesManager"] = None

    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        self.config_path = Path(config_path) if config_path else Path("./config/clinical_guidelines.json")
        self.data: Dict[str, Any] = {}
        self.load()

    @classmethod
    def get_instance(cls, config_path: Optional[Union[str, Path]] = None) -> "ClinicalGuidelinesManager":
        """Singleton accessor."""
        if cls._instance is None:
            cls._instance = cls(config_path=config_path)
        elif config_path and Path(config_path) != cls._instance.config_path:
            cls._instance = cls(config_path=config_path)
        return cls._instance

    def load(self) -> None:
        """Loads guidelines from JSON config file or initializes defaults if not present."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                # Merge file data on top of defaults
                self.data = self._deep_merge(DEFAULT_CLINICAL_GUIDELINES, file_data)
                logger.info(f"Linee guida cliniche caricate con successo da {self.config_path}")
            except Exception as e:
                logger.warning(f"Errore nella lettura di {self.config_path} ({e}). Utilizzo linee guida predefinite.")
                self.data = json.loads(json.dumps(DEFAULT_CLINICAL_GUIDELINES))
        else:
            # File doesn't exist yet: initialize with defaults and save to provide a template
            self.data = json.loads(json.dumps(DEFAULT_CLINICAL_GUIDELINES))
            self.save()
            logger.info(f"File linee guida cliniche inizializzato con i default in {self.config_path}")

    def save(self) -> None:
        """Saves current guidelines back to JSON config file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Linee guida cliniche salvate in {self.config_path}")
        except Exception as e:
            logger.error(f"Errore nel salvataggio delle linee guida cliniche su {self.config_path}: {e}")

    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively merges override dictionary into base dictionary."""
        result = json.loads(json.dumps(base))
        for key, val in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = self._deep_merge(result[key], val)
            else:
                result[key] = val
        return result

    def get_forms(self) -> Dict[str, Any]:
        """Returns the dictionary of configured forms."""
        return self.data.get("forms", {})

    def list_forms(self) -> List[str]:
        """Returns list of form names having guidelines."""
        return list(self.get_forms().keys())

    def get_form_title(self, form_name: str) -> str:
        """Gets user-friendly title for a form."""
        form_info = self.get_forms().get(form_name, {})
        return form_info.get("title", form_name)

    def get_form_description(self, form_name: str) -> str:
        """Gets description for a form."""
        form_info = self.get_forms().get(form_name, {})
        return form_info.get("description", "")

    def get_form_general_guidelines(self, form_name: str) -> List[str]:
        """Returns list of general prompt instructions for a given form."""
        form_info = self.get_forms().get(form_name, {})
        return list(form_info.get("general_guidelines", []))

    def get_field_guidelines(self, form_name: str) -> Dict[str, str]:
        """Returns field-level guidelines dictionary for a given form."""
        form_info = self.get_forms().get(form_name, {})
        return dict(form_info.get("field_guidelines", {}))

    def get_field_guideline(self, field_name: str, form_name: Optional[str] = None) -> Optional[str]:
        """Searches for a specific field guideline, checking form_name first or scanning all forms."""
        forms = self.get_forms()
        if form_name and form_name in forms:
            fields = forms[form_name].get("field_guidelines", {})
            if field_name in fields:
                return fields[field_name]

        # Scan other forms if not found in target form
        for f_name, f_data in forms.items():
            fields = f_data.get("field_guidelines", {})
            if field_name in fields:
                return fields[field_name]
        return None

    def set_field_guideline(
        self,
        form_name: str,
        field_name: str,
        guideline: str,
        save: bool = True
    ) -> None:
        """Adds or updates a field-level guideline."""
        forms = self.data.setdefault("forms", {})
        form_dict = forms.setdefault(form_name, {
            "title": form_name,
            "description": "",
            "general_guidelines": [],
            "field_guidelines": {}
        })
        field_dict = form_dict.setdefault("field_guidelines", {})
        field_dict[field_name] = guideline
        if save:
            self.save()

    def add_form_general_guideline(
        self,
        form_name: str,
        rule: str,
        save: bool = True
    ) -> None:
        """Adds a general guideline rule to a form."""
        forms = self.data.setdefault("forms", {})
        form_dict = forms.setdefault(form_name, {
            "title": form_name,
            "description": "",
            "general_guidelines": [],
            "field_guidelines": {}
        })
        rules = form_dict.setdefault("general_guidelines", [])
        if rule not in rules:
            rules.append(rule)
        if save:
            self.save()

    def remove_field_guideline(self, form_name: str, field_name: str, save: bool = True) -> bool:
        """Removes a field guideline if present."""
        forms = self.get_forms()
        if form_name in forms:
            fields = forms[form_name].get("field_guidelines", {})
            if field_name in fields:
                del fields[field_name]
                if save:
                    self.save()
                return True
        return False
