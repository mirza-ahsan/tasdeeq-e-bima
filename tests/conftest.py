"""Test isolation, applied before anything from `backend` is imported.

* Qwen is switched off: an empty DASHSCOPE_API_KEY in the environment outranks `.env`,
  so the suite never makes a billed, network-dependent call. Tests marked `live` build
  their own config from `.env` and are skipped unless run with `-m live`.
* The feedback log points at a throwaway file, so test rows never reach
  backend/feedback.db, the human-correction log meant to become training labels.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["DASHSCOPE_API_KEY"] = ""
os.environ["FEEDBACK_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="tasdeeq-tests-")) / "feedback.db")
