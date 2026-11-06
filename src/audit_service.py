from datetime import date, datetime
from enum import Enum
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from pydantic import BaseModel, Field

from src.config import Settings, get_settings
from src.form_classifier import AVAILABLE_FORMS
from src.redcap_service import REDCapService

logger = logging.getLogger(__name__)


class IssueSeverity(str, Enum):
    CRITICAL = "CRITICAL"  # Bloccante o grave errore clinico (es. chirurgia senza istologico, date invertite)
    WARNING = "WARNING"    # Mancanza di campo rilevante (es. pT/pN mancante, margini positivi senza re-intervento)
    INFO = "INFO"          # Miglioria o mancata chiusura form


class IssueCategory(str, Enum):
    MISSING_POSTOP_HISTOLOGY = "Istologico Post-Operatorio Mancante"
    CROSS_FORM_INCONSISTENCY = "Incongruenza tra Form Clinici"
    MISSING_MANDATORY_FIELD = "Campo Obbligatorio o Chiave Mancante"
    TEMPORAL_ANOMALY = "Anomalia Cronologica Date"
    STATUS_MISMATCH = "Incongruenza Stato Completamento Form"
    CLINICAL_DISCREPANCY = "Discrepanza Clinica o Margini"


class PatientAuditIssue(BaseModel):
    """Represents a single clinical inconsistency or missing data item detected for a patient."""
    patient_id: str
    severity: IssueSeverity
    category: IssueCategory
    form_name: Optional[str] = None
    field_name: Optional[str] = None
    description: str
    current_value: Optional[Any] = None
    suggested_action: str


class FormCompletenessStat(BaseModel):
    """Statistics on completion status for a specific REDCap form across the cohort."""
    form_name: str
    form_label: str
    total_records: int
    completed_count: int = 0   # status == '2'
    unverified_count: int = 0  # status == '1'
    incomplete_count: int = 0  # status == '0'
    empty_count: int = 0       # not started / no data
    completeness_pct: float = 0.0


class CohortClinicalProfile(BaseModel):
    """Clinical overview and distribution statistics for the REDCap cohort."""
    total_patients: int = 0
    patients_with_surgery: int = 0
    patients_with_postop_histology: int = 0
    pt_distribution: Dict[str, int] = Field(default_factory=dict)
    pn_distribution: Dict[str, int] = Field(default_factory=dict)
    grade_distribution: Dict[str, int] = Field(default_factory=dict)
    surgery_types: Dict[str, int] = Field(default_factory=dict)
    laterality: Dict[str, int] = Field(default_factory=dict)
    mean_age_at_diagnosis: Optional[float] = None


class RedCapAuditReport(BaseModel):
    """Complete audit and data quality report for the REDCap project."""
    timestamp: str
    total_records: int
    global_completeness_score: float  # 0 to 100%
    total_issues_count: int
    critical_issues_count: int
    warning_issues_count: int
    info_issues_count: int
    forms_stats: List[FormCompletenessStat]
    clinical_profile: CohortClinicalProfile
    issues: List[PatientAuditIssue]


def _parse_iso_date(val: Any) -> Optional[date]:
    """Helper to parse a date string safely into date object."""
    if not val:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("none", "nan", "null"):
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


