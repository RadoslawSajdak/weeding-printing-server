"""CRUD unit tests — no HTTP layer, raw AsyncSession."""
import asyncio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.models import PrintJobStatus


async def test_create_job_first_position(db: AsyncSession):
    job, position = await crud.create_print_job(db, "file1.jpg", "original1.jpg")
    assert position == 1
    assert job.status == PrintJobStatus.PENDING


async def test_create_second_job_has_position_2(db: AsyncSession):
    await crud.create_print_job(db, "file1.jpg", "a.jpg")
    _, position = await crud.create_print_job(db, "file2.jpg", "b.jpg")
    assert position == 2


async def test_get_job_returns_correct_record(db: AsyncSession):
    job, _ = await crud.create_print_job(db, "file.jpg", "original.jpg")
    fetched = await crud.get_job(db, job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.filename == "file.jpg"


async def test_get_job_unknown_id_returns_none(db: AsyncSession):
    result = await crud.get_job(db, "nonexistent-id")
    assert result is None


async def test_get_queue_position_returns_none_for_processing(db: AsyncSession):
    job, _ = await crud.create_print_job(db, "file.jpg", "original.jpg")
    await crud.claim_next_job(db)
    job = await crud.get_job(db, job.id)

    position = await crud.get_queue_position(db, job)
    assert position is None


async def test_get_queue_position_updates_after_claim(db: AsyncSession):
    await crud.create_print_job(db, "first.jpg", "first.jpg")
    job2, _ = await crud.create_print_job(db, "second.jpg", "second.jpg")

    # Claim job 1 → job 2 should move to position 1
    await crud.claim_next_job(db)

    job2 = await crud.get_job(db, job2.id)
    position = await crud.get_queue_position(db, job2)
    assert position == 1


async def test_claim_next_job_returns_oldest_pending(db: AsyncSession):
    job1, _ = await crud.create_print_job(db, "first.jpg", "first.jpg")
    await crud.create_print_job(db, "second.jpg", "second.jpg")

    claimed = await crud.claim_next_job(db)
    assert claimed is not None
    assert claimed.id == job1.id


async def test_claim_next_job_marks_status_processing(db: AsyncSession):
    await crud.create_print_job(db, "file.jpg", "file.jpg")
    claimed = await crud.claim_next_job(db)

    assert claimed.status == PrintJobStatus.PROCESSING


async def test_claim_next_job_empty_queue_returns_none(db: AsyncSession):
    result = await crud.claim_next_job(db)
    assert result is None


async def test_claim_skips_already_processing_job(db: AsyncSession):
    job1, _ = await crud.create_print_job(db, "file1.jpg", "file1.jpg")
    job2, _ = await crud.create_print_job(db, "file2.jpg", "file2.jpg")

    await crud.claim_next_job(db)  # claims job1
    claimed = await crud.claim_next_job(db)  # should claim job2, not job1 again
    assert claimed is not None
    assert claimed.id == job2.id


async def test_complete_job_success(db: AsyncSession):
    job, _ = await crud.create_print_job(db, "file.jpg", "file.jpg")
    result = await crud.complete_job(db, job.id, success=True)

    assert result is not None
    assert result.status == PrintJobStatus.SUCCESS


async def test_complete_job_failure(db: AsyncSession):
    job, _ = await crud.create_print_job(db, "file.jpg", "file.jpg")
    result = await crud.complete_job(db, job.id, success=False)

    assert result is not None
    assert result.status == PrintJobStatus.FAILED


async def test_complete_nonexistent_job_returns_none(db: AsyncSession):
    result = await crud.complete_job(db, "does-not-exist", success=True)
    assert result is None
