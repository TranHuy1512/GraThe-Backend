from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image

from app.core.config import settings


IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"


def is_supported_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def load_document_pages(path: Path) -> tuple[str, list[Image.Image]]:
    if is_pdf(path):
        return "pdf", _load_pdf_pages(path)
    if is_supported_image(path):
        return "image", [Image.open(path).convert("RGB")]
    raise ValueError("Unsupported file type. Please upload a PDF or image file.")


def _load_pdf_pages(path: Path) -> list[Image.Image]:
    pages: list[Image.Image] = []
    zoom = settings.PDF_RENDER_DPI / 72
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(path) as document:
        for page in document:
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")
            pages.append(image)

    if not pages:
        raise ValueError("PDF has no pages.")
    return pages
