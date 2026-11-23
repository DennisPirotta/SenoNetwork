import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Union
import requests

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

AVAILABLE_FORMS = {
    "radiologia": "Indagini radiologiche (Mammografia, Ecografia, Risonanza Magnetica, Tomosintesi, BI-RADS)",
    "anatomia_patologica_preoperatoria": "Biopsie pre-operatorie (Core Biopsy, Agobiopsia con frustoli, Agoaspirato FNA, B-classification B1-B5)",
    "anatomia_patologica": "Esame istologico definitivo post-operatorio su pezzo chirurgico (Quadrantectomia, Mastectomia, pTNM, OSNA)",
    "struttura_della_lesione": "Chirurgia Senologica e verbale operatorio (Tipo di intervento, chirurgia oncoplastica, chirurgia ascellare)",
    "dati_del_paziente": "Anagrafica, prima visita senologica, familiarità, indagine genetica BRCA, comorbidità",
    "chemioterapia": "Oncologia medica (Chemioterapia neoadiuvante/adiuvante, terapia ormonale, farmaci biologici)",
    "radioterapia": "Trattamento radioterapico (Dosi Gy, frazionamento, volume irradiato)",
    "recidiva": "Monitoraggio recidiva loco-regionale o a distanza"
}

# Mapping esplicito di alias e denominazioni cliniche verso i form REDCap
FORM_ALIASES = {
    "chirurgia senologica": "struttura_della_lesione",
    "chirurgia_senologica": "struttura_della_lesione",
    "chirurgica senologica": "struttura_della_lesione",
    "chirurgica_senologica": "struttura_della_lesione",
    "senologia chirurgica": "struttura_della_lesione",
    "senologia_chirurgica": "struttura_della_lesione",
    "visita chirurgica senologica": "struttura_della_lesione",
    "visita_chirurgica_senologica": "struttura_della_lesione",
    "verbale operatorio": "struttura_della_lesione",
    "verbale_operatorio": "struttura_della_lesione",
    "chirurgia": "struttura_della_lesione",
}


