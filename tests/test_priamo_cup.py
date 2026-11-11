"""Test unitari per il client PriamoCUP e l'estrazione di anagrafiche dai fogli di lavoro."""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from src.priamo_cup_client import (
    AGENDE_SENOLOGIA,
    CODICE_STRUTTURA_VALDUCE,
    UNITA_SENOLOGIA,
    PriamoCupAuthError,
    PriamoCupClient,
    to_form_pairs,
)
from scripts.extract_patients_from_shared_file import (
    Paziente,
    estrai_pazienti_da_fogli_cup,
    estrai_pazienti_univoci,
)


def test_to_form_pairs_kendo_serialization():
    data = {
        "tipo": "xlsx",
        "model": {
            "filtri": {
                "riga1": {"daData": "2026-11-09T00:00:00.000Z"},
                "riga4": {
                    "strutturaErogatrice": {
                        "selezionati": [{"codice": "3000"}, {"codice": "3003"}]
                    }
                },
            },
            "attivo": True,
            "vuoto": None,
        },
    }
    pairs = to_form_pairs(data)
    assert ("tipo", "xlsx") in pairs
    assert ("model[filtri][riga1][daData]", "2026-11-09T00:00:00.000Z") in pairs
    assert ("model[filtri][riga4][strutturaErogatrice][selezionati][0][codice]", "3000") in pairs
    assert ("model[filtri][riga4][strutturaErogatrice][selezionati][1][codice]", "3003") in pairs
    assert ("model[attivo]", "true") in pairs
    assert ("model[vuoto]", "") in pairs


def test_priamo_cup_constants():
    assert CODICE_STRUTTURA_VALDUCE == "2037"
    assert UNITA_SENOLOGIA == "37002"
    assert AGENDE_SENOLOGIA == ["3000", "3003", "3004", "3035", "3036"]


def test_priamo_cup_client_authenticate_success():
    client = PriamoCupClient(username="test_user", password="test_password")

    mock_get = MagicMock()
    mock_get.status_code = 200
    mock_get.text = '<html><input name="__RequestVerificationToken" value="mock_token_123" /></html>'

    mock_post = MagicMock()
    mock_post.status_code = 200
    mock_post.url = "https://priamo-home.valduce.it:8062/PriamoHOME/Dashboard/Dashboard"

    with patch.object(client.session, "get", return_value=mock_get):
        with patch.object(client.session, "post", return_value=mock_post):
            client.session.cookies.set(".PRIAMOAUTH", "valid_cookie", domain=".valduce.it")
            client.authenticate()
            assert client._is_authenticated is True


def test_priamo_cup_client_authenticate_failure():
    client = PriamoCupClient(username="test_user", password="wrong_password")

    mock_get = MagicMock()
    mock_get.status_code = 200
    mock_get.text = '<html><input name="__RequestVerificationToken" value="mock_token_123" /></html>'

    mock_post = MagicMock()
    mock_post.status_code = 200
    mock_post.url = "https://priamo-home.valduce.it:8062/PriamoHOME/Account/Login"

    with patch.object(client.session, "get", return_value=mock_get):
        with patch.object(client.session, "post", return_value=mock_post):
            with pytest.raises(PriamoCupAuthError):
                client.authenticate(force=True)


