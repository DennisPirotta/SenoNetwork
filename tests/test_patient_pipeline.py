from datetime import date
from unittest.mock import patch
import pytest
from pathlib import Path
from src.form_classifier import FormClassifier
from src.dotnet_client import DotNetApiClient
from src.priamo_client import PriamoClient, PriamoPatient


def test_form_classifier_by_keywords():
    # Radiologia
    text_mx = """
    REFERTO MAMMOGRAFIA BILATERALE ED ECOGRAFIA MAMMARIA
    Paziente asintomatica in screening.
    Densità fibro-ghiandolare di tipo C ACR.
    A destra opacità spiculata in QSE di 12 mm.
    Classificazione: BI-RADS 4.
    """
    assert FormClassifier.classify_report(text_mx) == "radiologia"

    # Anatomia patologica preoperatoria (Biopsia)
    text_biopsia = """
    ANATOMIA PATOLOGICA - BIOPSIA MAMMARIA
    Pervenuti 4 frustoli agobioptici di parenchima mammario mediante core biopsy.
    Diagnosi: Carcinoma duttale infiltrante NST. Grado nucleare G2.
    ER: 90%, PgR: 70%, HER2: 1+, Ki-67: 20%.
    """
    assert FormClassifier.classify_report(text_biopsia) == "anatomia_patologica_preoperatoria"

    # Anatomia patologica postoperatoria (Definitivo su pezzo chirurgico)
    text_definitivo = """
    ANATOMIA PATOLOGICA - REFERTO ISTOLOGICO DEFINITIVO
    Pezzo operatorio di quadrantectomia mammaria sinistra con linfonodo sentinella.
    Carcinoma invasivo NST, grado istologico Nottingham 3.
    Margini chirurgici: indenni, distanza minima 8 mm.
    pT1c pN0.
    """
    assert FormClassifier.classify_report(text_definitivo) == "anatomia_patologica"


def test_form_classifier_hint():
    assert FormClassifier.classify_report("qualsiasi testo", hint="mammografia_dx.pdf") == "radiologia"
    assert FormClassifier.classify_report("qualsiasi testo", hint="core_biopsy_sinistra.pdf") == "anatomia_patologica_preoperatoria"
    assert FormClassifier.classify_report("qualsiasi testo", hint="istologico_definitivo.pdf") == "anatomia_patologica"


def test_dotnet_client_mock():
    client = DotNetApiClient()
    # In mock mode, if not configured, reads from local directory
    docs = client.get_patient_documents("SENO-2024-TEST")
    assert len(docs) >= 1
    assert any("pdf" in d["filename"].lower() for d in docs)


def test_load_patients_from_excel():
    excel_path = Path("/home/dennis/PycharmProjects/PriamoScraper/files/interventi.xls")
    if excel_path.exists():
        patients = DotNetApiClient.load_patients_from_excel(excel_path)
        assert len(patients) > 0
        assert "name" in patients[0]
        assert "surname" in patients[0]
        assert "date_of_birth" in patients[0]


def test_priamo_fetch_reports_no_nested_directory(tmp_path):
    """Ensures fetch_all_patient_reports does not create a redundant subfolder with the same slug."""
    client = PriamoClient(base_url="http://mock-priamo", username="user", password="pwd")
    patient = PriamoPatient(
        patient_id="12345",
        name="Sonia Valentina",
        surname="Lombardi",
        date_of_birth=date(1967, 8, 13)
    )

    # Case 1: When destination_dir already targets the patient's slug folder
    target_dir = tmp_path / patient.slug
    target_dir.mkdir(parents=True, exist_ok=True)

    with patch.object(client, "get_patient_events", return_value=[]):
        client.fetch_all_patient_reports(patient, destination_dir=target_dir)

    # Must NOT create target_dir / patient.slug
    assert not (target_dir / patient.slug).exists()
    assert target_dir.exists()

    # Case 2: When destination_dir is a general parent folder (e.g. data/downloads)
    parent_dir = tmp_path / "downloads"
    parent_dir.mkdir(parents=True, exist_ok=True)

    with patch.object(client, "get_patient_events", return_value=[]):
        client.fetch_all_patient_reports(patient, destination_dir=parent_dir)

    # Must create parent_dir / patient.slug, and NOT parent_dir / patient.slug / patient.slug
    assert (parent_dir / patient.slug).exists()
    assert not (parent_dir / patient.slug / patient.slug).exists()


