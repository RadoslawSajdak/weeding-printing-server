#!/usr/bin/env python3
"""
Wedding Gallery — interactive printer simulator.

Fetches jobs from the backend, displays images with feh (or any available
viewer), and lets you approve/reject each job via a simple REPL.

Usage:
  python printer_sim.py [--api http://localhost:8000] [--key printer-secret-key]

Environment variables (override defaults):
  API_BASE          backend URL
  PRINTER_API_KEY   printer API key
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from typing import Optional

# ── CLI args / config ─────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(description="Printer simulator")
_parser.add_argument("--api", default=os.environ.get("API_BASE", "http://localhost:8000"))
_parser.add_argument("--key", default=os.environ.get("PRINTER_API_KEY", "printer-secret-key"))
_args = _parser.parse_args()

API_BASE = _args.api.rstrip("/")
API_KEY  = _args.key

# ── ANSI colours ──────────────────────────────────────────────────────────────
_tty = sys.stdout.isatty()
def _c(code: str) -> str: return f"\033[{code}m" if _tty else ""
RST = _c("0");  BOLD = _c("1");  DIM = _c("2")
GRN = _c("32"); YLW = _c("33"); RED = _c("31"); CYN = _c("36")

# ── History (arrow keys in input()) ───────────────────────────────────────────
try:
    import readline  # noqa: F401
except ImportError:
    pass

# ── Image viewer (first one found wins) ───────────────────────────────────────
_VIEWERS = [
    # name,  extra args when name matches
    ("feh",      ["--scale-down", "--auto-zoom", "--title", "PRINT PREVIEW"]),
    ("display",  ["-title", "PRINT PREVIEW"]),
    ("eog",      []),
    ("xdg-open", []),
    ("open",     []),
]
VIEWER, VIEWER_EXTRA = next(
    ((v, ex) for v, ex in _VIEWERS if shutil.which(v)),
    (None, []),
)

# ── Temp dir for downloaded files ─────────────────────────────────────────────
TMPDIR = tempfile.mkdtemp(prefix="printer_sim_")

# ── State ─────────────────────────────────────────────────────────────────────
class Job:
    __slots__ = ("job_id", "filename", "original_name", "local_path")

    def __init__(self, job_id: str, filename: str, original_name: str, local_path: str):
        self.job_id        = job_id
        self.filename      = filename
        self.original_name = original_name
        self.local_path    = local_path

    def sid(self) -> str:       # short display id
        return self.job_id[:8] + "…"

_lock        = threading.Lock()
_current_job: Optional[Job]             = None
_poll_thread: Optional[threading.Thread] = None
_poll_stop   = threading.Event()
_viewer_proc: Optional[subprocess.Popen] = None

# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _request(method: str, path: str, body: Optional[dict] = None) -> tuple[int, bytes]:
    url  = API_BASE + path
    data = json.dumps(body).encode() if body else None
    hdrs: dict[str, str] = {"X-Printer-Key": API_KEY}
    if data:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _api_next() -> Optional[dict]:
    code, body = _request("GET", "/printer/next")
    if code == 204:
        return None
    if code == 200:
        return json.loads(body)
    raise RuntimeError(f"HTTP {code}: {body.decode()}")


def _api_download(job_id: str, dest: str):
    req = urllib.request.Request(
        API_BASE + f"/printer/file/{job_id}",
        headers={"X-Printer-Key": API_KEY},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        with open(dest, "wb") as f:
            f.write(r.read())


def _api_complete(job_id: str, success: bool, error_msg: str = ""):
    body: dict = {"success": success}
    if error_msg:
        body["error_message"] = error_msg
    code, resp = _request("POST", f"/printer/complete/{job_id}", body)
    if code != 200:
        raise RuntimeError(f"HTTP {code}: {resp.decode()}")


# ── Viewer helpers ────────────────────────────────────────────────────────────
def _open_viewer(path: str):
    global _viewer_proc
    _close_viewer()
    if not VIEWER:
        print(f"  {YLW}No viewer found — file at: {path}{RST}")
        return
    try:
        _viewer_proc = subprocess.Popen(
            [VIEWER, *VIEWER_EXTRA, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"  {YLW}Could not open viewer: {e}{RST}")


def _close_viewer():
    global _viewer_proc
    if _viewer_proc and _viewer_proc.poll() is None:
        _viewer_proc.terminate()
    _viewer_proc = None


# ── Core command implementations ──────────────────────────────────────────────
def cmd_next():
    """Fetch the next pending job and display it."""
    global _current_job
    with _lock:
        if _current_job:
            print(f"{YLW}Job {_current_job.sid()} still active. Run 'done' or 'fail' first.{RST}")
            return

    try:
        data = _api_next()
    except Exception as e:
        print(f"{RED}Error fetching job: {e}{RST}")
        return

    if data is None:
        print(f"{DIM}Queue is empty.{RST}")
        return

    job_id = data["job_id"]
    ext    = os.path.splitext(data["filename"])[1] or ".jpg"
    local  = os.path.join(TMPDIR, job_id + ext)

    print(f"\n{CYN}{BOLD}▶  {data['original_name']}{RST}  {DIM}[{job_id[:8]}…]{RST}")
    print(f"   downloading… ", end="", flush=True)
    try:
        _api_download(job_id, local)
        kb = os.path.getsize(local) // 1024
        print(f"{GRN}OK{RST}  ({kb} KB)  →  {local}")
    except Exception as e:
        print(f"{RED}FAILED{RST}  ({e})")
        return

    with _lock:
        _current_job = Job(job_id, data["filename"], data["original_name"], local)

    _open_viewer(local)
    print(f"   {DIM}type {RST}{GRN}done{RST}{DIM} to confirm print, {RST}{RED}fail [reason]{RST}{DIM} to reject{RST}\n")


def cmd_done():
    """Mark the active job as successfully printed."""
    global _current_job
    with _lock:
        job = _current_job
    if not job:
        print(f"{YLW}No active job.{RST}")
        return
    try:
        _api_complete(job.job_id, True)
    except Exception as e:
        print(f"{RED}API error: {e}{RST}")
        return
    print(f"{GRN}{BOLD}✓  PRINTED{RST}  {job.original_name}  {DIM}[{job.sid()}]{RST}")
    _close_viewer()
    with _lock:
        _current_job = None


def cmd_fail(reason: str):
    """Mark the active job as failed."""
    global _current_job
    with _lock:
        job = _current_job
    if not job:
        print(f"{YLW}No active job.{RST}")
        return
    msg = reason or "Manual failure"
    try:
        _api_complete(job.job_id, False, msg)
    except Exception as e:
        print(f"{RED}API error: {e}{RST}")
        return
    print(f"{RED}{BOLD}✗  FAILED{RST}  {job.original_name}  {DIM}[{job.sid()}]{RST}")
    if reason:
        print(f"   reason: {reason}")
    _close_viewer()
    with _lock:
        _current_job = None


def cmd_clear():
    """Drop the current job locally without calling the API."""
    global _current_job
    with _lock:
        job = _current_job
        _current_job = None
    if job:
        _close_viewer()
        print(f"{DIM}Cleared {job.sid()} locally — no API call made.{RST}")
    else:
        print(f"{DIM}No active job.{RST}")


def cmd_status():
    with _lock:
        job = _current_job
    polling = bool(_poll_thread and _poll_thread.is_alive())

    print(f"\n{BOLD}── status ──────────────────────────────────────{RST}")
    if job:
        print(f"  job      {YLW}PROCESSING{RST}  {job.original_name}")
        print(f"  id       {DIM}{job.job_id}{RST}")
        print(f"  file     {job.local_path}")
    else:
        print(f"  job      {DIM}idle{RST}")
    print(f"  polling  {GRN+'active'+RST if polling else DIM+'off'+RST}")
    print(f"  api      {CYN}{API_BASE}{RST}")
    print(f"  viewer   {VIEWER or DIM+'not found'+RST}")
    print()


# ── Auto-poll ─────────────────────────────────────────────────────────────────
def _poll_worker(interval: int):
    """Background thread: repeatedly tries to claim the next job."""
    global _current_job
    while True:
        if _poll_stop.wait(interval):
            break
        with _lock:
            busy = _current_job is not None
        if busy:
            continue

        try:
            data = _api_next()
        except Exception:
            continue
        if not data:
            continue

        job_id = data["job_id"]
        ext    = os.path.splitext(data["filename"])[1] or ".jpg"
        local  = os.path.join(TMPDIR, job_id + ext)

        # write above the current prompt
        sys.stdout.write(
            f"\n{CYN}{BOLD}[auto] ▶  {data['original_name']}{RST}  {DIM}[{job_id[:8]}…]{RST}\n"
            f"       downloading… "
        )
        sys.stdout.flush()
        try:
            _api_download(job_id, local)
            kb = os.path.getsize(local) // 1024
            sys.stdout.write(f"{GRN}OK{RST}  ({kb} KB)\n")
        except Exception as e:
            sys.stdout.write(f"{RED}FAILED{RST}  ({e})\n")
            sys.stdout.flush()
            continue

        with _lock:
            _current_job = Job(job_id, data["filename"], data["original_name"], local)

        _open_viewer(local)
        sys.stdout.write(
            f"       {DIM}type {RST}{GRN}done{RST}{DIM} or {RST}{RED}fail [reason]{RST}\n"
            f">>> "
        )
        sys.stdout.flush()


def cmd_poll(arg: str):
    global _poll_thread
    if _poll_thread and _poll_thread.is_alive():
        print(f"{YLW}Already polling — run 'stop' first.{RST}")
        return
    try:
        interval = max(1, int(arg)) if arg else 5
    except ValueError:
        print(f"{YLW}Usage: poll [seconds]{RST}")
        return
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_worker, args=(interval,), daemon=True)
    _poll_thread.start()
    print(f"{GRN}Auto-polling every {interval}s{RST}  (type 'stop' to disable)")


def cmd_stop():
    global _poll_thread
    _poll_stop.set()
    if _poll_thread:
        _poll_thread.join(timeout=2)
        _poll_thread = None
    print(f"{DIM}Polling stopped.{RST}")


# ── REPL ──────────────────────────────────────────────────────────────────────
HELP = f"""
{BOLD}Commands:{RST}
  {GRN}next{RST}  (n)           Fetch next queued job and open image
  {GRN}done{RST}  (ok)          Mark active job as SUCCESS (printed)
  {RED}fail{RST}  [reason]      Mark active job as FAILED
  {CYN}poll{RST}  [seconds]     Auto-poll every N sec (default 5)
  {CYN}stop{RST}                Stop auto-polling
  {CYN}status{RST} (s)          Show active job + config
  {DIM}clear{RST}               Drop job locally, no API call (for testing)
  {DIM}help{RST}  (h)           Show this help
  {DIM}quit{RST}  (q / Ctrl-D)  Exit
