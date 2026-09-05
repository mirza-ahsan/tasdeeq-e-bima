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
        """Diagnostic summary. Deliberately reports only presence of the key,
        never its value, length, or any prefix."""
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
