from pydantic import BaseModel, Field


class RestoredFile(BaseModel):
    page: int = Field(..., ge=1)
    filename: str
    url: str


class RestorationResponse(BaseModel):
    request_id: str
    input_filename: str
    input_type: str
    total_pages: int
    outputs: list[RestoredFile]
