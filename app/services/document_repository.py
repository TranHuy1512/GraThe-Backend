"""CRUD layer for the documents, pdf_jobs, and pdf_pages tables."""

import logging
from typing import Any

from app.core.database import get_db
from app.schemas.document import DocumentCreate, DocumentListResponse, DocumentRecord, DocumentUpdate

logger = logging.getLogger(__name__)


def _row_to_document(row) -> DocumentRecord:
    d = dict(row)
    return DocumentRecord(
        id=d["id"],
        user_id=d.get("user_id", ""),
        mode=d["mode"],
        file_name=d["file_name"],
        file_size=d["file_size"],
        page_count=d["page_count"],
        original_url=d["original_url"],
        restored_url=d["restored_url"],
        content_hash=d["content_hash"],
        width=d["width"],
        height=d["height"],
        soft_content_hash=d["soft_content_hash"],
        soft_image_url=d["soft_image_url"],
        output_pdf_url=d["output_pdf_url"],
        patch_size=d["patch_size"],
        threshold=d["threshold"],
        binarize_output=bool(d["binarize_output"]) if d["binarize_output"] is not None else None,
        overlap=bool(d["overlap"]) if d["overlap"] is not None else None,
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


class DocumentRepository:

    # ------------------------------------------------------------------ #
    #  Documents                                                           #
    # ------------------------------------------------------------------ #

    async def create(self, doc: DocumentCreate) -> DocumentRecord:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO documents (
                    id, user_id, mode, file_name, file_size, page_count,
                    original_url, restored_url, content_hash, width, height,
                    soft_content_hash, soft_image_url, output_pdf_url,
                    patch_size, threshold, binarize_output, overlap
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc.id, doc.user_id, doc.mode, doc.file_name, doc.file_size, doc.page_count,
                    doc.original_url, doc.restored_url, doc.content_hash, doc.width, doc.height,
                    doc.soft_content_hash, doc.soft_image_url, doc.output_pdf_url,
                    doc.patch_size, doc.threshold,
                    int(doc.binarize_output) if doc.binarize_output is not None else None,
                    int(doc.overlap) if doc.overlap is not None else None,
                ),
            )
            await db.commit()
        created = await self.get(doc.id)
        assert created is not None
        return created

    async def get(self, doc_id: str) -> DocumentRecord | None:
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_document(row) if row else None

    async def list_all(
        self, user_id: str, limit: int = 100, offset: int = 0,
    ) -> DocumentListResponse:
        async with get_db() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM documents WHERE user_id = ?", (user_id,)
            ) as cursor:
                total = (await cursor.fetchone())[0]
            async with db.execute(
                "SELECT * FROM documents WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        return DocumentListResponse(
            items=[_row_to_document(r) for r in rows],
            total=total,
        )

    async def update(self, doc_id: str, update: DocumentUpdate) -> DocumentRecord | None:
        fields: dict[str, Any] = {
            k: v for k, v in update.model_dump().items() if v is not None
        }
        if not fields:
            return await self.get(doc_id)

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        set_clause += ", updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        values = list(fields.values()) + [doc_id]

        async with get_db() as db:
            await db.execute(
                f"UPDATE documents SET {set_clause} WHERE id = ?", values
            )
            await db.commit()
        return await self.get(doc_id)

    async def delete(self, doc_id: str, user_id: str) -> bool:
        """Hard-delete a document; only the owning user may delete."""
        async with get_db() as db:
            cursor = await db.execute(
                "DELETE FROM documents WHERE id = ? AND user_id = ?",
                (doc_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def find_by_content_hash(
        self, content_hash: str, user_id: str,
    ) -> DocumentRecord | None:
        """Find the most recent document with this content hash for this user."""
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM documents "
                "WHERE content_hash = ? AND user_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (content_hash, user_id),
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_document(row) if row else None

    # ------------------------------------------------------------------ #
    #  PDF jobs                                                            #
    # ------------------------------------------------------------------ #

    async def create_pdf_job(
        self,
        job_id: str,
        document_id: str,
        input_filename: str,
        user_id: str = "",
    ) -> None:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO pdf_jobs (job_id, user_id, document_id, status, input_filename)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (job_id, user_id, document_id, input_filename),
            )
            await db.commit()

    async def update_pdf_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        scalar_fields = {k: v for k, v in fields.items()}
        set_parts = [f"{k} = ?" for k in scalar_fields]
        set_parts.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")
        values = list(scalar_fields.values()) + [job_id]
        async with get_db() as db:
            await db.execute(
                f"UPDATE pdf_jobs SET {', '.join(set_parts)} WHERE job_id = ?",
                values,
            )
            await db.commit()

    async def add_pdf_page(
        self,
        job_id: str,
        page: int,
        filename: str,
        r2_object_key: str,
        public_url: str | None,
        content_hash: str,
        cached: bool,
    ) -> None:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO pdf_pages
                    (job_id, page, filename, r2_object_key, public_url, content_hash, cached)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, page, filename, r2_object_key, public_url, content_hash, int(cached)),
            )
            await db.commit()

    async def get_pdf_pages(self, job_id: str) -> list[dict]:
        """Return all pages for a job ordered by page number."""
        async with get_db() as db:
            async with db.execute(
                "SELECT page, filename, r2_object_key, public_url, content_hash, cached "
                "FROM pdf_pages WHERE job_id = ? ORDER BY page ASC",
                (job_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_pdf_job_user(self, job_id: str) -> str | None:
        """Return the user_id that owns the given PDF job, or None if not found."""
        async with get_db() as db:
            async with db.execute(
                "SELECT user_id FROM pdf_jobs WHERE job_id = ?", (job_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return row["user_id"] if row else None


document_repository = DocumentRepository()
