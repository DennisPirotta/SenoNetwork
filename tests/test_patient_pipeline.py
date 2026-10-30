import pytest
from pathlib import Path
from src.form_classifier import FormClassifier
from src.dotnet_client import DotNetApiClient


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
