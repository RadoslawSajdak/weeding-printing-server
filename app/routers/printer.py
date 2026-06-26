import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.config import settings
from app.database import get_db
from app.dependencies import verify_printer
from app.schemas import PrinterCompleteRequest, PrinterNextResponse

router = APIRouter(prefix="/printer", tags=["printer"], dependencies=[Depends(verify_printer)])

DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("/next", response_model=PrinterNextResponse)
async def get_next_job(db: DbDep) -> PrinterNextResponse:
    job = await crud.claim_next_job(db)
    if not job:
        raise HTTPException(status_code=status.HTTP_204_NO_CONTENT)
    return PrinterNextResponse(job_id=job.id, filename=job.filename, original_name=job.original_name)


@router.get("/file/{job_id}")
async def download_file(job_id: str, db: DbDep) -> FileResponse:
    job = await crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    file_path = os.path.join(settings.upload_dir, job.filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found on disk")

    return FileResponse(path=file_path, filename=job.original_name)


@router.post("/complete/{job_id}", status_code=status.HTTP_200_OK)
async def complete_job(job_id: str, payload: PrinterCompleteRequest, db: DbDep) -> dict:
    job = await crud.complete_job(db, job_id, payload.success)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    # Clean up file after successful print
    if payload.success:
        file_path = os.path.join(settings.upload_dir, job.filename)
        if os.path.exists(file_path):
            os.unlink(file_path)

    return {"job_id": job.id, "status": job.status}
