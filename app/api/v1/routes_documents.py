"""REST endpoints for the persistent document library.

GET    /api/v1/documents           — paginated list (user-scoped)
GET    /api/v1/documents/{id}      — single record
PATCH  /api/v1/documents/{id}      — update fields (e.g. after threshold confirm)
DELETE /api/v1/documents/{id}      — hard delete (R2 files are kept)
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.auth import get_current_user
from app.schemas.document import DocumentListResponse, DocumentRecord, DocumentUpdate
from app.services.document_repository import document_repository

router = APIRouter()


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user),
):
    return await document_repository.list_all(user_id=user_id, limit=limit, offset=offset)


@router.get("/{doc_id}", response_model=DocumentRecord)
async def get_document(
    doc_id: str,
    user_id: str = Depends(get_current_user),
):
    doc = await document_repository.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    if doc.user_id and doc.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return doc


@router.patch("/{doc_id}", response_model=DocumentRecord)
async def update_document(
    doc_id: str,
    update: DocumentUpdate,
    user_id: str = Depends(get_current_user),
):
    doc = await document_repository.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    if doc.user_id and doc.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    updated = await document_repository.update(doc_id, update)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return updated


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: str,
    user_id: str = Depends(get_current_user),
):
    deleted = await document_repository.delete(doc_id, user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
