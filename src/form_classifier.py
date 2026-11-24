from datetime import datetime
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Union
import requests

from src.config import Settings, get_settings
from src.utils import atomic_write_text, llm_slot

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
    def _build_mapping_markdown(result: Dict[str, Any]) -> str:
        """Builds Markdown report content for exam-to-form mapping results."""
        lines = [
            "# Mappa Referti Esami -> Form REDCap",
            f"Data generazione: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Totale referti analizzati: {result.get('total', 0)}\n",
            "## Elenco Mappature Esame -> Form REDCap",
        ]
        for item in result.get("mappings", []):
            lines.append(f"- {item.get('display', '')}")

        lines.append("\n## Riepilogo Contatori per Form REDCap")
        lines.append("| Form REDCap | Descrizione Clinica | Conteggio Referti | % sul Totale |")
        lines.append("| :--- | :--- | :---: | :---: |")
        total = result.get("total", 0)
        for form_name, cnt in sorted(result.get("counts", {}).items(), key=lambda x: x[1], reverse=True):
            pct = (cnt / total * 100) if total > 0 else 0
            desc = AVAILABLE_FORMS.get(form_name, "Documento escluso / non senologico" if form_name == "skip" else "-")
            lines.append(f"| `{form_name}` | {desc} | **{cnt}** | {pct:.1f}% |")
        return "\n".join(lines) + "\n"

    @staticmethod
    def export_mapping(
        result: Dict[str, Any],
        output_path: Optional[Union[str, Path]] = None,
        output_dir: Union[str, Path] = "data/output"
    ) -> Dict[str, Path]:
        """Exports exam-to-form mapping result to JSON and Markdown output files.
        
        Args:
            result: The mapping dictionary returned by map_exams().
            output_path: Optional specific file path for export (.json, .md, or .txt).
            output_dir: Target directory if output_path is not specified.
            
        Returns:
            Dict containing format names ('json', 'markdown') and their Path objects.
        """
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        exported: Dict[str, Path] = {}

        if output_path:
            p = Path(output_path)
            if p.suffix.lower() == ".json":
                atomic_write_text(p, json.dumps(result, indent=2, ensure_ascii=False))
                exported["json"] = p
            else:
                atomic_write_text(p, FormClassifier._build_mapping_markdown(result))
                exported["markdown"] = p
            return exported

        # Default: export both JSON and Markdown atomically
        json_path = out_dir / "mappa_esami_form.json"
        atomic_write_text(json_path, json.dumps(result, indent=2, ensure_ascii=False))
        exported["json"] = json_path

        md_path = out_dir / "mappa_esami_form.md"
        atomic_write_text(md_path, FormClassifier._build_mapping_markdown(result))
        exported["markdown"] = md_path

        return exported

    @staticmethod
    def _match_form_key(text_val: str) -> Optional[str]:
        """Matches a canonical form name ordered longest-first to avoid prefix collisions."""
        lowered = text_val.strip().lower()
        if re.search(r"(?:^|[^a-z0-9_])skip(?:$|[^a-z0-9_])", lowered):
            return "skip"
        for form_key in sorted(AVAILABLE_FORMS.keys(), key=len, reverse=True):
            if re.search(rf"(?:^|[^a-z0-9_]){re.escape(form_key)}(?:$|[^a-z0-9_])", lowered):
                return form_key
        return None

    @staticmethod
    def _strip_phi_headers(text_excerpt: str) -> str:
        """Removes explicit patient identifier lines before optional LLM classification."""
        cleaned_lines = []
        phi_pat = re.compile(
            r"^\s*(?:paziente|nominativo|assistit[oa]|cognome(?:\s+e\s+nome)?|codice\s+fiscale|c\.?\s*f\.?|nat[oa]\s+il|data\s+di\s+nascita)\s*[:\-].*$",
            re.IGNORECASE,
        )
        for line in text_excerpt.splitlines():
            if not phi_pat.match(line):
                cleaned_lines.append(line)
        return "\n".join(cleaned_lines)

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
            # 2.0 Explicit pathology / biopsy hints must be checked before generic surgery department hints
            has_core_word = bool(re.search(r"(?:^|[^a-z])core(?:$|[^a-z])", lower_hint))
            if has_core_word or any(b in lower_hint for b in ["biops", "fna", "agobiopt", "agoaspirat"]):
                return "anatomia_patologica_preoperatoria"
            if any(i in lower_hint for i in ["istolog", "definitiv", "recettore", "estrogen", "progesteron", "sentinella", "osna", "bls", "e_i_c", "pezzo_operatorio", "pezzo operatorio"]):
                return "anatomia_patologica"

            # 2.1 Chirurgia Senologica -> collega a 'struttura_della_lesione'
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
                "mastec",
                "dimissione",
            ]
            if any(term in lower_hint for term in chirurgia_senologica_hints):
                if not any(h in lower_hint for h in ["istolog", "e_i_c", "esame istologico", "anatomia patologica", "core biopsy", "agobiops", "oncolog", "radioterap"]):
                    return "struttura_della_lesione"

            # 2.2 Multidisciplinare
            if "multidisciplinare" in lower_hint:
                return "dati_del_paziente"

            for form_key in sorted(AVAILABLE_FORMS.keys(), key=len, reverse=True):
                if form_key in lower_hint:
                    return form_key

            for alias_term, target_form in FORM_ALIASES.items():
                if alias_term in lower_hint:
                    return target_form

            if (
                any(r in lower_hint for r in ["mammo", "rmn", "mri", "marcatura", "ecografia", "eco_mamm", "eco mamm"])
                or (
                    re.search(r"(?:^|[_\-\s])eco(?:grafia)?(?:[_\-\s]|$)", lower_hint)
                    and not any(ex in lower_hint for ex in ["ecocardio", "ecodoppler", "ginecolog"])
                )
            ):
                return "radiologia"
            if any(o in lower_hint for o in ["oncolog", "chemioter"]) and "psiconcolog" not in lower_hint:
                return "chemioterapia"
            if "radioterap" in lower_hint:
                return "radioterapia"
            if "visita" in lower_hint:
                if "diagnostica per immagini" in lower_text or "mammografia" in lower_text or "eco mammaria" in lower_text:
                    return "radiologia"
                return "dati_del_paziente"

        # 3. Heuristic rules based on clinical text keywords
        has_postop_surgery = any(t in lower_text for t in ("pezzo operatorio", "quadrantectomia", "mastectomia", "istologico definitivo", "resezione chirurgica"))
        has_tnm_margins = bool(
            re.search(
                r"\b(pt\s*[0-4is]|pn\s*[0-3]|margini\s+(?:di\s+resezione|chirurgic[ih]|indenni|positivi|negativi|a\s+ridosso))\b",
                lower_text,
            )
        )
        is_explicit_postop_pathology = (
            (has_postop_surgery and has_tnm_margins)
            or "recettori degli estrogeni" in lower_text
            or "metodica osna" in lower_text
            or "stadiazione istopatologica" in lower_text
            or "esame istologico definitivo" in lower_text
            or "referto istologico definitivo" in lower_text
        )

        # 3.1 Biopsia / Citologia pre-operatoria (Core Biopsy, agobioptici, frustoli, FNA)
        # Valutata prima dell'istologico definitivo post-operatorio per evitare falsi positivi
        has_biopsy_context = any(k in lower_text for k in (
            "biops", "frustol", "agobiopt", "agoaspirat", "citolog", "microistolog",
            "tru-cut", "trucut", "vabb", "mammotome", "categoria b", "classe b"
        ))
        has_b_c_code = bool(re.search(r"\b(b[1-5][abc]?|c[1-5])\b", lower_text))
        if (
            ("frustoli" in lower_text or "agobioptici" in lower_text or "core biopsy" in lower_text or "agobiopsia" in lower_text)
            or ("agoaspirato" in lower_text and ("fna" in lower_text or "citologico" in lower_text or bool(re.search(r"\bc[1-5]\b", lower_text))))
            or "esame citologico da agoaspirat" in lower_text
            or (has_b_c_code and has_biopsy_context and not is_explicit_postop_pathology)
        ):
            return "anatomia_patologica_preoperatoria"

        # 3.2 Istologico definitivo post-operatorio e biologia molecolare / OSNA
        # Valutato PRIMA del generico "chirurgia senologica" (spesso presente come reparto richiedente sui referti istologici)
        if is_explicit_postop_pathology:
            return "anatomia_patologica"

        # 3.3 Chirurgia Senologica / Verbale operatorio / Lettera di dimissione
        has_standalone_pathology_header = bool(
            re.search(r"^\s*(?:servizio\s+di\s+|istituto\s+di\s+)?anatomia\s+patologica", lower_text)
            or (
                re.search(r"(?<!attesa\sdi\s)diagnosi\s+istopatologica", lower_text)
                and "verbale operatorio" not in lower_text
                and "lettera di dimissione" not in lower_text
            )
            or "frustoli agobioptici" in lower_text
        )
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
        ) and not has_standalone_pathology_header:
            return "struttura_della_lesione"

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
        if (
            "chemioterapia" in lower_text
            or "schema ac" in lower_text
            or "trastuzumab" in lower_text
            or "letrozolo" in lower_text
            or "anastrozolo" in lower_text
            or "tamoxifene" in lower_text
            or "antiestrogeno" in lower_text
            or "visita oncologica" in lower_text
            or "u.o.s. di oncologia" in lower_text
            or "u.o.c. di ematologia e oncologia" in lower_text
        ):
            return "chemioterapia"

        # Recidiva
        if "recidiva locale" in lower_text or "ripresa di malattia" in lower_text:
            return "recidiva"

        # 5. Fallback to quick local LLM classification (supports llama.cpp or Ollama configured in Settings)
        llm_timeout = getattr(cfg, "llm_fallback_timeout_seconds", 30)
        redacted_text = FormClassifier._strip_phi_headers(text)
        text_excerpt = redacted_text if len(redacted_text) <= 2500 else f"{redacted_text[:1500]}\n...\n{redacted_text[-1000:]}"
        max_conc = getattr(cfg, "ai_max_concurrent_requests", 1)
        try:
            prompt = (
                "Classifica il seguente referto medico senologico in uno e uno solo di questi strumenti REDCap:\n"
                "- radiologia (esami mammografici, ecografie, RM)\n"
                "- anatomia_patologica_preoperatoria (agobiopsie, core biopsy, agoaspirati pre-intervento)\n"
                "- anatomia_patologica (esame istologico definitivo post-operatorio su pezzo chirurgico)\n"
                "- struttura_della_lesione (Chirurgia Senologica, verbale operatorio, lettera di dimissione, tipo intervento chirurgico)\n"
                "- chemioterapia (oncologia medica)\n"
                "- radioterapia (trattamento radiante)\n"
                "- dati_del_paziente (anamnesi, prima visita, riunioni multidisciplinari)\n"
                "- skip (referto non senologico / irrilevante)\n\n"
                f"Referto:\n{text_excerpt}\n\n"
                "Rispondi ESCLUSIVAMENTE con il nome dello strumento (es. 'radiologia' o 'skip'):"
            )

            with llm_slot(max_conc):
                # 5.1 Try llama.cpp server first if configured
                if target_llama_url:
                    try:
                        headers = {}
                        if cfg.ai_cloudflared_client_id and cfg.ai_cloudflared_client_secret:
                            headers["CF-Access-Client-Id"] = cfg.ai_cloudflared_client_id
                            headers["CF-Access-Client-Secret"] = cfg.ai_cloudflared_client_secret

                        if getattr(cfg, "llama_cpp_api_mode", "chat").lower() == "chat":
                            with requests.post(
                                f"{target_llama_url}/v1/chat/completions",
                                headers=headers,
                                json={
                                    "model": cfg.llama_cpp_model or "llama-cpp-rpc",
                                    "messages": [{"role": "user", "content": prompt}],
                                    "temperature": 0.0,
                                    "max_tokens": 20,
                                    "stream": False,
                                },
                                timeout=llm_timeout,
                            ) as r_llama:
                                predicted = (
                                    r_llama.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip().lower()
                                    if r_llama.status_code == 200 else ""
                                )
                        else:
                            with requests.post(
                                f"{target_llama_url}/completion",
                                headers=headers,
                                json={"prompt": prompt, "temperature": 0.0, "n_predict": 20, "stream": False},
                                timeout=llm_timeout,
                            ) as r_llama:
                                predicted = r_llama.json().get("content", "").strip().lower() if r_llama.status_code == 200 else ""

                        matched = FormClassifier._match_form_key(predicted)
                        if matched:
                            return matched
                    except Exception as e:
                        logger.debug(f"Classificazione LLM llama.cpp fallback fallita: {e}")

                # 5.2 Try Ollama fallback
                if target_ollama_url:
                    with requests.post(
                        f"{target_ollama_url.rstrip('/')}/api/generate",
                        json={
                            "model": target_ollama_model,
                            "prompt": prompt,
                            "stream": False,
                            "options": {"temperature": 0.0, "num_predict": 20}
                        },
                        timeout=llm_timeout,
                    ) as r:
                        if r.status_code == 200:
                            predicted = r.json().get("response", "").strip().lower()
                            matched = FormClassifier._match_form_key(predicted)
                            if matched:
                                return matched
        except Exception as e:
            logger.warning(f"Classificazione LLM non riuscita: {e}")

        # Fail-safe fallback: return 'unknown' if senological terms present (for human review), otherwise 'skip'
        seno_terms = ["mamm", "senolog", "cavo ascellare", "capezzolo", "areolare", "nodulo", "bi-rads", "birads"]
        if any(st in lower_text for st in seno_terms):
            return "unknown"

        return "skip"

    @staticmethod
    def _extract_section_body(lower_text: str, start_markers: List[str], stop_markers: List[str]) -> str:
        """Extracts text following the first matching start marker up to the earliest stop marker."""
        start_idx = -1
        used_len = 0
        for marker in start_markers:
            pos = lower_text.find(marker)
            if pos != -1 and (start_idx == -1 or pos < start_idx):
                start_idx = pos
                used_len = len(marker)
        if start_idx == -1:
            return ""
        rest = lower_text[start_idx + used_len:]
        end_idx = len(rest)
        for stop in stop_markers:
            pos = rest.find(stop)
            if pos != -1 and pos < end_idx:
                end_idx = pos
        return rest[:end_idx].strip()

    @staticmethod
    def _is_surgery_section_populated(lower_text: str) -> bool:
        """Detects whether surgical intervention data is genuinely populated (not a blank template)."""
        proc_terms = (
            "quadrantectomia", "mastectomia", "tumorectomia", "resezione ampia",
            "duttectomia", "linfonodo sentinella", "dissezione ascellare", "svuotamento ascellare",
        )
        if any(t in lower_text for t in proc_terms):
            return True

        sec = FormClassifier._extract_section_body(
            lower_text,
            start_markers=["intervento eseguito", "tipo di intervento"],
            stop_markers=["esame istologico definitivo", "istologico definitivo", "meeting multidisciplinare", "indicazione terapeutica"],
        )
        if not sec:
            return False
        # Strip standard template boilerplate ("In data ……… è stato eseguito intervento di")
        cleaned = re.sub(r"in\s+data|è\s+stato\s+eseguito\s+intervento\s+di|[\.\…_\s:/\-]+", " ", sec).strip()
        has_date = bool(re.search(r"\b\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}\b", sec))
        return has_date or len(cleaned) >= 4

    @staticmethod
    def _is_postop_histology_populated(lower_text: str) -> bool:
        """Detects whether definitive post-operative histology is genuinely populated (not a blank template)."""
        sec = FormClassifier._extract_section_body(
            lower_text,
            start_markers=[
                "esame istologico definitivo",
                "istologico definitivo",
                "istologia definitiva",
                "esame istopatologico definitivo",
            ],
            stop_markers=["meeting multidisciplinare", "indicazione terapeutica", "terapia adiuvante", "presenti"],
        )
        if sec:
            has_clinical_value = bool(
                re.search(
                    r"\b(?:"
                    r"pt\s*[:=]?\s*(?:[0-4](?:mic|[a-d])?|is|x)"
                    r"|pn\s*[:=]?\s*(?:[0-3](?:mi|[a-c])?|x)"
                    r"|g\s*[:=]?\s*[1-3]"
                    r"|(?:er|pr|pgr|ki\s*-?67)\s*[:=]?\s*\d{1,3}\s*%"
                    r"|her\s*2\s*[:=]?\s*(?:score\s*)?(?:0|1\+|2\+|3\+|negativ|positiv)"
                    r"|margini\s*(?:chirurgici\s*)?(?:indenni|negativi|positivi|interessati|a\s+ridosso|[<>]?\s*\d+)"
                    r"|osna|ck19|carcinoma"
                    r")\b",
                    sec,
                )
            )
            if has_clinical_value:
                return True

        return (
            any(k in lower_text for k in ("pezzo operatorio", "quadrantectomia", "mastectomia"))
            and bool(re.search(r"\b(pt\s*[0-4is]|pn\s*[0-3]|margini\s+(?:indenni|positivi|negativi|a\s+ridosso))\b", lower_text))
        )

    @staticmethod
    def classify_report_targets(
        text: str,
        hint: Optional[str] = None,
        settings: Optional[Settings] = None,
        ollama_url: Optional[str] = None,
        ollama_model: Optional[str] = None
    ) -> List[str]:
        """Determines all target REDCap forms for a report (primary and optional secondary forms).

        Composite medical documents (e.g. oncology visits, surgical discharge letters,
        multidisciplinary visits) often contain information across multiple clinical domains:
        - Oncology visit: primary 'chemioterapia' + secondary 'dati_del_paziente' (comorbidities,
          diabetes, hypoglycemia, hypertension, thyroid) + optional 'radioterapia' (if radiation doses/gy mentioned).
        - Surgical discharge / visit: primary 'struttura_della_lesione' + secondary 'dati_del_paziente'.
        - Multidisciplinary visit: primary 'dati_del_paziente' + secondary 'radiologia' / 'anatomia_patologica_preoperatoria' / 'chemioterapia'.

        Returns:
            List of target form names (e.g. ['chemioterapia', 'dati_del_paziente']).
            If irrelevant or non-senological, returns ['skip'].
        """
        primary = FormClassifier.classify_report(
            text=text,
            hint=hint,
            settings=settings,
            ollama_url=ollama_url,
            ollama_model=ollama_model
        )
        if primary in ("skip", "irrelevant", "unknown"):
            return [primary]

        targets = [primary]
        lower_text = text.lower()
        lower_hint = hint.lower() if hint else ""

        is_oncology = any(k in lower_hint for k in ["oncolog", "chemioter"]) or any(k in lower_text for k in [
            "visita oncologica", "u.o.s. di oncologia", "u.o.c. di ematologia e oncologia",
            "antiestrogeno", "chemioterapia", "anastrozolo", "letrozolo", "tamoxifene",
            "trastuzumab"
        ])

        is_surgery_or_discharge = (
            primary == "struttura_della_lesione"
            or any(k in lower_hint for k in ["dimissione", "verbale_operatorio", "intervento"])
            or any(k in lower_text for k in ["lettera di dimissione", "verbale operatorio", "decorso post-operatorio"])
        )

        is_multidisciplinary = "multidisciplinare" in lower_hint or any(k in lower_text for k in [
            "visita multidisciplinare", "meeting multidisciplinare", "riunione multidisciplinare",
            "gruppo multidisciplinare", "breast unit"
        ])

        has_anamnesis_or_comorbidity = any(k in lower_text for k in [
            "anamnesi", "comorbidit", "pat remota", "pat. remota", "fisiologica",
            "diabete", "ipoglicem", "ipertens", "tiroid", "eutirox", "cardiopat",
            "menopausa", "familiarit", "brca"
        ])

        has_chemo_or_endocrine = any(k in lower_text for k in [
            "chemioterapia", "anastrozolo", "letrozolo", "tamoxifene", "trastuzumab",
            "antiestrogeno", "ormonoterapia", "schema ac", "taxani", "adiuvante"
        ])

        has_radiotherapy = (
            "radioterapia" in lower_text
            and any(k in lower_text for k in ["gray", "gy", "imrt", "irradiazione", "tot 48gy", "sib"])
        )

        has_biopsy_summary = (
            any(k in lower_text for k in [
                "tru-cut", "core biopsy", "agobiopsia", "agoaspirato", "citologico c"
            ])
            or bool(re.search(r"\bvabb?\b", lower_text))
            or bool(re.search(r"\bb[1-5][abc]?\b", lower_text))
        )

        has_surgery_section = FormClassifier._is_surgery_section_populated(lower_text)
        has_postop_histology = FormClassifier._is_postop_histology_populated(lower_text)

        has_radiology_findings = bool(
            re.search(
                r"\b(?:bi-?rads\s*[0-6]|u[1-5]|mr[0-6]|r[1-5]|opacit|nodul|microcalcificazion|densit|lesion[ei]\s+(?:nodular|sospett))",
                lower_text,
            )
            and any(k in lower_text for k in ["mammografia", "ecografia", "risonanza", "tomosintesi", "birads", "bi-rads"])
        )

        # Rule 1: Oncology visit / report
        if is_oncology:
            if "chemioterapia" not in targets:
                targets.append("chemioterapia")
            if has_anamnesis_or_comorbidity and "dati_del_paziente" not in targets:
                targets.append("dati_del_paziente")
            if has_radiotherapy and "radioterapia" not in targets:
                targets.append("radioterapia")

        # Rule 2: Surgery / Discharge summary (additive on composite reports)
        if is_surgery_or_discharge:
            if "struttura_della_lesione" not in targets and has_surgery_section:
                targets.append("struttura_della_lesione")
            if has_anamnesis_or_comorbidity and "dati_del_paziente" not in targets:
                targets.append("dati_del_paziente")

        # Rule 3: Multidisciplinary visit (additive for genuinely populated sections)
        if is_multidisciplinary:
            if "dati_del_paziente" not in targets:
                if not is_oncology and not is_surgery_or_discharge:
                    targets.insert(0, "dati_del_paziente")
                else:
                    targets.append("dati_del_paziente")
            if has_chemo_or_endocrine and "chemioterapia" not in targets:
                targets.append("chemioterapia")
            if has_radiology_findings and "radiologia" not in targets:
                targets.append("radiologia")
            if has_biopsy_summary and "anatomia_patologica_preoperatoria" not in targets:
                targets.append("anatomia_patologica_preoperatoria")
            # Sezione intervento chirurgico eseguito (solo se realmente popolata)
            if has_surgery_section and "struttura_della_lesione" not in targets:
                targets.append("struttura_della_lesione")
            # Sezione istologico definitivo popolata (solo se realmente popolata)
            if has_postop_histology and "anatomia_patologica" not in targets:
                targets.append("anatomia_patologica")
            if has_radiotherapy and "radioterapia" not in targets:
                targets.append("radioterapia")

        # Rule 4: General medical visit (dati_del_paziente) with therapies mentioned
        if primary == "dati_del_paziente" and not is_oncology and not is_surgery_or_discharge and not is_multidisciplinary:
            if has_chemo_or_endocrine and "chemioterapia" not in targets:
                targets.append("chemioterapia")
            if has_radiotherapy and "radioterapia" not in targets:
                targets.append("radioterapia")

        return targets
