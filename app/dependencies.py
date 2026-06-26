from fastapi import HTTPException, Request, status
from .config import settings

COOKIE_NAME = "gallery_session"
DEVICE_COOKIE = "device_id"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}


def verify_printer(request: Request) -> None:
    api_key = request.headers.get("X-Printer-Key")
    if api_key != settings.printer_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid printer API key")


def get_device_id(request: Request) -> str:
    device_id = request.cookies.get(DEVICE_COOKIE)
    if not device_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing device ID")
    return device_id
