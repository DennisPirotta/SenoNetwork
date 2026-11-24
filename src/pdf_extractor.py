import logging
from pathlib import Path
import re
from typing import List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


class PDFExtractor:
    """Extracts text from medical PDF reports using PyMuPDF (fitz) or fallback mechanisms."""

    @staticmethod
    def extract_text(pdf_path: Union[str, Path]) -> str:
        """Extracts and cleans all text from a PDF file.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            Extracted text content as a single clean string.
        """
        path = Path(pdf_path)
        if not path.is_file():
            raise FileNotFoundError(f"PDF file does not exist: {path}")

        text_content: List[str] = []

        try:
            import pymupdf
            with pymupdf.open(path) as doc:
                for page_idx, page in enumerate(doc):
                    page_text = page.get_text("text")
                    if page_text.strip():
                        text_content.append(page_text.strip())
        except ImportError:
            # Fallback to pypdf if PyMuPDF is not installed
            try:
                import pypdf
                reader = pypdf.PdfReader(str(path))
                for page_idx, page in enumerate(reader.pages):
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        text_content.append(page_text.strip())
            except ImportError:
                raise RuntimeError(
                    "Neither pymupdf nor pypdf is installed. Please install pymupdf via 'uv add pymupdf'."
                )

        full_text = "\n\n--- PAGINA SUCCESSIVA ---\n\n".join(text_content) if len(text_content) > 1 else ("".join(text_content))

        # Basic cleanup: remove excessive blank lines
        lines = [line.strip() for line in full_text.splitlines()]
        cleaned = "\n".join(line for line in lines if line)

        return cleaned

    @staticmethod
    def find_pdf_files(target_path: Union[str, Path]) -> List[Path]:
        """Finds all PDF files in a given directory or returns a list with single file.

        Args:
            target_path: Path to a file or directory.

        Returns:
            Sorted list of Path objects for all .pdf files found.
        """
        path = Path(target_path)
        if path.is_file():
            if path.suffix.lower() == ".pdf":
                return [path]
            raise ValueError(f"File {path} is not a PDF file.")

        if path.is_dir():
            pdfs = sorted(list(path.glob("*.pdf")) + list(path.glob("*.PDF")))
            return pdfs

        raise FileNotFoundError(f"Target path does not exist: {target_path}")

    @staticmethod
    def _validate_iso_date(year: str, month: str, day: str) -> Optional[str]:
        """Validates a calendar date and formats it as YYYY-MM-DD."""
        try:
            y, m, d = int(year), int(month), int(day)
            if not (1900 <= y <= 2099):
                return None
            from datetime import date as _date
            return _date(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def extract_document_date(pdf_path: Union[str, Path], text: Optional[str] = None) -> str:
        """Extracts the clinical document date (YYYY-MM-DD) from text or filename.

        Prioritizes explicit clinical report date markers (e.g. 'Data Ref.', 'Data Acc.', 'Data visita'),
        supports both DD/MM/YYYY and YYYY-MM-DD formats, validates calendar dates, and ignores DOB lines.
        """
        path = Path(pdf_path)
        doc_text = text if text is not None else (PDFExtractor.extract_text(path) if path.is_file() else "")

        # 1. Search for explicit date labels in Italian medical reports (highest priority)
        explicit_dmy_patterns = [
            r'(?:data\s*ref(?:erto)?|data\s*acc(?:ettazione)?|data\s*esame|data\s*prelievo|eseguito\s*il|data\s*visita|data\s*:)\s*[:\s]*(\d{1,2})[./\-](\d{1,2})[./\-]((?:19|20)\d{2})\b',
            r'firmato\s*digitalmente[^\n]*?il\s*(\d{1,2})[./\-](\d{1,2})[./\-]((?:19|20)\d{2})\b',
            r'\b(\d{1,2})[./\-](\d{1,2})[./\-]((?:19|20)\d{2})\s*alle\s*ore',
        ]
        explicit_ymd_patterns = [
            r'(?:data\s*ref(?:erto)?|data\s*acc(?:ettazione)?|data\s*esame|data\s*prelievo|eseguito\s*il|data\s*visita|data\s*:)\s*[:\s]*((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})\b',
            r'firmato\s*digitalmente[^\n]*?il\s*((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})\b',
        ]
        for pat in explicit_dmy_patterns:
            m = re.search(pat, doc_text, re.IGNORECASE)
            if m:
                iso = PDFExtractor._validate_iso_date(m.group(3), m.group(2), m.group(1))
                if iso:
                    return iso
        for pat in explicit_ymd_patterns:
            m = re.search(pat, doc_text, re.IGNORECASE)
            if m:
                iso = PDFExtractor._validate_iso_date(m.group(1), m.group(2), m.group(3))
                if iso:
                    return iso

        # 2. Secondary contextual date markers (e.g. "referto del", "in data", "del", "li")
        secondary_dmy = [
            r'(?:referto\s*del|in\s*data|del|l[iì])\s*[:\s]*(\d{1,2})[./\-](\d{1,2})[./\-]((?:19|20)\d{2})\b',
        ]
        secondary_ymd = [
            r'(?:referto\s*del|in\s*data|del|l[iì])\s*[:\s]*((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})\b',
        ]
        for pat in secondary_dmy:
            m = re.search(pat, doc_text, re.IGNORECASE)
            if m:
                iso = PDFExtractor._validate_iso_date(m.group(3), m.group(2), m.group(1))
                if iso:
                    return iso
        for pat in secondary_ymd:
            m = re.search(pat, doc_text, re.IGNORECASE)
            if m:
                iso = PDFExtractor._validate_iso_date(m.group(1), m.group(2), m.group(3))
                if iso:
                    return iso

        # 3. Any standard date in text (1900-2099), ignoring explicit DOB lines
        for line in doc_text.splitlines():
            lower_line = line.lower()
            if any(marker in lower_line for marker in ("data di nascita", "nato il", "nata il", "dob")):
                continue
            for m in re.finditer(r'\b(\d{1,2})[./\-](\d{1,2})[./\-]((?:19|20)\d{2})\b', line):
                iso = PDFExtractor._validate_iso_date(m.group(3), m.group(2), m.group(1))
                if iso:
                    return iso
            for m in re.finditer(r'\b((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})\b', line):
                iso = PDFExtractor._validate_iso_date(m.group(1), m.group(2), m.group(3))
                if iso:
                    return iso

        # 4. Filename numeric prefix: e.g. 20260000... or 20250000...
        m_fn = re.match(r'^(?:evento_)?(20\d{2})', path.stem)
        if m_fn:
            return f"{m_fn.group(1)}-01-01"

        return "1970-01-01"

    @staticmethod
    def sort_pdfs_with_text(pdf_paths: List[Union[str, Path]]) -> List[Tuple[Path, str, str]]:
        """Extracts text and document date once per PDF and returns tuples `(path, text, doc_date)` sorted chronologically."""
        dated: List[Tuple[str, str, Path, str]] = []
        for p in pdf_paths:
            path = Path(p)
            try:
                t = PDFExtractor.extract_text(path)
            except Exception:
                t = ""
            dt = PDFExtractor.extract_document_date(path, t)
            dated.append((dt, path.stem, path, t))

        dated.sort(key=lambda x: (x[0], x[1]))
        return [(item[2], item[3], item[0]) for item in dated]

    @staticmethod
    def sort_pdfs_by_date(pdf_paths: List[Union[str, Path]]) -> List[Path]:
        """Sorts a list of PDF file paths in chronological order (from oldest to newest).

        Ensures that when merging records, newer clinical reports update older ones.
        """
        return [item[0] for item in PDFExtractor.sort_pdfs_with_text(pdf_paths)]
