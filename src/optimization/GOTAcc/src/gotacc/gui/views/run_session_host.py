from __future__ import annotations

import sys
from pathlib import Path

try:
    from .run_session import RunSession, RunSessionEvents
except ImportError:  # pragma: no cover - local script fallback
    CURRENT_DIR = Path(__file__).resolve().parent
    if str(CURRENT_DIR) not in sys.path:
        sys.path.insert(0, str(CURRENT_DIR))
    from run_session import RunSession, RunSessionEvents


RunSessionHost = RunSession

__all__ = ["RunSessionHost", "RunSessionEvents"]
