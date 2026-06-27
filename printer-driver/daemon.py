#!/usr/bin/env python3
"""
Wedding Gallery — printer daemon.

Polls the backend for print jobs, downloads each file, prints it via CUPS
(Canon Selphy CP1500), then reports completion back to the server.

Configuration via environment variables or /etc/weeding-printer.env:
  API_BASE          Backend URL (required)
  PRINTER_API_KEY   Shared secret sent in X-Printer-Key header
  PRINTER_NAME      CUPS printer name (auto-detected when omitted)
  POLL_INTERVAL     Seconds between /printer/next polls (default: 5)
  JOB_TIMEOUT       Max seconds to wait for a print job (default: 300)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import printer as selphy

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("weeding-printer")

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE      = os.environ.get("API_BASE", "").rstrip("/")
API_KEY       = os.environ.get("PRINTER_API_KEY", "printer-secret-key")
PRINTER_NAME  = os.environ.get("PRINTER_NAME") or None  # None → auto-detect
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "5"))
JOB_TIMEOUT   = float(os.environ.get("JOB_TIMEOUT", str(selphy.JOB_TIMEOUT)))

if not API_BASE:
    sys.exit("ERROR: API_BASE environment variable is required")

# ── Graceful shutdown ──────────────────────────────────────────────────────────

_running = True


def _handle_signal(sig: int, _frame: Any) -> None:
    global _running
    log.info("Signal %d received — shutting down after current job", sig)
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)

# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    return {"X-Printer-Key": API_KEY, "User-Agent": "WeedingPrinterDaemon/1.0"}


def _api_next() -> dict | None:
    """GET /printer/next — returns job dict or None when queue is empty."""
    req = urllib.request.Request(
        API_BASE + "/printer/next",
        headers=_headers(),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 204:
                return None
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 204:
            return None
        raise


def _api_download(job_id: str, dest: Path) -> None:
    """GET /printer/file/{job_id} — save to *dest*."""
    req = urllib.request.Request(
        API_BASE + f"/printer/file/{job_id}",
        headers=_headers(),
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        dest.write_bytes(r.read())


def _api_complete(job_id: str, *, success: bool, error_message: str = "") -> None:
    """POST /printer/complete/{job_id}."""
    body: dict[str, Any] = {"success": success}
    if error_message:
        body["error_message"] = error_message
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        API_BASE + f"/printer/complete/{job_id}",
        data=data,
        headers={**_headers(), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        if r.status != 200:
            raise RuntimeError(f"Unexpected status {r.status} from /printer/complete")


# ── Placeholder for future status notifications ────────────────────────────────

def _api_report_status(event: str, detail: str = "") -> None:
    """POST /printer/status — report printer events (paper out, ink low, …).

    TODO: implement when backend endpoint is ready.
    """
    # body = {"event": event, "detail": detail}
    # _post("/printer/status", body)
    log.debug("Status report (not yet sent): event=%s detail=%s", event, detail)


# ── Job processing ─────────────────────────────────────────────────────────────

def _process_job(job: dict) -> None:
    job_id        = job["job_id"]
    original_name = job.get("original_name", job.get("filename", job_id))
    ext           = Path(job.get("filename", ".jpg")).suffix or ".jpg"

    log.info("Job %s — %s", job_id[:8], original_name)

    with tempfile.TemporaryDirectory(prefix="weeding_print_") as tmpdir:
        dest = Path(tmpdir) / (job_id + ext)

        # 1. Download
        log.info("Downloading %s …", original_name)
        try:
            _api_download(job_id, dest)
        except Exception as exc:
            log.error("Download failed: %s", exc)
            _safe_complete(job_id, success=False, error_message=f"Download failed: {exc}")
            return

        log.info("Downloaded %.1f KB → %s", dest.stat().st_size / 1024, dest)

        # 2. Print
        log.info("Submitting to CUPS …")
        try:
            cups_job_id = selphy.print_image(
                str(dest),
                printer_name=PRINTER_NAME,
            )
        except Exception as exc:
            log.error("Print submission failed: %s", exc)
            _safe_complete(job_id, success=False, error_message=f"Print failed: {exc}")
            return

        log.info("CUPS job %d — waiting (timeout=%ss) …", cups_job_id, JOB_TIMEOUT)

        # 3. Wait for completion
        try:
            ok = selphy.wait_for_job(cups_job_id, timeout=JOB_TIMEOUT)
        except Exception as exc:
            log.error("Error waiting for CUPS job: %s", exc)
            _safe_complete(job_id, success=False, error_message=f"Wait error: {exc}")
            return

        if ok:
            log.info("Printed successfully — %s", original_name)
            _safe_complete(job_id, success=True)
        else:
            log.error("Print job failed or timed out")
            # TODO: inspect CUPS job state for paper-out / ink errors and call
            # _api_report_status("paper_out") or _api_report_status("ink_low")
            _safe_complete(job_id, success=False, error_message="CUPS job failed or timed out")


def _safe_complete(job_id: str, *, success: bool, error_message: str = "") -> None:
    try:
        _api_complete(job_id, success=success, error_message=error_message)
        log.info("Reported job %s as %s", job_id[:8], "success" if success else "failure")
    except Exception as exc:
        log.error("Failed to report job %s completion: %s", job_id[:8], exc)


# ── Main loop ──────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("Weeding Gallery Printer Daemon starting")
    log.info("  api      = %s", API_BASE)
    log.info("  printer  = %s", PRINTER_NAME or "(auto-detect)")
    log.info("  poll     = %.0fs", POLL_INTERVAL)

    backoff = POLL_INTERVAL

    while _running:
        try:
            job = _api_next()
        except Exception as exc:
            log.warning("Poll error: %s — retrying in %.0fs", exc, backoff)
            backoff = min(backoff * 2, 60.0)
            time.sleep(backoff)
            continue

        backoff = POLL_INTERVAL

        if job is None:
            time.sleep(POLL_INTERVAL)
            continue

        _process_job(job)

    log.info("Daemon stopped")


if __name__ == "__main__":
    main()
