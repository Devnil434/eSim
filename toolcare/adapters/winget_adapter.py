"""
toolcare.adapters.winget_adapter - Windows Package Manager (winget) adapter.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional, Tuple

from toolcare.adapters.base import BaseAdapter
from toolcare.core.registry import ToolSpec
from toolcare.logger import get_logger
from toolcare.platform_utils import IS_WINDOWS, run_safe_command

logger = get_logger("adapters.winget")


class WingetAdapter(BaseAdapter):
    """
    Adapter wrapping Microsoft Windows Package Manager (winget).
    Enforces verified package identifier checks and dry-run execution safety.
    """

    name: str = "winget"

    def is_available(self) -> bool:
        """Check if winget is available on Windows."""
        return IS_WINDOWS and shutil.which("winget") is not None

    def install(
        self,
        spec: ToolSpec,
        version: Optional[str] = None,
        dry_run: bool = False,
        target_dir: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """
        Install tool using winget.
        Correction 7: Rejects unverified package identifiers.
        Correction 6: Dry-run performs zero execution.
        """
        if not self.is_available() and not dry_run:
            return False, "Winget is not available on this system."

        if not spec.is_package_id_verified("winget"):
            msg = (
                f"Tool '{spec.id}' does not have a verified Winget package identifier. "
                "Installation refused to prevent incompatible upstream package deployment. "
                "Use the verified vendor archive adapter instead."
            )
            logger.warning(msg)
            return False, msg

        pkg_id = spec.get_package_id("winget")
        if not pkg_id:
            return False, f"Missing package identifier for {spec.id}."

        cmd = [
            "winget",
            "install",
            "--id",
            pkg_id,
            "--exact",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ]
        if version:
            cmd.extend(["--version", version])

        if dry_run:
            preview_msg = f"[Dry-Run] Would execute command: {' '.join(cmd)}"
            logger.info(preview_msg)
            return True, preview_msg

        logger.info("Executing winget installation: %s", " ".join(cmd))
        retcode, stdout, stderr = run_safe_command(cmd, timeout=300)

        if retcode == 0:
            return True, f"Successfully executed winget install for {pkg_id}."
        return False, f"Winget installation failed (code {retcode}): {stderr or stdout}"

    def uninstall(self, spec: ToolSpec, dry_run: bool = False) -> Tuple[bool, str]:
        """Uninstall package using winget."""
        pkg_id = spec.get_package_id("winget")
        if not pkg_id:
            return False, f"No winget package ID registered for {spec.id}."

        cmd = ["winget", "uninstall", "--id", pkg_id, "--exact"]

        if dry_run:
            return True, f"[Dry-Run] Would execute: {' '.join(cmd)}"

        retcode, stdout, stderr = run_safe_command(cmd, timeout=120)
        return retcode == 0, stdout if retcode == 0 else stderr
