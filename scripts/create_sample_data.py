"""Script to generate realistic synthetic Italian breast clinical reports in PDF format for testing and demonstration."""

from pathlib import Path


SAMPLE_REPORTS = [
    {
        "filename": "referto_01_istologico_SENO0891.pdf",
        "text": """OSPEDALE UNIVERSITARIO DI SENOLOGIA E ONCOLOGIA CLINICA
CENTRO DI SENOLOGIA - BREAST UNIT (RETE SENONETWORK)

REFERTO DI ESAME ANATOMO-PATOLOGICO (BIOPSIA MAMMARIA)
ID Paziente: SENO-2024-0891
Data Referto: 2024-04-12
Paziente: B.M., Anni 54
Procedura / Tipo Esame: Biopsia / Istologico (Core needle biopsy ecoguidata)

QUESITO CLINICO:
Paziente con nodulo palpabile al seno sinistro comparso da circa 2 mesi.

DESCRIZIONE MACROSCOPICA:
Pervenuti 4 frustoli agobioptici di tessuto mammario di colorito bianco-grigiastro, 
il maggiore di mm 18 di lunghezza, inclusi in toto.

DIAGNOSI ISTOPATOLOGICA:
Frustoli agobioptici di parenchima mammario sede di:
CARCINOMA DUTTALE INFILTRANTE (Non Special Type - NST).
Grado istologico (secondo Nottingham / Elston-Ellis): Grado 2 (G2 - moderatamente differenziato).
Sede: Mammella Sinistra, Quadrante Supero-Esterno (QSE).
Dimensione massima stimata del nodulo primario: 18 mm.

INDAGINI IMMUNOISTOCHIMICHE (PROFILO BIOLOGICO):
- Recettori Estrogeni (ER): Positivi nel 90% delle cellule neoplastiche (forte intensità).
- Recettori Progestinici (PgR): Positivi nel 65% delle cellule neoplastiche.
- HER2/neu (anticorpo 4B5): Negativo (Score 1+ - debole colorazione di membrana incompleta).
- Ki-67 (indice di proliferazione MIB-1): 22% delle cellule neoplastiche.

VALUTAZIONE LINFONODALE:
Linfoadenomegalia ascellare omolaterale reattiva documentata all'ecografia, priva di segni citologici 
di metastasi (Interessamento linfonodale: No).

SINTOMATOLOGIA RIFERITA:
Nodulo palpabile riferito dalla paziente. Assenza di secrezioni dal capezzolo o mastodinia.

CONCLUSIONI:
Carcinoma duttale infiltrante NST mammella sinistra cT1c cN0, sottotipo molecolare Luminal B like (HER2-).
Si consiglia discussione del caso nel GIC (Gruppo Interdisciplinare Cure) senologico per indicazione chirurgica conservativa.

Firmato digitalmente:
Dott. R. Valenti - Anatomia Patologica Senologica
"""
    },
    {
        "filename": "referto_02_mammografia_SENO1142.pdf",
        "text": """SERVIZIO DI RADIODIAGNOSTICA SENOLOGICA E SCREENING
CENTRO ACCREDITATO SENONETWORK

REFERTO RADIOLOGICO: MAMMOGRAFIA DIGITALE DIRETTA + ECOGRAFIA
ID Paziente: SENO-2024-1142
Data Referto: 2024-05-20
Paziente: C.F., Anni 49
Tipo Esame: Mammografia bilaterale con integrazione ecografica

INDICAZIONE CLINICA:
Controllo periodico biennale di screening mammografico. Paziente asintomatica.

MAMMOGRAFIA DIGITALE (2D + Tomosintesi 3D):
Struttura mammaria ad elevata densità parenchimale fibro-ghiandolare (tipo C ACR).
A destra, in sede retro-ghiandolare a livello del Quadrante Supero-Interno (QSI), si evidenzia una formazione
a contorni parzialmente sfumati e spiculati del diametro massimo di circa 12 mm.
Non microcalcificazioni pleomorfe raggruppate patologiche a sinistra.

COMPLETAMENTO ECOGRAFICO MIRATO:
A destra, in corrispondenza del reperto radiologico segnalato in QSI, l'ecografia documenta un'area solida ipoecogena,
con margini angolati e cono d'ombra acustico posteriore, di dimensioni 12 x 9 mm.
Non linfoadenopatie clinicamente o ecograficamente sospette al cavo ascellare omolaterale (Linfonodi negativi).

CLASSIFICAZIONE BI-RADS:
BI-RADS 4 (Lesione sospetta per eteroplasia).

QUADRO CLINICO:
Paziente asintomatica, reperto identificato in corso di screening di routine.

CONCLUSIONI E RACCOMANDAZIONI:
Opacità nodulare spiculata in QSI mammella destra (12 mm, BI-RADS 4).
Si pone indicazione per approfondimento microistologico mediante biopsia con ago tranciante (14 Gauge)
ecoguidata per tipizzazione della lesione.

Dott.ssa M. Grassi - Specialista in Radiodiagnostica Senologica
"""
    }
]


def create_pdf_from_text(text: str, output_path: Path):
    """Generates a PDF using PyMuPDF (fitz) or pypdf."""
    try:
        import pymupdf
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)  # A4

        # Insert title and text
        margin_x = 50
        margin_y = 50
        rect = pymupdf.Rect(margin_x, margin_y, 595 - margin_x, 842 - margin_y)
        page.insert_textbox(rect, text, fontsize=10, fontname="helv", color=(0, 0, 0))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
        doc.close()
        print(f"Creato PDF: {output_path}")
    except Exception as e:
        print(f"Errore creazione PDF {output_path}: {e}")


def main():
    base_dir = Path(__file__).resolve().parent.parent / "data" / "input_pdf"
    base_dir.mkdir(parents=True, exist_ok=True)

    for item in SAMPLE_REPORTS:
        pdf_path = base_dir / item["filename"]
        create_pdf_from_text(item["text"], pdf_path)


if __name__ == "__main__":
    main()
