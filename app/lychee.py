"""Async Lychee API client — login + photo upload to a fixed album."""

import asyncio
import io
import logging
from pathlib import Path
from urllib.parse import unquote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()

_API        = "/api/v2/"
_CSRF_COOKIE = "XSRF-TOKEN"
_CSRF_HEADER = "X-XSRF-TOKEN"
_ACCEPT      = "application/json, text/javascript, */*; q=0.01"
_CHUNK_SIZE  = 4 * 1024 * 1024  # 4 MB


def _is_configured() -> bool:
    return bool(settings.lychee_url and settings.lychee_username and settings.lychee_password)


def _csrf_token(client: httpx.AsyncClient) -> str:
    """URL-decode the XSRF-TOKEN cookie and strip base64 padding as Laravel expects."""
    raw = client.cookies.get(_CSRF_COOKIE, "")
    return unquote(raw).replace("=", "") if raw else ""


def _csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    token = _csrf_token(client)
    return {_CSRF_HEADER: token} if token else {}


async def _build_client() -> httpx.AsyncClient:
    client = httpx.AsyncClient(
        base_url=settings.lychee_url,
        timeout=30.0,
        follow_redirects=True,
        headers={"Accept": _ACCEPT},
    )
    try:
        # GET / sets the XSRF-TOKEN and session cookies
        await client.get("/")

        # Authenticate
        resp = await client.post(
            f"{_API}Auth::login",
            json={"username": settings.lychee_username, "password": settings.lychee_password},
            headers=_csrf_headers(client),
        )
        resp.raise_for_status()
    except Exception:
        await client.aclose()
        raise

    logger.info("Lychee: authenticated as %s", settings.lychee_username)
    return client


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            _client = await _build_client()
        return _client


async def _invalidate_client() -> None:
    global _client
    async with _client_lock:
        if _client is not None:
            await _client.aclose()
            _client = None


async def upload_photo(file_path: str) -> None:
    """Upload a photo to the configured Lychee album. Errors are logged, never raised."""
    if not _is_configured():
        return

    path = Path(file_path)
    if not path.exists():
        logger.error("Lychee: file not found at %s", file_path)
        return

    size         = path.stat().st_size
    total_chunks = max(1, (size + _CHUNK_SIZE - 1) // _CHUNK_SIZE)

    for attempt in range(2):
        try:
            client   = await _get_client()
            srv_uuid: str = ""
            srv_ext:  str = ""
            failed   = False

            with open(path, "rb") as fh:
                for chunk_num in range(1, total_chunks + 1):
                    chunk = fh.read(_CHUNK_SIZE)

                    data: dict = {
                        "album_id":     settings.lychee_album_id,
                        "file_name":    path.name,
                        "chunk_number": chunk_num,
                        "total_chunks": total_chunks,
                        "uuid_name":    srv_uuid,
                        "extension":    srv_ext,
                    }

                    resp = await client.post(
                        f"{_API}Photo",
                        data=data,
                        files={"file": (path.name, io.BytesIO(chunk))},
                        headers=_csrf_headers(client),
                    )

                    if resp.status_code in (401, 419) and attempt == 0:
                        logger.info("Lychee: session invalid, re-authenticating")
                        await _invalidate_client()
                        failed = True
                        break

                    resp.raise_for_status()

                    if chunk_num == 1 and total_chunks > 1:
                        body     = resp.json()
                        srv_uuid = body.get("uuid_name", "")
                        srv_ext  = body.get("extension", "")
                        if not srv_uuid or not srv_ext:
                            raise ValueError(f"Server did not return uuid_name/extension: {body}")

            if not failed:
                logger.info("Lychee: uploaded %s to album %s", path.name, settings.lychee_album_id)
                return

        except Exception as exc:
            logger.error("Lychee upload failed (attempt %d): %s", attempt + 1, exc)
            if attempt == 0:
                await _invalidate_client()

    logger.error("Lychee: gave up uploading %s after 2 attempts", path.name)


async def close() -> None:
    """Release the shared HTTP client (call during app shutdown)."""
    await _invalidate_client()
