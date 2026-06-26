"""Auth middleware: token-in-URL, session cookie, and bypass rules."""
import pytest
from httpx import AsyncClient

from app.config import settings
from tests.conftest import PRINTER_HEADERS


async def test_health_accessible_without_auth(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200


async def test_root_no_auth_returns_403(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 403


async def test_root_valid_token_redirects_to_clean_url(client: AsyncClient):
    resp = await client.get(f"/?t={settings.access_token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


async def test_root_valid_token_sets_session_cookie(client: AsyncClient):
    resp = await client.get(f"/?t={settings.access_token}", follow_redirects=False)
    cookie = resp.headers.get("set-cookie", "")
    assert "gallery_session" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()


async def test_root_valid_cookie_returns_200(client: AsyncClient):
    client.cookies.set("gallery_session", settings.access_token)
    resp = await client.get("/")
    assert resp.status_code == 200


async def test_token_in_url_with_valid_cookie_redirects_to_clean_url(client: AsyncClient):
    """Re-visiting the share link when already authenticated must strip the token."""
    client.cookies.set("gallery_session", settings.access_token)
    resp = await client.get(f"/?t={settings.access_token}", follow_redirects=False)
    assert resp.status_code == 302
    assert "t=" not in resp.headers["location"]


async def test_root_invalid_token_returns_403(client: AsyncClient):
    resp = await client.get("/?t=wrong-token")
    assert resp.status_code == 403


async def test_root_invalid_cookie_returns_403(client: AsyncClient):
    client.cookies.set("gallery_session", "wrong-value")
    resp = await client.get("/")
    assert resp.status_code == 403


async def test_printer_routes_bypass_guest_auth(client: AsyncClient):
    # Printer endpoint reachable without guest cookie (uses its own API key)
    resp = await client.get("/printer/next", headers=PRINTER_HEADERS)
    # 204 = no jobs, but auth passed
    assert resp.status_code == 204


async def test_printer_route_no_api_key_returns_401(client: AsyncClient):
    resp = await client.get("/printer/next")
    assert resp.status_code == 401
