"""Test unitari per la verifica dell'esistenza dei pazienti su Priamo (GetAnagrafica)."""

from datetime import date
from unittest.mock import MagicMock, patch
import pytest

from src.priamo_client import (
    PriamoClient,
    PriamoPatient,
    normalize_priamo_accents
)
from src.dotnet_client import DotNetApiClient


@pytest.fixture
def mock_priamo_settings():
    settings = MagicMock()
    settings.priamo_api_url = "https://mock.priamo.local"
    settings.priamo_username = "user"
    settings.priamo_password = "password"
    settings.priamo_cloudflared_client_id = None
    settings.priamo_cloudflared_client_secret = None
    settings.priamo_timeout_seconds = 10
    settings.priamo_max_workers = 2
    return settings


def test_search_patient_progressivo_paz1(mock_priamo_settings):
    """Verifica che search_patient estragga correttamente il progressivoPaz1 da datiAa

    anche quando consensi è una lista vuota.
    """
    client = PriamoClient(settings=mock_priamo_settings)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "datiMef": {
                "cognome": "CRAPELLA",
                "nome": "LOREDANA",
                "codiceFiscale": "CRPLDN65R62I829P",
                "comuneNascita": {"nome": "SONDRIO"},
                "sesso": "F"
            },
            "datiAsl": {
                "tesseraSanitaria": "106JH937"
            },
            "datiAa": {
                "progressivoPaz1": 1743189,
                "cellulare": "335123456"
            },
            "consensi": []  # Empty!
        }
    ]

    with patch.object(client, "authenticate"):
        with patch.object(client, "_request", return_value=mock_resp):
            p = client.search_patient("Loredana", "Crapella", "1965-10-22")
            assert p is not None
            assert p.patient_id == "1743189"
            assert p.name == "Loredana"
            assert p.surname == "Crapella"
            assert p.codice_fiscale == "CRPLDN65R62I829P"
            assert p.comune_nascita == "SONDRIO"
            assert p.sesso == "F"
            assert p.tessera_sanitaria == "106JH937"
            assert p.telefono == "335123456"


def test_search_patient_fallback_consensi(mock_priamo_settings):
    """Verifica che search_patient estragga idAnagPaz1 da consensi se datiAa non ha progressivoPaz1."""
    client = PriamoClient(settings=mock_priamo_settings)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "datiMef": {"cognome": "ROSSI", "nome": "MARIA", "codiceFiscale": "RSSMRA70A41C933A"},
            "datiAa": {},
            "consensi": [{"idAnagPaz1": "554433"}]
        }
    ]

    with patch.object(client, "authenticate"):
        with patch.object(client, "_request", return_value=mock_resp):
            p = client.search_patient("Maria", "Rossi", date(1970, 1, 1))
            assert p is not None
            assert p.patient_id == "554433"
            assert p.codice_fiscale == "RSSMRA70A41C933A"


def test_search_patient_not_found(mock_priamo_settings):
    """Verifica che search_patient restituisca None quando Priamo non trova corrispondenze."""
    client = PriamoClient(settings=mock_priamo_settings)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []

    with patch.object(client, "authenticate"):
        with patch.object(client, "_request", return_value=mock_resp):
            p = client.search_patient("Paziente", "Inesistente", "1980-01-01", exact_only=True)
            assert p is None


def test_verify_patient_cohort(mock_priamo_settings):
    """Verifica l'esecuzione batch di verify_patient_cohort."""
    client = PriamoClient(settings=mock_priamo_settings)

    cohort = [
        {"name": "Loredana", "surname": "Crapella", "date_of_birth": "1965-10-22", "patient_id": "l-c"},
        {"name": "Inesistente", "surname": "Paziente", "date_of_birth": "1980-01-01", "patient_id": "i-p"},
        {"name": "", "surname": "Ndiaye", "date_of_birth": None, "patient_id": "ndiaye"}
    ]

    found_patient = PriamoPatient(
        patient_id="1743189",
        name="Loredana",
        surname="Crapella",
        date_of_birth=date(1965, 10, 22),
        codice_fiscale="CRPLDN65R62I829P"
    )

    def fake_search(name, surname, date_of_birth, exact_only=False):
        if surname == "Crapella":
            return found_patient
        return None

    with patch.object(client, "authenticate"):
        with patch.object(client, "search_patient", side_effect=fake_search):
            results = client.verify_patient_cohort(cohort, max_workers=2)
            assert len(results) == 3

            # Primo paziente: trovato
            assert results[0]["status"] == "found"
            assert results[0]["exists_in_priamo"] is True
            assert results[0]["priamo_id"] == "1743189"
            assert results[0]["codice_fiscale"] == "CRPLDN65R62I829P"

            # Secondo paziente: non trovato
            assert results[1]["status"] == "not_found"
            assert results[1]["exists_in_priamo"] is False
            assert results[1]["priamo_id"] is None

            # Terzo paziente: incompleto
            assert results[2]["status"] == "incomplete"
            assert results[2]["exists_in_priamo"] is False


