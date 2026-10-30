import csv
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from src.ai_extractor import (
    AIExtractor,
    LlamaCppExtractor,
    OllamaExtractor,
    get_ai_extractor
)
from src.config import Settings
from src.models import ExtractionResult, REDCapField
from src.pdf_extractor import PDFExtractor
from src.redcap_service import REDCapService

logger = logging.getLogger(__name__)


class ExtractionPipeline:
    """End-to-end pipeline to process PDF medical reports and generate REDCap records."""

    def __init__(
        self,
        settings: Settings,
        redcap_service: Optional[REDCapService] = None,
        ai_extractor: Optional[Union[AIExtractor, LlamaCppExtractor, OllamaExtractor]] = None,
        metadata_file: Optional[Union[str, Path]] = None,
        target_form: Optional[str] = None
    ):
        self.settings = settings
        self.redcap_service = redcap_service or REDCapService(
            api_url=settings.redcap_api_url,
            api_token=settings.redcap_api_token
        )
        self.ai_extractor = ai_extractor or get_ai_extractor(settings)
        self.metadata_file = metadata_file
        self.target_form = target_form or settings.default_form_name
        self.fields: List[REDCapField] = []

    def initialize_metadata(self) -> List[REDCapField]:
        """Loads and prepares REDCap metadata from local file or REDCap API."""
        if self.metadata_file:
            logger.info(f"Caricamento metadata dal file locale: {self.metadata_file}")
            raw_fields = self.redcap_service.load_metadata_from_file(self.metadata_file)
        else:
            logger.info("Scaricamento metadata dall'API REDCap...")
            raw_fields = self.redcap_service.fetch_metadata_from_api(
                forms=[self.target_form] if self.target_form else None
            )

        self.fields = self.redcap_service.filter_fields_by_form(raw_fields, self.target_form)
        logger.info(f"Caricati {len(self.fields)} campi estraibili per lo strumento: {self.target_form or 'Tutti'}")
        return self.fields

    def process_pdf(
        self,
        pdf_path: Union[str, Path],
        record_id: Optional[str] = None
    ) -> ExtractionResult:
        """Processes a single PDF medical report and returns structured extraction result."""
        path = Path(pdf_path)
        if not self.fields:
            self.initialize_metadata()

        # Derive default record ID from file stem if not provided
        inferred_record_id = record_id or path.stem

        # Extract text from PDF
        try:
            report_text = PDFExtractor.extract_text(path)
        except Exception as e:
            logger.error(f"Errore lettura PDF {path}: {e}")
            return ExtractionResult(
                pdf_path=str(path),
                record_id=inferred_record_id,
                status="error",
                error_message=f"Lettura PDF fallita: {e}"
            )

        # Run AI extraction
        result = self.ai_extractor.extract_from_text(
            report_text=report_text,
            fields=self.fields,
            pdf_path=str(path),
            record_id=inferred_record_id,
            form_name=self.target_form
        )
        return result

    def process_batch(
        self,
        pdf_source: Union[str, Path, List[Union[str, Path]]],
        progress_callback: Optional[Callable[[int, int, Path], None]] = None
    ) -> List[ExtractionResult]:
        """Processes a folder of PDFs or list of PDF paths."""
        if not self.fields:
            self.initialize_metadata()

        if isinstance(pdf_source, (list, tuple)):
            pdf_files = [Path(p) for p in pdf_source]
        else:
            pdf_files = PDFExtractor.find_pdf_files(pdf_source)

        total = len(pdf_files)
        results: List[ExtractionResult] = []

        for idx, pdf in enumerate(pdf_files, 1):
            if progress_callback:
                progress_callback(idx, total, pdf)
            res = self.process_pdf(pdf)
            results.append(res)

        return results

    @staticmethod
    def export_to_json(results: List[ExtractionResult], output_file: Union[str, Path]) -> Path:
        """Saves extraction results and REDCap records to a JSON file."""
        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        data = [res.model_dump() for res in results]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return out_path

    @staticmethod
    def export_to_redcap_csv(results: List[ExtractionResult], output_file: Union[str, Path]) -> Path:
        """Exports normalized records to a CSV file matching REDCap Data Import Tool specifications."""
        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Gather successful records
        records = [r.redcap_record for r in results if r.status == "success" and r.redcap_record]
        if not records:
            # Write empty file with header if no records
            with open(out_path, "w", encoding="utf-8-sig") as f:
                f.write("record_id\n")
            return out_path

        # Determine all distinct column headers preserving order
        all_columns = []
        for rec in records:
            for k in rec.keys():
                if k not in all_columns:
                    all_columns.append(k)

        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_columns)
            writer.writeheader()
            for rec in records:
                writer.writerow(rec)

        return out_path
