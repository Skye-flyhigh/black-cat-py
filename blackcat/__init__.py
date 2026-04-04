"""
blackcat - A lightweight AI agent framework
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("blackcat")
except PackageNotFoundError:
    __version__ = "0.1.0"

__logo__ = "🐈‍⬛"


from blackcat.blackcat import Blackcat, RunResult

__all__ = ["Blackcat", "RunResult"]
