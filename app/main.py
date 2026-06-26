import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.config import settings
from app.database import init_db
from app.dependencies import COOKIE_NAME, DEVICE_COOKIE
from app.routers import guests, printer

UNPROTECTED_PREFIXES = ("/printer", "/health", "/docs", "/openapi.json", "/redoc")
FRONTEND = Path(__file__).parent.parent / "frontend"
_COOKIE_OPTS = dict(httponly=True, samesite="lax", max_age=86400)


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(os.path.dirname(settings.database_url.split("///")[-1]), exist_ok=True)
    await init_db()
    yield


app = FastAPI(title="Weselny Serwer Druku", lifespan=lifespan)


@app.middleware("http")
async def token_auth(request: Request, call_next):
    if any(request.url.path.startswith(p) for p in UNPROTECTED_PREFIXES):
        return await call_next(request)

    has_session = request.cookies.get(COOKIE_NAME) == settings.access_token
    has_device = bool(request.cookies.get(DEVICE_COOKIE))
    secure = settings.secure_cookies

    if has_session:
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
    return {"status": "ok"}


@app.get("/")
async def root():
    return FileResponse(FRONTEND / "index.html")


app.include_router(guests.router)
app.include_router(printer.router)
