from __future__ import annotations

import asyncio
import sys

if sys.platform.startswith("win") and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from ATC.app.main import app


__all__ = ["app"]
