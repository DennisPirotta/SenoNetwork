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
    def extract_document_date(pdf_path: Union[str, Path], text: Optional[str] = None) -> str:
        """Extracts the clinical document date (YYYY-MM-DD) from text or filename.

        Prioritizes explicit clinical report date markers (e.g. 'Data Ref.', 'Data Acc.', 'Data visita'),
        then general date formats in the text, and falls back to the year/sequence encoded in the filename.
        """
        path = Path(pdf_path)
        doc_text = text if text is not None else (PDFExtractor.extract_text(path) if path.is_file() else "")

        # 1. Search for explicit date labels in Italian medical reports (highest priority)
        explicit_patterns = [
            r'(?:data\s*ref(?:erto)?|data\s*acc(?:ettazione)?|data\s*esame|data\s*prelievo|eseguito\s*il|data\s*visita|data\s*:)\s*[:\s]*(\d{1,2}[./\-]\d{1,2}[./\-]((?:19|20)\d{2}))',
            r'firmato\s*digitalmente[^\n]*?il\s*(\d{1,2}[./\-]\d{1,2}[./\-]((?:19|20)\d{2}))',
            r'(\d{1,2}[./\-]\d{1,2}[./\-]((?:19|20)\d{2}))\s*alle\s*ore',
        ]
        for pat in explicit_patterns:
            m = re.search(pat, doc_text, re.IGNORECASE)
            if m:
                raw = m.group(1).replace('/', '-').replace('.', '-')
                parts = raw.split('-')
                if len(parts) == 3:
                    d, mth, y = parts
                    if len(y) == 4 and 1 <= int(mth) <= 12 and 1 <= int(d) <= 31:
                        return f"{y}-{int(mth):02d}-{int(d):02d}"

        # 2. Secondary contextual date markers (e.g. "referto del", "del", "li")
        secondary_patterns = [
            r'(?:referto\s*del|in\s*data|del|l[iì])\s*[:\s]*(\d{1,2}[./\-]\d{1,2}[./\-]((?:19|20)\d{2}))',
        ]
        for pat in secondary_patterns:
            m = re.search(pat, doc_text, re.IGNORECASE)
            if m:
                raw = m.group(1).replace('/', '-').replace('.', '-')
                parts = raw.split('-')
                if len(parts) == 3:
                    d, mth, y = parts
                    if len(y) == 4 and 1 <= int(mth) <= 12 and 1 <= int(d) <= 31:
                        return f"{y}-{int(mth):02d}-{int(d):02d}"

        # 3. Any standard date in text (1900-2099)
        for m in re.finditer(r'\b(\d{1,2})[./\-](\d{1,2})[./\-]((?:19|20)\d{2})\b', doc_text):
            d, mth, y = m.group(1), m.group(2), m.group(3)
            if 1 <= int(mth) <= 12 and 1 <= int(d) <= 31:
                return f"{y}-{int(mth):02d}-{int(d):02d}"

        # 3. Filename numeric prefix: e.g. 20260000... or 20250000...
        m_fn = re.match(r'^(?:evento_)?(20\d{2})', path.stem)
        if m_fn:
            return f"{m_fn.group(1)}-01-01"

        return "1970-01-01"

    @staticmethod
    def sort_pdfs_by_date(pdf_paths: List[Union[str, Path]]) -> List[Path]:
        """Sorts a list of PDF file paths in chronological order (from oldest to newest).

        Ensures that when merging records, newer clinical reports update older ones.
        """
        dated: List[Tuple[str, str, Path]] = []
        for p in pdf_paths:
            path = Path(p)
            try:
                t = PDFExtractor.extract_text(path)
            except Exception:
                t = ""
            dt = PDFExtractor.extract_document_date(path, t)
            dated.append((dt, path.stem, path))

        dated.sort(key=lambda x: (x[0], x[1]))
        return [item[2] for item in dated]
