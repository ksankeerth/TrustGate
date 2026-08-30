import uuid
from dataclasses import dataclass, field

from app.core.contracts import DocumentJobStatus


@dataclass
class DocumentJob:
    job_id: str
    user_ref: str
    id_photo: bytes
    mrz_text: str | None = None
    status: DocumentJobStatus = DocumentJobStatus.PENDING
    findings: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)
    reviewer_note: str | None = None


class DocumentJobStore:
    """In-memory queue of enqueued document-review jobs, keyed by job_id."""

    def __init__(self) -> None:
        self._jobs: dict[str, DocumentJob] = {}

    def enqueue(self, user_ref: str, id_photo: bytes, mrz_text: str | None = None) -> str:
        job = DocumentJob(job_id=str(uuid.uuid4()), user_ref=user_ref, id_photo=id_photo, mrz_text=mrz_text)
        self._jobs[job.job_id] = job
        return job.job_id

    def get(self, job_id: str) -> DocumentJob | None:
        return self._jobs.get(job_id)

    def record_automated_result(
        self,
        job_id: str,
        status: DocumentJobStatus,
        findings: list[str],
        detail: dict,
    ) -> DocumentJob | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.status = status
        job.findings = findings
        job.detail = detail
        return job

    def settle(self, job_id: str, status: DocumentJobStatus, reviewer_note: str | None) -> DocumentJob | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.status = status
        job.reviewer_note = reviewer_note
        return job
