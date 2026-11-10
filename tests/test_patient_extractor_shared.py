"""Test per l'estrazione e deduplicazione dell'anagrafica pazienti

dal file condiviso multidisciplinare (positivi_2026.xls).
"""

from datetime import date
from pathlib import Path
import pytest

from scripts.extract_patients_from_shared_file import (
    Paziente,
    clean_title,
    estrai_pazienti_univoci,
    split_nominativo
)
from src.dotnet_client import DotNetApiClient


def test_clean_title():
    assert clean_title("mario rossi") == "Mario Rossi"
    assert clean_title("d'elia") == "D'Elia"
    assert clean_title("D'ELIA") == "D'Elia"
    assert clean_title("spano'") == "Spano'"
    assert clean_title("SPANO'") == "Spano'"
    assert clean_title("dell'anna") == "Dell'Anna"
    assert clean_title("salvadé") == "Salvadé"
    assert clean_title("") == ""
    assert clean_title(None) == ""


def test_split_nominativo_standard():
    assert split_nominativo("Strano carmen") == ("Strano", "Carmen")
    assert split_nominativo("Dilda Francesca") == ("Dilda", "Francesca")
    assert split_nominativo("Favarin Sara") == ("Favarin", "Sara")


def test_split_nominativo_single_word():
    assert split_nominativo("Ndiaye") == ("Ndiaye", None)
    assert split_nominativo("Volpi") == ("Volpi", None)
    assert split_nominativo("") == ("", None)


def test_split_nominativo_prefixes():
    # Prefissi all'inizio con nome
    assert split_nominativo("Di Leo Maria Grazia") == ("Di Leo", "Maria Grazia")
    assert split_nominativo("De Martinis Annunziata") == ("De Martinis", "Annunziata")
    assert split_nominativo("La Delfa Rita") == ("La Delfa", "Rita")
    assert split_nominativo("De Giani Sonia") == ("De Giani", "Sonia")

    # Prefisso solo cognome (2 parti)
    assert split_nominativo("Di Grigoli") == ("Di Grigoli", None)


def test_split_nominativo_intermediate_particles():
    # Preposizioni intermedie nel cognome
    assert split_nominativo("Herrera della rosa Rosaura") == ("Herrera Della Rosa", "Rosaura")
    assert split_nominativo("Mateo De Los Santos Arelis") == ("Mateo De Los Santos", "Arelis")


def test_split_nominativo_compound_surnames():
    # Doppi cognomi
    assert split_nominativo("Cantali Carnesano Francesca") == ("Cantali Carnesano", "Francesca")
    assert split_nominativo("Jaime Rojas Leonor") == ("Jaime Rojas", "Leonor")


def test_split_nominativo_multiple_first_names():
    assert split_nominativo("Ciceri Luigia Maria") == ("Ciceri", "Luigia Maria")
    assert split_nominativo("Balducchi Maria Clelia") == ("Balducchi", "Maria Clelia")
    assert split_nominativo("Masciocchi Angela Rosa") == ("Masciocchi", "Angela Rosa")
    assert split_nominativo("Spano' Rosa Maria") == ("Spano'", "Rosa Maria")


def test_paziente_model_attributes_and_slug():
    p = Paziente(
        cognome="Rossi",
        nome="Mario",
        data_nascita=date(1980, 5, 12),
        righe_excel=[3, 10]
    )
    assert p.patient_id == "mario-rossi-1980-05-12"
    assert p.nominativo == "Rossi Mario"
    assert p.is_complete is True
    assert p.righe_excel == [3, 10]

    redcap_dict = p.to_redcap_dict()
    assert redcap_dict["record_id"] == "mario-rossi-1980-05-12"
    assert redcap_dict["name"] == "Mario"
    assert redcap_dict["surname"] == "Rossi"
    assert redcap_dict["date_of_birth"] == "1980-05-12"


def test_paziente_model_incomplete():
    p = Paziente(cognome="Ndiaye")
    assert p.patient_id == "ndiaye"
    assert p.nome is None
    assert p.data_nascita is None
    assert p.is_complete is False
    assert p.nominativo == "Ndiaye"


def test_estrai_pazienti_univoci_real_file():
    excel_path = Path("data/positivi_2026.xls")
    if not excel_path.exists():
        pytest.skip("data/positivi_2026.xls non presente")

    pazienti = estrai_pazienti_univoci(excel_path)
    # Nel foglio originale ci sono 215 pazienti univoci
    assert len(pazienti) == 215

    # Verifica deduplicazione di Balzarotti Rosella
    balzarotti = [p for p in pazienti if "Balzarotti" in p.cognome]
    assert len(balzarotti) == 1
    assert balzarotti[0].nome == "Rosella"
    assert balzarotti[0].data_nascita == date(1966, 8, 2)
    assert len(balzarotti[0].righe_excel) == 2
    assert balzarotti[0].righe_excel == [63, 98]

    # Verifica Ndiaye e Volpi presenti e completi con nome e data nascita
    ndiaye = [p for p in pazienti if p.cognome == "Ndiaye"]
    assert len(ndiaye) == 1
    assert ndiaye[0].nome == "Khady"
    assert ndiaye[0].data_nascita == date(1987, 3, 14)

    volpi = [p for p in pazienti if p.cognome == "Volpi"]
    assert len(volpi) == 1
    assert volpi[0].nome == "Gabriella"
    assert volpi[0].data_nascita == date(1978, 5, 24)

    # Filtro only_complete
    completi = estrai_pazienti_univoci(excel_path, only_complete=True)
    assert len(completi) == 215
    assert all(p.is_complete for p in completi)


def test_dotnet_client_loads_positivi_2026():
    excel_path = Path("data/positivi_2026.xls")
    if not excel_path.exists():
        pytest.skip("data/positivi_2026.xls non presente")

    pts = DotNetApiClient.load_patients_from_excel(excel_path)
    assert len(pts) == 215
    assert pts[0]["surname"] == "Acquati"
    assert pts[0]["name"] == "Lidia"
    assert pts[0]["date_of_birth"] == date(1959, 8, 1)
