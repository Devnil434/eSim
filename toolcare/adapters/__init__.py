"""
toolcare.adapters - Package manager and archive installation adapters.
"""

from toolcare.adapters.base import BaseAdapter
from toolcare.adapters.winget_adapter import WingetAdapter
from toolcare.adapters.apt_adapter import AptAdapter
from toolcare.adapters.archive_adapter import ArchiveAdapter, SecurityError

__all__ = [
    "BaseAdapter",
    "WingetAdapter",
    "AptAdapter",
    "ArchiveAdapter",
    "SecurityError",
]
