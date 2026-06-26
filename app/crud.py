from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from .models import PrintJob, PrintJobStatus


async def create_print_job(
    db: AsyncSession, filename: str, original_name: str, device_id: str | None = None
) -> tuple[PrintJob, int]:
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
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    return result.scalar_one_or_none()


async def get_queue_position(db: AsyncSession, job: PrintJob) -> int | None:
    if job.status != PrintJobStatus.PENDING:
        return None
    result = await db.execute(
        select(func.count(PrintJob.id))
        .where(PrintJob.status == PrintJobStatus.PENDING)
        .where(PrintJob.created_at < job.created_at)
    )
    return (result.scalar() or 0) + 1


async def count_user_jobs_this_hour(db: AsyncSession, device_id: str) -> int:
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    result = await db.execute(
        select(func.count(PrintJob.id))
        .where(PrintJob.device_id == device_id)
        .where(PrintJob.created_at >= one_hour_ago)
        .where(PrintJob.status != PrintJobStatus.FAILED)
    )
    return result.scalar() or 0


async def get_user_jobs(db: AsyncSession, device_id: str) -> list[PrintJob]:
    result = await db.execute(
        select(PrintJob)
        .where(PrintJob.device_id == device_id)
        .order_by(PrintJob.created_at.desc())
    )
    return list(result.scalars().all())


async def claim_next_job(db: AsyncSession) -> PrintJob | None:
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
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        return None
    job.status = PrintJobStatus.SUCCESS if success else PrintJobStatus.FAILED
    await db.commit()
    await db.refresh(job)
    return job
