"""Pydantic schemas for request/response serialisation."""

from datetime import datetime
from pydantic import BaseModel
from .models import PrintJobStatus


class UploadResponse(BaseModel):
    """Response returned after a successful photo upload to the print queue."""

    job_id: str
    queue_position: int
    status: PrintJobStatus
    prints_this_hour: int
    prints_allowed: int


class JobStatusResponse(BaseModel):
    """Current status and queue position for a single print job."""

    job_id: str
    status: PrintJobStatus
    queue_position: int | None


class JobSummary(BaseModel):
    """Compact summary of a print job used in queue listings."""

    job_id: str
    original_name: str
    status: PrintJobStatus
    queue_position: int | None
    created_at: datetime


class MyQueueResponse(BaseModel):
    """A device's complete job history together with per-hour usage stats."""

    jobs: list[JobSummary]
    prints_this_hour: int
    prints_allowed: int


class PrinterNextResponse(BaseModel):
    """Describes the next job a printer should process."""

    job_id: str
    filename: str
    original_name: str


class PrinterCompleteRequest(BaseModel):
    """Request body sent by a printer when it finishes processing a job."""

    success: bool
    error_message: str | None = None
