"""REST endpoints for the persistent document library.

GET    /api/v1/documents           — paginated list
GET    /api/v1/documents/{id}      — single record
PATCH  /api/v1/documents/{id}      — update fields (e.g. after threshold confirm)
DELETE /api/v1/documents/{id}      — hard delete (R2 files are kept)
"""

from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.document import DocumentListResponse, DocumentRecord, DocumentUpdate
from app.services.document_repository import document_repository

router = APIRouter()


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return await document_repository.list_all(limit=limit, offset=offset)


@router.get("/{doc_id}", response_model=DocumentRecord)
async def get_document(doc_id: str):
    doc = await document_repository.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return doc


@router.patch("/{doc_id}", response_model=DocumentRecord)
async def update_document(doc_id: str, update: DocumentUpdate):
    doc = await document_repository.update(doc_id, update)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return doc


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: str):
    deleted = await document_repository.delete(doc_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
