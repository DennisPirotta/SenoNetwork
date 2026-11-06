from datetime import date
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.api_server import get_audit_data_quality_report
from src.audit_service import (
    CohortClinicalProfile,
    IssueCategory,
    IssueSeverity,
    RedCapAuditReport,
    RedCapDataAuditor
)
from src.config import Settings
from src.redcap_service import REDCapService


@pytest.fixture
def mock_settings(tmp_path):
    return Settings(
        redcap_api_url="https://mock-redcap.org/api/",
        redcap_api_token="MOCK_TOKEN_123",
        output_dir=tmp_path / "output",
        redcap_cache_file=Path("examples/redcap_metadata_senonetwork.json")
    )


def test_audit_form_completeness_calculation(mock_settings):
    """Tests calculation of form completion rates across mock records."""
    mock_records = [
        {
            "record_id": "PZ-1",
            "dati_del_paziente_complete": "2",
            "radiologia_complete": "2",
            "anatomia_patologica_complete": "2"
        },
        {
            "record_id": "PZ-2",
            "dati_del_paziente_complete": "2",
            "radiologia_complete": "0",
            "anatomia_patologica_complete": "0"
        },
        {
            "record_id": "PZ-3",
            "dati_del_paziente_complete": "1",
            "radiologia_complete": "",
            "anatomia_patologica_complete": ""
        }
    ]

    mock_redcap = MagicMock(spec=REDCapService)
    auditor = RedCapDataAuditor(settings=mock_settings, redcap_service=mock_redcap)

    report = auditor.run_audit(records=mock_records)

    assert report.total_records == 3
    dp_stat = next(f for f in report.forms_stats if f.form_name == "dati_del_paziente")
    assert dp_stat.completed_count == 2
    assert dp_stat.unverified_count == 1
    assert dp_stat.completeness_pct == pytest.approx(66.7, 0.1)

    ap_stat = next(f for f in report.forms_stats if f.form_name == "anatomia_patologica")
    assert ap_stat.completed_count == 1
    assert ap_stat.incomplete_count == 1
    assert ap_stat.empty_count == 1


def test_audit_surgery_without_postop_histology(mock_settings):
    """Clinical rule test: patient underwent surgery but post-operative histology is missing."""
    mock_records = [
        {
            "record_id": "PZ-SURG-1",
            "name": "Maria",
            "surname": "Rossi",
            "date_of_birth": "1968-05-10",
            "bi1": "1",  # Surgery performed
            "date_of_bi1": "2024-03-15",
            "first_breast_operation": "5",  # Quadrantectomy
            "anatomia_patologica_complete": "0",  # Post-op histology missing
            "pt": "",
            "pn": "",
            "grade": ""
        }
    ]

    auditor = RedCapDataAuditor(settings=mock_settings)
    report = auditor.run_audit(records=mock_records)

    crit_issues = [i for i in report.issues if i.severity == IssueSeverity.CRITICAL]
    assert any(i.category == IssueCategory.MISSING_POSTOP_HISTOLOGY for i in crit_issues)
    miss_hist = next(i for i in crit_issues if i.category == IssueCategory.MISSING_POSTOP_HISTOLOGY)
    assert miss_hist.patient_id == "PZ-SURG-1"
    assert "2024-03-15" in miss_hist.description


def test_audit_temporal_chronological_anomalies(mock_settings):
    """Tests detection of inverted clinical dates (e.g. surgery prior to core biopsy)."""
    mock_records = [
        {
            "record_id": "PZ-ANOMALY-1",
            "name": "Anna",
            "surname": "Bianchi",
            "date_of_birth": "1975-01-01",
            "date_of_cb": "2024-06-01",  # Biopsy on June 1st
            "date_of_bi1": "2024-04-10",  # Surgery on April 10th (before biopsy!)
            "date_of_bi2": "2024-03-01",  # 2nd surgery before 1st surgery!
            "bi1": "1"
        }
    ]

    auditor = RedCapDataAuditor(settings=mock_settings)
    report = auditor.run_audit(records=mock_records)

    temp_issues = [i for i in report.issues if i.category == IssueCategory.TEMPORAL_ANOMALY]
    assert len(temp_issues) >= 2
    assert any("antecedente alla core biopsy" in i.description for i in temp_issues)
    assert any("antecedente al 1° intervento" in i.description for i in temp_issues)


