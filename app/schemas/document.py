"""Pydantic schemas for the persistent document library."""

from pydantic import BaseModel


class DocumentCreate(BaseModel):
    id: str
    mode: str  # 'image' | 'pdf'
    file_name: str
    file_size: int | None = None
    page_count: int = 1
    original_url: str | None = None
    restored_url: str | None = None
    content_hash: str | None = None
    width: int | None = None
    height: int | None = None
    soft_content_hash: str | None = None
    soft_image_url: str | None = None
    output_pdf_url: str | None = None
    patch_size: int | None = None
    threshold: float | None = None
    binarize_output: bool | None = None
    overlap: bool | None = None


class DocumentRecord(DocumentCreate):
    created_at: str
    updated_at: str


class DocumentUpdate(BaseModel):
    page_count: int | None = None
    restored_url: str | None = None
    content_hash: str | None = None
    output_pdf_url: str | None = None
    threshold: float | None = None
    soft_content_hash: str | None = None
    soft_image_url: str | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentRecord]
    total: int
