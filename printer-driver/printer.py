#!/usr/bin/env python3
"""
Canon Selphy CP1500 — printer interface via CUPS + gutenprint.

Architecture:
  Python CLI/API → pycups → CUPS → usb backend → CP1500

Prerequisites:
  sudo apt install cups cups-filters printer-driver-gutenprint python3-dev libcups2-dev
  pip install pycups
  # Add printer in CUPS: URI usb://Canon/SELPHY%20CP1500, driver gutenprint CP1500
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import cups

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("selphy")

# ── Constants ─────────────────────────────────────────────────────────────────

_DISCOVERY_KEYWORDS = ("selphy", "cp1500", "cp-1500")

# PWG standard media names
MEDIA_4X6 = "oe_postcard_4x6in"
MEDIA_2X6 = "oe_2x6-photo_2x6in"

DEFAULT_OPTIONS: dict[str, str] = {
    "media":             MEDIA_4X6,
    "print-quality":     "5",      # 5 = high (standard IPP)
    "print-color-mode":  "color",
    "StpBorderless":     "True",   # gutenprint borderless (no margins)
    "StpiShrinkOutput":  "Crop", # Crop image to fill full printable area
}

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf"}

_CUPS_RETRY_DELAY  = 2.0
_JOB_POLL_INTERVAL = 2.0
JOB_TIMEOUT        = 300

_PRINTER_STATES: dict[int, str] = {3: "idle", 4: "processing", 5: "stopped"}
_JOB_STATES: dict[int, str] = {
    3: "pending",
    4: "held",
    5: "processing",
    6: "stopped",
    7: "canceled",
    8: "aborted",
    9: "completed",
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cups_connect(retries: int = 3) -> cups.Connection:
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(1, retries + 1):
        try:
            return cups.Connection()
        except RuntimeError as exc:
            last_exc = exc
            wait = _CUPS_RETRY_DELAY * attempt
            log.warning("CUPS connect attempt %d/%d failed: %s — retry in %.0fs",
                        attempt, retries, exc, wait)
            if attempt < retries:
                time.sleep(wait)
    raise RuntimeError(f"Cannot connect to CUPS after {retries} attempts: {last_exc}") from last_exc


def _find_printer(conn: cups.Connection, name: Optional[str] = None) -> str:
    printers: dict = conn.getPrinters()

    if name:
        if name in printers:
            return name
        raise RuntimeError(
            f"Printer '{name}' not found in CUPS. "
            f"Available: {list(printers)}"
        )

    for pname, attrs in printers.items():
        uri = attrs.get("device-uri", "").lower()
        label = pname.lower()
        if any(kw in label or kw in uri for kw in _DISCOVERY_KEYWORDS):
            log.info("Auto-discovered printer: %s (%s)", pname, attrs.get("device-uri", ""))
            return pname

    raise RuntimeError(
        "No Selphy printer found in CUPS.\n"
        "  Add printer at http://localhost:631\n"
        f"  Currently registered: {list(printers) or '(none)'}"
    )


# ── Public API ────────────────────────────────────────────────────────────────

def get_status(printer_name: Optional[str] = None) -> dict:
    """
    Return a status dict for the Selphy printer.

    Keys: name, state, reasons, message, ink_levels
    """
    conn = _cups_connect()
    name = _find_printer(conn, printer_name)
    attrs = conn.getPrinterAttributes(name)

    state_code: int = attrs.get("printer-state", 0)
    reasons         = attrs.get("printer-state-reasons", ["none"])
    message: str    = attrs.get("printer-state-message", "")
    levels          = attrs.get("marker-levels", [])
    marker_names    = attrs.get("marker-names", [])

    if isinstance(reasons, str):
        reasons = [reasons]

    return {
        "name":       name,
        "state":      _PRINTER_STATES.get(state_code, f"unknown({state_code})"),
        "reasons":    reasons,
        "message":    message,
        "ink_levels": dict(zip(marker_names, levels)) if levels else {},
    }


def print_image(
    image_path: str,
    printer_name: Optional[str] = None,
    copies: int = 1,
    options: Optional[dict[str, str]] = None,
) -> int:
    """
    Submit *image_path* to the Selphy via CUPS.

    Returns CUPS job ID. Use wait_for_job() to block until completion.

    Raises FileNotFoundError, ValueError, RuntimeError.
    """
    path = Path(image_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported format '{path.suffix}'. "
            f"Supported: {sorted(SUPPORTED_SUFFIXES)}"
        )
    if not (1 <= copies <= 99):
        raise ValueError(f"copies must be 1–99, got {copies}")

    conn = _cups_connect()
    name = _find_printer(conn, printer_name)

    job_options = dict(DEFAULT_OPTIONS)
    if options:
        job_options.update(options)
    if copies > 1:
        job_options["copies"] = str(copies)

    try:
        job_id: int = conn.printFile(name, str(path), path.name, job_options)
    except cups.IPPError as exc:
        raise RuntimeError(f"CUPS rejected print job: {exc}") from exc

    log.info("Job %d submitted → %s  (%s, %d cop%s)",
             job_id, name, path.name, copies, "y" if copies == 1 else "ies")
    return job_id


def wait_for_job(
    job_id: int,
    timeout: float = JOB_TIMEOUT,
    poll: float = _JOB_POLL_INTERVAL,
) -> bool:
    """Block until job_id reaches a terminal state or timeout elapses."""
    conn = _cups_connect()
    deadline = time.monotonic() + timeout
    prev_state = -1

    while time.monotonic() < deadline:
        try:
            attrs = conn.getJobAttributes(job_id)
        except cups.IPPError as exc:
            log.error("IPP error polling job %d: %s", job_id, exc)
            time.sleep(poll)
            continue

        state_code: int = attrs.get("job-state", 0)

        if state_code != prev_state:
            log.info("Job %d → %s", job_id, _JOB_STATES.get(state_code, f"unknown({state_code})"))
            prev_state = state_code

        if state_code == 9:
            return True
        if state_code in (7, 8):
            log.error("Job %d failed (%s): %s",
                      job_id, _JOB_STATES[state_code],
                      attrs.get("job-state-reasons", "unknown"))
            return False

        time.sleep(poll)

    log.error("Job %d timed out after %.0f s", job_id, timeout)
    return False


def monitor(printer_name: Optional[str] = None, interval: float = 5.0) -> None:
    """Poll and print printer status every interval seconds until Ctrl-C."""
    log.info("Monitoring Selphy — Ctrl-C to stop")
    backoff = interval

    while True:
        try:
            s = get_status(printer_name)
            ink_str = (
                "  ".join(f"{k}={v}%" for k, v in s["ink_levels"].items())
                or "n/a"
            )
            print(
                f"[{s['name']}]  state={s['state']:<12} "
                f"reasons={', '.join(s['reasons'])}  ink={ink_str}"
            )
            backoff = interval
        except Exception as exc:
            log.warning("Poll failed: %s — retry in %.0f s", exc, backoff)
            backoff = min(backoff * 2, 60.0)

        time.sleep(backoff)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="printer",
        description="Canon Selphy CP1500 — IPP Everywhere controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python printer.py status
  python printer.py print photo.jpg
  python printer.py print photo.jpg --copies 2 -o media=oe_2x6-photo_2x6in
  python printer.py monitor --interval 10
  python printer.py print photo.jpg --no-wait
""",
    )
    ap.add_argument("-p", "--printer", metavar="NAME",
                    help="CUPS printer name (auto-detected if omitted)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Enable DEBUG logging")

    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show current printer status and ink levels")

    p_print = sub.add_parser("print", help="Print an image file")
    p_print.add_argument("image", help="Path to JPEG, PNG, or PDF")
    p_print.add_argument("-n", "--copies", type=int, default=1, metavar="N",
                         help="Number of copies (default: 1)")
    p_print.add_argument("--no-wait", action="store_true",
                         help="Return immediately after submitting the job")
    p_print.add_argument("-o", "--option", metavar="KEY=VALUE", action="append",
                         help="Extra CUPS option, repeatable")

    p_mon = sub.add_parser("monitor", help="Continuous status monitoring")
    p_mon.add_argument("-i", "--interval", type=float, default=5.0, metavar="SEC",
                       help="Poll interval in seconds (default: 5)")

    return ap