def test_priamo_cup_client_export_worksheets(tmp_path):
    client = PriamoCupClient(username="test_user", password="test_password")
    client._is_authenticated = True

    mock_csrf = MagicMock()
    mock_csrf.status_code = 200
    mock_csrf.text = "<script>priamoMasterPage.site.requestVerificationToken = 'csrf_abc_123';</script>"

    mock_export = MagicMock()
    mock_export.status_code = 200
    mock_export.text = '"temp_report_123.xlsx"'

    mock_download = MagicMock()
    mock_download.status_code = 200
    mock_download.iter_content.return_value = [b"PK\x03\x04mock_excel_content"]

    def mock_get(url, *args, **kwargs):
        if "Riepilogo/FoglioLavoro" in url:
            return mock_csrf
        if "Preleva" in url:
            return mock_download
        return MagicMock(status_code=200)

    with patch.object(client.session, "get", side_effect=mock_get):
        with patch.object(client.session, "post", return_value=mock_export) as mock_post:
            out_file = client.export_worksheets(
                start_date=date(2026, 11, 9),
                end_date=date(2026, 11, 15),
                output_dir=tmp_path,
                custom_filename="test_cup.xlsx",
            )
            assert out_file.exists()
            assert out_file.name == "test_cup.xlsx"
            assert mock_post.called
            # Verifica che il token CSRF sia stato passato negli headers
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["headers"]["RequestVerificationToken"] == "csrf_abc_123"


def test_priamo_cup_client_export_ytd(tmp_path):
    client = PriamoCupClient(username="test_user", password="test_password")

    with patch.object(client, "export_worksheets") as mock_exp:
        mock_exp.return_value = tmp_path / "mock_ytd.xlsx"
        ref_d = date(2026, 11, 11)
        res = client.export_ytd(ref_date=ref_d, output_dir=tmp_path)

        assert mock_exp.called
        call_kwargs = mock_exp.call_args[1]
        assert call_kwargs["start_date"] == date(2026, 1, 1)
        assert call_kwargs["end_date"] == date(2026, 11, 10)  # Oggi - 1 giorno


def test_priamo_cup_client_export_year(tmp_path):
    client = PriamoCupClient(username="test_user", password="test_password")

    # Anno precedente (2025 intero)
    with patch.object(client, "export_worksheets") as mock_exp:
        mock_exp.return_value = tmp_path / "mock_2025.xlsx"
        res = client.export_year(year=2025, output_dir=tmp_path)

        assert mock_exp.called
        call_kwargs = mock_exp.call_args[1]
        assert call_kwargs["start_date"] == date(2025, 1, 1)
        assert call_kwargs["end_date"] == date(2025, 12, 31)


def test_priamo_cup_client_export_month(tmp_path):
    client = PriamoCupClient(username="test_user", password="test_password")

    # Mese specifico (febbraio 2024, anno bisestile)
    with patch.object(client, "export_worksheets") as mock_exp:
        mock_exp.return_value = tmp_path / "mock_month.xlsx"
        res = client.export_month(year=2024, month=2, output_dir=tmp_path)

        assert mock_exp.called
        call_kwargs = mock_exp.call_args[1]
        assert call_kwargs["start_date"] == date(2024, 2, 1)
        assert call_kwargs["end_date"] == date(2024, 2, 29)


def test_priamo_cup_client_export_last_months(tmp_path):
    client = PriamoCupClient(username="test_user", password="test_password")

    with patch.object(client, "export_worksheets") as mock_exp:
        mock_exp.return_value = tmp_path / "mock_months.xlsx"
        ref_d = date(2026, 11, 11)
        res = client.export_last_months(num_months=3, ref_date=ref_d, output_dir=tmp_path)

        assert mock_exp.called
        call_kwargs = mock_exp.call_args[1]
        assert call_kwargs["start_date"] == date(2026, 8, 1)
        assert call_kwargs["end_date"] == date(2026, 11, 10)


