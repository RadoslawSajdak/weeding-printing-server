"""FastAPI application for the wedding print server.

Token-based authentication is enforced via middleware on all routes except
/printer (printer API), /health, and interactive API docs.
"""

import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.config import settings
from app.database import init_db
from app.dependencies import COOKIE_NAME, DEVICE_COOKIE
from app import lychee as _lychee
from app.routers import guests, printer

UNPROTECTED_PREFIXES = ("/printer", "/health", "/docs", "/openapi.json", "/redoc")
FRONTEND = Path(__file__).parent.parent / "frontend"
_COOKIE_OPTS = dict(httponly=True, samesite="lax", max_age=86400)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure upload/DB directories exist, initialise the database, and close the Lychee client on shutdown."""
    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(os.path.dirname(settings.database_url.split("///")[-1]), exist_ok=True)
    await init_db()
    yield
    await _lychee.close()


app = FastAPI(title="Weselny Serwer Druku", lifespan=lifespan)


@app.middleware("http")
async def token_auth(request: Request, call_next):
    """Enforce token authentication for all guest-facing routes.

    Guests authenticate by visiting any URL with ``?t=<access_token>``. The token
    is stored in a session cookie so subsequent requests don't need the query
    parameter. A separate per-device cookie is set on first access to identify
    the device across requests.

    Args:
        request: The incoming HTTP request.
        call_next: The next handler in the middleware chain.

    Returns:
        A 302 redirect stripping ``?t=`` from the URL if the session cookie is
        already valid, a 302 redirect that sets the session cookie when
        authenticating via ``?t=``, or a 403 response when no valid token is
        present.
    """
    if any(request.url.path.startswith(p) for p in UNPROTECTED_PREFIXES):
        return await call_next(request)

    has_session = request.cookies.get(COOKIE_NAME) == settings.access_token
    has_device = bool(request.cookies.get(DEVICE_COOKIE))
    secure = settings.secure_cookies

    if has_session:
        if request.query_params.get("t"):
            response = RedirectResponse(url=request.url.path or "/", status_code=302)
            if not has_device:
                response.set_cookie(DEVICE_COOKIE, str(uuid.uuid4()), secure=secure, **_COOKIE_OPTS)
            return response
        response = await call_next(request)
        if not has_device:
            response.set_cookie(DEVICE_COOKIE, str(uuid.uuid4()), secure=secure, **_COOKIE_OPTS)
        return response

    token = request.query_params.get("t")
    if token == settings.access_token:
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(COOKIE_NAME, settings.access_token, secure=secure, **_COOKIE_OPTS)
        if not has_device:
            response.set_cookie(DEVICE_COOKIE, str(uuid.uuid4()), secure=secure, **_COOKIE_OPTS)
        return response

    return Response(content="Access denied", status_code=403)


@app.get("/health")
async def health() -> dict:
    """Return service liveness status."""
    return {"status": "ok"}


@app.get("/")
async def root():
    """Serve the frontend single-page application."""
    return FileResponse(FRONTEND / "index.html")


app.include_router(guests.router)
app.include_router(printer.router)