"""

DISPATCH = {
    "next": lambda a: cmd_next(),
    "n":    lambda a: cmd_next(),
    "done": lambda a: cmd_done(),
    "ok":   lambda a: cmd_done(),
    "fail": lambda a: cmd_fail(a),
    "f":    lambda a: cmd_fail(a),
    "poll": lambda a: cmd_poll(a),
    "stop": lambda a: cmd_stop(),
    "status": lambda a: cmd_status(),
    "s":    lambda a: cmd_status(),
    "clear": lambda a: cmd_clear(),
    "help": lambda a: print(HELP),
    "h":    lambda a: print(HELP),
    "?":    lambda a: print(HELP),
}


def main():
    print(f"\n{BOLD}Wedding Gallery — Printer Simulator{RST}")
    print(f"  api    {CYN}{API_BASE}{RST}")
    print(f"  viewer {VIEWER or YLW+'not found (install feh)'+RST}")
    print(f"  tmpdir {DIM}{TMPDIR}{RST}")
    print(f"\nType {BOLD}help{RST} for commands, {BOLD}poll{RST} to start auto-mode.\n")

    try:
        while True:
            try:
                line = input(">>> ").strip()
            except EOFError:
                break
            if not line:
                continue

            parts = line.split(None, 1)
            cmd   = parts[0].lower()
            arg   = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("quit", "q", "exit"):
                break

            handler = DISPATCH.get(cmd)
            if handler:
                handler(arg)
            else:
                print(f"{DIM}Unknown command '{cmd}'. Type 'help'.{RST}")

    except KeyboardInterrupt:
        pass
    finally:
        cmd_stop()
        _close_viewer()
        shutil.rmtree(TMPDIR, ignore_errors=True)
        print(f"\n{DIM}Bye.{RST}")


if __name__ == "__main__":
    main()
