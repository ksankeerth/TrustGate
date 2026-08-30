import uuid
from dataclasses import dataclass


@dataclass
class DocumentJob:
    job_id: str
    user_ref: str
    id_photo: bytes
    status: str = "PENDING"
    reviewer_note: str | None = None


class DocumentJobStore:
    """In-memory queue of enqueued document-review jobs, keyed by job_id."""

    def __init__(self) -> None:
        self._jobs: dict[str, DocumentJob] = {}

    def enqueue(self, user_ref: str, id_photo: bytes) -> str:
        job = DocumentJob(job_id=str(uuid.uuid4()), user_ref=user_ref, id_photo=id_photo)
        self._jobs[job.job_id] = job
        return job.job_id

    def get(self, job_id: str) -> DocumentJob | None:
        return self._jobs.get(job_id)

    def settle(self, job_id: str, status: str, reviewer_note: str | None) -> DocumentJob | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.status = status
        job.reviewer_note = reviewer_note
        return job
