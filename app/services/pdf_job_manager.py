"""In-memory job manager for tracking PDF restoration job states.

Jobs are automatically evicted after ``JOB_TTL_HOURS`` to prevent
unbounded memory growth.  This is an intentional trade-off for a
single-instance deployment — jobs do not survive server restarts.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.schemas.pdf_restoration import JobStatus, PageResult, PdfJobResponse

JOB_TTL_HOURS = 12

_TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED}


@dataclass
class JobState:
    """Mutable internal state for a single PDF restoration job."""

    job_id: str
    status: JobStatus
    input_filename: str
    user_id: str = ""
    file_hash: str = ""
    document_id: str | None = None
    total_pages: int | None = None
    processed_pages: int = 0
    pages: list[PageResult] = field(default_factory=list)
    output_pdf_url: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def progress_percent(self) -> float:
        if self.total_pages is None or self.total_pages == 0:
            return 0.0
        return round((self.processed_pages / self.total_pages) * 100, 1)

    def to_response(self) -> PdfJobResponse:
        cached = sum(1 for p in self.pages if p.cached)
        return PdfJobResponse(
            job_id=self.job_id,
            document_id=self.document_id,
            status=self.status,
            input_filename=self.input_filename,
            total_pages=self.total_pages,
            processed_pages=self.processed_pages,
            cached_pages=cached,
            restored_pages=self.processed_pages - cached,
            pages=sorted(self.pages, key=lambda p: p.page),
            output_pdf_url=self.output_pdf_url,
            error=self.error,
            created_at=self.created_at,
            updated_at=self.updated_at,
            progress_percent=self.progress_percent,
        )

    def to_progress_event(self) -> str:
        """Serialise current state as an SSE ``data:`` line."""
        payload = {
            "status": self.status.value,
            "total_pages": self.total_pages,
            "processed_pages": self.processed_pages,
            "progress_percent": self.progress_percent,
            "output_pdf_url": self.output_pdf_url,
            "document_id": self.document_id,
            "error": self.error,
        }
        return f"data: {json.dumps(payload)}\n\n"


class PdfJobManager:
    """Thread-safe, in-memory store for PDF restoration job states."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def create_job(
        self,
        job_id: str,
        filename: str,
        document_id: str | None = None,
        user_id: str = "",
        file_hash: str = "",
    ) -> JobState:
        async with self._lock:
            self._cleanup_expired()
            job = JobState(
                job_id=job_id,
                status=JobStatus.PENDING,
                input_filename=filename,
                document_id=document_id,
                user_id=user_id,
                file_hash=file_hash,
            )
            self._jobs[job_id] = job
            self._events[job_id] = asyncio.Event()
            return job

    async def find_active_job(self, user_id: str, file_hash: str) -> JobState | None:
        """Return an in-progress job owned by *user_id* with the same *file_hash*, or None."""
        async with self._lock:
            for job in self._jobs.values():
                if (
                    job.user_id == user_id
                    and job.file_hash == file_hash
                    and job.status not in _TERMINAL
                ):
                    return job
        return None

    async def wait_for_completion(self, job_id: str) -> JobState | None:
        """Block until the job reaches a terminal state (completed/failed)."""

        event = self._events.get(job_id)
        if event is not None:
            await event.wait()
        async with self._lock:
            return self._jobs.get(job_id)

    async def get_job(self, job_id: str) -> JobState | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        total_pages: int | None = None,
    ) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = status
            if total_pages is not None:
                job.total_pages = total_pages
            job.updated_at = datetime.now(timezone.utc)

    async def add_page_result(self, job_id: str, page_result: PageResult) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.pages.append(page_result)
            job.processed_pages = len(job.pages)
            job.updated_at = datetime.now(timezone.utc)

    async def mark_completed(
        self, job_id: str, output_pdf_url: str | None = None,
    ) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = JobStatus.COMPLETED
            job.output_pdf_url = output_pdf_url
            job.updated_at = datetime.now(timezone.utc)
        event = self._events.get(job_id)
        if event is not None:
            event.set()

    async def mark_failed(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = JobStatus.FAILED
            job.error = error
            job.updated_at = datetime.now(timezone.utc)
        event = self._events.get(job_id)
        if event is not None:
            event.set()

    async def list_jobs(self) -> list[JobState]:
        async with self._lock:
            self._cleanup_expired()
            return list(self._jobs.values())

    async def subscribe(self, job_id: str):
        """Async generator that yields SSE ``data:`` lines until the job completes.

        Polls the in-memory state every 300 ms.  Each yield happens when
        ``processed_pages`` changes or when the job reaches a terminal state.
        Closes immediately if the job is not found.
        """
        last_processed = -1
        last_status = None

        while True:
            async with self._lock:
                job = self._jobs.get(job_id)

            if job is None:
                return

            changed = job.processed_pages != last_processed or job.status != last_status
            is_terminal = job.status in _TERMINAL

            if changed or is_terminal:
                last_processed = job.processed_pages
                last_status = job.status
                yield job.to_progress_event()
                if is_terminal:
                    return

            await asyncio.sleep(0.3)

    def _cleanup_expired(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=JOB_TTL_HOURS)
        expired = [
            jid for jid, job in self._jobs.items() if job.created_at < cutoff
        ]
        for jid in expired:
            del self._jobs[jid]
            self._events.pop(jid, None)


pdf_job_manager = PdfJobManager()
