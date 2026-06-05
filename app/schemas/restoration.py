"""Pydantic schemas for single-image restoration responses."""

from pydantic import BaseModel, Field


class RestoredFile(BaseModel):
    """Result for a single restored image."""

    page: int = Field(..., ge=1)
    filename: str
    url: str
    content_hash: str
    cached: bool = False


class RestorationResponse(BaseModel):
    """Response schema for a single-image restoration request."""

    request_id: str
    input_filename: str
    input_type: str
    total_pages: int
    outputs: list[RestoredFile]


class SoftRestorationResponse(BaseModel):
    """Response schema for non-binarized single-image restoration."""

    request_id: str
    input_filename: str
    input_type: str
    total_pages: int
    soft_output: RestoredFile
    recommended_threshold: float = Field(..., ge=0, le=1)