def test_audit_false_complete_detection(mock_settings):
    """Tests detection of forms marked as complete ('2') but with empty fields."""
    mock_records = [
        {
            "record_id": "PZ-FALSE-COMPLETE",
            "radiologia_complete": "2"  # Marked complete but zero fields populated!
        }
    ]

    auditor = RedCapDataAuditor(settings=mock_settings)
    report = auditor.run_audit(records=mock_records)

    status_issues = [i for i in report.issues if i.category == IssueCategory.STATUS_MISMATCH]
    assert len(status_issues) >= 1
    assert any("privo di dati clinici effettivi" in i.description for i in status_issues)


def test_audit_missing_core_pathology_fields(mock_settings):
    """Tests detection of missing pT, pN, and grade when post-op pathology is present."""
    mock_records = [
        {
            "record_id": "PZ-PATH-INCOMPLETE",
            "anatomia_patologica_complete": "2",
            "histopathological_diagnosis_of_operative_specimen": "3",  # Invasivo
            "pt": "",  # Missing pT
            "pn": "",  # Missing pN
            "grade": "",  # Missing Grade
            "invasive_histological_type": ""  # Missing istotype
        }
    ]

    auditor = RedCapDataAuditor(settings=mock_settings)
    report = auditor.run_audit(records=mock_records)

    missing_fields = [i for i in report.issues if i.category == IssueCategory.MISSING_MANDATORY_FIELD]
    field_names = [i.field_name for i in missing_fields]
    assert "pt" in field_names
    assert "pn" in field_names
    assert "grade" in field_names
    assert "invasive_histological_type" in field_names


def test_audit_positive_margins_warning(mock_settings):
    """Tests warning when margins are involved at 1st surgery without recorded 2nd surgery."""
    mock_records = [
        {
            "record_id": "PZ-MARGINS",
            "bi1": "1",
            "date_of_bi1": "2024-05-10",
            "involved_margins_at_1st_surgical_procedure": "2",  # Margini positivi
            "second_surgical_intervention": "0",  # Nessun secondo intervento
            "date_of_bi2": ""
        }
    ]

    auditor = RedCapDataAuditor(settings=mock_settings)
    report = auditor.run_audit(records=mock_records)

    margin_issues = [i for i in report.issues if "Margini chirurgici" in i.description]
    assert len(margin_issues) == 1
    assert margin_issues[0].severity == IssueSeverity.WARNING


def test_audit_markdown_and_export_reports(mock_settings, tmp_path):
    """Tests generating markdown report and exporting files."""
    mock_records = [
        {
            "record_id": "PZ-OK",
            "name": "Carla",
            "surname": "Verdi",
            "date_of_birth": "1960-04-15",
            "dati_del_paziente_complete": "2",
            "bi1": "1",
            "date_of_bi1": "2024-01-20",
            "anatomia_patologica_complete": "2",
            "pt": "1c",
            "pn": "0",
            "grade": "2"
        }
    ]

    auditor = RedCapDataAuditor(settings=mock_settings)
    report = auditor.run_audit(records=mock_records)

    md = auditor.generate_markdown_report(report)
    assert "# Report di Audit Qualità & Incongruenze Dati REDCap" in md
    assert "Pazienti Totali Analizzati" in md
    assert "Stato di Avanzamento e Completamento Form" in md

    # Test export
    out_dir = tmp_path / "reports_test"
    paths = auditor.export_reports(report, output_dir=out_dir)
    assert paths["markdown"].exists()
    assert paths["json"].exists()

    with open(paths["json"], "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["total_records"] == 1
        assert data["clinical_profile"]["patients_with_surgery"] == 1


def test_api_server_audit_report_endpoint(mock_settings):
    """Directly tests the FastAPI get_audit_data_quality_report endpoint."""
    with patch("src.api_server.RedCapDataAuditor.run_audit") as mock_audit:
        with patch("src.api_server.RedCapDataAuditor.export_reports") as mock_export:
            mock_report = RedCapAuditReport(
                timestamp="2026-11-06T10:00:00",
                total_records=5,
                global_completeness_score=85.0,
                total_issues_count=1,
                critical_issues_count=0,
                warning_issues_count=1,
                info_issues_count=0,
                forms_stats=[],
                clinical_profile=CohortClinicalProfile(),
                issues=[]
            )
            mock_audit.return_value = mock_report
            res = get_audit_data_quality_report(patient_id="PZ-10", settings=mock_settings)
            assert res.total_records == 5
            assert res.global_completeness_score == 85.0
            mock_audit.assert_called_with(patient_id="PZ-10")
            assert mock_export.called
