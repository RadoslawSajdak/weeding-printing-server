"""GET /status/{job_id} — queue position calculations and edge cases."""
import pytest
from httpx import AsyncClient

from app.models import PrintJobStatus
from tests.conftest import FAKE_JPEG, PRINTER_HEADERS


async def test_status_pending_job_returns_position_1(auth_client: AsyncClient, uploaded_job):
    resp = await auth_client.get(f"/status/{uploaded_job['job_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == PrintJobStatus.PENDING
    assert body["queue_position"] == 1


async def test_status_nonexistent_job_returns_404(auth_client: AsyncClient):
    resp = await auth_client.get("/status/does-not-exist")
    assert resp.status_code == 404


async def test_status_processing_job_has_no_queue_position(
    auth_client: AsyncClient, client: AsyncClient, uploaded_job
):
    # Printer claims the job → status becomes PROCESSING
    await client.get("/printer/next", headers=PRINTER_HEADERS)

    resp = await auth_client.get(f"/status/{uploaded_job['job_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == PrintJobStatus.PROCESSING
    assert body["queue_position"] is None


async def test_status_queue_position_updates_after_claim(auth_client: AsyncClient, client: AsyncClient):
    r1 = await auth_client.post("/upload", files={"file": ("a.jpg", FAKE_JPEG, "image/jpeg")})
    r2 = await auth_client.post("/upload", files={"file": ("b.jpg", FAKE_JPEG, "image/jpeg")})
    job2_id = r2.json()["job_id"]

    assert r2.json()["queue_position"] == 2

    # Printer claims job 1
    await client.get("/printer/next", headers=PRINTER_HEADERS)

    # Job 2 should now be at position 1
    resp = await auth_client.get(f"/status/{job2_id}")
    assert resp.json()["queue_position"] == 1


async def test_status_success_job_has_no_queue_position(auth_client: AsyncClient, client: AsyncClient, uploaded_job):
    await client.get("/printer/next", headers=PRINTER_HEADERS)
    await client.post(
        f"/printer/complete/{uploaded_job['job_id']}",
        json={"success": True},
        headers=PRINTER_HEADERS,
    )

    resp = await auth_client.get(f"/status/{uploaded_job['job_id']}")
    assert resp.json()["status"] == PrintJobStatus.SUCCESS
    assert resp.json()["queue_position"] is None


async def test_status_requires_auth(client: AsyncClient):
    # auth_client mutates the shared client cookie jar, so test auth in isolation
    resp = await client.get("/status/any-id")
    assert resp.status_code == 403
