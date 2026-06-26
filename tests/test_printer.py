"""Printer endpoints: claim, download, complete — auth and happy/error paths."""
import os
import pytest
from httpx import AsyncClient

from app.models import PrintJobStatus
from tests.conftest import FAKE_JPEG, PRINTER_HEADERS


async def test_next_empty_queue_returns_204(client: AsyncClient):
    resp = await client.get("/printer/next", headers=PRINTER_HEADERS)
    assert resp.status_code == 204


async def test_next_claims_oldest_pending_job(auth_client: AsyncClient, client: AsyncClient):
    r1 = await auth_client.post("/upload", files={"file": ("first.jpg", FAKE_JPEG, "image/jpeg")})
    r2 = await auth_client.post("/upload", files={"file": ("second.jpg", FAKE_JPEG, "image/jpeg")})

    resp = await client.get("/printer/next", headers=PRINTER_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    # FIFO: first uploaded job is claimed
    assert body["job_id"] == r1.json()["job_id"]
    assert body["original_name"] == "first.jpg"


async def test_next_job_becomes_processing_after_claim(
    auth_client: AsyncClient, client: AsyncClient, uploaded_job
):
    await client.get("/printer/next", headers=PRINTER_HEADERS)

    resp = await auth_client.get(f"/status/{uploaded_job['job_id']}")
    assert resp.json()["status"] == PrintJobStatus.PROCESSING


async def test_next_wrong_api_key_returns_401(client: AsyncClient):
    resp = await client.get("/printer/next", headers={"X-Printer-Key": "wrong"})
    assert resp.status_code == 401


async def test_next_no_api_key_returns_401(client: AsyncClient):
    resp = await client.get("/printer/next")
    assert resp.status_code == 401


async def test_download_file_returns_content(auth_client: AsyncClient, client: AsyncClient, uploaded_job):
    await client.get("/printer/next", headers=PRINTER_HEADERS)  # claim it first

    resp = await client.get(f"/printer/file/{uploaded_job['job_id']}", headers=PRINTER_HEADERS)
    assert resp.status_code == 200
    assert resp.content == FAKE_JPEG


async def test_download_nonexistent_job_returns_404(client: AsyncClient):
    resp = await client.get("/printer/file/does-not-exist", headers=PRINTER_HEADERS)
    assert resp.status_code == 404


async def test_download_missing_file_returns_404(
    auth_client: AsyncClient, client: AsyncClient, uploaded_job, tmp_path
):
    # Delete file from disk but leave the DB record
    next_resp = await client.get("/printer/next", headers=PRINTER_HEADERS)
    filename = next_resp.json()["filename"]
    os.unlink(tmp_path / filename)

    resp = await client.get(f"/printer/file/{uploaded_job['job_id']}", headers=PRINTER_HEADERS)
    assert resp.status_code == 404


async def test_complete_success_updates_status(
    auth_client: AsyncClient, client: AsyncClient, uploaded_job
):
    await client.get("/printer/next", headers=PRINTER_HEADERS)

    resp = await client.post(
        f"/printer/complete/{uploaded_job['job_id']}",
        json={"success": True},
        headers=PRINTER_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == PrintJobStatus.SUCCESS


async def test_complete_success_deletes_file(
    auth_client: AsyncClient, client: AsyncClient, uploaded_job, tmp_path
):
    next_resp = await client.get("/printer/next", headers=PRINTER_HEADERS)
    filename = next_resp.json()["filename"]

    await client.post(
        f"/printer/complete/{uploaded_job['job_id']}",
        json={"success": True},
        headers=PRINTER_HEADERS,
    )

    assert not os.path.exists(tmp_path / filename)


async def test_complete_failure_preserves_file(
    auth_client: AsyncClient, client: AsyncClient, uploaded_job, tmp_path
):
    next_resp = await client.get("/printer/next", headers=PRINTER_HEADERS)
    filename = next_resp.json()["filename"]

    resp = await client.post(
        f"/printer/complete/{uploaded_job['job_id']}",
        json={"success": False, "error_message": "Paper jam"},
        headers=PRINTER_HEADERS,
    )
    assert resp.json()["status"] == PrintJobStatus.FAILED
    assert os.path.exists(tmp_path / filename)


async def test_complete_nonexistent_job_returns_404(client: AsyncClient):
    resp = await client.post(
        "/printer/complete/does-not-exist",
        json={"success": True},
        headers=PRINTER_HEADERS,
    )
    assert resp.status_code == 404
