import logging
from pathlib import Path
from typing import List, Optional, Union

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