def test_priamo_download_pdf_refertato_and_fallback(tmp_path):
    """Tests both FIRMATO and REFERTATO download modes in PriamoClient."""
    from unittest.mock import MagicMock
    client = PriamoClient(base_url="http://mock-priamo", username="user", password="pwd")
    dest_pdf = tmp_path / "test_report.pdf"

    # Mock authentication
    client._authenticated = True

    # Scenario 1: Direct REFERTATO download
    mock_resp_pdf = MagicMock()
    mock_resp_pdf.content = b"%PDF-1.4 mock content"
    mock_resp_pdf.status_code = 200

    with patch.object(client, "_request", return_value=mock_resp_pdf) as mock_req:
        res = client.download_document_pdf(
            document_id="",
            event_id="27189768",
            repository_id="N",
            destination_path=dest_pdf,
            event_type="AMB",
            preferred_stato="REFERTATO"
        )
        assert res == dest_pdf
        assert dest_pdf.exists()
        assert dest_pdf.read_bytes().startswith(b"%PDF")
        
        # Check params sent
        called_params = mock_req.call_args[1]["params"]
        assert called_params["stato"] == "REFERTATO"
        assert called_params["idEvento"] == "27189768"
        assert called_params["tipoEvento"] == "AMB"
        assert called_params["repository"] == "N"

    # Scenario 2: FIRMATO fails (returns HTML error), falls back to REFERTATO
    dest_fallback = tmp_path / "fallback.pdf"
    mock_resp_html = MagicMock()
    mock_resp_html.content = b"<html>Document not found in repository</html>"
    mock_resp_html.status_code = 200

    with patch.object(client, "_request", side_effect=[mock_resp_html, mock_resp_pdf]) as mock_req2:
        res2 = client.download_document_pdf(
            document_id="DOC123",
            event_id="27189768",
            repository_id="REPO1",
            destination_path=dest_fallback,
            event_type="AMB",
            preferred_stato="FIRMATO"
        )
        assert res2 == dest_fallback
        assert dest_fallback.exists()
        # Verify fallback was invoked (2 calls)
        assert mock_req2.call_count == 2
        first_call_params = mock_req2.call_args_list[0][1]["params"]
        second_call_params = mock_req2.call_args_list[1][1]["params"]
        assert first_call_params["stato"] == "FIRMATO"
        assert second_call_params["stato"] == "REFERTATO"


def test_priamo_fetch_outpatient_reports_without_documents(tmp_path):
    """Verifies that events with referto='S' but empty doc_list are downloaded via REFERTATO mode."""
    from unittest.mock import MagicMock
    client = PriamoClient(base_url="http://mock-priamo", username="user", password="pwd")
    client._authenticated = True
    patient = PriamoPatient(
        patient_id="12345",
        name="Anna",
        surname="Rossi",
        date_of_birth=date(1975, 5, 20)
    )

    mock_events = [
        {
            "idEvento": "999888",
            "referto": "S",
            "pdf": "N",
            "primoEsame": "VISITA SENOLOGICA",
            "tipo": "AMB",
            "repository": "N"
        }
    ]

    mock_pdf = MagicMock()
    mock_pdf.content = b"%PDF-1.4 Outpatient report"
    mock_pdf.status_code = 200

    with patch.object(client, "get_patient_events", return_value=mock_events):
        with patch.object(client, "get_event_documents", return_value=[]):
            with patch.object(client, "_request", return_value=mock_pdf) as mock_req:
                docs = client.fetch_all_patient_reports(patient, destination_dir=tmp_path)
                assert len(docs) == 1
                assert docs[0].event_id == "999888"
                assert docs[0].local_path.exists()
                assert "VISITA_SENOLOGICA" in docs[0].local_path.name
                # Check that download was done with stato=REFERTATO
                called_params = mock_req.call_args[1]["params"]
                assert called_params["stato"] == "REFERTATO"
                assert called_params["idEvento"] == "999888"


def test_priamo_concurrent_pdf_downloads(tmp_path):
    """Verifies that multiple documents are downloaded concurrently using thread pools."""
    from unittest.mock import MagicMock
    client = PriamoClient(base_url="http://mock-priamo", username="user", password="pwd", max_workers=4)
    client._authenticated = True
    patient = PriamoPatient(
        patient_id="12345",
        name="Luisa",
        surname="Verdi",
        date_of_birth=date(1980, 1, 1)
    )

    mock_events = [
        {"idEvento": f"EVT_{i}", "referto": "S", "pdf": "S", "primoEsame": f"ESAME_{i}", "tipo": "AMB", "repository": "N"}
        for i in range(5)
    ]

    mock_pdf = MagicMock()
    mock_pdf.content = b"%PDF-1.4 Concurrent report"
    mock_pdf.status_code = 200

    with patch.object(client, "get_patient_events", return_value=mock_events):
        with patch.object(client, "get_event_documents", return_value=[]):
            with patch.object(client, "_request", return_value=mock_pdf) as mock_req:
                docs = client.fetch_all_patient_reports(patient, destination_dir=tmp_path, max_workers=4)
                assert len(docs) == 5
                for doc in docs:
                    assert doc.local_path.exists()
                    assert doc.local_path.read_bytes().startswith(b"%PDF")
                # Ensure 5 requests were made
                assert mock_req.call_count == 5



