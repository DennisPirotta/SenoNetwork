import logging
import re
from typing import Optional
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

        # 2. Multidisciplinary consultation (MDM pre/post meeting dates -> dati_del_paziente)
        if (
            "multidisciplinare" in lower_hint
            or "visita senologica multidisciplinare" in lower_text
            or "visita multidisciplinare" in lower_text
            or "meeting multidisciplinare" in lower_text
            or "riunione multidisciplinare" in lower_text
        ):
            return "dati_del_paziente"

        # 3. Check explicit hint (e.g. from .NET API metadata or filename)
        if hint:
            # 3.1 Chirurgia Senologica -> collega SEMPRE a 'struttura_della_lesione'
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
                if not ("istolog" in lower_hint or "e_i_c" in lower_hint or "esame istologico" in lower_hint or "anatomia patologica" in lower_hint or "core biopsy" in lower_hint or "agobiops" in lower_hint):
                    return "struttura_della_lesione"

            for form_key in AVAILABLE_FORMS.keys():
                if form_key in lower_hint:
                    return form_key

            for alias_term, target_form in FORM_ALIASES.items():
                if alias_term in lower_hint:
                    return target_form

            if "biops" in lower_hint or "core" in lower_hint or "fna" in lower_hint or "agobiopt" in lower_hint or "agoaspirat" in lower_hint:
                return "anatomia_patologica_preoperatoria"
            if "istolog" in lower_hint or "definitiv" in lower_hint or "recettore" in lower_hint or "estrogen" in lower_hint or "progesteron" in lower_hint or "sentinella" in lower_hint or "osna" in lower_hint or "bls" in lower_hint or "mastec" in lower_hint:
                return "anatomia_patologica"
            if "mammo" in lower_hint or "eco" in lower_hint or "rmn" in lower_hint or "mri" in lower_hint or "marcatura" in lower_hint:
                return "radiologia"
            if "visita" in lower_hint:
                return "dati_del_paziente"

        # 4. Heuristic rules based on clinical text keywords
        # Biopsia / Citologia pre-operatoria
        if (
            ("frustoli" in lower_text or "agobioptici" in lower_text or "core biopsy" in lower_text or "agobiopsia" in lower_text)
            or ("agoaspirato" in lower_text and ("fna" in lower_text or "citologico" in lower_text or "c1" in lower_text or "c2" in lower_text or "c3" in lower_text or "c4" in lower_text or "c5" in lower_text))
            or "esame citologico da agoaspirat" in lower_text
        ):
            return "anatomia_patologica_preoperatoria"

        # Chirurgia Senologica / Verbale operatorio / Lettera di dimissione
        if (
            "chirurgia senologica" in lower_text
            or "chirurgica senologica" in lower_text
            or "senologia chirurgica" in lower_text
            or "u.o. chirurgia senologica" in lower_text
            or "reparto di chirurgia senologica" in lower_text
            or "verbale operatorio" in lower_text
            or "lettera di dimissione" in lower_text
            or "tempo chirurgico" in lower_text
            or "visita chirurgica senologica" in lower_text
            or "intervento chirurgico senologico" in lower_text
        ) and ("diagnosi istopatologica" not in lower_text and "frustoli" not in lower_text):
            return "struttura_della_lesione"

        # Istologico definitivo post-operatorio e biologia molecolare / OSNA
        if (
            ("pezzo operatorio" in lower_text or "quadrantectomia" in lower_text or "mastectomia" in lower_text or "istologico definitivo" in lower_text or "diagnosi istopatologica" in lower_text)
            and ("margini" in lower_text or "pt" in lower_text or "pn" in lower_text or "linfonodo sentinella" in lower_text or "ptis" in lower_text)
            or "recettori degli estrogeni" in lower_text
            or "metodica osna" in lower_text
            or "stadiazione istopatologica" in lower_text
        ):
            return "anatomia_patologica"

        # Radiologia
        if (
            ("mammografia" in lower_text or "tomosintesi" in lower_text or "ecografia mammaria" in lower_text or "marcatura" in lower_text)
            and ("bi-rads" in lower_text or "birads" in lower_text or "densità parenchimale" in lower_text or "radiologico" in lower_text or "ecostrutturato" in lower_text)
            and "frustoli" not in lower_text
        ):
            return "radiologia"

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
