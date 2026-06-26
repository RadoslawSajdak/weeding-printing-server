import os

# Override settings BEFORE any app module is imported so the engine and
# upload_dir are configured for the test environment from the start.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:////tmp/test_weeding_gallery.db"
os.environ["UPLOAD_DIR"] = "/tmp/test_uploads_gallery"
os.environ["SECURE_COOKIES"] = "false"
os.environ["PRINTER_API_KEY"] = "printer-secret-key"

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import Base, AsyncSessionLocal, engine, get_db
from app.main import app

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64  # Minimal JPEG-like header bytes
TEST_DEVICE_ID = "test-device-00000000-0000-0000-0000-000000000000"
OTHER_DEVICE_ID = "other-device-00000000-0000-0000-0000-000000000001"


async def override_get_db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def reset_db():
    """Drop and recreate all tables before each test for isolation."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client(reset_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(client):
    """Client with a valid guest session cookie and device_id pre-set."""
    client.cookies.set("gallery_session", settings.access_token)
    client.cookies.set("device_id", TEST_DEVICE_ID)
    return client


@pytest_asyncio.fixture
async def db(reset_db) -> AsyncSession:
    """Raw async DB session for CRUD unit tests."""
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def uploaded_job(auth_client):
    """Upload one photo and return the response JSON."""
    resp = await auth_client.post(
        "/upload",
        files={"file": ("wedding.jpg", FAKE_JPEG, "image/jpeg")},
    )
    assert resp.status_code == 201
    return resp.json()


PRINTER_HEADERS = {"X-Printer-Key": "printer-secret-key"}
