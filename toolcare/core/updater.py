"""
toolcare.core.updater - Dedicated update engine with plan generation, interactive confirmation,
audit history logging, and automatic rollback on verification failure.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from toolcare.core.config_handler import ConfigHandler, atomic_write
from toolcare.core.dependency import DependencyGraph
from toolcare.core.detector import ToolDetector
from toolcare.core.installer import ToolInstaller
from toolcare.core.registry import ToolRegistry, ToolSpec
from toolcare.core.version import compare_versions
from toolcare.logger import get_logger
from toolcare.platform_utils import (
    IS_WINDOWS,
    get_esim_config_dir,
    get_nghdl_config_dir,
)

logger = get_logger("updater")


@dataclass(frozen=True)
class UpdatePlan:
    """Immutable pre-update execution plan."""
    tool_id: str
    current_version: Optional[str]
    target_version: str
    is_update_available: bool
    is_verified: bool
    method: str
    target_source: str
    backups_required: List[str]
    affected_workflows: List[str]
    actions: List[str]


@dataclass(frozen=True)
class UpdateResult:
    """Result of an update operation."""
    tool_id: str
    success: bool
    prior_version: Optional[str]
    new_version: Optional[str]
    message: str
    rolled_back: bool = False
    dry_run: bool = False


class UpdateEngine:
    """
    Update coordinator for eSim ToolCare.
    Generates non-destructive update plans, requires explicit user consent,
    enforces rollback upon failure, and maintains persistent update history.
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        detector: Optional[ToolDetector] = None,
        dep_graph: Optional[DependencyGraph] = None,
        config_handler: Optional[ConfigHandler] = None,
        installer: Optional[ToolInstaller] = None,
    ):
        self.registry = registry or ToolRegistry()
        self.detector = detector or ToolDetector(registry=self.registry)
        self.dep_graph = dep_graph or DependencyGraph()
        self.config_handler = config_handler or ConfigHandler()
        self.installer = installer or ToolInstaller(
            registry=self.registry,
            detector=self.detector,
        )
        self.history_file = get_esim_config_dir() / "logs" / "update_history.json"

    # -------------------------------------------------------------------------
    # Update Plan Generation (Correction 6: Rejection of Unverified Sources)
    # -------------------------------------------------------------------------
    def plan_update(self, tool_id: str) -> UpdatePlan:
        """
        Generate structured update plan without making any filesystem or network changes.
        """
        spec = self.registry.get_tool(tool_id)
        if not spec:
            raise KeyError(f"Tool '{tool_id}' is not defined in tools.json.")

        detection = self.detector.detect_tool(tool_id)
        current_ver = detection.detected_version
        target_ver = spec.compatibility.recommended_version

        # Trace affected simulation workflows
        affected_wf = self.dep_graph.get_dependent_workflows(tool_id)

        # Select adapter and determine verification status
        adapter = self.installer.select_adapter(spec)
        method_name = adapter.name

        target_source = "unknown"
        is_verified = False

        if method_name == "archive":
            arch_spec = spec.get_archive("windows_x64")
            if arch_spec and arch_spec.is_verified:
                target_source = arch_spec.url
                is_verified = True
            else:
                target_source = "UNVERIFIED archive specification"
        elif method_name == "winget":
            if spec.is_package_id_verified("winget"):
                target_source = f"winget:{spec.get_package_id('winget')}"
                is_verified = True
            else:
                target_source = "UNVERIFIED winget package ID"
        elif method_name == "apt":
            if spec.is_package_id_verified("apt"):
                target_source = f"apt:{spec.get_package_id('apt')}"
                is_verified = True
            else:
                target_source = "UNVERIFIED apt package ID"

        # Determine if an update is available
        is_update_available = False
        if not detection.is_installed:
            is_update_available = True
        elif current_ver:
            is_update_available = compare_versions(current_ver, target_ver) < 0

        # Gather relevant config files requiring backup
        backups: List[str] = []
        nghdl_ini = get_nghdl_config_dir() / "config.ini"
        if nghdl_ini.is_file():
            backups.append(str(nghdl_ini))

        kicad_table = ConfigHandler().esim_root
        home_sym = Path.home() / ".config" / "kicad" / "sym-lib-table"
        if home_sym.is_file():
            backups.append(str(home_sym))

        actions: List[str] = []
        if not is_verified:
            actions.append(
                f"REJECTED: Update source for '{tool_id}' is UNVERIFIED ({target_source}). "
                "ToolCare will not deploy unconfirmed upstream packages."
            )
        elif not is_update_available:
            actions.append(f"No update necessary. Current version ({current_ver}) satisfies recommended {target_ver}.")
        else:
            actions.append(f"1. Snapshot {len(backups)} configuration files to ~/.esim/backups/.")
            actions.append(f"2. Deploy {tool_id} v{target_ver} using {method_name} adapter.")
            actions.append(f"3. Execute post-installation verification check.")
            actions.append(f"4. Re-synchronize eSim configuration files.")

        return UpdatePlan(
            tool_id=spec.id,
            current_version=current_ver,
            target_version=target_ver,
            is_update_available=is_update_available and is_verified,
            is_verified=is_verified,
            method=method_name,
            target_source=target_source,
            backups_required=backups,
            affected_workflows=affected_wf,
            actions=actions,
        )

    def plan_all_updates(self) -> Dict[str, UpdatePlan]:
        """Generate update plans for all registered tools."""
        plans = {}
        for tool in self.registry.get_all_tools():
            plans[tool.id] = self.plan_update(tool.id)
        return plans

    # -------------------------------------------------------------------------
    # Live Update Execution & Interactive Confirmation (Corrections 7 & 8)
    # -------------------------------------------------------------------------
    def execute_update(
        self,
        tool_id: str,
        auto_confirm: bool = False,
        dry_run: bool = False,
    ) -> UpdateResult:
        """
        Execute an update following pre-flight checks, interactive confirmation,
        backup snapshotting, and automatic rollback upon verification failure.
        """
        plan = self.plan_update(tool_id)

        # Correction 10: Dry-run performs zero writes or commands
        if dry_run:
            preview_lines = [
                f"[Dry-Run] Update Plan for '{tool_id}':",
                f"  * Current Version   : {plan.current_version or 'NOT INSTALLED'}",
                f"  * Target Version    : {plan.target_version}",
                f"  * Method            : {plan.method}",
                f"  * Verified Source   : {plan.target_source}",
                f"  * Affected Flows    : {', '.join(plan.affected_workflows)}",
                f"  * Backups Required  : {len(plan.backups_required)} files",
            ]
            for act in plan.actions:
                preview_lines.append(f"  * Action: {act}")
            return UpdateResult(
                tool_id=tool_id,
                success=True,
                prior_version=plan.current_version,
                new_version=plan.target_version,
                message="\n".join(preview_lines),
                dry_run=True,
            )

        # Correction 6: Reject update if source or hash is unverified
        if not plan.is_verified:
            err_msg = (
                f"Update rejected for '{tool_id}': Target package ID, archive URL, or SHA-256 "
                f"hash is unverified ({plan.target_source}). Refusing unconfirmed update."
            )
            logger.error(err_msg)
            self._record_history(tool_id, plan.current_version, plan.target_version, "REJECTED_UNVERIFIED", err_msg)
            return UpdateResult(
                tool_id=tool_id,
                success=False,
                prior_version=plan.current_version,
                new_version=None,
                message=err_msg,
            )

        if not plan.is_update_available:
            return UpdateResult(
                tool_id=tool_id,
                success=True,
                prior_version=plan.current_version,
                new_version=plan.current_version,
                message=f"Tool '{tool_id}' is already up-to-date (v{plan.current_version}).",
            )

        # Correction 8: Interactive confirmation prompt (default is NO)
        if not auto_confirm:
            try:
                resp = input(
                    f"Proceed with updating {tool_id} ({plan.current_version or 'None'} -> {plan.target_version})? [y/N]: "
                )
                if resp.strip().lower() not in ("y", "yes"):
                    logger.info("Update cancelled by user for %s", tool_id)
                    return UpdateResult(
                        tool_id=tool_id,
                        success=False,
                        prior_version=plan.current_version,
                        new_version=None,
                        message="Update cancelled by user.",
                    )
            except (EOFError, KeyboardInterrupt):
                return UpdateResult(
                    tool_id=tool_id,
                    success=False,
                    prior_version=plan.current_version,
                    new_version=None,
                    message="Update interrupted.",
                )

        # Step 1: Pre-modification snapshot of configuration files (Correction 7)
        backup_paths = [Path(p) for p in plan.backups_required]
        ts_id, _ = self.config_handler.create_backup(backup_paths, label=f"pre_update_{tool_id}")

        # Step 2: Deploy new version via ToolInstaller
        install_res = self.installer.install_tool(
            tool_id=tool_id,
            method=plan.method,
            version=plan.target_version,
            dry_run=False,
        )

        # Step 3: Rollback on installation or verification failure (Correction 7)
        if not install_res.success:
            logger.error("Update failed post-check; triggering configuration rollback to %s", ts_id)
            self.config_handler.rollback(ts_id)
            fail_msg = f"Update failed: {install_res.message}. Rolled back configurations to snapshot {ts_id}."
            self._record_history(tool_id, plan.current_version, plan.target_version, "FAILED_ROLLED_BACK", fail_msg)
            return UpdateResult(
                tool_id=tool_id,
                success=False,
                prior_version=plan.current_version,
                new_version=None,
                message=fail_msg,
                rolled_back=True,
            )

        # Step 4: Record history upon success
        success_msg = f"Successfully updated and verified {tool_id} to v{install_res.post_install_version}."
        self._record_history(tool_id, plan.current_version, install_res.post_install_version, "SUCCESS", success_msg)
        return UpdateResult(
            tool_id=tool_id,
            success=True,
            prior_version=plan.current_version,
            new_version=install_res.post_install_version,
            message=success_msg,
        )

    # -------------------------------------------------------------------------
    # Persistent Update History Logging (~/.esim/logs/update_history.json)
    # -------------------------------------------------------------------------
    def _record_history(
        self,
        tool_id: str,
        prior_ver: Optional[str],
        target_ver: Optional[str],
        status: str,
        details: str,
    ) -> None:
        """Append update entry to update_history.json."""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        history: List[Dict[str, Any]] = []

        if self.history_file.is_file():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_id": tool_id,
            "prior_version": prior_ver,
            "target_version": target_ver,
            "status": status,
            "details": details,
        }
        history.append(entry)

        try:
            atomic_write(self.history_file, json.dumps(history, indent=2))
        except Exception as e:
            logger.warning("Could not record update history: %s", e)