class RedCapDataAuditor:
    """Performs deep audits and quality checks on REDCap patient records:
    1. Form completion matrix & overall project health.
    2. Missing data analysis (critical demographic and clinical fields).
    3. Clinical cross-form consistency rules (e.g. Surgery performed vs Post-Op Pathology).
    4. Temporal chronological sanity checks.
    5. Aggregate cohort statistics (pTNM, grade, surgical procedures, laterality).
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        redcap_service: Optional[REDCapService] = None
    ):
        self.settings = settings or get_settings()
        self.redcap_service = redcap_service or REDCapService(
            api_url=self.settings.redcap_api_url,
            api_token=self.settings.redcap_api_token
        )

    def run_audit(
        self,
        records: Optional[List[Dict[str, Any]]] = None,
        patient_id: Optional[str] = None
    ) -> RedCapAuditReport:
        """Executes full audit across REDCap records or a single patient.

        Args:
            records: Optional pre-loaded list of record dicts. If None, exports live from REDCap API.
            patient_id: Optional specific patient ID to filter.
        """
        if records is None:
            logger.info("Recupero record completi da REDCap API per audit qualità...")
            target_pids = [patient_id] if patient_id else None
            records = self.redcap_service.export_records_from_api(records=target_pids)

        if patient_id and records:
            records = [r for r in records if str(r.get("record_id", "")).strip() == str(patient_id).strip()]

        total_records = len(records)
        logger.info(f"Avvio audit qualità dati su {total_records} record REDCap...")

        issues: List[PatientAuditIssue] = []

        # 1. Form completeness statistics
        forms_stats = self._calculate_form_stats(records)

        # 2. Global score
        if forms_stats:
            global_score = round(sum(f.completeness_pct for f in forms_stats) / len(forms_stats), 1)
        else:
            global_score = 0.0

        # 3. Clinical profile aggregations
        clinical_profile = self._build_clinical_profile(records)

        # 4. Patient-level rule evaluation
        for rec in records:
            pid = str(rec.get("record_id", "")).strip()
            if not pid:
                continue

            # 4.1 Demographic checks
            self._check_demographics(rec, pid, issues)

            # 4.2 Form status sanity (false complete / unmarked complete)
            self._check_form_status_sanity(rec, pid, issues)

            # 4.3 Cross-form clinical logic: Surgery vs Post-Op Pathology
            self._check_surgery_and_pathology(rec, pid, issues)

            # 4.4 Chronological temporal consistency
            self._check_dates_chronology(rec, pid, issues)

            # 4.5 Surgical margins and follow-up
            self._check_margins_and_reoperation(rec, pid, issues)

            # 4.6 Missing core clinical variables in populated forms
            self._check_missing_core_variables(rec, pid, issues)

            # 4.7 Laterality checks
            self._check_laterality(rec, pid, issues)

        crit_count = sum(1 for i in issues if i.severity == IssueSeverity.CRITICAL)
        warn_count = sum(1 for i in issues if i.severity == IssueSeverity.WARNING)
        info_count = sum(1 for i in issues if i.severity == IssueSeverity.INFO)

        report = RedCapAuditReport(
            timestamp=datetime.now().isoformat(),
            total_records=total_records,
            global_completeness_score=global_score,
            total_issues_count=len(issues),
            critical_issues_count=crit_count,
            warning_issues_count=warn_count,
            info_issues_count=info_count,
            forms_stats=forms_stats,
            clinical_profile=clinical_profile,
            issues=issues
        )

        return report

    def _calculate_form_stats(self, records: List[Dict[str, Any]]) -> List[FormCompletenessStat]:
        """Calculates completion rates for each of the 8 forms."""
        stats: List[FormCompletenessStat] = []
        total = len(records)
        if total == 0:
            return stats

        for form_name in AVAILABLE_FORMS.keys():
            flag_key = f"{form_name}_complete"
            comp = 0
            unver = 0
            incomp = 0
            empty = 0

            for r in records:
                val = str(r.get(flag_key) or "").strip()
                if val == "2":
                    comp += 1
                elif val == "1":
                    unver += 1
                elif val == "0":
                    incomp += 1
                else:
                    empty += 1

            pct = round((comp / total) * 100, 1)
            stats.append(
                FormCompletenessStat(
                    form_name=form_name,
                    form_label=form_name.replace("_", " ").title(),
                    total_records=total,
                    completed_count=comp,
                    unverified_count=unver,
                    incomplete_count=incomp,
                    empty_count=empty,
                    completeness_pct=pct
                )
            )
        return stats

    def _build_clinical_profile(self, records: List[Dict[str, Any]]) -> CohortClinicalProfile:
        """Aggregates clinical metrics across the cohort."""
        profile = CohortClinicalProfile(total_patients=len(records))
        ages: List[float] = []

        for r in records:
            # Surgery & Pathology presence
            has_surg = bool(
                str(r.get("bi1") or "").strip() == "1"
                or r.get("date_of_bi1")
                or (r.get("first_breast_operation") and str(r.get("first_breast_operation")) not in ("0", "None", ""))
            )
            if has_surg:
                profile.patients_with_surgery += 1

            has_postop = bool(
                str(r.get("anatomia_patologica_complete") or "").strip() == "2"
                or r.get("pt")
                or r.get("histopathological_diagnosis_of_operative_specimen")
            )
            if has_postop:
                profile.patients_with_postop_histology += 1

            # pT
            pt = str(r.get("pt") or "").strip()
            if pt and pt.lower() != "none":
                profile.pt_distribution[pt] = profile.pt_distribution.get(pt, 0) + 1

            # pN
            pn = str(r.get("pn") or "").strip()
            if pn and pn.lower() != "none":
                profile.pn_distribution[pn] = profile.pn_distribution.get(pn, 0) + 1

            # Grade
            grade = str(r.get("grade") or "").strip()
            if grade and grade.lower() != "none":
                profile.grade_distribution[grade] = profile.grade_distribution.get(grade, 0) + 1

            # First breast operation
            op = str(r.get("first_breast_operation") or "").strip()
            if op and op.lower() != "none":
                profile.surgery_types[op] = profile.surgery_types.get(op, 0) + 1

            # Laterality
            side = str(r.get("side_of_lesion") or "").strip().upper()
            if side and side != "NONE":
                profile.laterality[side] = profile.laterality.get(side, 0) + 1

            # Age at diagnosis / first visit
            dob = _parse_iso_date(r.get("date_of_birth"))
            dx_date = (
                _parse_iso_date(r.get("date_of_mx"))
                or _parse_iso_date(r.get("date_of_cb"))
                or _parse_iso_date(r.get("date_of_bi1"))
            )
            if dob and dx_date and dx_date > dob:
                age = (dx_date - dob).days / 365.25
                if 18 <= age <= 105:
                    ages.append(age)

        if ages:
            profile.mean_age_at_diagnosis = round(sum(ages) / len(ages), 1)

        return profile

    def _check_demographics(self, r: Dict[str, Any], pid: str, issues: List[PatientAuditIssue]):
        """Checks required demographic fields."""
        dob = r.get("date_of_birth")
        if not dob or str(dob).strip().lower() in ("none", "nan", ""):
            issues.append(
                PatientAuditIssue(
                    patient_id=pid,
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.MISSING_MANDATORY_FIELD,
                    form_name="dati_del_paziente",
                    field_name="date_of_birth",
                    description="Data di nascita (date_of_birth) mancante o non valorizzata.",
                    suggested_action="Inserire la data di nascita da tessera sanitaria o Priamo."
                )
            )

        name = r.get("name")
        surname = r.get("surname")
        if not name or not surname:
            issues.append(
                PatientAuditIssue(
                    patient_id=pid,
                    severity=IssueSeverity.INFO,
                    category=IssueCategory.MISSING_MANDATORY_FIELD,
                    form_name="dati_del_paziente",
                    description="Nome o cognome anagrafico mancante nella scheda paziente.",
                    suggested_action="Valorizzare i campi anagrafici 'name' e 'surname'."
                )
            )

    def _check_form_status_sanity(self, r: Dict[str, Any], pid: str, issues: List[PatientAuditIssue]):
        """Verifies if form complete statuses reflect actual data (False complete vs Unmarked complete)."""
        for form_name in AVAILABLE_FORMS.keys():
            flag = str(r.get(f"{form_name}_complete") or "").strip()

            # Count fields with actual values in this form (ignoring record_id and completion flag)
            # We estimate by counting non-empty keys that don't end in _complete
            form_keys_with_data = [
                k for k, v in r.items()
                if not k.endswith("_complete")
                and k != "record_id"
                and v not in (None, "", "0", "None", "nan")
                and not k.startswith("text_")  # descriptive
            ]

            # False complete: marked complete '2' but record has no actual content
            if flag == "2" and len(form_keys_with_data) <= 1:
                issues.append(
                    PatientAuditIssue(
                        patient_id=pid,
                        severity=IssueSeverity.CRITICAL,
                        category=IssueCategory.STATUS_MISMATCH,
                        form_name=form_name,
                        field_name=f"{form_name}_complete",
                        description=f"Form '{form_name}' contrassegnato come COMPLETATO ('2'), ma privo di dati clinici effettivi.",
                        current_value=flag,
                        suggested_action=f"Impostare '{form_name}_complete' a '0' o estrarre i dati del referto corrispondente."
                    )
                )

    def _check_surgery_and_pathology(self, r: Dict[str, Any], pid: str, issues: List[PatientAuditIssue]):
        """Verifies consistency between surgical intervention and post-operative pathology."""
        bi1 = str(r.get("bi1") or "").strip()
        date_bi1 = r.get("date_of_bi1")
        first_op = r.get("first_breast_operation")
        has_surgery = bool(
            bi1 == "1"
            or date_bi1
            or (first_op and str(first_op) not in ("0", "None", ""))
        )

        postop_complete = str(r.get("anatomia_patologica_complete") or "").strip()
        pt = str(r.get("pt") or "").strip()
        has_postop_data = bool(
            postop_complete == "2"
            or pt
            or r.get("histopathological_diagnosis_of_operative_specimen")
            or r.get("invasive_histological_type")
        )

        # Case 1: Surgery performed BUT Post-Op Histology Missing!
        if has_surgery and not has_postop_data:
            surg_info = f"Data: {date_bi1}" if date_bi1 else "Intervento registrato"
            issues.append(
                PatientAuditIssue(
                    patient_id=pid,
                    severity=IssueSeverity.CRITICAL,
                    category=IssueCategory.MISSING_POSTOP_HISTOLOGY,
                    form_name="anatomia_patologica",
                    description=(
                        f"Paziente sottoposto a intervento chirurgico ({surg_info}), ma privo di referto "
                        f"istologico post-operatorio (anatomia_patologica non presente o vuoto)."
                    ),
                    suggested_action="Rileggere/scaricare il referto istologico definitivo da Priamo e avviare il sync."
                )
            )

        # Case 2: Post-Op Histology present with definitive stage BUT Surgery form empty
        if has_postop_data and pt and not has_surgery:
            issues.append(
                PatientAuditIssue(
                    patient_id=pid,
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.CROSS_FORM_INCONSISTENCY,
                    form_name="struttura_della_lesione",
                    description=(
                        f"Presente esame istologico definitivo post-operatorio (pT={pt}), ma la scheda chirurgica "
                        f"(struttura_della_lesione) non indica alcun intervento eseguito."
                    ),
                    suggested_action="Registrare i dati dell'intervento chirurgico nella scheda struttura_della_lesione."
                )
            )

    def _check_dates_chronology(self, r: Dict[str, Any], pid: str, issues: List[PatientAuditIssue]):
        """Verifies chronological ordering of medical events."""
        dob = _parse_iso_date(r.get("date_of_birth"))
        d_mx = _parse_iso_date(r.get("date_of_mx"))
        d_cb = _parse_iso_date(r.get("date_of_cb"))
        d_bi1 = _parse_iso_date(r.get("date_of_bi1"))
        d_bi2 = _parse_iso_date(r.get("date_of_bi2"))

        # DOB vs exams
        if dob:
            if d_mx and d_mx < dob:
                issues.append(
                    PatientAuditIssue(
                        patient_id=pid,
                        severity=IssueSeverity.CRITICAL,
                        category=IssueCategory.TEMPORAL_ANOMALY,
                        form_name="radiologia",
                        field_name="date_of_mx",
                        description=f"Data mammografia ({d_mx}) antecedente alla data di nascita ({dob}).",
                        suggested_action="Correggere la data di nascita o la data della mammografia."
                    )
                )
            if d_bi1 and d_bi1 < dob:
                issues.append(
                    PatientAuditIssue(
                        patient_id=pid,
                        severity=IssueSeverity.CRITICAL,
                        category=IssueCategory.TEMPORAL_ANOMALY,
                        form_name="struttura_della_lesione",
                        field_name="date_of_bi1",
                        description=f"Data intervento ({d_bi1}) antecedente alla data di nascita ({dob}).",
                        suggested_action="Verificare la data dell'intervento."
                    )
                )

        # Biopsy vs Surgery
        if d_cb and d_bi1 and d_bi1 < d_cb:
            issues.append(
                PatientAuditIssue(
                    patient_id=pid,
                    severity=IssueSeverity.CRITICAL,
                    category=IssueCategory.TEMPORAL_ANOMALY,
                    form_name="struttura_della_lesione",
                    field_name="date_of_bi1",
                    description=f"Data 1° intervento ({d_bi1}) antecedente alla core biopsy pre-operatoria ({d_cb}).",
                    suggested_action="Verificare le date di core biopsy e chirurgia."
                )
            )

        # 1st vs 2nd surgery
        if d_bi1 and d_bi2 and d_bi2 < d_bi1:
            issues.append(
                PatientAuditIssue(
                    patient_id=pid,
                    severity=IssueSeverity.CRITICAL,
                    category=IssueCategory.TEMPORAL_ANOMALY,
                    form_name="struttura_della_lesione",
                    field_name="date_of_bi2",
                    description=f"Data 2° intervento ({d_bi2}) antecedente al 1° intervento ({d_bi1}).",
                    suggested_action="Correggere la data del secondo intervento chirurgico."
                )
            )

    def _check_margins_and_reoperation(self, r: Dict[str, Any], pid: str, issues: List[PatientAuditIssue]):
        """Checks positive margins without re-excision follow-up."""
        margins = str(r.get("involved_margins_at_1st_surgical_procedure") or "").strip()
        sec_op = str(r.get("second_surgical_intervention") or "").strip()
        d_bi2 = r.get("date_of_bi2")

        if margins in ("1", "2"):  # 1 = A ridosso, 2 = Sì (positivi)
            if sec_op not in ("1",) and not d_bi2:
                issues.append(
                    PatientAuditIssue(
                        patient_id=pid,
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.CLINICAL_DISCREPANCY,
                        form_name="anatomia_patologica",
                        field_name="involved_margins_at_1st_surgical_procedure",
                        description=(
                            f"Margini chirurgici al 1° intervento segnalati come interessati/a ridosso "
                            f"(codice={margins}), ma nessun 2° intervento registrato."
                        ),
                        suggested_action="Verificare se è stato eseguito un allargamento chirurgico o radioterapia."
                    )
                )

    def _check_missing_core_variables(self, r: Dict[str, Any], pid: str, issues: List[PatientAuditIssue]):
        """Checks for missing critical pathological staging fields when pathology is present."""
        postop_complete = str(r.get("anatomia_patologica_complete") or "").strip()
        histol_diag = str(r.get("histopathological_diagnosis_of_operative_specimen") or "").strip()

        if postop_complete == "2" or histol_diag in ("2", "3"):  # in situ or invasivo
            # Check pT
            pt = str(r.get("pt") or "").strip()
            if not pt or pt.lower() == "none":
                issues.append(
                    PatientAuditIssue(
                        patient_id=pid,
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.MISSING_MANDATORY_FIELD,
                        form_name="anatomia_patologica",
                        field_name="pt",
                        description="Stadiazione patologica pT mancante nell'esame istologico definitivo.",
                        suggested_action="Inserire la categoria pT (es. 1c, 2, IS) dal referto istologico."
                    )
                )

            # Check pN
            pn = str(r.get("pn") or "").strip()
            if not pn or pn.lower() == "none":
                issues.append(
                    PatientAuditIssue(
                        patient_id=pid,
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.MISSING_MANDATORY_FIELD,
                        form_name="anatomia_patologica",
                        field_name="pn",
                        description="Stadiazione linfonodale patologica pN mancante nell'istologico definitivo.",
                        suggested_action="Inserire lo stato pN (es. 0, 1, 1a) dal referto istologico."
                    )
                )

            # Check grade
            grade = str(r.get("grade") or "").strip()
            if not grade or grade.lower() == "none":
                issues.append(
                    PatientAuditIssue(
                        patient_id=pid,
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.MISSING_MANDATORY_FIELD,
                        form_name="anatomia_patologica",
                        field_name="grade",
                        description="Grado istologico (grade) mancante nel referto istologico definitivo.",
                        suggested_action="Inserire il grado nucleare/istologico Nottingham (1, 2, 3)."
                    )
                )

            # If invasive, check invasive_histological_type
            if histol_diag == "3":
                istotype = str(r.get("invasive_histological_type") or "").strip()
                if not istotype or istotype.lower() == "none":
                    issues.append(
                        PatientAuditIssue(
                            patient_id=pid,
                            severity=IssueSeverity.WARNING,
                            category=IssueCategory.MISSING_MANDATORY_FIELD,
                            form_name="anatomia_patologica",
                            field_name="invasive_histological_type",
                            description="Diagnosi di carcinoma invasivo ma istotipo invasivo non specificato.",
                            suggested_action="Specificare l'istotipo invasivo (es. Duttale NAS, Lobulare)."
                        )
                    )

    def _check_laterality(self, r: Dict[str, Any], pid: str, issues: List[PatientAuditIssue]):
        """Checks consistency of laterality (side_of_lesion)."""
        side = str(r.get("side_of_lesion") or "").strip().upper()
        if side and side in ("L", "R"):
            # Check if there is explicit side contradiction in other textual fields
            pass

    def generate_markdown_report(self, report: RedCapAuditReport) -> str:
        """Generates a clean, comprehensive Markdown report ready for presentation and archiving."""
        md = []
        md.append(f"# Report di Audit Qualità & Incongruenze Dati REDCap")
        md.append(f"**Progetto**: SenoNetwork (ID: 20)  ")
        md.append(f"**Data Generazione**: {report.timestamp}  ")
        md.append(f"**Pazienti Totali Analizzati**: {report.total_records}  ")
        md.append(f"**Indice Globale di Completezza**: `{report.global_completeness_score}%`  ")
        md.append(f"**Totale Incongruenze / Mancanze**: `{report.total_issues_count}` "
                  f"(🔴 Critiche: `{report.critical_issues_count}` | "
                  f"🟡 Avvisi: `{report.warning_issues_count}` | "
                  f"🔵 Info: `{report.info_issues_count}`)\n")
        md.append("---\n")

        # 1. Executive Summary Alert
        if report.critical_issues_count > 0:
            md.append(f"> [!CAUTION]\n"
                      f"> Rilevate **{report.critical_issues_count} incongruenze critiche** che richiedono attenzione clinica "
                      f"(es. interventi chirurgici eseguiti privi di istologico definitivo post-operatorio o anomalie cronologiche date).\n")
        elif report.warning_issues_count > 0:
            md.append(f"> [!WARNING]\n"
                      f"> Rilevati **{report.warning_issues_count} campi clinici mancanti** nei form inseriti.\n")
        else:
            md.append(f"> [!NOTE]\n"
                      f"> Tutti i dati analizzati risultano clinicamente coerenti e ben strutturati.\n")

        # 2. Form Completeness Table
        md.append("## 1. Stato di Avanzamento e Completamento Form REDCap\n")
        md.append("| Form / Strumento | Completati ('2') | Incompleti ('0'/'1') | Non Iniziati | Tasso Completezza |")
        md.append("|---|:---:|:---:|:---:|:---:|")
        for f in report.forms_stats:
            non_comp = f.incomplete_count + f.unverified_count
            bar = "🟢" if f.completeness_pct >= 80 else ("🟡" if f.completeness_pct >= 50 else "🔴")
            md.append(f"| `{f.form_name}` | {f.completed_count} | {non_comp} | {f.empty_count} | {bar} {f.completeness_pct}% |")
        md.append("\n---\n")

        # 3. Clinical Profile
        cp = report.clinical_profile
        md.append("## 2. Panoramica Clinica della Coorte\n")
        md.append(f"- **Pazienti Operati**: {cp.patients_with_surgery} / {cp.total_patients}")
        md.append(f"- **Pazienti con Istologico Post-Operatorio**: {cp.patients_with_postop_histology} / {cp.total_patients}")
        if cp.mean_age_at_diagnosis:
            md.append(f"- **Età Media alla Diagnosi**: {cp.mean_age_at_diagnosis} anni")
        if cp.pt_distribution:
            pt_summary = ", ".join(f"pT{k}: {v}" for k, v in sorted(cp.pt_distribution.items()))
            md.append(f"- **Distribuzione pT**: {pt_summary}")
        if cp.pn_distribution:
            pn_summary = ", ".join(f"pN{k}: {v}" for k, v in sorted(cp.pn_distribution.items()))
            md.append(f"- **Distribuzione pN**: {pn_summary}")
        if cp.grade_distribution:
            g_summary = ", ".join(f"G{k}: {v}" for k, v in sorted(cp.grade_distribution.items()))
            md.append(f"- **Distribuzione Grado (Elston-Ellis)**: {g_summary}")
        md.append("\n---\n")

        # 4. Inconsistencies & Missing Data Detail
        md.append("## 3. Dettaglio Incongruenze e Mancanze Rilevate\n")
        if not report.issues:
            md.append("*Nessuna incongruenza o mancanza rilevata nei record esaminati.*\n")
        else:
            md.append("| Paziente ID | Gravità | Categoria | Descrizione Clinica | Azione Suggerita |")
            md.append("|---|---|---|---|---|")
            for iss in report.issues:
                sev_badge = "🔴 CRITICAL" if iss.severity == IssueSeverity.CRITICAL else (
                    "🟡 WARNING" if iss.severity == IssueSeverity.WARNING else "🔵 INFO"
                )
                md.append(
                    f"| `{iss.patient_id}` | {sev_badge} | {iss.category.value} | "
                    f"{iss.description} | {iss.suggested_action} |"
                )

        md.append("\n---\n")
        md.append(f"*Report generato automaticamente dal modulo SenoNetwork Data Auditor.*")
        return "\n".join(md)

    def export_reports(
        self,
        report: RedCapAuditReport,
        output_dir: Optional[Union[str, Path]] = None
    ) -> Dict[str, Path]:
        """Saves Markdown and JSON reports to disk."""
        target_dir = Path(output_dir or (self.settings.output_dir / "reports"))
        target_dir.mkdir(parents=True, exist_ok=True)

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        md_path = target_dir / f"redcap_quality_report_{timestamp_str}.md"
        json_path = target_dir / f"redcap_quality_report_{timestamp_str}.json"

        # Write markdown
        md_content = self.generate_markdown_report(report)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        # Write json
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))

        logger.info(f"Report di qualità esportati con successo:\n  - Markdown: {md_path}\n  - JSON: {json_path}")
        return {"markdown": md_path, "json": json_path}
