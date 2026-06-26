from datetime import datetime
from pydantic import BaseModel
from .models import PrintJobStatus


class UploadResponse(BaseModel):
    job_id: str
    queue_position: int
    status: PrintJobStatus
    prints_this_hour: int
    prints_allowed: int


class JobStatusResponse(BaseModel):
    job_id: str
    status: PrintJobStatus
    queue_position: int | None


class JobSummary(BaseModel):
    job_id: str
    original_name: str
    status: PrintJobStatus
    queue_position: int | None
    created_at: datetime


class MyQueueResponse(BaseModel):
    jobs: list[JobSummary]
    prints_this_hour: int
    prints_allowed: int


class PrinterNextResponse(BaseModel):
    job_id: str
    filename: str
    original_name: str


class PrinterCompleteRequest(BaseModel):
    success: bool
    error_message: str | None = None
