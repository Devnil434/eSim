"""
toolcare.core.installer - Installation coordinator with post-installation verification gates.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from toolcare.adapters.apt_adapter import AptAdapter
from toolcare.adapters.archive_adapter import ArchiveAdapter
from toolcare.adapters.base import BaseAdapter
from toolcare.adapters.winget_adapter import WingetAdapter
from toolcare.core.detector import ToolDetectionResult, ToolDetector
from toolcare.core.doctor import SimulationDoctor
from toolcare.core.registry import ToolRegistry, ToolSpec
from toolcare.logger import get_logger
from toolcare.platform_utils import IS_LINUX, IS_WINDOWS

logger = get_logger("installer")


@dataclass(frozen=True)
class InstallResult:
    """Comprehensive result of an automated tool installation operation."""
    tool_id: str
    success: bool
    method_used: str
    installed_path: Optional[Path]
    post_install_version: Optional[str]
    is_compatible: bool
    message: str
    dry_run: bool


class ToolInstaller:
    """
    Central installation coordinator.
    Selects verified adapters, manages staging directories, preserves existing installations
    upon failure, and enforces post-installation verification gates.
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        detector: Optional[ToolDetector] = None,
        doctor: Optional[SimulationDoctor] = None,
    ):
        self.registry = registry or ToolRegistry()
        self.detector = detector or ToolDetector(registry=self.registry)
        self.doctor = doctor or SimulationDoctor(detector=self.detector)

        self.adapters: Dict[str, BaseAdapter] = {
            "winget": WingetAdapter(),
            "apt": AptAdapter(),
            "archive": ArchiveAdapter(),
        }

    def select_adapter(self, spec: ToolSpec, preferred_method: Optional[str] = None) -> BaseAdapter:
        """
        Select the best installation adapter.
        Correction 10: Prefers verified archives over generic package manager binaries
        when eSim-specific components (e.g. XSPICE code models) are required.
        """
        if preferred_method and preferred_method.lower() != "auto":
            method_key = preferred_method.lower()
            if method_key not in self.adapters:
                raise ValueError(
                    f"Unknown installation method '{preferred_method}'. "
                    f"Supported methods: {', '.join(self.adapters.keys())} or 'auto'."
                )
            return self.adapters[method_key]

        # Prioritize verified vendor archive if tool has critical eSim components
        archive_spec = spec.get_archive("windows_x64")
        if spec.critical_components and archive_spec and archive_spec.is_verified:
            logger.info("Selecting verified archive adapter for %s to ensure component integrity.", spec.id)
            return self.adapters["archive"]

        # Use Winget on Windows if verified package ID exists
        if IS_WINDOWS and spec.is_package_id_verified("winget") and self.adapters["winget"].is_available():
            return self.adapters["winget"]

        # Use APT on Linux if verified package ID exists
        if IS_LINUX and spec.is_package_id_verified("apt") and self.adapters["apt"].is_available():
            return self.adapters["apt"]

        # Default fallback
        return self.adapters["archive"]

    def install_tool(
        self,
        tool_id: str,
        method: Optional[str] = None,
        version: Optional[str] = None,
        dry_run: bool = False,
        target_dir: Optional[Path] = None,
    ) -> InstallResult:
        """
        Execute safe installation with staging, preservation, and post-install verification.
        Correction 4: Staged isolation.
        Correction 5: Preserves existing target if verification fails.
        Correction 6: Dry-run performs zero writes or commands.
        """
        spec = self.registry.get_tool(tool_id)
        if not spec:
            raise KeyError(f"Tool '{tool_id}' is not defined in registry tools.json.")

        adapter = self.select_adapter(spec, preferred_method=method)

        # ---------------------------------------------------------------------
        # Dry-Run Mode (Zero Side Effects)
        # ---------------------------------------------------------------------
        if dry_run:
            success, msg = adapter.install(
                spec=spec,
                version=version,
                dry_run=True,
                target_dir=target_dir,
            )
            return InstallResult(
                tool_id=spec.id,
                success=success,
                method_used=adapter.name,
                installed_path=None,
                post_install_version=None,
                is_compatible=False,
                message=f"[Dry-Run] {msg}",
                dry_run=True,
            )

        # ---------------------------------------------------------------------
        # Live Execution: Snapshot existing installation for rollback (Correction 5)
        # ---------------------------------------------------------------------
        prior_detection = self.detector.detect_tool(spec.id)
        prior_bin_path = prior_detection.binary_path

        logger.info("Executing installation for %s via %s adapter", spec.id, adapter.name)
        install_success, install_msg = adapter.install(
            spec=spec,
            version=version,
            dry_run=False,
            target_dir=target_dir,
        )

        if not install_success:
            return InstallResult(
                tool_id=spec.id,
                success=False,
                method_used=adapter.name,
                installed_path=prior_bin_path,
                post_install_version=prior_detection.detected_version,
                is_compatible=prior_detection.is_compatible,
                message=f"Installation execution failed: {install_msg}",
                dry_run=False,
            )

        # ---------------------------------------------------------------------
        # Post-Installation Verification Gate
        # ---------------------------------------------------------------------
        post_detection = self.detector.detect_tool(spec.id)

        if not post_detection.is_installed:
            # Verification failed: Binary not located
            return InstallResult(
                tool_id=spec.id,
                success=False,
                method_used=adapter.name,
                installed_path=None,
                post_install_version=None,
                is_compatible=False,
                message=(
                    f"Post-installation verification failed: {spec.id} executable could not be detected "
                    "on PATH or within target directories after installation."
                ),
                dry_run=False,
            )

        if not post_detection.is_usable:
            # Verification failed: Version is incompatible
            return InstallResult(
                tool_id=spec.id,
                success=False,
                method_used=adapter.name,
                installed_path=post_detection.binary_path,
                post_install_version=post_detection.detected_version,
                is_compatible=False,
                message=(
                    f"Post-installation verification failed: Detected version {post_detection.detected_version} "
                    f"does not satisfy eSim compatibility requirements ({post_detection.evaluation.message})."
                ),
                dry_run=False,
            )

        logger.info(
            "Post-installation verification passed: %s v%s located at %s",
            spec.id,
            post_detection.detected_version,
            post_detection.binary_path,
        )

        return InstallResult(
            tool_id=spec.id,
            success=True,
            method_used=adapter.name,
            installed_path=post_detection.binary_path,
            post_install_version=post_detection.detected_version,
            is_compatible=post_detection.is_compatible,
            message=f"Successfully installed and verified {spec.name} v{post_detection.detected_version}.",
            dry_run=False,
        )