class FormClassifier:
    """Classifies a medical PDF report to automatically route it to the appropriate REDCap form."""

    @staticmethod
    def extract_exam_name(pdf_path_or_hint: Union[str, Path], text: Optional[str] = None) -> str:
        """Extracts and normalizes the exam/report name from filename or document content.
        
        Examples:
            '20260000552052_VISITA_CHIRURGICA_SENOLOGICA_C.pdf' -> 'VISITA_CHIRURGICA_SENOLOGICA'
            '20260000004108_VISITA_MULTIDISCIPLINARE_CENTR.pdf' -> 'VISITA_MULTIDISCIPLINARE_CENTR'
            '20260000035792.pdf' (with text 'Lettera di Dimissione') -> 'LETTERA_DI_DIMISSIONE'
        """
        raw_name = Path(pdf_path_or_hint).stem if isinstance(pdf_path_or_hint, (str, Path)) else str(pdf_path_or_hint)
        
        # 1. Pattern: {doc_id}_{safe_exam} or evento_{event_id}_{safe_exam}
        m = re.match(r"^(?:evento_)?\d+_(.+)$", raw_name)
        if m:
            exam_part = m.group(1).strip("_")
            # Normalize trailing hospital visit suffixes (_C = Controllo, _I = Iniziale/Prima visita)
            cleaned = re.sub(r"_[CI]$", "", exam_part)
            return cleaned.strip("_")

        # 2. Pattern: _NAME_
        stripped = raw_name.strip("_")
        if stripped and not stripped.isdigit():
            return stripped

        # 3. Fallback: inspect document text headers if provided
        if text:
            lower_text = text.lower()
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            for l in lines[:6]:
                lower_line = l.lower()
                if "lettera di dimissione" in lower_line:
                    return "LETTERA_DI_DIMISSIONE"
                if "laboratorio di immunoematologia" in lower_line:
                    return "LABORATORIO_IMMUNOEMATOLOGIA"
                if "laboratorio clinico generale" in lower_line:
                    return "LABORATORIO_ANALISI"
                if "diagnostica per immagini" in lower_line:
                    return "DIAGNOSTICA_PER_IMMAGINI"
                if "anatomia patologica" in lower_line:
                    return "ANATOMIA_PATOLOGICA"
                if "cardiologia" in lower_line or "elettrocardiogramma" in lower_line:
                    return "ECG"

            if "lettera di dimissione" in lower_text:
                return "LETTERA_DI_DIMISSIONE"
            if "verbale operatorio" in lower_text:
                return "VERBALE_OPERATORIO"
            if "visita chirurgica senologica" in lower_text:
                return "VISITA_CHIRURGICA_SENOLOGICA"

        return stripped or "ESAME_CLINICO"

    @staticmethod
    def map_exams(
        pdf_paths: List[Path],
        settings: Optional[Settings] = None
    ) -> Dict[str, Any]:
        """Maps a collection of PDF exam reports to their target REDCap forms with counters.
        
        Returns:
            Dict containing:
                - 'mappings': list of dicts with exam_name, filename, form, display string
                - 'counts': dict mapping form_name -> count of exams
                - 'total': total number of exams analyzed
        """
        from src.pdf_extractor import PDFExtractor

        mappings = []
        counts: Dict[str, int] = {}

        for pdf_path in pdf_paths:
            try:
                text = PDFExtractor.extract_text(pdf_path)
            except Exception:
                text = ""

            exam_name = FormClassifier.extract_exam_name(pdf_path, text)
            form = FormClassifier.classify_report(text=text, hint=pdf_path.name, settings=settings)

            display_str = f"[{exam_name}] (file: {pdf_path.name}) -> {form}"
            mappings.append({
                "exam_name": exam_name,
                "filename": pdf_path.name,
                "path": str(pdf_path),
                "form": form,
                "display": display_str
            })
            counts[form] = counts.get(form, 0) + 1

        return {
            "mappings": mappings,
            "counts": counts,
            "total": len(mappings)
        }

    @staticmethod
    def classify_report(
        text: str,
        hint: Optional[str] = None,
        settings: Optional[Settings] = None,
        ollama_url: Optional[str] = None,
        ollama_model: Optional[str] = None
    ) -> str:
        """Determines the target REDCap form name based on metadata hint, keyword heuristics, or local LLM.

        Args:
            text: Raw report text.
            hint: Optional filename or exam description.
            settings: Optional application Settings instance.
            ollama_url: Optional override for Ollama URL.
            ollama_model: Optional override for Ollama model.

        Returns:
            The matched REDCap form_name (e.g. 'radiologia', 'anatomia_patologica_preoperatoria', etc.)
        """
        cfg = settings or get_settings()
        target_ollama_url = ollama_url or cfg.ollama_base_url
        target_ollama_model = ollama_model or cfg.ollama_model
        target_llama_url = cfg.llama_cpp_base_url.rstrip("/")

        lower_text = text.lower()
        lower_hint = hint.lower() if hint else ""

        # 1. Early filtering: Non-senology / Irrelevant clinical documents
        irrelevant_keywords = [
            "ecg", "elettrocardiogramma", "torace_standard", "torace standard", "rx torace",
            "gruppo_-_rh", "gruppo rh", "gruppo sanguigno", "immunoematologia", "chimica clinica",
            "esami ematochimici", "emocromo", "fisiatr", "fisioterap", "ortoped",
            "infiltraz", "tampone", "antigenico"
        ]
        if any(kw in lower_hint for kw in irrelevant_keywords):
            return "skip"

        if (
            "laboratorio di immunoematologia" in lower_text
            or "laboratorio clinico generale" in lower_text
            or ("elettrocardiogramma" in lower_text and "mamm" not in lower_text)
            or ("ritmo sinusale" in lower_text and "mamm" not in lower_text)
            or ("campi polmonari indenni da lesioni" in lower_text and "mamm" not in lower_text)
            or ("trattamento riabilitativo motorio" in lower_text and "mamm" not in lower_text)
            or ("fisiatria" in lower_text and "mamm" not in lower_text)
            or ("fisioterapia" in lower_text and "mamm" not in lower_text)
        ):
            return "skip"

        # 2. Check explicit hint (e.g. from .NET API metadata, exam name or filename)
        if hint:
            # 2.1 Chirurgia Senologica -> collega SEMPRE a 'struttura_della_lesione'
            chirurgia_senologica_hints = [
                "chirurgia senologica",
                "chirurgia_senologica",
                "chirurgica senologica",
                "chirurgica_senologica",
                "senologia chirurgica",
                "senologia_chirurgica",
                "visita chirurgica senologica",
                "visita_chirurgica_senologica",
                "chirurgia",
                "chirurg",
                "verbale operatorio",
                "verbale_operatorio",
                "intervent",
                "operatori",
                "dimissione",
            ]
            if any(term in lower_hint for term in chirurgia_senologica_hints):
                if not any(h in lower_hint for h in ["istolog", "e_i_c", "esame istologico", "anatomia patologica", "core biopsy", "agobiops"]):
                    return "struttura_della_lesione"

            # 2.2 Multidisciplinare
            if "multidisciplinare" in lower_hint:
                return "dati_del_paziente"

            for form_key in AVAILABLE_FORMS.keys():
                if form_key in lower_hint:
                    return form_key

            for alias_term, target_form in FORM_ALIASES.items():
                if alias_term in lower_hint:
                    return target_form

            if any(b in lower_hint for b in ["biops", "core", "fna", "agobiopt", "agoaspirat"]):
                return "anatomia_patologica_preoperatoria"
            if any(i in lower_hint for i in ["istolog", "definitiv", "recettore", "estrogen", "progesteron", "sentinella", "osna", "bls", "mastec", "e_i_c"]):
                return "anatomia_patologica"
            if any(r in lower_hint for r in ["mammo", "eco", "rmn", "mri", "marcatura"]):
                return "radiologia"
            if "visita" in lower_hint:
                if "diagnostica per immagini" in lower_text or "mammografia" in lower_text or "eco mammaria" in lower_text:
                    return "radiologia"
                return "dati_del_paziente"

        # 3. Heuristic rules based on clinical text keywords
        # 3.1 Chirurgia Senologica / Verbale operatorio / Lettera di dimissione
        if (
            "lettera di dimissione" in lower_text
            or "verbale operatorio" in lower_text
            or "chirurgia senologica" in lower_text
            or "chirurgica senologica" in lower_text
            or "senologia chirurgica" in lower_text
            or "u.o. chirurgia senologica" in lower_text
            or "reparto di chirurgia senologica" in lower_text
            or "tempo chirurgico" in lower_text
            or "visita chirurgica senologica" in lower_text
            or "intervento chirurgico senologico" in lower_text
        ) and ("diagnosi istopatologica" not in lower_text and "frustoli" not in lower_text):
            return "struttura_della_lesione"

        # 3.2 Istologico definitivo post-operatorio e biologia molecolare / OSNA
        if (
            ("pezzo operatorio" in lower_text or "quadrantectomia" in lower_text or "mastectomia" in lower_text or "istologico definitivo" in lower_text or "diagnosi istopatologica" in lower_text)
            and ("margini" in lower_text or "pt" in lower_text or "pn" in lower_text or "linfonodo sentinella" in lower_text or "ptis" in lower_text)
            or "recettori degli estrogeni" in lower_text
            or "metodica osna" in lower_text
            or "stadiazione istopatologica" in lower_text
        ):
            return "anatomia_patologica"

        # 3.3 Biopsia / Citologia pre-operatoria
        if (
            ("frustoli" in lower_text or "agobioptici" in lower_text or "core biopsy" in lower_text or "agobiopsia" in lower_text)
            or ("agoaspirato" in lower_text and ("fna" in lower_text or "citologico" in lower_text or "c1" in lower_text or "c2" in lower_text or "c3" in lower_text or "c4" in lower_text or "c5" in lower_text))
            or "esame citologico da agoaspirat" in lower_text
        ):
            return "anatomia_patologica_preoperatoria"

        # 3.4 Radiologia
        if (
            ("mammografia" in lower_text or "tomosintesi" in lower_text or "ecografia mammaria" in lower_text or "marcatura" in lower_text)
            and ("bi-rads" in lower_text or "birads" in lower_text or "densità parenchimale" in lower_text or "radiologico" in lower_text or "ecostrutturato" in lower_text)
            and "frustoli" not in lower_text
        ):
            return "radiologia"

        # 3.5 Visita / Meeting multidisciplinare
        if (
            "visita senologica multidisciplinare" in lower_text
            or "visita multidisciplinare" in lower_text
            or "meeting multidisciplinare" in lower_text
            or "riunione multidisciplinare" in lower_text
        ):
            return "dati_del_paziente"

        # Radioterapia
        if "radioterapia" in lower_text and ("gray" in lower_text or "gy" in lower_text or "irradiazione" in lower_text):
            return "radioterapia"

        # Chemioterapia / Oncologia
        if "chemioterapia" in lower_text or "schema ac" in lower_text or "trastuzumab" in lower_text or "letrozolo" in lower_text:
            return "chemioterapia"

        # Recidiva
        if "recidiva locale" in lower_text or "ripresa di malattia" in lower_text:
            return "recidiva"

        # 5. Fallback to quick local LLM classification (supports llama.cpp or Ollama configured in Settings)
        try:
            prompt = (
                "Classifica il seguente referto medico senologico in uno e uno solo di questi strumenti REDCap:\n"
                "- radiologia (esami mammografici, ecografie, RM)\n"
                "- anatomia_patologica_preoperatoria (agobiopsie, core biopsy, agoaspirati pre-intervento)\n"
                "- anatomia_patologica (esame istologico definitivo post-operatorio su pezzo chirurgico)\n"
                "- struttura_della_lesione (Chirurgia Senologica, verbale operatorio, lettera di dimissione, tipo intervento chirurgico)\n"
                "- chemioterapia (oncologia medica)\n"
                "- radioterapia (trattamento radiante)\n"
                "- dati_del_paziente (anamnesi, prima visita, riunioni multidisciplinari)\n\n"
                f"Referto:\n{text[:1000]}\n\n"
                "Rispondi ESCLUSIVAMENTE con il nome dello strumento (es. 'radiologia'):"
            )

            # 5.1 Try llama.cpp server first if configured
            if target_llama_url:
                try:
                    headers = {}
                    if cfg.ai_cloudflared_client_id and cfg.ai_cloudflared_client_secret:
                        headers["CF-Access-Client-Id"] = cfg.ai_cloudflared_client_id
                        headers["CF-Access-Client-Secret"] = cfg.ai_cloudflared_client_secret

                    r_llama = requests.post(
                        f"{target_llama_url}/completion",
                        headers=headers,
                        json={"prompt": prompt, "temperature": 0.0, "n_predict": 20, "stream": False},
                        timeout=3
                    )
                    if r_llama.status_code == 200:
                        predicted = r_llama.json().get("content", "").strip().lower()
                        for form_key in AVAILABLE_FORMS.keys():
                            if form_key in predicted:
                                return form_key
                except Exception:
                    pass

            # 5.2 Try Ollama fallback
            if target_ollama_url:
                r = requests.post(
                    f"{target_ollama_url.rstrip('/')}/api/generate",
                    json={
                        "model": target_ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.0, "num_predict": 20}
                    },
                    timeout=5
                )
                if r.status_code == 200:
                    predicted = r.json().get("response", "").strip().lower()
                    for form_key in AVAILABLE_FORMS.keys():
                        if form_key in predicted:
                            return form_key
        except Exception as e:
            logger.warning(f"Classificazione LLM non riuscita: {e}")

        # Default fallback: check if document contains senological terms; if not, skip
        seno_terms = ["mamm", "senolog", "cavo ascellare", "capezzolo", "areolare", "nodulo", "bi-rads", "birads"]
        if any(st in lower_text for st in seno_terms):
            return "radiologia"

        return "skip"
