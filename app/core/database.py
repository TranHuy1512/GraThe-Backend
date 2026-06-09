"""SQLite database connection manager and schema initialisation.

Uses aiosqlite for async access.  The DB file location is configured via
``settings.DATABASE_PATH`` (default: ``storage/app.db`` relative to the
backend package root).
"""

from contextlib import asynccontextmanager

import aiosqlite

from app.core.config import settings

_CREATE_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS documents (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL DEFAULT '',
    mode              TEXT NOT NULL CHECK (mode IN ('image', 'pdf')),
    file_name         TEXT NOT NULL,
    file_size         INTEGER,
    page_count        INTEGER NOT NULL DEFAULT 1,
    original_url      TEXT,
    restored_url      TEXT,
    content_hash      TEXT,
    width             INTEGER,
    height            INTEGER,
    soft_content_hash TEXT,
    soft_image_url    TEXT,
    output_pdf_url    TEXT,
    patch_size        INTEGER,
    threshold         REAL,
    binarize_output   INTEGER,
    overlap           INTEGER,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""

_CREATE_PDF_JOBS = """
CREATE TABLE IF NOT EXISTS pdf_jobs (
    job_id          TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT '',
    document_id     TEXT REFERENCES documents(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending',
    input_filename  TEXT NOT NULL,
    total_pages     INTEGER,
    processed_pages INTEGER NOT NULL DEFAULT 0,
    output_pdf_url  TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""

_CREATE_PDF_PAGES = """
CREATE TABLE IF NOT EXISTS pdf_pages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT NOT NULL REFERENCES pdf_jobs(job_id) ON DELETE CASCADE,
    page          INTEGER NOT NULL,
    filename      TEXT NOT NULL,
    r2_object_key TEXT NOT NULL,
    public_url    TEXT,
    content_hash  TEXT NOT NULL DEFAULT '',
    cached        INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_documents_user_id    ON documents(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(user_id, content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_pdf_jobs_user_id     ON pdf_jobs(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_pdf_pages_job        ON pdf_pages(job_id, page)",
]

# Columns added after the initial schema — applied via ALTER TABLE so that
# existing databases are migrated without data loss.
_MIGRATIONS = [
    ("documents", "user_id", "TEXT NOT NULL DEFAULT ''"),
    ("pdf_jobs",  "user_id", "TEXT NOT NULL DEFAULT ''"),
]


async def _apply_migrations(db: aiosqlite.Connection) -> None:
    """Add new columns to existing tables if they don't already exist."""
    async with db.execute("PRAGMA table_info(documents)") as cur:
        doc_cols = {row[1] async for row in cur}
    async with db.execute("PRAGMA table_info(pdf_jobs)") as cur:
        job_cols = {row[1] async for row in cur}

    col_map = {"documents": doc_cols, "pdf_jobs": job_cols}

    for table, column, definition in _MIGRATIONS:
        if column not in col_map.get(table, set()):
            await db.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )


async def init_db() -> None:
    """Create tables, run column migrations, and build indexes."""
    db_path = settings.DATABASE_PATH
    assert db_path is not None
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(_CREATE_DOCUMENTS)
        await db.execute(_CREATE_PDF_JOBS)
        await db.execute(_CREATE_PDF_PAGES)
        await _apply_migrations(db)
        for idx_sql in _CREATE_INDEXES:
            await db.execute(idx_sql)
        await db.commit()


@asynccontextmanager
async def get_db():
    """Async context manager yielding an ``aiosqlite.Connection``."""
    db_path = settings.DATABASE_PATH
    assert db_path is not None
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = aiosqlite.Row
        yield db
