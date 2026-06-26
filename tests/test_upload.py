"""POST /upload — happy paths, validation, auth enforcement."""
import pytest
from httpx import AsyncClient

from app.config import settings
from app.models import PrintJobStatus
from tests.conftest import FAKE_JPEG


async def test_upload_jpeg_returns_201_with_job_id(auth_client: AsyncClient):
    resp = await auth_client.post(
        "/upload",
        files={"file": ("photo.jpg", FAKE_JPEG, "image/jpeg")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == PrintJobStatus.PENDING
    assert body["queue_position"] == 1


async def test_upload_png_accepted(auth_client: AsyncClient):
    resp = await auth_client.post(
        "/upload",
        files={"file": ("shot.png", b"\x89PNG\r\n", "image/png")},
    )
    assert resp.status_code == 201


async def test_upload_heic_accepted(auth_client: AsyncClient):
    resp = await auth_client.post(
        "/upload",
        files={"file": ("iphone.heic", b"HEICDATA", "image/heic")},
    )
    assert resp.status_code == 201


async def test_second_upload_increments_queue_position(auth_client: AsyncClient):
    await auth_client.post("/upload", files={"file": ("a.jpg", FAKE_JPEG, "image/jpeg")})
    resp = await auth_client.post("/upload", files={"file": ("b.jpg", FAKE_JPEG, "image/jpeg")})

    assert resp.status_code == 201
    assert resp.json()["queue_position"] == 2


async def test_upload_invalid_extension_returns_422(auth_client: AsyncClient):
    resp = await auth_client.post(
        "/upload",
        files={"file": ("malware.exe", b"MZ\x00\x00", "application/octet-stream")},
    )
    assert resp.status_code == 422


async def test_upload_txt_extension_returns_422(auth_client: AsyncClient):
    resp = await auth_client.post(
        "/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 422


async def test_upload_file_too_large_returns_413(auth_client, monkeypatch):
    import app.routers.guests as guests_mod

    monkeypatch.setattr(guests_mod, "MAX_BYTES", 10)  # 10-byte ceiling for test

    resp = await auth_client.post(
        "/upload",
        files={"file": ("big.jpg", b"x" * 100, "image/jpeg")},
    )
    assert resp.status_code == 413


async def test_upload_without_auth_returns_403(client: AsyncClient):
    resp = await client.post(
        "/upload",
        files={"file": ("photo.jpg", FAKE_JPEG, "image/jpeg")},
    )
    assert resp.status_code == 403


async def test_upload_saves_file_to_upload_dir(auth_client, tmp_path):
    import os

    resp = await auth_client.post(
        "/upload",
        files={"file": ("check.jpg", FAKE_JPEG, "image/jpeg")},
    )
    assert resp.status_code == 201

    files_on_disk = list(tmp_path.iterdir())
    assert len(files_on_disk) == 1
    assert files_on_disk[0].suffix == ".jpg"


async def test_upload_returns_rate_limit_info(auth_client: AsyncClient):
    resp = await auth_client.post(
        "/upload",
        files={"file": ("photo.jpg", FAKE_JPEG, "image/jpeg")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["prints_this_hour"] == 1
    assert body["prints_allowed"] == settings.photos_per_hour


async def test_upload_rate_limit_exceeded_returns_429(auth_client: AsyncClient, monkeypatch):
    from app.config import settings as s
    monkeypatch.setattr(s, "photos_per_hour", 2)

    for i in range(2):
        r = await auth_client.post("/upload", files={"file": (f"p{i}.jpg", FAKE_JPEG, "image/jpeg")})
        assert r.status_code == 201

    resp = await auth_client.post("/upload", files={"file": ("over.jpg", FAKE_JPEG, "image/jpeg")})
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["prints_this_hour"] == 2
    assert detail["prints_allowed"] == 2


async def test_rate_limit_is_per_device(auth_client: AsyncClient, client: AsyncClient, monkeypatch):
    from app.config import settings as s
    monkeypatch.setattr(s, "photos_per_hour", 1)

    # Device 1 exhausts its limit
    r = await auth_client.post("/upload", files={"file": ("d1.jpg", FAKE_JPEG, "image/jpeg")})
    assert r.status_code == 201

    # Device 2 can still upload
    client.cookies.set("gallery_session", settings.access_token)
    client.cookies.set("device_id", "other-device-00000000-0000-0000-0000-000000000001")
    resp = await client.post("/upload", files={"file": ("d2.jpg", FAKE_JPEG, "image/jpeg")})
    assert resp.status_code == 201


async def test_upload_without_device_id_returns_401(client: AsyncClient):
    client.cookies.set("gallery_session", settings.access_token)
    resp = await client.post("/upload", files={"file": ("photo.jpg", FAKE_JPEG, "image/jpeg")})
    assert resp.status_code == 401