def main() -> None:
    ap = _build_parser()
    args = ap.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.cmd == "status":
        try:
            s = get_status(args.printer)
        except Exception as exc:
            log.error("%s", exc)
            sys.exit(1)

        print(f"Printer : {s['name']}")
        print(f"State   : {s['state']}")
        print(f"Reasons : {', '.join(s['reasons'])}")
        print(f"Message : {s['message'] or '(none)'}")
        if s["ink_levels"]:
            for marker, level in s["ink_levels"].items():
                bar = "#" * (level // 5) + "." * (20 - level // 5)
                print(f"Ink [{marker:10s}]: [{bar}] {level}%")
        else:
            print("Ink     : (no marker data)")

    elif args.cmd == "print":
        extra: dict[str, str] = {}
        for kv in args.option or []:
            k, _, v = kv.partition("=")
            if not k or not v:
                ap.error(f"Bad option format '{kv}' — expected KEY=VALUE")
            extra[k.strip()] = v.strip()

        try:
            job_id = print_image(
                args.image,
                printer_name=args.printer,
                copies=args.copies,
                options=extra or None,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            log.error("%s", exc)
            sys.exit(1)

        print(f"Job ID: {job_id}")

        if not args.no_wait:
            ok = wait_for_job(job_id)
            sys.exit(0 if ok else 1)

    elif args.cmd == "monitor":
        if args.interval < 1:
            ap.error("--interval must be ≥ 1 second")
        try:
            monitor(args.printer, args.interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")


if __name__ == "__main__":
    main()
