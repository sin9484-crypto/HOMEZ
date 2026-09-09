"""
=========================================================
Homez OS

File : app/core/version.py
=========================================================
"""

from __future__ import annotations

MAJOR = 2
MINOR = 1
PATCH = 4

VERSION = f"{MAJOR}.{MINOR}.{PATCH}"


def get_version() -> str:
    return VERSION


__version__ = VERSION


__all__ = [
    "MAJOR",
    "MINOR",
    "PATCH",
    "VERSION",
    "__version__",
    "get_version",
]