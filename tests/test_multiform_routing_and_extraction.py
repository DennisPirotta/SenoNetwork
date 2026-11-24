from datetime import date
from unittest.mock import MagicMock, patch

from src.form_classifier import FormClassifier
from src.models import ExtractionResult, REDCapField
from src.normalizer import REDCapNormalizer
from src.patient_pipeline import PatientPipeline
from src.redcap_service import REDCapService


def test_classify_report_targets_single_form():
    # Radiologia pura
    targets_mx = FormClassifier.classify_report_targets(
        text="Mammografia bilaterale con reperti BI-RADS 2",
        hint="mammografia.pdf"
    )
    assert targets_mx == ["radiologia"]

    # Biopsia preoperatoria
    targets_bx = FormClassifier.classify_report_targets(
        text="Core biopsy Tru-cut con frustoli mammari B5 invasivo",
        hint="core_biopsy.pdf"
    )
    assert targets_bx == ["anatomia_patologica_preoperatoria"]

    # Non senologico / skip
    targets_ecg = FormClassifier.classify_report_targets(
        text="ECG a 12 derivazioni con ritmo sinusale nei limiti",
        hint="20260000015257_ECG.pdf"
    )
    assert targets_ecg == ["skip"]


def test_classify_report_targets_oncology_visit_composite():
    text_oncology = """
    Referto di Ambulatorio - Visita Oncologica
    DIAGNOSI: neoplasia mammella sx ER+ Her2- in antiestrogeno adiuvante
    ANAMNESI FAMILIARE: neg x npl mamm/ovaio
    ANAMNESI FISIOLOGICA: menopausa a 55 anni. Fumo no
    ANAMNESI PAT REMOTA: diabete mellito tipo 2 in tp ipoglicemizzante orale, ipertensione arteriosa.
    TERAPIA: Anastrozolo 1 mg 1 cp al giorno x 5 anni.
    Eseguita radioterapia adiuvante SIB IMRT tot 48Gy.
    PRESTAZIONE ODIERNA: paziente in buone condizioni generali, assenza di ripresa di malattia.
    """
    targets = FormClassifier.classify_report_targets(
        text=text_oncology,
        hint="_VISITA_ONCOLOGICA_CONTROLLI.pdf"
    )
    # Deve rilevare primario chemioterapia e secondari dati_del_paziente e radioterapia
    assert "chemioterapia" in targets
    assert "dati_del_paziente" in targets
    assert "radioterapia" in targets
    assert targets[0] == "chemioterapia"  # primario


def test_classify_report_targets_surgical_discharge_composite():
    text_discharge = """
    Lettera di Dimissione - Reparto di Chirurgia Senologica
    Intervento chirurgico: quadrantectomia supero-esterna mammella sinistra con biopsia linfonodo sentinella.
    Anamnesi: ipertensione arteriosa, familiarità positiva per carcinoma mammario madre.
    Decorso post-operatorio regolare, rimozione drenaggio.
    """
    targets = FormClassifier.classify_report_targets(
        text=text_discharge,
        hint="2026_DIMISSIONE.pdf"
    )
    assert targets[0] == "struttura_della_lesione"
    assert "dati_del_paziente" in targets


def test_classify_report_targets_multidisciplinary_composite():
    text_mdm = """
    Verbale Riunione Multidisciplinare Senologica
    Paziente discussa collegialmente.
    Mammografia ed ecografia mammaria: nodulo BI-RADS 4C.
    Core biopsy Tru-cut: carcinoma invasivo G2 ER 95% Ki67 20%.
    Indicazione: chirurgia conservativa dopo discussione.
    """
    targets = FormClassifier.classify_report_targets(
        text=text_mdm,
        hint="2026_VISITA_MULTIDISCIPLINARE.pdf"
    )
    assert "dati_del_paziente" in targets
    assert "radiologia" in targets
    assert "anatomia_patologica_preoperatoria" in targets


