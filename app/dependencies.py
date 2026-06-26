"""FastAPI dependencies and shared constants for auth and device identification."""

from fastapi import HTTPException, Request, status
from .config import settings

COOKIE_NAME = "gallery_session"
DEVICE_COOKIE = "device_id"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}


def verify_printer(request: Request) -> None:
    """Validate the X-Printer-Key request header.

    Raises:
        HTTPException: 401 if the header is missing or does not match
            ``settings.printer_api_key``.
    """
    api_key = request.headers.get("X-Printer-Key")
    if api_key != settings.printer_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid printer API key")


def get_device_id(request: Request) -> str:
    """Extract the device_id cookie value.

    Returns:
        The device ID string from the request cookie.

    Raises:
        HTTPException: 401 if the device_id cookie is not present.
    """
    device_id = request.cookies.get(DEVICE_COOKIE)
    if not device_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing device ID")
    return device_id
