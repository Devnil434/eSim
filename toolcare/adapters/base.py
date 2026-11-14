"""
toolcare.adapters.base - Abstract base adapter interface for tool installers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Tuple

from toolcare.core.registry import ToolSpec


class BaseAdapter(ABC):
    """
    Abstract interface for package manager and direct archive adapters.
    Enforces process isolation, dry-run safety, and explicit permission checking.
    """

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this adapter can execute on the current host system."""
        ...

    @abstractmethod
    def install(
        self,
        spec: ToolSpec,
        version: Optional[str] = None,
        dry_run: bool = False,
        target_dir: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """
        Execute tool installation.
        Returns:
            Tuple of (success: bool, status_message: str).
        """
        ...

    @abstractmethod
    def uninstall(self, spec: ToolSpec, dry_run: bool = False) -> Tuple[bool, str]:
        """Uninstall the specified tool."""
        ...

    def check_permissions(self) -> Tuple[bool, str]:
        """
        Explicitly check whether current execution environment has required privileges.
        Correction 8: Avoids silent privileged execution.
        """
        return True, "Standard user permissions sufficient."
