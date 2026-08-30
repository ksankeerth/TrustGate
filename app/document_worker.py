import asyncio
import logging

from app.core.contracts import DocumentJobStatus, VerificationState
from app.layers.document_review import MrzFormatError, parse_mrz
from app.state.document_job_store import DocumentJobStore
from app.state.store import IllegalStateTransition, VerificationStateStore

logger = logging.getLogger(__name__)

OCR_UNAVAILABLE_NOTE = (
    "no MRZ text supplied and no OCR backend installed, so nothing could be checked "
    "automatically; install tesseract and pytesseract to extract the MRZ from the image"
)


def extract_mrz_text(id_photo: bytes) -> str | None:
    """Best-effort OCR of the MRZ from a document image.

    Returns None when no OCR backend is installed, which is the normal case
    here: tesseract is a system package, not a pip dependency, so the service
    does not assume it. The deterministic value in this pipeline is the check
    digit validation, which runs on whatever MRZ text it is given -- from OCR
    when available, from the client otherwise.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None

    try:
        import io

        image = Image.open(io.BytesIO(id_photo)).convert("L")
        text = pytesseract.image_to_string(image, config="--psm 6")
    except Exception:
        logger.exception("OCR failed for document image")
        return None

    # Keep only lines that look like MRZ: long, uppercase, filler characters.
    candidates = [line.strip().replace(" ", "") for line in text.splitlines() if "<" in line]
    return "\n".join(candidates) if candidates else None


def run_automated_checks(job) -> tuple[DocumentJobStatus, list[str], dict]:
    """Run what can be decided deterministically, with no human involved.

    A failed MRZ check digit is one of the few unambiguous signals available:
    the document's own fields contradict their check digits, so it is rejected
    outright. Everything else -- including any judgement about whether the
    document is a good forgery -- is escalated to a human, because nothing here
    can settle it.
    """
    mrz_text = job.mrz_text or extract_mrz_text(job.id_photo)
    if not mrz_text:
        return DocumentJobStatus.AWAITING_REVIEW, [OCR_UNAVAILABLE_NOTE], {"mrz_checked": False}

    try:
        result = parse_mrz(mrz_text)
    except MrzFormatError as exc:
        return (
            DocumentJobStatus.REJECTED,
            [f"MRZ could not be parsed: {exc}"],
            {"mrz_checked": True, "mrz_parsed": False},
        )

    detail = {
        "mrz_checked": True,
        "mrz_parsed": True,
        "document_format": result.document_format,
        "fields": result.fields,
        "checks": {check.name: check.valid for check in result.checks},
    }

    if not result.all_checks_valid:
        return (
            DocumentJobStatus.REJECTED,
            [f"MRZ check digits failed: {', '.join(result.failed_checks)}"],
            detail,
        )

    return (
        DocumentJobStatus.AWAITING_REVIEW,
        [
            f"MRZ check digits valid ({result.document_format}); this confirms the transcription is "
            "internally consistent, NOT that the document is genuine -- awaiting human review"
        ],
        detail,
    )


class DocumentReviewWorker:
    """In-process background worker for the asynchronous document tier.

    Deliberately out of band: it runs after the sync tier has already answered
    and the login flow has ended, which is what lets the user hold provisional
    access while the document is still being checked.
    """

    def __init__(self, job_store: DocumentJobStore, state_store: VerificationStateStore) -> None:
        self._job_store = job_store
        self._state_store = state_store
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def submit(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def process_job(self, job_id: str) -> None:
        job = self._job_store.get(job_id)
        if job is None:
            logger.warning("document job %s no longer exists", job_id)
            return

        status, findings, detail = run_automated_checks(job)
        self._job_store.record_automated_result(job_id, status, findings, detail)

        if status is DocumentJobStatus.REJECTED:
            # An automated rejection is final and settles the user's state
            # without waiting for a reviewer.
            try:
                self._state_store.transition(job.user_ref, VerificationState.REJECTED)
            except IllegalStateTransition:
                # Already settled (e.g. the sync tier rejected them first);
                # the job outcome still stands.
                logger.info("user %s already in a terminal state; job %s stays REJECTED", job.user_ref, job_id)

        logger.info("document job %s -> %s (%s)", job_id, status.value, "; ".join(findings))

    async def _run(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self.process_job(job_id)
            except Exception:
                logger.exception("document job %s failed", job_id)
            finally:
                self._queue.task_done()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def wait_until_idle(self) -> None:
        """Block until the queue drains. For tests and shutdown, not request paths."""
        await self._queue.join()
