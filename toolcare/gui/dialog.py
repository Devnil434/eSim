"""
toolcare.gui.dialog - Full-featured, modern four-tab GUI dialog for eSim ToolCare.
Reuses existing core modules (Registry, Detector, Doctor, Installer, Updater, ConfigHandler)
without duplicating logic or creating blocking GUI operations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from toolcare import __version__
from toolcare.core.config_handler import ConfigHandler, ConfigSecurityError
from toolcare.core.dependency import (
    ALL_WORKFLOW_IDS,
    WORKFLOW_DCOSIM,
    WORKFLOW_NGHDL,
    WORKFLOW_NGVERI,
    WORKFLOW_SIMULATION,
    WORKFLOW_VERIFIER,
    DependencyGraph,
)
from toolcare.core.detector import ToolDetector
from toolcare.core.doctor import DoctorReport, Severity, SimulationDoctor
from toolcare.core.installer import ToolInstaller
from toolcare.core.registry import ToolRegistry
from toolcare.core.updater import UpdateEngine, UpdatePlan
from toolcare.core.version import ToolStatus
from toolcare.gui.qt_compat import (
    AlignmentCenter,
    AlignmentLeft,
    HeaderResizeContents,
    HeaderResizeStretch,
    MessageBoxNo,
    MessageBoxYes,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from toolcare.gui.worker import WorkerThread
from toolcare.logger import get_logger
from toolcare.platform_utils import get_esim_config_dir

logger = get_logger("gui.dialog")


class ToolCareDialog(QDialog):
    """
    Main graphical management dialog for eSim ToolCare.
    Provides 4 tabs:
      1. Toolchain Status (Tool detection, version checks, safe install/update)
      2. Simulation Doctor (5-stage audit, workflow readiness chips)
      3. Configuration & KiCad Libraries (sym-lib-table sync, verified backup rollback)
      4. Audit & Update Logs (Live log viewer, update history)
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"eSim ToolCare — Toolchain & Dependency Manager (v{__version__})")
        self.setMinimumSize(880, 620)
        self.resize(920, 650)

        # Core logic services
        self.registry = ToolRegistry()
        self.detector = ToolDetector(registry=self.registry)
        self.dep_graph = DependencyGraph()
        self.config_handler = ConfigHandler()
        self.installer = ToolInstaller(registry=self.registry, detector=self.detector)
        self.updater = UpdateEngine(
            registry=self.registry,
            detector=self.detector,
            dep_graph=self.dep_graph,
            config_handler=self.config_handler,
            installer=self.installer,
        )

        # Worker management
        self._active_worker: Optional[WorkerThread] = None

        # Build UI layout
        self._init_ui()

        # Initial data population
        self.refresh_tool_table()
        self.refresh_config_tab()
        self.refresh_logs_tab()

    # -------------------------------------------------------------------------
    # UI Layout Construction
    # -------------------------------------------------------------------------
    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # Tabs container
        self.tabs = QTabWidget(self)
        main_layout.addWidget(self.tabs)

        # Tab 1: Toolchain Status
        self.tab_status = QWidget()
        self._build_status_tab(self.tab_status)
        self.tabs.addTab(self.tab_status, "Toolchain Status")

        # Tab 2: Simulation Doctor
        self.tab_doctor = QWidget()
        self._build_doctor_tab(self.tab_doctor)
        self.tabs.addTab(self.tab_doctor, "Simulation Doctor")

        # Tab 3: Configuration & KiCad Libraries
        self.tab_config = QWidget()
        self._build_config_tab(self.tab_config)
        self.tabs.addTab(self.tab_config, "Configuration & KiCad")

        # Tab 4: Audit & Update Logs
        self.tab_logs = QWidget()
        self._build_logs_tab(self.tab_logs)
        self.tabs.addTab(self.tab_logs, "Audit Logs")

        # Status and progress bar at bottom
        bottom_box = QHBoxLayout()
        self.lbl_status = QLabel("Ready.", self)
        bottom_box.addWidget(self.lbl_status)
        bottom_box.addStretch()

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        bottom_box.addWidget(self.progress_bar)

        self.btn_close = QPushButton("Close", self)
        self.btn_close.clicked.connect(self.close)
        bottom_box.addWidget(self.btn_close)

        main_layout.addLayout(bottom_box)

    # -------------------------------------------------------------------------
    # Tab 1: Toolchain Status
    # -------------------------------------------------------------------------
    def _build_status_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)

        desc_label = QLabel(
            "Manage core simulation tools, compilers, and dependencies required by eSim. "
            "Updates and installations verify upstream checksums and preserve existing configurations.",
            parent,
        )
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        # Tools Table
        self.tools_table = QTableWidget(0, 7, parent)
        self.tools_table.setHorizontalHeaderLabels([
            "Tool ID", "Tool Name", "Category", "Installed Ver", "Recommended", "Status", "Update Avail"
        ])
        header = self.tools_table.horizontalHeader()
        header.setSectionResizeMode(1, HeaderResizeStretch)
        for c in (0, 2, 3, 4, 5, 6):
            header.setSectionResizeMode(c, HeaderResizeContents)
        layout.addWidget(self.tools_table)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.btn_check_updates = QPushButton("Check Upstream Updates", parent)
        self.btn_check_updates.clicked.connect(self.action_check_updates)
        btn_layout.addWidget(self.btn_check_updates)

        self.btn_update_tool = QPushButton("Update Selected Tool", parent)
        self.btn_update_tool.clicked.connect(self.action_update_selected_tool)
        btn_layout.addWidget(self.btn_update_tool)

        self.btn_install_tool = QPushButton("Install Selected Tool", parent)
        self.btn_install_tool.clicked.connect(self.action_install_selected_tool)
        btn_layout.addWidget(self.btn_install_tool)

        btn_layout.addStretch()
        self.btn_refresh_status = QPushButton("Refresh Status", parent)
        self.btn_refresh_status.clicked.connect(self.refresh_tool_table)
        btn_layout.addWidget(self.btn_refresh_status)

        layout.addLayout(btn_layout)

    def refresh_tool_table(self):
        """Populate tools dynamically from registry and detector."""
        tools = self.registry.get_all_tools()
        self.tools_table.setRowCount(len(tools))

        for row, t in enumerate(tools):
            det = self.detector.detect_tool(t.id)
            plan = self.updater.plan_update(t.id)

            status_text = det.evaluation.status.value if det.evaluation else "UNKNOWN"
            update_text = "YES" if plan.is_update_available else "NO"

            items = [
                QTableWidgetItem(t.id),
                QTableWidgetItem(t.name),
                QTableWidgetItem(t.category),
                QTableWidgetItem(det.detected_version or "None"),
                QTableWidgetItem(t.compatibility.recommended_version),
                QTableWidgetItem(status_text),
                QTableWidgetItem(update_text),
            ]
            for col, item in enumerate(items):
                if col in (0, 2, 3, 4, 5, 6):
                    item.setTextAlignment(AlignmentCenter)
                self.tools_table.setItem(row, col, item)

    def _get_selected_tool_id(self) -> Optional[str]:
        cur_row = self.tools_table.currentRow()
        if cur_row < 0:
            return None
        item = self.tools_table.item(cur_row, 0)
        return item.text() if item else None

    # -------------------------------------------------------------------------
    # Tab 2: Simulation Doctor
    # -------------------------------------------------------------------------
    def _build_doctor_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)

        # Top section: Workflow Readiness Chips
        wf_group = QGroupBox("Simulation Workflow Readiness", parent)
        wf_layout = QHBoxLayout(wf_group)

        self.wf_labels: Dict[str, QLabel] = {}
        for wf_id in ALL_WORKFLOW_IDS:
            lbl = QLabel(f"{wf_id}: [UNKNOWN]", wf_group)
            lbl.setAlignment(AlignmentCenter)
            self.wf_labels[wf_id] = lbl
            wf_layout.addWidget(lbl)

        layout.addWidget(wf_group)

        # Doctor Checks Table (Displays all 12 checks with real Severity, Result, Details, Remediation)
        self.doctor_table = QTableWidget(0, 6, parent)
        self.doctor_table.setHorizontalHeaderLabels([
            "Stage", "Diagnostic Check", "Result", "Severity", "Details", "Remediation"
        ])
        header = self.doctor_table.horizontalHeader()
        header.setSectionResizeMode(0, HeaderResizeContents)
        header.setSectionResizeMode(1, HeaderResizeContents)
        header.setSectionResizeMode(2, HeaderResizeContents)
        header.setSectionResizeMode(3, HeaderResizeContents)
        header.setSectionResizeMode(4, HeaderResizeStretch)
        header.setSectionResizeMode(5, HeaderResizeStretch)
        layout.addWidget(self.doctor_table)

        # Retain doctor_tree for stage-hierarchical view and backward compatibility
        self.doctor_tree = QTreeWidget(parent)
        self.doctor_tree.setHeaderLabels(["Diagnostic Check", "Result", "Severity", "Details & Remediation"])
        self.doctor_tree.setVisible(False)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_run_doctor = QPushButton("Run Diagnostics Audit", parent)
        self.btn_run_doctor.clicked.connect(self.action_run_doctor)
        btn_layout.addWidget(self.btn_run_doctor)
        btn_layout.addStretch()

        layout.addLayout(btn_layout)

    def render_doctor_report(self, report: Optional[DoctorReport]):
        """
        Render doctor report into workflow badges, diagnostic table, and tree widget.
        Keeps all widget updates strictly on the GUI thread.
        """
        if report is None:
            logger.warning("render_doctor_report called with None. Preserving UNKNOWN workflow states.")
            for wf_id in ALL_WORKFLOW_IDS:
                if wf_id in self.wf_labels:
                    self.wf_labels[wf_id].setText(f"{wf_id}: [UNKNOWN]")
            self.doctor_table.setRowCount(0)
            self.doctor_tree.clear()
            return

        if not hasattr(report, "workflow_readiness") or not hasattr(report, "items"):
            raise ValueError("Malformed DoctorReport provided: missing workflow_readiness or items attribute.")

        # Correction 7: Debug logging for result counts and workflow readiness values
        logger.debug(
            "Rendering Doctor report: %d total checks (%d passed, %d failed). Workflow readiness evaluated for %d workflows.",
            report.total_checks,
            report.passed_checks,
            report.failed_checks,
            len(report.workflow_readiness),
        )

        # 1. Update workflow readiness badges
        for wf_id in ALL_WORKFLOW_IDS:
            lbl = self.wf_labels.get(wf_id)
            if not lbl:
                continue
            status = report.workflow_readiness.get(wf_id)
            if status is None:
                # Correction 4: Preserve UNKNOWN only when underlying status genuinely cannot be determined
                lbl.setText(f"{wf_id}: [UNKNOWN]")
                lbl.setToolTip("Workflow status could not be determined.")
                logger.debug("Workflow '%s' readiness: UNKNOWN", wf_id)
            elif status.is_runnable:
                if status.missing_optional_tools:
                    missing_opt = ", ".join(status.missing_optional_tools)
                    lbl.setText(f"{wf_id}: DEGRADED")
                    lbl.setToolTip(f"Workflow '{status.workflow_name}' is runnable, but optional tools are missing ({missing_opt}).")
                    logger.debug("Workflow '%s' readiness: DEGRADED (missing optional: %s)", wf_id, missing_opt)
                else:
                    lbl.setText(f"{wf_id}: HEALTHY [READY]")
                    lbl.setToolTip(f"Workflow '{status.workflow_name}' is fully runnable.")
                    logger.debug("Workflow '%s' readiness: HEALTHY [READY]", wf_id)
            else:
                reasons = "; ".join(status.diagnostic_messages) if status.diagnostic_messages else "Dependencies unsatisfied."
                lbl.setText(f"{wf_id}: BLOCKED")
                lbl.setToolTip(f"Blocked: {reasons}")
                logger.debug("Workflow '%s' readiness: BLOCKED (%s)", wf_id, reasons)

        # 2. Update diagnostic results table (Correction 1 & 2: Display all checks with real severity and remediation)
        self.doctor_table.setRowCount(len(report.items))
        for row, it in enumerate(report.items):
            res_str = "PASS" if it.passed else "FAIL"
            sev_str = it.severity.value if hasattr(it.severity, "value") else str(it.severity)
            remed_str = it.remediation or "None"

            item_stage = QTableWidgetItem(it.stage)
            item_check = QTableWidgetItem(it.check_name)
            item_result = QTableWidgetItem(res_str)
            item_sev = QTableWidgetItem(sev_str)
            item_details = QTableWidgetItem(it.details)
            item_remed = QTableWidgetItem(remed_str)

            item_result.setTextAlignment(AlignmentCenter)
            item_sev.setTextAlignment(AlignmentCenter)

            self.doctor_table.setItem(row, 0, item_stage)
            self.doctor_table.setItem(row, 1, item_check)
            self.doctor_table.setItem(row, 2, item_result)
            self.doctor_table.setItem(row, 3, item_sev)
            self.doctor_table.setItem(row, 4, item_details)
            self.doctor_table.setItem(row, 5, item_remed)

        # 3. Update tree widget grouped by stage
        self.doctor_tree.clear()
        stages: Dict[str, List] = {}
        for it in report.items:
            stages.setdefault(it.stage, []).append(it)

        for stage_name, stage_items in stages.items():
            stage_node = QTreeWidgetItem([stage_name, "", "", ""])
            for it in stage_items:
                res_str = "PASS" if it.passed else "FAIL"
                sev_str = it.severity.value if hasattr(it.severity, "value") else str(it.severity)
                details = it.details
                if not it.passed and it.remediation:
                    details += f" | Remediation: {it.remediation}"
                check_node = QTreeWidgetItem([it.check_name, res_str, sev_str, details])
                stage_node.addChild(check_node)
            self.doctor_tree.addTopLevelItem(stage_node)

        self.doctor_tree.expandAll()

    # -------------------------------------------------------------------------
    # Tab 3: Configuration & KiCad Libraries
    # -------------------------------------------------------------------------
    def _build_config_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)

        # KiCad & Ngspice Integration Status
        info_group = QGroupBox("eSim Symbol & Integration Settings", parent)
        info_layout = QVBoxLayout(info_group)

        self.lbl_kicad_table = QLabel("KiCad sym-lib-table: Not checked", info_group)
        info_layout.addWidget(self.lbl_kicad_table)

        self.lbl_kicad_symbols = QLabel("eSim Symbol Libraries Registered: 0", info_group)
        info_layout.addWidget(self.lbl_kicad_symbols)

        btn_sync_layout = QHBoxLayout()
        self.btn_sync_kicad = QPushButton("Synchronize KiCad eSim Symbols", info_group)
        self.btn_sync_kicad.clicked.connect(self.action_sync_kicad)
        btn_sync_layout.addWidget(self.btn_sync_kicad)
        btn_sync_layout.addStretch()
        info_layout.addLayout(btn_sync_layout)

        layout.addWidget(info_group)

        # Backups & Rollback Section
        backup_group = QGroupBox("Configuration Snapshots & Rollback", parent)
        backup_layout = QVBoxLayout(backup_group)

        backup_desc = QLabel(
            "Every update or sync operation snapshots configurations with cryptographic SHA-256 manifests. "
            "Rollbacks verify snapshot integrity before restoring any files.",
            backup_group,
        )
        backup_desc.setWordWrap(True)
        backup_layout.addWidget(backup_desc)

        self.backup_table = QTableWidget(0, 4, backup_group)
        self.backup_table.setHorizontalHeaderLabels(["Snapshot ID", "Label", "Files Count", "Integrity Status"])
        header = self.backup_table.horizontalHeader()
        header.setSectionResizeMode(0, HeaderResizeStretch)
        header.setSectionResizeMode(1, HeaderResizeStretch)
        header.setSectionResizeMode(2, HeaderResizeContents)
        header.setSectionResizeMode(3, HeaderResizeContents)
        backup_layout.addWidget(self.backup_table)

        b_btn_layout = QHBoxLayout()
        self.btn_rollback = QPushButton("Rollback to Selected Snapshot", backup_group)
        self.btn_rollback.clicked.connect(self.action_rollback_snapshot)
        b_btn_layout.addWidget(self.btn_rollback)

        b_btn_layout.addStretch()
        self.btn_refresh_backups = QPushButton("Refresh Snapshots", backup_group)
        self.btn_refresh_backups.clicked.connect(self.refresh_config_tab)
        b_btn_layout.addWidget(self.btn_refresh_backups)

        backup_layout.addLayout(b_btn_layout)
        layout.addWidget(backup_group)

    def refresh_config_tab(self):
        """Inspect KiCad sym-lib-table and enumerate backup snapshots."""
        kicad_table = self.config_handler.get_kicad_sym_lib_table_path()
        if kicad_table and kicad_table.is_file():
            self.lbl_kicad_table.setText(f"KiCad sym-lib-table: {kicad_table}")
            try:
                content = kicad_table.read_text(encoding="utf-8", errors="replace")
                esim_count = content.count('(name "eSim_')
                self.lbl_kicad_symbols.setText(f"eSim Symbol Libraries Registered: {esim_count}")
            except Exception:
                self.lbl_kicad_symbols.setText("eSim Symbol Libraries Registered: Error reading table")
        else:
            self.lbl_kicad_table.setText("KiCad sym-lib-table: Not found in standard locations")
            self.lbl_kicad_symbols.setText("eSim Symbol Libraries Registered: 0")

        # Enumerate backup snapshots
        backups_dir = self.config_handler.backups_dir
        snapshots: List[Path] = []
        if backups_dir.is_dir():
            snapshots = sorted([p for p in backups_dir.iterdir() if p.is_dir()], reverse=True)

        self.backup_table.setRowCount(len(snapshots))
        for row, s in enumerate(snapshots):
            manifest_file = s / "manifest.json"
            label = "None"
            f_count = "0"
            integrity = "NO MANIFEST"

            if manifest_file.is_file():
                try:
                    data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    label = data.get("label", "") or "unlabeled"
                    files = data.get("files", [])
                    f_count = str(len(files))
                    integrity = "VALID MANIFEST"
                except Exception:
                    integrity = "CORRUPT MANIFEST"

            self.backup_table.setItem(row, 0, QTableWidgetItem(s.name))
            self.backup_table.setItem(row, 1, QTableWidgetItem(label))
            self.backup_table.setItem(row, 2, QTableWidgetItem(f_count))
            self.backup_table.setItem(row, 3, QTableWidgetItem(integrity))

    # -------------------------------------------------------------------------
    # Tab 4: Audit & Update Logs
    # -------------------------------------------------------------------------
    def _build_logs_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)

        desc = QLabel("Persistent audit logs and update history (~/.esim/logs/):", parent)
        layout.addWidget(desc)

        self.log_text = QTextEdit(parent)
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        btn_layout = QHBoxLayout()
        self.btn_refresh_logs = QPushButton("Refresh Logs", parent)
        self.btn_refresh_logs.clicked.connect(self.refresh_logs_tab)
        btn_layout.addWidget(self.btn_refresh_logs)

        self.btn_clear_view = QPushButton("Clear View", parent)
        self.btn_clear_view.clicked.connect(self.action_clear_log_view)
        btn_layout.addWidget(self.btn_clear_view)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def refresh_logs_tab(self):
        """Read recent entries from toolcare.log and update_history.json."""
        lines = []
        log_file = get_esim_config_dir() / "logs" / "toolcare.log"
        if log_file.is_file():
            try:
                content = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                lines.extend(content[-150:])
            except Exception as e:
                lines.append(f"Error reading toolcare.log: {e}")

        history_file = get_esim_config_dir() / "logs" / "update_history.json"
        if history_file.is_file():
            try:
                hist_data = json.loads(history_file.read_text(encoding="utf-8"))
                lines.append("\n--- Recent Update History ---")
                for entry in hist_data[-10:]:
                    lines.append(
                        f"[{entry.get('timestamp')}] Tool: {entry.get('tool_id')} | "
                        f"{entry.get('prior_version')} -> {entry.get('target_version')} | "
                        f"Status: {entry.get('status')} | {entry.get('details')}"
                    )
            except Exception as e:
                lines.append(f"Error reading update_history.json: {e}")

        self.log_text.setPlainText("\n".join(lines) if lines else "No logs recorded yet.")

    def action_clear_log_view(self):
        """Clear GUI display text without modifying persistent log files on disk."""
        self.log_text.clear()
        self.lbl_status.setText("Log display cleared (persistent logs on disk remain intact).")

    # -------------------------------------------------------------------------
    # Asynchronous Actions & Worker Dispatch
    # -------------------------------------------------------------------------
    def _start_worker(
        self,
        task_name: str,
        task_func,
        *args,
        on_result: Optional[Callable[[Any], None]] = None,
        on_finished: Optional[Callable[[bool, str], None]] = None,
        **kwargs,
    ) -> bool:
        """
        Safely start a background worker, preventing concurrent operations.
        Requirement 4: Prevent multiple conflicting install/update/config operations.
        """
        if self._active_worker and self._active_worker.is_running():
            QMessageBox.warning(
                self,
                "Operation in Progress",
                f"Cannot start '{task_name}': A background task ('{self._active_worker.task_name}') is currently executing.",
            )
            return False

        self._set_ui_busy(True, task_name)

        worker = WorkerThread(task_name, task_func, *args, parent=self, **kwargs)
        self._active_worker = worker

        # Connect signals BEFORE starting thread
        worker.started_task.connect(self._on_worker_started)
        worker.progress.connect(self._on_worker_progress)
        worker.log_message.connect(self._on_worker_log)

        if on_result:
            worker.result_ready.connect(on_result)

        def handle_finished(success: bool, msg: str):
            try:
                if on_finished:
                    on_finished(success, msg)
            finally:
                self._on_worker_finished(success, msg)

        worker.finished_task.connect(handle_finished)

        worker.start()
        return True

    def _set_ui_busy(self, busy: bool, task_name: str = ""):
        """Enable or disable interactive action buttons while a task runs."""
        self.btn_check_updates.setEnabled(not busy)
        self.btn_update_tool.setEnabled(not busy)
        self.btn_install_tool.setEnabled(not busy)
        self.btn_run_doctor.setEnabled(not busy)
        self.btn_sync_kicad.setEnabled(not busy)
        self.btn_rollback.setEnabled(not busy)

        if busy:
            self.lbl_status.setText(f"Executing: {task_name}...")
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)  # Indeterminate progress
        else:
            self.progress_bar.setVisible(False)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)

    def _on_worker_started(self, task_name: str):
        self.lbl_status.setText(f"Started: {task_name}...")

    def _on_worker_progress(self, percent: int, msg: str):
        if percent >= 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percent)
        else:
            self.progress_bar.setRange(0, 0)
        if msg:
            self.lbl_status.setText(msg)

    def _on_worker_log(self, message: str):
        self.log_text.append(message)

    def _on_worker_finished(self, success: bool, message: str):
        self._set_ui_busy(False)
        self._active_worker = None

        if success:
            self.lbl_status.setText(f"Completed: {message}")
        else:
            self.lbl_status.setText(f"Error: {message}")
            QMessageBox.critical(self, "Operation Failed", message)

        # Refresh relevant tabs
        self.refresh_tool_table()
        self.refresh_config_tab()

    # -------------------------------------------------------------------------
    # Action Handlers
    # -------------------------------------------------------------------------
    def action_check_updates(self):
        """Non-destructive update scan across all registered tools."""
        def check_task(worker: WorkerThread):
            worker.log("Scanning registered tools for upstream updates...")
            plans = self.updater.plan_all_updates()
            avail = [p for p in plans.values() if p.is_update_available]
            return f"Found {len(avail)} updates available out of {len(plans)} registered tools."

        self._start_worker("Check Upstream Updates", check_task)

    def action_update_selected_tool(self):
        """Update selected tool with explicit confirmation modal (default No)."""
        tool_id = self._get_selected_tool_id()
        if not tool_id:
            QMessageBox.information(self, "No Selection", "Please select a tool from the table to update.")
            return

        plan = self.updater.plan_update(tool_id)

        # Check verification status
        if not plan.is_verified:
            QMessageBox.critical(
                self,
                "Update Rejected",
                f"Update rejected for '{tool_id}': Target package ID or archive checksum is unverified.\n"
                "ToolCare refuses unconfirmed upstream sources.",
            )
            return

        if not plan.is_update_available:
            QMessageBox.information(
                self,
                "Up to Date",
                f"Tool '{tool_id}' is already at the recommended version (v{plan.current_version}).",
            )
            return

        # Explicit confirmation modal (Correction 8: Default answer No)
        confirm = QMessageBox.question(
            self,
            f"Confirm Update: {tool_id}",
            f"Update {tool_id} from {plan.current_version or 'None'} to v{plan.target_version}?\n\n"
            f"Affected simulation workflows: {', '.join(plan.affected_workflows)}\n"
            f"Verified source: {plan.target_source}\n\n"
            "Proceed with installation?",
            MessageBoxYes | MessageBoxNo,
            defaultButton=MessageBoxNo,
        )
        if confirm != MessageBoxYes:
            self.lbl_status.setText(f"Update of '{tool_id}' cancelled by user.")
            return

        def update_task(worker: WorkerThread):
            worker.log(f"Starting update for '{tool_id}' to v{plan.target_version}...")
            res = self.updater.execute_update(tool_id, auto_confirm=True, dry_run=False)
            if not res.success:
                raise RuntimeError(res.message)
            return res.message

        self._start_worker(f"Update {tool_id}", update_task)

    def action_install_selected_tool(self):
        """Install selected tool with explicit confirmation modal (default No)."""
        tool_id = self._get_selected_tool_id()
        if not tool_id:
            QMessageBox.information(self, "No Selection", "Please select a tool from the table to install.")
            return

        spec = self.registry.get_tool(tool_id)
        if not spec:
            return

        confirm = QMessageBox.question(
            self,
            f"Confirm Installation: {tool_id}",
            f"Install {spec.name} ({tool_id}) version {spec.compatibility.recommended_version}?\n\n"
            "Installation will verify package integrity before deploying to ~/.esim/tools/.",
            MessageBoxYes | MessageBoxNo,
            defaultButton=MessageBoxNo,
        )
        if confirm != MessageBoxYes:
            return

        def install_task(worker: WorkerThread):
            worker.log(f"Installing {tool_id}...")
            res = self.installer.install_tool(tool_id, method="auto", dry_run=False)
            if not res.success:
                raise RuntimeError(res.message)
            return res.message

        self._start_worker(f"Install {tool_id}", install_task)

    def action_run_doctor(self):
        """Asynchronously run 5-stage simulation doctor audit."""
        def doctor_task(worker: WorkerThread) -> DoctorReport:
            worker.log("Running comprehensive 5-stage health diagnostic audit...")
            doc = SimulationDoctor(detector=self.detector, dep_graph=self.dep_graph)
            report = doc.run_diagnostics()
            logger.debug(
                "Doctor audit complete: %d checks generated, %d workflows evaluated.",
                report.total_checks,
                len(report.workflow_readiness),
            )
            return report

        def handle_doctor_result(report: Any):
            logger.debug("handle_doctor_result called on GUI thread with object: %s", type(report))
            if isinstance(report, DoctorReport):
                self.render_doctor_report(report)
            elif report is not None:
                logger.warning("Unexpected doctor result type received: %s", type(report))

        self._start_worker(
            "Simulation Health Doctor",
            doctor_task,
            on_result=handle_doctor_result,
        )

    def action_sync_kicad(self):
        """Synchronize KiCad symbol library table with verified eSim libraries."""
        def sync_task(worker: WorkerThread):
            worker.log("Synchronizing KiCad sym-lib-table with eSim libraries...")
            kicad_table = self.config_handler.get_kicad_sym_lib_table_path()
            if not kicad_table:
                raise FileNotFoundError("KiCad sym-lib-table not detected in user profile.")
            count = self.config_handler.sync_kicad_sym_lib_table(kicad_table)
            return f"Successfully synchronized {count} eSim symbol libraries."

        self._start_worker("Sync KiCad Symbols", sync_task)

    def action_rollback_snapshot(self):
        """Rollback configuration to selected snapshot with integrity verification."""
        cur_row = self.backup_table.currentRow()
        if cur_row < 0:
            QMessageBox.information(self, "No Snapshot Selected", "Please select a backup snapshot from the table.")
            return

        snapshot_id_item = self.backup_table.item(cur_row, 0)
        if not snapshot_id_item:
            return
        snapshot_id = snapshot_id_item.text()

        # Pre-inspect manifest to display affected files
        manifest_file = self.config_handler.backups_dir / snapshot_id / "manifest.json"
        if not manifest_file.is_file():
            QMessageBox.critical(self, "Invalid Snapshot", f"Snapshot '{snapshot_id}' does not contain a valid manifest.")
            return

        try:
            m_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            affected_files = [f.get("original_path") for f in m_data.get("files", [])]
        except Exception as e:
            QMessageBox.critical(self, "Corrupted Manifest", f"Failed to read snapshot manifest: {e}")
            return

        files_list_str = "\n".join(f"  * {f}" for f in affected_files)
        confirm = QMessageBox.question(
            self,
            f"Confirm Rollback: {snapshot_id}",
            f"Restore configurations from snapshot '{snapshot_id}'?\n\n"
            f"Files to be restored:\n{files_list_str}\n\n"
            "All snapshot files will be verified with SHA-256 before restoration.\n"
            "Proceed with rollback?",
            MessageBoxYes | MessageBoxNo,
            defaultButton=MessageBoxNo,
        )
        if confirm != MessageBoxYes:
            return

        def rollback_task(worker: WorkerThread):
            worker.log(f"Verifying integrity and restoring files from snapshot {snapshot_id}...")
            restored = self.config_handler.rollback(snapshot_id)
            return f"Restored {len(restored)} configuration files cleanly."

        self._start_worker(f"Rollback {snapshot_id}", rollback_task)

    # -------------------------------------------------------------------------
    # Dialog Closure Safety (Requirement 4)
    # -------------------------------------------------------------------------
    def closeEvent(self, event):
        """
        Handle dialog closure while worker is running without crashing or destroying active QThread.
        """
        if self._active_worker and self._active_worker.is_running():
            confirm = QMessageBox.question(
                self,
                "Task Running",
                f"A background task ('{self._active_worker.task_name}') is currently executing.\n"
                "Closing the window will cancel the task. Do you want to cancel and exit?",
                MessageBoxYes | MessageBoxNo,
                defaultButton=MessageBoxNo,
            )
            if confirm == MessageBoxYes:
                self._active_worker.cancel()
                self._active_worker.wait(500)
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
