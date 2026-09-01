"""Run Docling OCR on JPEG/JPG/PNG pages under Data/Processed."""

from src.ocr.ocr import ocr_all, ocr_document, ocr_image

__all__ = ["ocr_all", "ocr_document", "ocr_image"]
