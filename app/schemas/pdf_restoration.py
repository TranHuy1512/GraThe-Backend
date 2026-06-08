"""Pydantic schemas for async PDF restoration jobs."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Lifecycle states of a PDF restoration job."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    PROCESSING = "processing"
    MERGING = "merging"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"


class PageResult(BaseModel):
    """Result for a single restored page."""

    page: int = Field(..., ge=1, description="1-indexed page number")
    filename: str
    r2_object_key: str
    public_url: str | None = None
    content_hash: str = ""
    cached: bool = False


class PdfJobResponse(BaseModel):
    """Response schema for a PDF restoration job."""

    job_id: str
    document_id: str | None = None
    status: JobStatus
    input_filename: str
    total_pages: int | None = None
    processed_pages: int = 0
    cached_pages: int = 0
    restored_pages: int = 0
    pages: list[PageResult] = Field(default_factory=list)
    output_pdf_url: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    progress_percent: float = Field(0.0, ge=0.0, le=100.0)
