"""
blackcat - A lightweight AI agent framework
"""

import os
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from loguru import logger

from blackcat.blackcat import Blackcat, RunResult


def _read_pyproject_version() -> str | None:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _resolve_version() -> str:
    try:
        return _pkg_version("blackcat-ai")
    except PackageNotFoundError:
        # Source checkouts often import blackcat without installed dist-info.
        return _read_pyproject_version() or "0.1.5.post3"


__version__ = _resolve_version()
__logo__ = "🐈‍⬛"

# ---------------------------------------------------------------------------
# File logging sink — persistent, rotatable, so Dream & agent activity is
# visible to the user via `tail -f ~/.blackcat/workspace/logs/blackcat.log`
# ---------------------------------------------------------------------------
_LOG_DIR = Path.home() / ".blackcat" / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "blackcat.log"

logger.add(
    str(_LOG_FILE),
    rotation="10 MB",
    retention="7 days",
    level=os.environ.get("BLACKCAT_LOG_LEVEL", "INFO"),
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    enqueue=True,
    backtrace=False,
    diagnose=False,
)
logger.debug("blackcat logging initialised — {}", _LOG_FILE)

__all__ = ["Blackcat", "RunResult"]