def test_dotnet_client_verification_methods(mock_priamo_settings):
    """Verifica i metodi di facciata su DotNetApiClient."""
    client = DotNetApiClient(settings=mock_priamo_settings)

    with patch.object(client.priamo, "search_patient") as mock_search:
        mock_search.return_value = PriamoPatient(patient_id="123", name="A", surname="B")
        res = client.verify_patient_in_priamo("A", "B", "1970-01-01")
        assert res.patient_id == "123"
        mock_search.assert_called_once_with(name="A", surname="B", date_of_birth="1970-01-01")

    with patch.object(client.priamo, "verify_patient_cohort") as mock_cohort:
        mock_cohort.return_value = [{"status": "found"}]
        res_cohort = client.verify_patients_cohort([{"name": "A", "surname": "B"}])
        assert len(res_cohort) == 1
        assert res_cohort[0]["status"] == "found"


def test_normalize_priamo_accents():
    """Verifica che le vocali accentate vengano convertite in lettera + apostrofo

    secondo la convenzione del database Priamo.
    """
    assert normalize_priamo_accents("Salvadé") == "Salvade'"
    assert normalize_priamo_accents("Salvadè") == "Salvade'"
    assert normalize_priamo_accents("D'Angiò") == "D'Angio'"
    assert normalize_priamo_accents("Nicolò") == "Nicolo'"
    assert normalize_priamo_accents("Cantù") == "Cantu'"
    assert normalize_priamo_accents("D’Elia") == "D'Elia"  # Fancy apostrophe
    assert normalize_priamo_accents("Èlena") == "E'lena"
    assert normalize_priamo_accents("") == ""
    assert normalize_priamo_accents(None) == ""


def test_search_patient_with_accents_converted(mock_priamo_settings):
    """Verifica che search_patient invii a Priamo i parametri con accenti convertiti in apostrofi."""
    client = PriamoClient(settings=mock_priamo_settings)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "datiMef": {
                "cognome": "SALVADE'",
                "nome": "ANNA MARIA",
                "codiceFiscale": "SLVNMR62C60L956G"
            },
            "datiAa": {
                "progressivoPaz1": 1508277
            }
        }
    ]

    with patch.object(client, "authenticate"):
        with patch.object(client, "_request", return_value=mock_resp) as mock_req:
            p = client.search_patient(name="Anna Maria", surname="Salvadé", date_of_birth="1962-03-20")
            assert p is not None
            assert p.patient_id == "1508277"
            assert p.surname == "Salvade'"

            # Verifica che la chiamata sia partita con Cognome="Salvade'"
            call_kwargs = mock_req.call_args[1]
            params = call_kwargs["params"]
            assert params["Cognome"] == "Salvade'"


def test_search_patient_compound_permutation_fallback(mock_priamo_settings):
    """Verifica che search_patient provi automaticamente le permutazioni di partizione

    (es. per doppi cognomi ispanici) quando la ricerca standard fallisce.
    """
    client = PriamoClient(settings=mock_priamo_settings)

    empty_resp = MagicMock()
    empty_resp.status_code = 200
    empty_resp.json.return_value = []

    success_resp = MagicMock()
    success_resp.status_code = 200
    success_resp.json.return_value = [
        {
            "datiMef": {
                "cognome": "JAIME ROJAS",
                "nome": "LEONOR ISIDORA",
                "codiceFiscale": "JMRLRS74P42Z611W",
                "comuneNascita": {"nome": "PERU'"}
            },
            "datiAa": {
                "progressivoPaz1": 1613811
            }
        }
    ]

    # Simula primo tentativo fallito (Jaime / Rojas Leonor), ma successo con la permutazione (Jaime Rojas / Leonor)
    def mock_request(method, url, params=None, **kwargs):
        if params.get("Cognome") == "Jaime Rojas" and params.get("Nome") == "Leonor":
            return success_resp
        return empty_resp

    with patch.object(client, "authenticate"):
        with patch.object(client, "_request", side_effect=mock_request):
            p = client.search_patient(name="Rojas Leonor", surname="Jaime", date_of_birth="1974-09-02")
            assert p is not None
            assert p.patient_id == "1613811"
            assert p.surname == "Jaime Rojas"
            assert p.name == "Leonor Isidora"
            assert p.codice_fiscale == "JMRLRS74P42Z611W"
            assert p.comune_nascita == "PERU'"


def test_get_event_documents_post_csrf(mock_priamo_settings):
    """Verifica che get_event_documents esegua una POST con IdEvento e header CSRF."""
    client = PriamoClient(settings=mock_priamo_settings)
    client._ces_token = "mock_csrf_token_123"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "documenti": [
            {
                "idDocumento": "20260000033482",
                "idEvento": "26784078",
                "stato": "FIRMATO",
                "tipoDocumento": "LAB",
                "versione": "1",
                "natura": "01",
                "repository": "S",
            }
        ]
    }

    with patch.object(client, "authenticate"):
        with patch.object(client, "_request", return_value=mock_resp) as mock_req:
            docs = client.get_event_documents("26784078")
            assert len(docs) == 1
            assert docs[0]["idDocumento"] == "20260000033482"

            method, url = mock_req.call_args[0]
            assert method == "POST"
            kwargs = mock_req.call_args[1]
            assert kwargs["data"] == {"IdEvento": "26784078"}
            assert kwargs["headers"]["RequestVerificationToken"] == "mock_csrf_token_123"
            assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded; charset=UTF-8"


