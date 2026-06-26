"""GET /my-queue — personal queue with rate-limit info."""
from httpx import AsyncClient

from app.config import settings
from app.models import PrintJobStatus
from tests.conftest import FAKE_JPEG, OTHER_DEVICE_ID


async def test_my_queue_empty_for_new_device(auth_client: AsyncClient):
    resp = await auth_client.get("/my-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jobs"] == []
    assert body["prints_this_hour"] == 0
    assert body["prints_allowed"] == settings.photos_per_hour


async def test_my_queue_shows_uploaded_job(auth_client: AsyncClient):
    await auth_client.post("/upload", files={"file": ("p.jpg", FAKE_JPEG, "image/jpeg")})

    resp = await auth_client.get("/my-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["jobs"]) == 1
    job = body["jobs"][0]
    assert job["status"] == PrintJobStatus.PENDING
    assert job["queue_position"] == 1
    assert body["prints_this_hour"] == 1


async def test_my_queue_shows_correct_positions(auth_client: AsyncClient):
    # auth_client is the shared client object, so we swap device_id between uploads
    # to simulate two distinct devices without needing a second client fixture.

    # "Other device" uploads first — gets position 1
    auth_client.cookies.set("device_id", OTHER_DEVICE_ID)
    await auth_client.post("/upload", files={"file": ("first.jpg", FAKE_JPEG, "image/jpeg")})

    # Our device uploads second — should land at position 2
    auth_client.cookies.set("device_id", "test-device-00000000-0000-0000-0000-000000000000")
    await auth_client.post("/upload", files={"file": ("mine.jpg", FAKE_JPEG, "image/jpeg")})

    resp = await auth_client.get("/my-queue")
    body = resp.json()
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["queue_position"] == 2


async def test_my_queue_isolates_by_device(auth_client: AsyncClient, client: AsyncClient):
    await auth_client.post("/upload", files={"file": ("p.jpg", FAKE_JPEG, "image/jpeg")})

    client.cookies.set("gallery_session", settings.access_token)
    client.cookies.set("device_id", OTHER_DEVICE_ID)
    resp = await client.get("/my-queue")
    assert resp.json()["jobs"] == []


async def test_my_queue_without_device_id_returns_401(client: AsyncClient):
    client.cookies.set("gallery_session", settings.access_token)
    resp = await client.get("/my-queue")
    assert resp.status_code == 401


async def test_my_queue_multiple_jobs_ordered_newest_first(auth_client: AsyncClient):
    await auth_client.post("/upload", files={"file": ("a.jpg", FAKE_JPEG, "image/jpeg")})
    await auth_client.post("/upload", files={"file": ("b.jpg", FAKE_JPEG, "image/jpeg")})

    resp = await auth_client.get("/my-queue")
    body = resp.json()
    assert len(body["jobs"]) == 2
    assert body["prints_this_hour"] == 2
    # newest first — b.jpg was uploaded last
    assert body["jobs"][0]["original_name"] == "b.jpg"
