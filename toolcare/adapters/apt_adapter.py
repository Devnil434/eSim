"""
toolcare.adapters.apt_adapter - Debian / Ubuntu Advanced Package Tool (APT) adapter.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional, Tuple

from toolcare.adapters.base import BaseAdapter
from toolcare.core.registry import ToolSpec
from toolcare.logger import get_logger
from toolcare.platform_utils import IS_LINUX, run_safe_command

logger = get_logger("adapters.apt")


class AptAdapter(BaseAdapter):
    """
    Adapter wrapping Debian/Ubuntu apt-get package manager.
    Architecture-compatible for eSim on Linux.
    """

    name: str = "apt"

    def is_available(self) -> bool:
        """Check if apt-get is available on Linux."""
        return IS_LINUX and shutil.which("apt-get") is not None

    def check_permissions(self) -> Tuple[bool, str]:
        """Check whether execution has root privileges required by APT."""
        if hasattr(os, "geteuid"):
            if os.geteuid() != 0:
                return False, "APT installation requires root privileges (sudo)."
        return True, "Root permissions confirmed."

    def install(
        self,
        spec: ToolSpec,
        version: Optional[str] = None,
        dry_run: bool = False,
        target_dir: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """
        Install package via APT.
        Correction 7: Enforces verified package identifiers (e.g. ghdl-llvm over ghdl-mcode).
        Correction 8: Explicit privilege checking.
        """
        if not self.is_available() and not dry_run:
            return False, "APT is not available on this system."

        if not spec.is_package_id_verified("apt"):
            msg = (
                f"Tool '{spec.id}' does not have a verified APT package identifier. "
                "Installation refused to avoid incompatible packaging."
            )
            logger.warning(msg)
            return False, msg

        pkg_id = spec.get_package_id("apt")
        if not pkg_id:
            return False, f"Missing APT package ID for {spec.id}."

        cmd = ["apt-get", "install", "-y", pkg_id]

        if dry_run:
            preview_msg = f"[Dry-Run] Would execute command: {' '.join(cmd)}"
            logger.info(preview_msg)
            return True, preview_msg

        has_root, perm_msg = self.check_permissions()
        if not has_root:
            logger.error("Permission denied: %s", perm_msg)
            return False, perm_msg

        logger.info("Executing APT installation: %s", " ".join(cmd))
        retcode, stdout, stderr = run_safe_command(cmd, timeout=300)
        if retcode == 0:
            return True, f"Successfully executed apt-get install for {pkg_id}."
        return False, f"APT installation failed (code {retcode}): {stderr or stdout}"

    def uninstall(self, spec: ToolSpec, dry_run: bool = False) -> Tuple[bool, str]:
        """Uninstall package via APT."""
        pkg_id = spec.get_package_id("apt")
        if not pkg_id:
            return False, f"Missing APT package ID for {spec.id}."

        cmd = ["apt-get", "remove", "-y", pkg_id]
        if dry_run:
            return True, f"[Dry-Run] Would execute: {' '.join(cmd)}"

        retcode, stdout, stderr = run_safe_command(cmd, timeout=120)
        return retcode == 0, stdout if retcode == 0 else stderr
