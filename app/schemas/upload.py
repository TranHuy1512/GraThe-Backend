from pydantic import BaseModel


class ImageUploadResponse(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    bucket: str
    object_key: str
    etag: str | None = None
    public_url: str | None = None
