"""Dashboard authentication: local-first token auth.

Threat model: the dashboard binds to localhost by default and is a
local-first product, but its API can write arbitrary project files,
change config, spawn agents, and run agent tasks. Anything on the machine
(or a drive-by page in the user's browser, via DNS rebinding) can reach
http://127.0.0.1:<port> — so every *mutating* route requires a shared
secret token in the ``X-Nexus-Token`` header. Read-only GET routes stay
open for the local UI unless ``dashboard_auth: strict`` (config.json, or
the NEXUS_DASHBOARD_AUTH env var) locks those down too.

The token is generated once with ``secrets.token_urlsafe(32)`` and stored
at ``~/.nexus/dashboard-token`` with 0600 permissions — Jupyter-style
local-dev flow: it is printed ONCE at dashboard startup so the user can
paste it into the web UI, and never logged again afterwards.
"""

import hmac
import json
import os
import secrets
from pathlib import Path

TOKEN_FILE_NAME = "dashboard-token"
TOKEN_HEADER = "X-Nexus-Token"
TOKEN_ENV = "NEXUS_DASHBOARD_TOKEN"
STRICT_ENV = "NEXUS_DASHBOARD_AUTH"


def load_or_create_token(path: Path) -> str:
    """Load the dashboard token from ``path``, or generate + persist one.

    Precedence: ``NEXUS_DASHBOARD_TOKEN`` env var wins (useful for tests
    and scripted launches), then the existing token file, then a fresh
    ``secrets.token_urlsafe(32)`` written with 0600 permissions.
    """
    env_token = os.environ.get(TOKEN_ENV, "").strip()
    if env_token:
        return env_token

    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the start (no world-readable window), then
    # chmod again in case the file already existed with loose permissions.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


class DashboardAuth:
    """Token + auth-mode state for one dashboard process."""

    def __init__(self, config_dir: Path | None = None):
        self._config_dir = config_dir
        self._token: str | None = None

    @property
    def token_path(self) -> Path:
        base = self._config_dir or (Path.home() / ".nexus")
        return base / TOKEN_FILE_NAME

    def get_token(self) -> str:
        """Load-or-generate the dashboard token (env override wins)."""
        if self._token is None:
            self._token = load_or_create_token(self.token_path)
        return self._token

    def token_matches(self, supplied: str | None) -> bool:
        """Constant-time comparison; never raises on odd header bytes."""
        if not supplied:
            return False
        try:
            return hmac.compare_digest(supplied.encode("utf-8"), self.get_token().encode("utf-8"))
        except (UnicodeEncodeError, AttributeError):
            return False

    def is_strict_mode(self) -> bool:
        """True when ``dashboard_auth`` is set to ``strict``.

        Locks down ALL /api routes including GET. Read via the existing
        config access pattern: the ``NEXUS_DASHBOARD_AUTH`` env var first,
        then the ``dashboard_auth`` field on the loaded NexusConfig, then
        the raw ``config.json`` (config.py does not model the field yet, so
        a key added by hand to config.json must be read from the file).
        """
        env = os.environ.get(STRICT_ENV, "").strip().lower()
        if env:
            return env == "strict"
        value: object = None
        try:
            from ..config import load_config

            cfg = load_config()
            value = getattr(cfg, "dashboard_auth", None)
        except Exception:
            value = None
        if not value:
            value = self._raw_config_value("dashboard_auth")
        return str(value or "").strip().lower() == "strict"

    def _raw_config_value(self, key: str) -> object | None:
        """Read a key straight from config.json (unknown-to-dataclass keys).

        Uses the same HOME-aware ``~/.nexus`` base as the token file, so it
        stays consistent even though config.py snapshots its default config
        dir at import time.
        """
        try:
            config_dir = self._config_dir or (Path.home() / ".nexus")
            config_file = Path(config_dir) / "config.json"
            if not config_file.exists():
                return None
            data = json.loads(config_file.read_text(encoding="utf-8"))
            return data.get(key)
        except Exception:
            return None

    def check_request(self, method: str, path: str, supplied_token: str | None) -> bool:
        """Decide whether a request may proceed.

        - Page shell + static assets are always reachable (the UI has to
          load so it can prompt for the token on 401).
        - Default (local-first): GET/HEAD/OPTIONS are open; mutating
          methods (POST/PUT/DELETE/PATCH) require the token.
        - Strict mode: every /api route requires the token, GET included.
        """
        if path == "/" or path.startswith("/static/") or path in ("/manifest.json", "/sw.js", "/favicon.ico"):
            return True
        if not self.is_strict_mode() and method in ("GET", "HEAD", "OPTIONS"):
            return True
        return self.token_matches(supplied_token)