def test_estrai_pazienti_da_fogli_cup_dataframe():
    df_cup = pd.DataFrame([
        {
            "PAZ_COGNOME": "ROSSI",
            "PAZ_NOME": "MARIA",
            "PAZ_DATA_NASCITA": "1975-06-15",
            "PAZ_CODICE_FISCALE": "RSSMRA75H55C933A",
            "PAZ_SESSO": "F",
            "PAZ_COMUNE_NASCITA": "COMO",
            "PAZ_PROGRESSIVO_PAZ1": "123456",
            "EROG_CODICE": "3003",
            "SLOT_DATA": "2026-11-10",
            "SLOT_ORA": "1000",
            "PREST_DESCRIZIONE": "Visita Senologica",
        },
        {
            # Stesso paziente, secondo appuntamento (occorrenza multipla da deduplicare)
            "PAZ_COGNOME": "ROSSI",
            "PAZ_NOME": "MARIA",
            "PAZ_DATA_NASCITA": "1975-06-15",
            "PAZ_CODICE_FISCALE": "RSSMRA75H55C933A",
            "PAZ_SESSO": "F",
            "PAZ_COMUNE_NASCITA": "COMO",
            "PAZ_PROGRESSIVO_PAZ1": "123456",
            "EROG_CODICE": "3004",
            "SLOT_DATA": "2026-11-12",
            "SLOT_ORA": "1130",
            "PREST_DESCRIZIONE": "Medicazione",
        },
        {
            "PAZ_COGNOME": "HERRERA DE LA ROSA",
            "PAZ_NOME": "ROSAURA",
            "PAZ_DATA_NASCITA": "1978-08-30",
            "PAZ_CODICE_FISCALE": "HRRRSR78M70Z505I",
            "PAZ_SESSO": "F",
            "PAZ_COMUNE_NASCITA": "REPUBBLICA DOMINICANA",
            "PAZ_PROGRESSIVO_PAZ1": "1536091",
            "EROG_CODICE": "3000",
            "SLOT_DATA": "2026-11-11",
            "SLOT_ORA": "0900",
            "PREST_DESCRIZIONE": "Visita Controllo",
        },
    ])

    pazienti = estrai_pazienti_da_fogli_cup(df_cup)
    assert len(pazienti) == 2

    # Verifica Rossi Maria
    p_rossi = next(p for p in pazienti if p.cognome == "Rossi")
    assert p_rossi.nome == "Maria"
    assert p_rossi.data_nascita == date(1975, 6, 15)
    assert p_rossi.codice_fiscale == "RSSMRA75H55C933A"
    assert p_rossi.priamo_id == "123456"
    assert p_rossi.sesso == "F"
    assert p_rossi.comune_nascita == "Como"
    assert p_rossi.num_appuntamenti == 2
    assert p_rossi.righe_excel == [2, 3]
    assert "3003" in p_rossi.agende
    assert "3004" in p_rossi.agende

    # Verifica Herrera De La Rosa Rosaura
    p_herrera = next(p for p in pazienti if "Herrera" in p.cognome)
    assert p_herrera.cognome == "Herrera De La Rosa"
    assert p_herrera.nome == "Rosaura"
    assert p_herrera.patient_id == "rosaura-herrera-de-la-rosa-1978-08-30"
    assert p_herrera.codice_fiscale == "HRRRSR78M70Z505I"
    assert p_herrera.comune_nascita == "Repubblica Dominicana"

    # Verifica to_export_dict
    export_dict = p_herrera.to_export_dict()
    assert export_dict["PAZ_COGNOME"] == "Herrera De La Rosa"
    assert export_dict["PAZ_NOME"] == "Rosaura"
    assert export_dict["PAZ_DATA_NASCITA"] == "1978-08-30"


def test_estrai_pazienti_univoci_auto_detect_cup():
    df_cup = pd.DataFrame([
        {
            "PAZ_COGNOME": "BIANCHI",
            "PAZ_NOME": "AMELIA",
            "PAZ_DATA_NASCITA": "1951-05-22",
            "PAZ_CODICE_FISCALE": "BNCMLA51E62F205N",
            "EROG_CODICE": "3004",
        }
    ])
    pazienti = estrai_pazienti_univoci(df_cup)
    assert len(pazienti) == 1
    assert pazienti[0].cognome == "Bianchi"
    assert pazienti[0].nome == "Amelia"
    assert pazienti[0].codice_fiscale == "BNCMLA51E62F205N"