def test_classify_report_targets_multidisciplinary_postop_histology():
    """Tests multidisciplinary meeting reports matching 'Lettera Multidisciplinare.odt' schema.
    When 'INTERVENTO ESEGUITO' and 'ESAME ISTOLOGICO DEFINITIVO' are populated,
    the patient underwent surgery and post-op histology is available.
    Therefore, targets must include both 'struttura_della_lesione' and 'anatomia_patologica'.
    """
    text_mdm_postop = """
    GRUPPO MULTIDISCIPLINARE BREAST UNIT
    Nome Cognome: Rossi Maria, Data Nascita: 1965-04-12
    ANAMNESI PATOLOGICA REMOTA: Ipertensione arteriosa
    FAMILIARITA'/FATTORI DI RISCHIO: Negativa npl mammaria. Menopausa a 52 anni.

    DIAGNOSI PREOPERATORIA
    Mammografia clinica: lesione nodulare sospetta.
    Microbiopsia con tru-cut: B5, ER 90%, PgR 80%, Ki-67 15%, HER2 negativo.

    MEETING MULTIDISCIPLINARE in data 10/01/2026: indicata chirurgia conservativa.

    INTERVENTO ESEGUITO
    In data 20/01/2026 è stato eseguito intervento di quadrantectomia supero-esterna destra e biopsia del linfonodo sentinella.

    ESAME ISTOLOGICO DEFINITIVO
    pT 1c G 2 N 0(sn)
    ER 95% PR 85% Ki67 18% Her2 score 1+
    margini chirurgici indenni (> 5 mm).

    MEETING MULTIDISCIPLINARE DATA 05/02/2026: indicata radioterapia adiuvante + ormonoterapia.
    """
    targets = FormClassifier.classify_report_targets(
        text=text_mdm_postop,
        hint="2026_LETTERA_MULTIDISCIPLINARE.pdf"
    )
    assert "dati_del_paziente" in targets
    assert "radiologia" in targets
    assert "anatomia_patologica_preoperatoria" in targets
    assert "struttura_della_lesione" in targets
    assert "anatomia_patologica" in targets


def test_normalizer_form_completion_status():
    field = REDCapField(
        field_name="diabetes",
        form_name="dati_del_paziente",
        field_type="dropdown",
        field_label="Diabete",
        select_choices_or_calculations="0, No | 1, Sì | 9, Ignoto"
    )

    # 1. Primary form -> complete status '2'
    rec_primary = REDCapNormalizer.normalize_record(
        extracted_data={"diabetes": "1"},
        fields=[field],
        record_id="101",
        form_name="dati_del_paziente",
        mark_form_complete=True,
        form_completion_status="2"
    )
    assert rec_primary["diabetes"] == "1"
    assert rec_primary["dati_del_paziente_complete"] == "2"

    # 2. Secondary partial form -> unverified status '1'
    rec_secondary = REDCapNormalizer.normalize_record(
        extracted_data={"diabetes": "1"},
        fields=[field],
        record_id="101",
        form_name="dati_del_paziente",
        mark_form_complete=True,
        form_completion_status="1"
    )
    assert rec_secondary["diabetes"] == "1"
    assert rec_secondary["dati_del_paziente_complete"] == "1"

    # 3. Secondary form with mark_form_complete=False -> no status field
    rec_no_complete = REDCapNormalizer.normalize_record(
        extracted_data={"diabetes": "1"},
        fields=[field],
        record_id="101",
        form_name="dati_del_paziente",
        mark_form_complete=False
    )
    assert rec_no_complete["diabetes"] == "1"
    assert "dati_del_paziente_complete" not in rec_no_complete


def test_merge_flat_records_multiform():
    rec_patient = {
        "record_id": "999",
        "_document_date": "2026-03-01",
        "diabetes": "1",
        "hypertension": "1",
        "dati_del_paziente_complete": "1"
    }
    rec_chemo = {
        "record_id": "999",
        "_document_date": "2026-03-01",
        "aromatase_inhibitor_ai": "2",
        "adjuvant_endocrine_therapy": "1",
        "chemioterapia_complete": "2"
    }
    rec_rt = {
        "record_id": "999",
        "_document_date": "2026-03-01",
        "radiotherapy_performed": "1",
        "whole_breast": "2",
        "radioterapia_complete": "2"
    }

    merged = REDCapService.merge_flat_records([rec_patient, rec_chemo, rec_rt])
    assert len(merged) == 1
    m = merged[0]
    assert m["record_id"] == "999"
    assert m["diabetes"] == "1"
    assert m["hypertension"] == "1"
    assert m["aromatase_inhibitor_ai"] == "2"
    assert m["adjuvant_endocrine_therapy"] == "1"
    assert m["radiotherapy_performed"] == "1"
    assert m["whole_breast"] == "2"
    assert m["chemioterapia_complete"] == "2"
    assert m["radioterapia_complete"] == "2"
    assert m["dati_del_paziente_complete"] == "1"


