"""Guest-facing API routes: photo upload to the print queue and to the gallery."""

import os
import uuid
from pathlib import Path
from typing import Annotated

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, lychee
from app.database import get_db
from app.config import settings
from app.dependencies import ALLOWED_EXTENSIONS, get_device_id
from app.schemas import JobStatusResponse, JobSummary, MyQueueResponse, UploadResponse

router = APIRouter(tags=["guests"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
DeviceId = Annotated[str, Depends(get_device_id)]

MAX_BYTES = settings.max_file_size_mb * 1024 * 1024


async def _lychee_upload_and_cleanup(file_path: str) -> None:
    """Upload a file to Lychee then delete it from local disk.

    Used as a background task so gallery-only uploads don't block the response.

    Args:
        file_path: Absolute path of the file to upload and remove.
    """
    await lychee.upload_photo(file_path)
    try:
        os.unlink(file_path)
    except OSError:
        pass


@router.post("/upload-gallery", status_code=status.HTTP_200_OK)
async def upload_to_gallery(file: UploadFile, background_tasks: BackgroundTasks) -> dict:
    """Upload a photo directly to the Lychee gallery without adding it to the print queue.

    Validates file type and size, saves to disk, then enqueues a background task
    that mirrors the photo to Lychee and removes the local copy.

    Args:
        file: The uploaded image file.
        background_tasks: FastAPI background task manager.

    Returns:
        ``{"status": "ok"}`` on success.

    Raises:
        HTTPException: 503 if Lychee integration is not configured.
        HTTPException: 422 if the file extension is not in ``ALLOWED_EXTENSIONS``.
        HTTPException: 413 if the file exceeds ``settings.max_file_size_mb``.
    """
    if not (settings.lychee_url and settings.lychee_username and settings.lychee_password):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Galeria nie jest skonfigurowana")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    unique_filename = f"{uuid.uuid4()}{ext}"
    dest_path = os.path.join(settings.upload_dir, unique_filename)

    bytes_written = 0
    async with aiofiles.open(dest_path, "wb") as out:
        while chunk := await file.read(256 * 1024):
            bytes_written += len(chunk)
            if bytes_written > MAX_BYTES:
                await file.close()
                os.unlink(dest_path)
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"File exceeds {settings.max_file_size_mb}MB limit",
                )
            await out.write(chunk)

    background_tasks.add_task(_lychee_upload_and_cleanup, dest_path)
    return {"status": "ok"}


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_photo(file: UploadFile, db: DbDep, device_id: DeviceId, background_tasks: BackgroundTasks) -> UploadResponse:
    """Upload a photo for printing and add it to the print queue.

    Validates file type, enforces the per-device hourly print limit, saves the
    file to disk, creates a PrintJob record, and asynchronously mirrors the photo
    to Lychee.

    Args:
        file: The uploaded image file.
        db: Async database session (injected).
        device_id: Device cookie value (injected).
        background_tasks: FastAPI background task manager.

    Returns:
        UploadResponse with job ID, queue position, and hourly usage stats.

    Raises:
        HTTPException: 422 if the file extension is not in ``ALLOWED_EXTENSIONS``.
        HTTPException: 429 if the device has reached its hourly print limit.
        HTTPException: 413 if the file exceeds ``settings.max_file_size_mb``.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    count = await crud.count_user_jobs_this_hour(db, device_id)
    if count >= settings.photos_per_hour:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Osiągnięto limit {settings.photos_per_hour} pocztówek na godzinę",
                "prints_this_hour": count,
                "prints_allowed": settings.photos_per_hour,
            },
        )

    unique_filename = f"{uuid.uuid4()}{ext}"
    dest_path = os.path.join(settings.upload_dir, unique_filename)

    bytes_written = 0
    async with aiofiles.open(dest_path, "wb") as out:
        while chunk := await file.read(256 * 1024):
            bytes_written += len(chunk)
            if bytes_written > MAX_BYTES:
                await file.close()
                os.unlink(dest_path)
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"File exceeds {settings.max_file_size_mb}MB limit",
                )
            await out.write(chunk)

    job, position = await crud.create_print_job(db, unique_filename, file.filename or unique_filename, device_id)
    background_tasks.add_task(lychee.upload_photo, dest_path)
    return UploadResponse(
        job_id=job.id,
        queue_position=position,
        status=job.status,
        prints_this_hour=count + 1,
        prints_allowed=settings.photos_per_hour,
    )


@router.get("/my-queue", response_model=MyQueueResponse)
async def get_my_queue(db: DbDep, device_id: DeviceId) -> MyQueueResponse:
    """Return the caller's full job history with current queue positions.

    Args:
        db: Async database session (injected).
        device_id: Device cookie value (injected).

    Returns:
        MyQueueResponse with all jobs and hourly usage stats.
    """
    jobs = await crud.get_user_jobs(db, device_id)
    summaries = []
    for job in jobs:
        pos = await crud.get_queue_position(db, job)
        summaries.append(JobSummary(
            job_id=job.id,
            original_name=job.original_name,
            status=job.status,
            queue_position=pos,
            created_at=job.created_at,
        ))
    count = await crud.count_user_jobs_this_hour(db, device_id)
    return MyQueueResponse(
        jobs=summaries,
        prints_this_hour=count,
        prints_allowed=settings.photos_per_hour,
    )


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_status(job_id: str, db: DbDep) -> JobStatusResponse:
    """Return the current status and queue position for a single job.

    Args:
        job_id: UUID of the print job.
        db: Async database session (injected).

    Returns:
        JobStatusResponse with status and optional queue position.

    Raises:
        HTTPException: 404 if no job with that ID exists.
    """
    job = await crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    position = await crud.get_queue_position(db, job)
    return JobStatusResponse(job_id=job.id, status=job.status, queue_position=position)
