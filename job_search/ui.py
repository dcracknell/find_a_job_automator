"""Local preferences editor — `job-search ui`.

Serves templates/preferences.html on localhost and exposes a tiny JSON API
that reads/writes config/profile.json and the editable keys of
config/settings.yaml (comment-preserving line edits, never a YAML re-dump).

The same HTML file is published statically on GitHub Pages (docs/), where the
API is absent: the page detects that and falls back to paste/copy editing.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from job_search import PROJECT_ROOT

logger = logging.getLogger(__name__)

_PROFILE_PATH = PROJECT_ROOT / "config" / "profile.json"
_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
_EDITOR_HTML = PROJECT_ROOT / "templates" / "preferences.html"

# The only settings.yaml keys the UI may modify — everything else in the file
# (paths, rates, email) stays hand-edited.
ALLOWED_SETTINGS_KEYS = frozenset(
    [
        "mode",
        "quota_soft_cap_gbp",
        "stale_job_days",
        "models.parse_cv.model",
        "models.rank.model",
        "models.rank.batch_size",
        "models.queries.model",
        "models.queries.use_claude",
        "models.queries.max_queries",
    ]
)

_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_]+):(.*)$")


def _format_yaml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9._\-]+", text):
        return text
    return json.dumps(text)  # JSON string quoting is valid YAML


def update_settings_text(text: str, updates: dict[str, object]) -> str:
    """Replace scalar values for dotted keys in YAML text, preserving comments.

    Walks the document tracking the mapping-key path by indentation; when a
    line's dotted path is in `updates`, only the value portion of that line is
    rewritten (inline comments survive). Keys not present in the text are left
    unadded — the UI only edits keys that already exist.
    """
    lines = text.split("\n")
    stack: list[tuple[int, str]] = []  # (indent, key)

    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#") or line.lstrip().startswith("-"):
            continue
        m = _KEY_RE.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        key = m.group(2)
        rest = m.group(3)

        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        stack.append((indent, key))

        if path not in updates:
            continue

        comment = ""
        comment_match = re.search(r"\s#.*$", rest)
        if comment_match:
            comment = comment_match.group(0)
        formatted = _format_yaml_value(updates[path])
        lines[i] = f"{m.group(1)}{key}: {formatted}{comment}"

    return "\n".join(lines)


def _editable_settings() -> dict:
    """Extract the UI-editable keys from settings.yaml as a flat dict."""
    try:
        with _SETTINGS_PATH.open() as f:
            settings = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("ui: could not read settings.yaml: %s", exc)
        return {}

    flat: dict[str, object] = {}
    for dotted in ALLOWED_SETTINGS_KEYS:
        node: object = settings
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is not None:
            flat[dotted] = node
    return flat


def _load_profile_or_default() -> dict:
    try:
        return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise ValueError(f"config/profile.json is not valid JSON: {exc}") from exc


def _list_domains() -> list[str]:
    try:
        from job_search.util.domain import list_packs
        return [p.name for p in list_packs()]
    except Exception:
        return []


class _Handler(BaseHTTPRequestHandler):
    server_version = "job-search-ui/1.0"

    # -- helpers -----------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: object, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _read_body_json(self) -> object:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def log_message(self, fmt: str, *args) -> None:  # quiet by default
        logger.debug("ui: " + fmt, *args)

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        if self.path in ("/", "/index.html"):
            try:
                html = _EDITOR_HTML.read_bytes()
            except FileNotFoundError:
                self._send(500, b"templates/preferences.html missing", "text/plain")
                return
            self._send(200, html, "text/html; charset=utf-8")
        elif self.path == "/api/profile":
            try:
                self._send_json(_load_profile_or_default())
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=500)
        elif self.path == "/api/settings":
            self._send_json(_editable_settings())
        elif self.path == "/api/domains":
            self._send_json(_list_domains())
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802 (stdlib API)
        try:
            if self.path == "/api/profile":
                profile = self._read_body_json()
                if not isinstance(profile, dict) or not profile:
                    raise ValueError("profile must be a non-empty JSON object")
                # Fill lat/lon for a new/changed city so distance filtering works
                try:
                    from job_search.profile.parse_cv import ensure_location_coords
                    ensure_location_coords(profile)
                except Exception as exc:
                    logger.warning("ui: geocode skipped: %s", exc)
                _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
                _PROFILE_PATH.write_text(
                    json.dumps(profile, indent=2) + "\n", encoding="utf-8"
                )
                self._send_json({"ok": True, "path": str(_PROFILE_PATH)})
            elif self.path == "/api/settings":
                updates = self._read_body_json()
                if not isinstance(updates, dict):
                    raise ValueError("settings payload must be a JSON object")
                unknown = set(updates) - ALLOWED_SETTINGS_KEYS
                if unknown:
                    raise ValueError(f"keys not editable from the UI: {sorted(unknown)}")
                text = _SETTINGS_PATH.read_text(encoding="utf-8")
                _SETTINGS_PATH.write_text(
                    update_settings_text(text, updates), encoding="utf-8"
                )
                self._send_json({"ok": True, "path": str(_SETTINGS_PATH)})
            else:
                self._send(404, b"not found", "text/plain")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:  # keep the server alive on any handler bug
            logger.exception("ui: request failed")
            self._send_json({"error": str(exc)}, status=500)


def serve(port: int = 8765, open_browser: bool = True) -> None:
    """Run the editor server until interrupted."""
    address = ("127.0.0.1", port)
    httpd = ThreadingHTTPServer(address, _Handler)
    url = f"http://{address[0]}:{port}/"
    print(f"Preferences editor running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
