"""Environment-backed configuration.

All secrets come from environment variables (loaded from a local .env in dev).
Nothing here is ever hardcoded, and the API key is never logged or printed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root if present. Real environment variables always win,
# so container/cloud-injected config is not overridden by a stray local file.
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env", override=False)

DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"


@dataclass(frozen=True)
class DashScopeConfig:
    api_key: str | None
    base_url: str
    workspace_id: str | None
    model: str

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict[str, str]:
        """Local diagnostic summary for scripts/test_dashscope.py. Reports only the
        presence of the key, never its value, length, or any prefix. The endpoint and
        workspace identify the account, so this is never served over HTTP."""
        return {
            "DASHSCOPE_API_KEY": "set" if self.api_key else "MISSING",
            "DASHSCOPE_BASE_URL": self.base_url,
            "DASHSCOPE_WORKSPACE_ID": self.workspace_id or "(not set)",
            "QWEN_MODEL": self.model,
        }


def _clean(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_dashscope_config() -> DashScopeConfig:
    return DashScopeConfig(
        api_key=_clean("DASHSCOPE_API_KEY"),
        base_url=_clean("DASHSCOPE_BASE_URL") or DEFAULT_BASE_URL,
        workspace_id=_clean("DASHSCOPE_WORKSPACE_ID"),
        model=_clean("QWEN_MODEL") or DEFAULT_MODEL,
    )


DEFAULT_CORS_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def cors_origins() -> list[str]:
    """Browser origins allowed to call the API directly. The web app normally reaches
    the API through its own same-origin proxy, so this only matters for direct use."""
    raw = _clean("CORS_ORIGINS")
    return [o.strip() for o in raw.split(",") if o.strip()] if raw else list(DEFAULT_CORS_ORIGINS)


def trust_proxy_headers() -> bool:
    """Whether X-Forwarded-For identifies the client. Only true behind a proxy we run
    (the web app's /api route, nginx); otherwise any caller could pick their own IP."""
    return (_clean("TRUST_PROXY_HEADERS") or "").lower() in {"1", "true", "yes"}