def test_patient_pipeline_multiform_extraction_flow(tmp_path):
    mock_pdf = tmp_path / "visita_oncologica_test.pdf"
    mock_pdf.write_text("dummy pdf content", encoding="utf-8")

    text_content = """
    Visita Oncologica - Dr.ssa Maria Duro
    Diagnosi: neoplasia mammella sx ER+ Her2-
    Anamnesi: diabete tipo 2 (ipoglicemia), ipertensione.
    Terapia: Anastrozolo 1mg cp.
    """

    mock_redcap = MagicMock()
    mock_redcap.api_url = "https://mock.redcap/api/"
    mock_redcap.api_token = "mocktoken"
    mock_redcap.find_record_id_by_demographics.return_value = None
    mock_redcap.generate_next_record_name.return_value = "1001"
    mock_redcap.import_records_to_api.return_value = {"count": 1}

    mock_ai = MagicMock()

    def mock_extract(report_text, fields, pdf_path, record_id, form_name, mark_form_complete=True, form_completion_status="2"):
        if form_name == "chemioterapia":
            return ExtractionResult(
                pdf_path=str(pdf_path),
                status="success",
                extracted_data={"aromatase_inhibitor_ai": "2"},
                redcap_record={"record_id": record_id, "aromatase_inhibitor_ai": "2", "chemioterapia_complete": "2"}
            )
        elif form_name == "dati_del_paziente":
            return ExtractionResult(
                pdf_path=str(pdf_path),
                status="success",
                extracted_data={"diabetes": "1", "hypertension": "1"},
                redcap_record={"record_id": record_id, "diabetes": "1", "hypertension": "1", "dati_del_paziente_complete": "1"}
            )
        return ExtractionResult(pdf_path=str(pdf_path), status="success", extracted_data={}, redcap_record={"record_id": record_id})

    mock_ai.extract_from_text.side_effect = mock_extract

    field_chemo = REDCapField(field_name="aromatase_inhibitor_ai", form_name="chemioterapia", field_type="dropdown", field_label="IA")
    field_diab = REDCapField(field_name="diabetes", form_name="dati_del_paziente", field_type="dropdown", field_label="Diabete")
    mock_redcap.filter_fields_by_form.side_effect = lambda all_f, form: [field_chemo] if form == "chemioterapia" else [field_diab]

    pipeline = PatientPipeline(redcap_service=mock_redcap, ai_extractor=mock_ai)

    with patch("src.pdf_extractor.PDFExtractor.extract_text", return_value=text_content), \
         patch("src.pdf_extractor.PDFExtractor.extract_document_date", return_value="2026-03-01"):
        res = pipeline.process_patient(
            patient_id="1001",
            pdf_paths=[mock_pdf],
            auto_upload=True,
            output_dir=tmp_path
        )

    assert res.total_pdfs == 1
    assert res.successful_pdfs == 1
    # Entrambi i form devono essere stati estratti e popolati
    assert "chemioterapia" in res.forms_populated
    assert "dati_del_paziente" in res.forms_populated

    # Verifica record inviato all'API REDCap
    mock_redcap.import_records_to_api.assert_called_once()
    uploaded_records = mock_redcap.import_records_to_api.call_args[0][0]
    assert len(uploaded_records) == 1
    rec = uploaded_records[0]
    assert rec["record_id"] == "1001"
    assert rec["aromatase_inhibitor_ai"] == "2"
    assert rec["diabetes"] == "1"
