"""OCR over stored identity documents.

Tesseract (pytesseract) is the default engine; PDFs are rasterised with
pdf2image/poppler first. The ``run_ocr`` entry point is engine-agnostic so an
EasyOCR backend could be slotted in behind the same signature later.
"""

import io

import pytesseract
from PIL import Image


def _ocr_image(data: bytes) -> str:
    image = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(image)


def _ocr_pdf(data: bytes) -> str:
    from pdf2image import convert_from_bytes

    pages = convert_from_bytes(data)
    return "\n".join(pytesseract.image_to_string(page) for page in pages)


def run_ocr(data: bytes, content_type: str) -> str:
    """Return extracted text for an image or PDF document."""
    if content_type == "pdf":
        return _ocr_pdf(data)
    return _ocr_image(data)
