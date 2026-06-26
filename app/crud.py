"""Database access layer for PrintJob records."""

from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from .models import PrintJob, PrintJobStatus


async def create_print_job(
    db: AsyncSession, filename: str, original_name: str, device_id: str | None = None
) -> tuple[PrintJob, int]:
    """Create a new PENDING print job and return it with its queue position.

    Args:
        db: Async database session.
        filename: Unique filename as stored on disk (UUID-prefixed).
        original_name: Original filename provided by the uploader.
        device_id: Device cookie value used to associate the job with a device.

    Returns:
        A tuple of (PrintJob, queue_position) where queue_position is 1-indexed.
    """
    job = PrintJob(filename=filename, original_name=original_name, device_id=device_id)
    db.add(job)
    await db.flush()

    position_result = await db.execute(
        select(func.count(PrintJob.id))
        .where(PrintJob.status == PrintJobStatus.PENDING)
        .where(PrintJob.created_at < job.created_at)
    )
    position = (position_result.scalar() or 0) + 1

    await db.commit()
    await db.refresh(job)
    return job, position


async def get_job(db: AsyncSession, job_id: str) -> PrintJob | None:
    """Fetch a single print job by its UUID.

    Args:
        db: Async database session.
        job_id: The UUID string of the job.

    Returns:
        The matching PrintJob, or None if not found.
    """
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    return result.scalar_one_or_none()


async def get_queue_position(db: AsyncSession, job: PrintJob) -> int | None:
    """Return the 1-indexed queue position of a PENDING job.

    Args:
        db: Async database session.
        job: The print job to query.

    Returns:
        The job's position in the pending queue, or None if the job is not PENDING.
    """
    if job.status != PrintJobStatus.PENDING:
        return None
    result = await db.execute(
        select(func.count(PrintJob.id))
        .where(PrintJob.status == PrintJobStatus.PENDING)
        .where(PrintJob.created_at < job.created_at)
    )
    return (result.scalar() or 0) + 1


async def count_user_jobs_this_hour(db: AsyncSession, device_id: str) -> int:
    """Count non-FAILED jobs submitted by a device in the past 60 minutes.

    Args:
        db: Async database session.
        device_id: Device cookie value.

    Returns:
        Number of jobs created by this device in the rolling 60-minute window.
    """
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    result = await db.execute(
        select(func.count(PrintJob.id))
        .where(PrintJob.device_id == device_id)
        .where(PrintJob.created_at >= one_hour_ago)
        .where(PrintJob.status != PrintJobStatus.FAILED)
    )
    return result.scalar() or 0


async def get_user_jobs(db: AsyncSession, device_id: str) -> list[PrintJob]:
    """Retrieve all jobs for a device, newest first.

    Args:
        db: Async database session.
        device_id: Device cookie value.

    Returns:
        List of PrintJob objects ordered by creation time descending.
    """
    result = await db.execute(
        select(PrintJob)
        .where(PrintJob.device_id == device_id)
        .order_by(PrintJob.created_at.desc())
    )
    return list(result.scalars().all())


async def claim_next_job(db: AsyncSession) -> PrintJob | None:
    """Atomically claim the oldest PENDING job and mark it PROCESSING.

    Args:
        db: Async database session.

    Returns:
        The claimed PrintJob, or None if the queue is empty.
    """
    result = await db.execute(
        select(PrintJob)
        .where(PrintJob.status == PrintJobStatus.PENDING)
        .order_by(PrintJob.created_at)
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if job:
        job.status = PrintJobStatus.PROCESSING
        await db.commit()
        await db.refresh(job)
    return job


async def complete_job(db: AsyncSession, job_id: str, success: bool) -> PrintJob | None:
    """Mark a job as SUCCESS or FAILED.

    Args:
        db: Async database session.
        job_id: UUID of the job to complete.
        success: True marks the job SUCCESS; False marks it FAILED.

    Returns:
        The updated PrintJob, or None if no job with that ID exists.
    """
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        return None
    job.status = PrintJobStatus.SUCCESS if success else PrintJobStatus.FAILED
    await db.commit()
    await db.refresh(job)
    return job
