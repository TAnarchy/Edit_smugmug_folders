"""
api_logger.py — Logs every SmugMug API request and response to api_requests.log.

Usage:
    from api_logger import logged_session

    session = logged_session()   # drop-in for requests.Session()
    session.auth = ...
    session.headers.update(...)
    # Then use session.get / session.patch / session.post normally.
"""

import json
import logging
import time
from datetime import datetime, timezone
from requests import Session

LOG_FILE = "api_requests.log"

# File logger — one JSON record per line, human-readable with separators
_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter("%(message)s"))

_logger = logging.getLogger("api_logger")
_logger.setLevel(logging.DEBUG)
_logger.addHandler(_file_handler)
_logger.propagate = False   # don't bleed into root logger / console


def _log_record(record: dict):
    """Write a single log record as pretty JSON separated by a rule."""
    _logger.debug("─" * 80)
    _logger.debug(json.dumps(record, indent=2, default=str))


def _safe_json(text: str):
    """Try to parse text as JSON; fall back to raw string."""
    try:
        return json.loads(text)
    except Exception:
        return text


class _LoggedSession(Session):
    """requests.Session subclass that logs every request + response."""

    def request(self, method, url, **kwargs):
        timestamp = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        # Build the log entry for the request side
        req_entry = {
            "timestamp": timestamp,
            "direction": "REQUEST",
            "method": method.upper(),
            "url": url,
            "params": kwargs.get("params"),
            "json_body": kwargs.get("json"),
            "data": kwargs.get("data"),
        }
        _log_record(req_entry)

        # Execute the actual HTTP call
        response = super().request(method, url, **kwargs)

        elapsed_ms = round((time.monotonic() - t0) * 1000)

        # Try to parse response body
        try:
            body = response.json()
        except Exception:
            body = response.text[:2000]   # truncate huge non-JSON bodies

        resp_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": "RESPONSE",
            "method": method.upper(),
            "url": url,
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
            "body": body,
        }
        _log_record(resp_entry)

        return response


def logged_session() -> _LoggedSession:
    """Return a requests.Session that logs all calls to api_requests.log."""
    return _LoggedSession()


# ── quick self-test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os

    print(f"Testing logger → writes to {os.path.abspath(LOG_FILE)}")
    s = logged_session()
    try:
        r = s.get("https://httpbin.org/get", params={"hello": "world"})
        print(f"HTTP {r.status_code}")
    except Exception as e:
        print(f"Network error (expected in restricted environments): {e}")

    # Verify log file was written even if network failed
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            content = f.read()
        print(f"Log file exists, {len(content)} bytes written.")
        print("First 300 chars of log:\n" + content[:300])
    else:
        print("Log file was NOT created — check permissions.")
