"""Comprehensive regression test suite for security, clinical integrity, parser safety, and Neon P0-P3 fixes."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.ai_extractor import BaseAIExtractor, LlamaCppExtractor
from src.api_server import app, run_server
from src.config import Settings, get_settings
from src.dotnet_client import DotNetApiClient
from src.form_classifier import FormClassifier
from src.models import REDCapField
from src.normalizer import REDCapNormalizer
from src.patient_pipeline import PatientPipeline, is_safe_webhook_url
from src.pdf_extractor import PDFExtractor
from src.priamo_cup_client import PriamoCupClient
from src.redcap_service import REDCapService
from src.schema_builder import SchemaBuilder
from scripts.extract_patients_from_shared_file import estrai_pazienti_da_fogli_cup, estrai_pazienti_univoci


def test_form_classifier_lettera_multidisciplinare_blank_template():
    """Blank surgical/pathology section headers in Lettera Multidisciplinare.odt must not misclassify biopsia."""
    blank_lettera = (
        "OSPEDALE VALDUCE - U.O. CHIRURGIA SENOLOGICA\n"
        "LETTERA MULTIDISCIPLINARE PREOPERATORIA\n"
        "Referto biopsia mammaria: 4 frustoli agobioptici core biopsy QSE destra.\n"
        "Diagnosi B5b Carcinoma duttale invasivo G2 ER 95% PgR 80%.\n\n"
        "INTERVENTO ESEGUITO\n"
        "In data ......... è stato eseguito intervento di \n\n"
        "ESAME ISTOLOGICO DEFINITIVO\n"
        "pT  G  N  ER  PR  Ki67  Her2  margini \n"
    )
    targets = FormClassifier.classify_report_targets(blank_lettera, hint="Visita Multidisciplinare")
    assert "anatomia_patologica_preoperatoria" in targets
    assert "struttura_della_lesione" not in targets
    assert "anatomia_patologica" not in targets


def test_form_classifier_word_boundaries_and_precedence():
    """Tests vabb/eco word boundaries, vitamin B1/room C1 safety, and pathology hint precedence."""
    # 'probabile', 'lavaggio', 'precedente' must not trigger preop biopsy or radiology via substring
    generic_surg = "U.O. Chirurgia Senologica. Verbale operatorio: probabile risposta completa, lavaggio cavo, precedente storia."
    assert FormClassifier.classify_report(generic_surg) == "struttura_della_lesione"

    # Room C1 / vitamin B1 / B2 must not misclassify as cytology/biopsy
    non_biopsy = "Somministrata vitamina B1 e vitamina B2 in ambulatorio C1."
    assert FormClassifier.classify_report(non_biopsy) != "anatomia_patologica_preoperatoria"

    # Explicit histology hint overrides chirurgia department hint
    assert (
        FormClassifier.classify_report(
            "U.O. Chirurgia Senologica - pT1c pN0 margini indenni",
            hint="Chirurgia_Senologica_istologico_definitivo.pdf",
        )
        == "anatomia_patologica"
    )

    # Real VABB keyword triggers core biopsy form
    assert FormClassifier.classify_report("Biopsia mammaria stereotassica VABB B3") == "anatomia_patologica_preoperatoria"


def test_pdf_extractor_iso_date_and_dob_skip():
    """Supports YYYY-MM-DD and DD/MM/YYYY, rejects invalid calendar dates, and skips DOB lines."""
    text_with_dob = (
        "Paziente: ROSSI MARIA\n"
        "Data di nascita: 12/05/1965\n"
        "Data Referto: 2026-03-14\n"
    )
    assert PDFExtractor.extract_document_date("referto.pdf", text=text_with_dob) == "2026-03-14"

    invalid_then_valid = "Data esame: 31/02/2025\nData firma: 02/03/2025"
    assert PDFExtractor.extract_document_date("referto.pdf", text=invalid_then_valid) == "2025-03-02"


def test_normalizer_ast_branching_and_validation_safety():
    """Verifies eval()-free AST branching logic, digit choice guard, date & numeric range checks."""
    # Safe AST branching handles code injection attempts safely without executing code
    assert REDCapNormalizer.evaluate_branching_logic("__import__('os').system('echo pwn')", {}) is False
    assert REDCapNormalizer.evaluate_branching_logic("[a] = '1' and [size] >= 12", {"a": "1", "size": "15"}) is True
    assert REDCapNormalizer.evaluate_branching_logic("[chk(2)] = '1'", {"chk___2": "1"}) is True

    # Digit/code token mismatch guard: 'Grado 3' must NEVER fuzzy-match 'Grado 1'
    grade_choices = {"1": "Grado 1", "2": "Grado 2", "3": "Grado 3"}
    assert REDCapNormalizer._normalize_choice("Grado 3", grade_choices) == "3"
    only_low_choices = {"1": "Grado 1", "2": "Grado 2"}
    assert REDCapNormalizer._normalize_choice("Grado 3", only_low_choices) == ""

    # Unchecked checkboxes omitted when include_unchecked_checkboxes=False & valid date/number bounds
    fields = [
        REDCapField(field_name="op_type", field_type="checkbox", select_choices_or_calculations="1, A | 2, B"),
        REDCapField(field_name="d_op", field_type="text", text_validation_type_or_show_slider_number="date_ymd"),
        REDCapField(
            field_name="ki67",
            field_type="text",
            text_validation_type_or_show_slider_number="number",
            text_validation_min="0",
            text_validation_max="100",
        ),
    ]
    rec = REDCapNormalizer.normalize_record(
        {"op_type": ["1"], "d_op": "2025-02-30", "ki67": "150"},
        fields=fields,
        record_id="10",
        form_name="test_form",
        include_unchecked_checkboxes=False,
    )
    assert rec["op_type___1"] == "1"
    assert "op_type___2" not in rec
    assert rec["d_op"] == ""
    assert rec["ki67"] == ""
    # Only checkbox checked triggers complete, not unchecked zero checkboxes
    rec_unchecked = REDCapNormalizer.normalize_record(
        {"op_type": []}, fields=fields, record_id="11", form_name="test_form", include_unchecked_checkboxes=True
    )
    assert "test_form_complete" not in rec_unchecked


def test_schema_builder_no_choice_truncation_and_optional_record_id():
    """Ensures SchemaBuilder exposes >10 choices and omits record_id when requested."""
    many_choices = " | ".join(f"{i}, Opzione {i}" for i in range(1, 15))
    fields = [
        REDCapField(field_name="record_id", field_type="text"),
        REDCapField(field_name="istotipo", field_type="dropdown", select_choices_or_calculations=many_choices),
    ]
    schema = SchemaBuilder.build_json_schema(fields, include_record_id=False)
    assert "record_id" not in schema["properties"]
    assert "'14'=Opzione 14" in schema["properties"]["istotipo"]["description"]

    guidelines = SchemaBuilder.build_prompt_guidelines(fields, include_record_id=False)
    assert "record_id" not in guidelines
    assert '"14"=Opzione 14' in guidelines


def test_ai_extractor_telemetry_and_deterministic_validation():
    """Verifies AI telemetry fields and removal of hallucinated clinical dates/out-of-range percentages."""
    fields = [
        REDCapField(field_name="date_of_mx", field_type="text", text_validation_type_or_show_slider_number="date_ymd"),
        REDCapField(field_name="er_positive_cells", field_label="ER (%)", field_type="text", text_validation_type_or_show_slider_number="integer"),
    ]

    class FakeExtractor(BaseAIExtractor):
        backend_name = "llama_cpp"

        def is_available(self) -> bool:
            return True

        def _call_llm(self, system_prompt, user_prompt, json_schema) -> str:
            return '{"date_of_mx": "2024-01-15", "er_positive_cells": 95}'

    ext = FakeExtractor(model="test-model")
    # Report text mentions 2026-03-10, NOT 2024-01-15 -> date_of_mx must be flagged & nulled
    res = ext.extract_from_text("Mammografia eseguita il 10/03/2026. ER positivi 95%.", fields=fields, record_id="5")
    assert res.status == "success"
    assert res.ai_backend == "llama_cpp"
    assert res.ai_model == "test-model"
    assert res.prompt_chars > 0
    assert any("date_of_mx" in w for w in res.validation_warnings)
    assert res.redcap_record["date_of_mx"] == ""
    assert res.redcap_record["er_positive_cells"] == "95"


def test_llamacpp_stream_uses_context_manager():
    """Verifies LlamaCppExtractor._call_llm closes requests.post streaming responses cleanly."""
    ext = LlamaCppExtractor(base_url="http://localhost:8080", api_mode="chat")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"choices": [{"message": {"content": '{"a": "1"}'}}]}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        out = ext._call_llm("sys", "user", {"type": "object"})
    assert out == '{"a": "1"}'
    assert mock_post.call_args.kwargs["stream"] is True
    assert mock_resp.close.called


def test_patient_pipeline_lazy_ai_identity_check_and_safe_diff(tmp_path):
    """Tests lazy AI init, PDF identity mismatch rejection, safe REDCap diff, and SSRF webhook checks."""
    with patch("src.patient_pipeline.get_ai_extractor") as mock_get_ai:
        pipe = PatientPipeline(settings=Settings(output_dir=tmp_path / "out", state_dir=tmp_path / "state"))
        assert not mock_get_ai.called

    # SSRF checks including single-label internal hostname and DNS resolution to loopback
    assert is_safe_webhook_url("http://127.0.0.1:8080/cb") is False
    assert is_safe_webhook_url("http://redis:6379/cb") is False
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
        assert is_safe_webhook_url("https://rebind.example.com/hook") is False

    # PDF identity mismatch skips extraction cleanly
    wrong_pdf = tmp_path / "wrong.pdf"
    wrong_pdf.write_bytes(b"%PDF-1.4")
    mock_redcap = MagicMock(spec=REDCapService)
    mock_redcap.api_url = "https://mock.redcap/api/"
    mock_redcap.api_token = "TOK"
    mock_redcap.find_record_id_by_demographics.return_value = "55"
    mock_redcap.get_record.return_value = {
        "record_id": "55",
        "grade": "1",
        "lesion___1": "1",
        "radiologia_complete": "2",
    }
    mock_ai = MagicMock()

    pipe2 = PatientPipeline(
        settings=Settings(output_dir=tmp_path / "out", state_dir=tmp_path / "state"),
        redcap_service=mock_redcap,
        ai_extractor=mock_ai,
    )
    with patch(
        "src.patient_pipeline.PDFExtractor.extract_text",
        return_value="Paziente: BIANCHI LUCIA\nMammografia bilaterale BI-RADS 2",
    ):
        res = pipe2.process_patient(
            patient_id="55",
            name="Anna",
            surname="Verdi",
            date_of_birth="1975-01-01",
            pdf_paths=[wrong_pdf],
            auto_upload=True,
        )
    assert res.documents[0].status == "error"
    assert "mismatch" in (res.documents[0].error or "").lower()
    assert not mock_ai.extract_from_text.called

    # Safe merge preserves existing REDCap field & checked checkbox
    merged = REDCapService.merge_with_existing_record(
        existing={"record_id": "55", "grade": "1", "lesion___1": "1", "radiologia_complete": "2"},
        updates={"record_id": "55", "grade": "3", "lesion___1": "0", "pt": "1c", "radiologia_complete": "1"},
        overwrite=False,
    )
    assert "grade" not in merged
    assert "lesion___1" not in merged
    assert "radiologia_complete" not in merged
    assert merged["pt"] == "1c"


def test_dotnet_does_not_conflate_record_id_with_priamo_id():
    """DotNetApiClient._resolve_patient must not invent PriamoPatient(patient_id=record_id) for integers."""
    client = DotNetApiClient(settings=Settings())
    assert client._resolve_patient("150") is None
    resolved_explicit = client._resolve_patient("150", priamo_id="987654")
    assert resolved_explicit is not None and resolved_explicit.patient_id == "987654"


def test_redcap_concurrent_id_allocation_and_homonym_rejection():
    """Thread-safe sequential ID allocation and ambiguity rejection for homonyms / accent-insensitive match."""
    svc = REDCapService(api_url="https://mock.redcap/api/", api_token="TOK")
    svc.export_records_from_api = MagicMock(return_value=[{"record_id": "10"}])

    with patch("requests.post", side_effect=RuntimeError("no generateNextRecordName")):
        with ThreadPoolExecutor(max_workers=6) as pool:
            allocated = list(pool.map(lambda _: svc.generate_next_record_name(), range(6)))
    assert len(set(allocated)) == 6
    assert sorted(int(x) for x in allocated) == [11, 12, 13, 14, 15, 16]

    # Homonym rejection vs accent-insensitive match & CF match
    ambiguous_records = [
        {"record_id": "1", "name": "Maria", "surname": "Salvadé", "date_of_birth": "1970-01-01"},
        {"record_id": "2", "name": "Maria", "surname": "Salvadé", "date_of_birth": "1970-01-01"},
        {"record_id": "3", "name": "Lucia", "surname": "Salvadè", "date_of_birth": "1975-02-02", "codice_fiscale": "SLVLCU75B42C933X"},
    ]
    assert svc.find_record_id_by_demographics("Maria", "Salvade", "1970-01-01", records_cache=ambiguous_records) is None
    assert svc.find_record_id_by_demographics("Lucia", "Salvadé", "1975-02-02", records_cache=ambiguous_records) == "3"
    assert svc.find_record_id_by_demographics(codice_fiscale="slvlcu75b42c933x", records_cache=ambiguous_records) == "3"


def test_priamo_cup_validates_xlsx_magic_bytes(tmp_path):
    """PriamoCupClient.export_worksheets rejects non-ZIP/HTML payloads with RuntimeError."""
    client = PriamoCupClient(username="u", password="p", verify_ssl=True)
    client._is_authenticated = True

    export_resp = MagicMock()
    export_resp.status_code = 200
    export_resp.text = '"fake.xlsx"'
    export_resp.raise_for_status.return_value = None

    html_resp = MagicMock()
    html_resp.status_code = 200
    html_resp.iter_content.return_value = [b"<html>Login Expired</html>"]
    html_resp.raise_for_status.return_value = None

    with patch.object(client, "get_csrf_token", return_value="csrf"):
        with patch.object(client.session, "post", return_value=export_resp):
            with patch.object(client.session, "get", return_value=html_resp):
                with pytest.raises(RuntimeError, match="Excel"):
                    client.export_worksheets("2026-01-01", "2026-01-02", output_dir=tmp_path)


def test_priamo_verify_cohort_counts_lowercase_statuses():
    """POST /api/priamo/verify-cohort counts 'found' and 'not_found' statuses accurately."""
    override_cfg = get_settings().model_copy(
        update={
            "api_key": None,
            "priamo_api_url": "http://mock.priamo",
            "priamo_username": "u",
            "priamo_password": "p",
        }
    )
    app.dependency_overrides[get_settings] = lambda: override_cfg
    try:
        mock_res = [
            {"patient_id": "1", "status": "found"},
            {"patient_id": "2", "status": "found"},
            {"patient_id": "3", "status": "not_found"},
            {"patient_id": "4", "status": "incomplete"},
        ]
        with patch("src.dotnet_client.DotNetApiClient.verify_patients_cohort", return_value=mock_res):
            client = TestClient(app)
            resp = client.post(
                "/api/priamo/verify-cohort",
                json={"patients": [{"name": "A", "surname": "B", "date_of_birth": "1970-01-01"}]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["found_count"] == 2
        assert body["not_found_count"] == 1
        assert body["incomplete_count"] == 1
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_cup_extractor_does_not_merge_distinct_rows_missing_dob(tmp_path):
    """Distinct patients sharing name/surname with missing DOB must not be conflated unless CF/PriamoID matches."""
    df_cup = pd.DataFrame([
        {"PAZ_COGNOME": "ROSSI", "PAZ_NOME": "MARIA", "PAZ_DATA_NASCITA": None, "PREST_DESCRIZIONE": "A"},
        {"PAZ_COGNOME": "ROSSI", "PAZ_NOME": "MARIA", "PAZ_DATA_NASCITA": None, "PREST_DESCRIZIONE": "B"},
    ])
    pzs = estrai_pazienti_da_fogli_cup(df_cup, only_complete=False)
    assert len(pzs) == 2

    df_xls = tmp_path / "reg.xlsx"
    raw = pd.DataFrame([
        [None, None, "PZ", "gg/m/aa"],
        [None, None, "Bianchi Anna", None],
        [None, None, "Bianchi Anna", None],
    ])
    raw.to_excel(df_xls, header=False, index=False, engine="openpyxl")
    pzs_reg = estrai_pazienti_univoci(df_xls, only_complete=False)
    assert len(pzs_reg) == 2


def test_api_server_run_server_refuses_insecure_non_loopback():
    """run_server refuses binding to 0.0.0.0 without SENONETWORK_API_KEY unless allow_insecure_local_dev=True."""
    cfg = Settings(api_host="0.0.0.0", api_key=None, allow_insecure_local_dev=False)
    with patch("src.api_server.get_settings", return_value=cfg):
        with pytest.raises(RuntimeError, match="SENONETWORK_API_KEY"):
            run_server(host="0.0.0.0")
