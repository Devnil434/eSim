"""
Tests for toolcare.gui (Phase 5).
Validates:
  1. Dialog construction and all four tabs.
  2. Dynamic tool table population from the registry.
  3. Doctor workflow status rendering.
  4. Worker success and progress signals.
  5. Worker exception handling.
  6. Dialog closure while a worker is running.
  7. Prevention of concurrent conflicting operations.
  8. Missing PyQt graceful fallback.
  9. Menu hook behavior with None and unsupported objects.
  10. Duplicate menu-hook attachment prevention (idempotency).
  11. Rollback confirmation and rejection of corrupted snapshots.
  12. Clear view does not delete persistent disk logs.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Ensure mock/offscreen Qt is active for headless test environment
os.environ["TOOLCARE_TEST_MOCK_QT"] = "1"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from toolcare.core.config_handler import ConfigHandler, atomic_write
from toolcare.core.dependency import ALL_WORKFLOW_IDS, WorkflowStatus
from toolcare.core.doctor import DoctorCheckItem, DoctorReport, Severity
from toolcare.gui import attach_toolcare_to_menu, launch_gui
from toolcare.gui.dialog import ToolCareDialog
from toolcare.gui.qt_compat import (
    QAction,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    _CloseEvent,
    is_qt_available,
)
from toolcare.gui.worker import WorkerThread


@pytest.fixture
def gui_dialog(tmp_path: Path):
    """Fixture providing a fresh ToolCareDialog with redirected config directories."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    dlg = ToolCareDialog()
    dlg.config_handler.backups_dir = backup_dir
    dlg.updater.config_handler = dlg.config_handler
    return dlg, tmp_path, backup_dir


# -----------------------------------------------------------------------------
# 1. Dialog Construction and Tabs
# -----------------------------------------------------------------------------
def test_dialog_construction_and_four_tabs(gui_dialog):
    dlg, _, _ = gui_dialog
    assert "eSim ToolCare" in dlg.windowTitle()
    assert dlg.tabs.count() == 4
    tab_names = [dlg.tabs.tabText(i) for i in range(4)]
    assert "Toolchain Status" in tab_names
    assert "Simulation Doctor" in tab_names
    assert "Configuration & KiCad" in tab_names
    assert "Audit Logs" in tab_names


# -----------------------------------------------------------------------------
# 2. Dynamic Tool Table Population
# -----------------------------------------------------------------------------
def test_dynamic_tool_table_population(gui_dialog):
    dlg, _, _ = gui_dialog
    assert dlg.tools_table.rowCount() == 6

    # Verify tool IDs are present
    table_tool_ids = [dlg.tools_table.item(r, 0).text() for r in range(dlg.tools_table.rowCount())]
    for expected_id in ["ngspice", "kicad", "iverilog", "ghdl", "verilator", "llvm"]:
        assert expected_id in table_tool_ids


# -----------------------------------------------------------------------------
# 3. Doctor Workflow Status Rendering
# -----------------------------------------------------------------------------
def test_doctor_workflow_status_rendering(gui_dialog):
    dlg, _, _ = gui_dialog

    mock_wf = {
        "simulation": WorkflowStatus(
            workflow_id="simulation",
            workflow_name="Analog Simulation",
            is_runnable=True,
            missing_mandatory_tools=[],
            missing_optional_tools=[],
            incompatible_tools=[],
            missing_critical_components=[],
            diagnostic_messages=[],
        ),
        "d_cosim": WorkflowStatus(
            workflow_id="d_cosim",
            workflow_name="Digital/Mixed-Signal Co-Simulation (d_cosim)",
            is_runnable=False,
            missing_mandatory_tools=["iverilog"],
            missing_optional_tools=[],
            incompatible_tools=[],
            missing_critical_components=[],
            diagnostic_messages=["Mandatory tool 'iverilog' is not installed."],
        ),
    }

    mock_items = [
        DoctorCheckItem(
            stage="Stage 1: Host Environment",
            check_name="Python Runtime",
            passed=True,
            severity=Severity.INFO,
            details="Python 3.14.3",
        ),
        DoctorCheckItem(
            stage="Stage 2: Analog Simulation",
            check_name="Ngspice Executable",
            passed=False,
            severity=Severity.CRITICAL,
            details="Ngspice binary not found",
            remediation="Install Ngspice 45.2",
        ),
    ]

    mock_report = DoctorReport(
        items=mock_items,
        overall_health=False,
        has_critical=True,
        total_checks=2,
        passed_checks=1,
        failed_checks=1,
        workflow_readiness=mock_wf,
        verified_components={},
    )

    dlg.render_doctor_report(mock_report)

    # Check workflow readiness labels
    assert "READY" in dlg.wf_labels["simulation"].text()
    assert "BLOCKED" in dlg.wf_labels["d_cosim"].text()

    # Check doctor tree items
    assert dlg.doctor_tree.topLevelItemCount() == 2


# -----------------------------------------------------------------------------
# 4. Worker Success and Progress Signals
# -----------------------------------------------------------------------------
def test_worker_success_and_progress_signals():
    events = []

    def sample_task(worker: WorkerThread):
        worker.report_progress(50, "Halfway done")
        worker.log("Executing unit operation")
        return "Task Completed"

    worker = WorkerThread("test_task", sample_task)
    worker.started_task.connect(lambda name: events.append(("started", name)))
    worker.progress.connect(lambda pct, msg: events.append(("progress", pct, msg)))
    worker.log_message.connect(lambda msg: events.append(("log", msg)))
    worker.finished_task.connect(lambda ok, msg: events.append(("finished", ok, msg)))

    worker.start()

    assert any(e[0] == "started" and e[1] == "test_task" for e in events)
    assert any(e[0] == "progress" and e[1] == 50 and e[2] == "Halfway done" for e in events)
    assert any(e[0] == "log" and "Executing unit operation" in e[1] for e in events)
    assert any(e[0] == "finished" and e[1] is True and "Task Completed" in e[2] for e in events)


# -----------------------------------------------------------------------------
# 5. Worker Exception Handling
# -----------------------------------------------------------------------------
def test_worker_exception_handling():
    finished_events = []

    def failing_task(worker: WorkerThread):
        raise ValueError("Simulated network timeout")

    worker = WorkerThread("fail_task", failing_task)
    worker.finished_task.connect(lambda ok, msg: finished_events.append((ok, msg)))

    worker.start()

    assert len(finished_events) == 1
    assert finished_events[0][0] is False
    assert "Simulated network timeout" in finished_events[0][1]


# -----------------------------------------------------------------------------
# 6. Dialog Closure While Worker is Active
# -----------------------------------------------------------------------------
def test_dialog_closure_while_worker_running(gui_dialog):
    dlg, _, _ = gui_dialog

    # Simulate active background worker
    worker = WorkerThread("long_task", lambda w: None)
    worker._is_running = True
    dlg._active_worker = worker

    # User declines cancellation
    QMessageBox.set_mock_response(QMessageBox.No)
    event_no = _CloseEvent()
    dlg.closeEvent(event_no)
    assert not event_no.isAccepted()
    assert worker.is_running()

    # User accepts cancellation
    QMessageBox.set_mock_response(QMessageBox.Yes)
    event_yes = _CloseEvent()
    dlg.closeEvent(event_yes)
    assert event_yes.isAccepted()
    assert worker.is_cancelled()


# -----------------------------------------------------------------------------
# 7. Prevention of Concurrent Conflicting Operations
# -----------------------------------------------------------------------------
def test_prevention_of_concurrent_conflicting_operations(gui_dialog):
    dlg, _, _ = gui_dialog

    # Set mock worker running
    mock_worker = MagicMock()
    mock_worker.is_running.return_value = True
    mock_worker.task_name = "active_install"
    dlg._active_worker = mock_worker

    # Attempt to start another worker must fail
    started = dlg._start_worker("conflicting_task", lambda w: None)
    assert started is False


# -----------------------------------------------------------------------------
# 8. Missing PyQt Graceful Fallback
# -----------------------------------------------------------------------------
def test_missing_pyqt_graceful_fallback(monkeypatch, capsys):
    # Simulate PyQt not available
    monkeypatch.setattr("toolcare.gui.is_qt_available", lambda: False)

    code = launch_gui()
    assert code == 1
    captured = capsys.readouterr()
    assert "PyQt (PyQt5 or PyQt6) is not installed" in captured.err
    assert "pip install PyQt5" in captured.err


# -----------------------------------------------------------------------------
# 9. Menu Hook with None and Unsupported Objects
# -----------------------------------------------------------------------------
def test_menu_hook_behavior_with_none_and_unsupported_objects():
    assert attach_toolcare_to_menu(None) is False
    assert attach_toolcare_to_menu(object()) is False

    class DummyWindowNoMenu:
        pass

    assert attach_toolcare_to_menu(DummyWindowNoMenu()) is False

    class DummyWindowBrokenMenu:
        def menuBar(self):
            return None

    assert attach_toolcare_to_menu(DummyWindowBrokenMenu()) is False


# -----------------------------------------------------------------------------
# 10. Duplicate Menu-Hook Attachment Prevention (Idempotency)
# -----------------------------------------------------------------------------
def test_duplicate_menu_hook_attachment_prevention():
    win = QMainWindow()
    bar = win.menuBar()
    tools_menu = bar.addMenu("&Tools")

    # First attachment
    res1 = attach_toolcare_to_menu(win)
    assert res1 is True
    initial_actions_count = len(tools_menu.actions())
    assert initial_actions_count == 1
    assert tools_menu.actions()[0].text() == "ToolCare Toolchain Manager..."

    # Second attachment must be idempotent
    res2 = attach_toolcare_to_menu(win)
    assert res2 is True
    assert len(tools_menu.actions()) == initial_actions_count


# -----------------------------------------------------------------------------
# 11. Rollback Confirmation and Rejection of Corrupted Snapshots
# -----------------------------------------------------------------------------
def test_rollback_rejection_of_corrupted_snapshots(gui_dialog):
    dlg, _, backup_dir = gui_dialog

    # Create a corrupted snapshot folder (missing manifest.json)
    corrupted_snap = backup_dir / "20261114T120000Z_corrupt"
    corrupted_snap.mkdir(parents=True, exist_ok=True)

    dlg.refresh_config_tab()
    # Select the corrupt snapshot row
    dlg.backup_table.setCurrentRow(0)

    # Rollback attempt should detect missing/corrupted manifest
    # We patch QMessageBox.critical to verify error display
    critical_shown = []
    with patch.object(QMessageBox, "critical", lambda parent, title, msg: critical_shown.append(msg)):
        dlg.action_rollback_snapshot()

    assert len(critical_shown) > 0
    assert "does not contain a valid manifest" in critical_shown[0]


# -----------------------------------------------------------------------------
# 12. Clear View Does Not Delete Persistent Disk Logs
# -----------------------------------------------------------------------------
def test_clear_log_view_does_not_delete_persistent_disk_logs(gui_dialog, tmp_path):
    dlg, _, _ = gui_dialog

    # Write a test log entry to disk
    log_dir = tmp_path / ".esim" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "toolcare.log"
    log_file.write_text("2026-11-14 12:00:00 [INFO] Persistent log audit line.\n", encoding="utf-8")

    dlg.log_text.setPlainText("Some text in GUI")
    assert dlg.log_text.toPlainText() == "Some text in GUI"

    # Action clear view
    dlg.action_clear_log_view()

    # GUI text must be empty
    assert dlg.log_text.toPlainText() == ""

    # Disk file must still exist and be untouched
    assert log_file.is_file()
    assert "Persistent log audit line" in log_file.read_text(encoding="utf-8")


# -----------------------------------------------------------------------------
# 13. Doctor Regression: All 12 Diagnostic Results Reach the GUI Table
# -----------------------------------------------------------------------------
def test_doctor_all_12_checks_reach_gui_table(gui_dialog):
    dlg, _, _ = gui_dialog

    # Trigger action_run_doctor
    dlg.action_run_doctor()

    # Verify all 12 diagnostic check results populate the doctor_table
    assert dlg.doctor_table.rowCount() == 12
    assert dlg.doctor_table.columnCount() == 6

    # Verify every row contains valid Stage, Check Name, Result, Severity, Details, Remediation
    stages_seen = set()
    for row in range(12):
        stage = dlg.doctor_table.item(row, 0).text()
        check_name = dlg.doctor_table.item(row, 1).text()
        result = dlg.doctor_table.item(row, 2).text()
        severity = dlg.doctor_table.item(row, 3).text()
        details = dlg.doctor_table.item(row, 4).text()
        remediation = dlg.doctor_table.item(row, 5).text()

        assert stage, f"Row {row} missing stage"
        assert check_name, f"Row {row} missing check_name"
        assert result in ("PASS", "FAIL"), f"Row {row} unexpected result: {result}"
        assert severity in ("INFO", "WARNING", "ERROR", "CRITICAL"), f"Row {row} unexpected severity: {severity}"
        assert details, f"Row {row} missing details"
        assert remediation, f"Row {row} missing remediation"
        stages_seen.add(stage)

    # All 5 stages must be represented
    assert len(stages_seen) == 5


# -----------------------------------------------------------------------------
# 14. Doctor Regression: All Five Workflow Readiness Values Rendered
# -----------------------------------------------------------------------------
def test_doctor_all_five_workflows_rendered(gui_dialog):
    dlg, _, _ = gui_dialog

    # Initially, cards display [UNKNOWN]
    for wf_id in ALL_WORKFLOW_IDS:
        assert "[UNKNOWN]" in dlg.wf_labels[wf_id].text()

    # Trigger action_run_doctor
    dlg.action_run_doctor()

    # After diagnostic audit, none of the 5 cards should remain [UNKNOWN]
    for wf_id in ALL_WORKFLOW_IDS:
        lbl_text = dlg.wf_labels[wf_id].text()
        assert "[UNKNOWN]" not in lbl_text, f"Workflow {wf_id} unexpectedly remained UNKNOWN"
        assert any(status in lbl_text for status in ("HEALTHY", "READY", "BLOCKED", "DEGRADED"))


# -----------------------------------------------------------------------------
# 15. Doctor Regression: Failed Checks Display Real Severity and Remediation
# -----------------------------------------------------------------------------
def test_doctor_failed_checks_display_actual_severity(gui_dialog):
    dlg, _, _ = gui_dialog

    # Build mock report with specific failing items of different severities
    mock_items = [
        DoctorCheckItem(
            stage="Stage 2: Analog Simulation",
            check_name="Ngspice Executable",
            passed=False,
            severity=Severity.CRITICAL,
            details="Ngspice binary not located.",
            remediation="Install Ngspice 45.2.0.",
        ),
        DoctorCheckItem(
            stage="Stage 3: Digital Simulation",
            check_name="Icarus Verilog Executable",
            passed=False,
            severity=Severity.ERROR,
            details="iverilog binary not found.",
            remediation="Install Icarus Verilog v12.",
        ),
        DoctorCheckItem(
            stage="Stage 4: VHDL & Compiler",
            check_name="LLVM / Clang Toolchain",
            passed=False,
            severity=Severity.WARNING,
            details="Clang compiler missing.",
            remediation="Install LLVM toolchain.",
        ),
    ]

    mock_report = DoctorReport(
        items=mock_items,
        overall_health=False,
        has_critical=True,
        total_checks=3,
        passed_checks=0,
        failed_checks=3,
        workflow_readiness={},
        verified_components={},
    )

    dlg.render_doctor_report(mock_report)

    assert dlg.doctor_table.rowCount() == 3
    # Check row 0 (CRITICAL)
    assert dlg.doctor_table.item(0, 2).text() == "FAIL"
    assert dlg.doctor_table.item(0, 3).text() == "CRITICAL"
    assert "Install Ngspice 45.2.0." in dlg.doctor_table.item(0, 5).text()

    # Check row 1 (ERROR)
    assert dlg.doctor_table.item(1, 2).text() == "FAIL"
    assert dlg.doctor_table.item(1, 3).text() == "ERROR"
    assert "Install Icarus Verilog v12." in dlg.doctor_table.item(1, 5).text()

    # Check row 2 (WARNING)
    assert dlg.doctor_table.item(2, 2).text() == "FAIL"
    assert dlg.doctor_table.item(2, 3).text() == "WARNING"


# -----------------------------------------------------------------------------
# 16. Doctor Regression: Exception Produces Visible Error in Status and Modal
# -----------------------------------------------------------------------------
def test_doctor_exception_produces_visible_error(gui_dialog, monkeypatch):
    dlg, _, _ = gui_dialog

    # Mock SimulationDoctor.run_diagnostics to raise an exception
    class BrokenDoctor:
        def __init__(self, *args, **kwargs):
            pass
        def run_diagnostics(self):
            raise RuntimeError("Hardware diagnostic bus I/O failure")

    monkeypatch.setattr("toolcare.gui.dialog.SimulationDoctor", BrokenDoctor)

    critical_messages = []
    with patch.object(QMessageBox, "critical", lambda parent, title, msg: critical_messages.append((title, msg))):
        dlg.action_run_doctor()

    # Verify status bar displays visible error
    assert "Error:" in dlg.lbl_status.text()
    assert "Hardware diagnostic bus I/O failure" in dlg.lbl_status.text()

    # Verify critical message box was shown
    assert len(critical_messages) == 1
    assert "Operation Failed" in critical_messages[0][0]
    assert "Hardware diagnostic bus I/O failure" in critical_messages[0][1]


# -----------------------------------------------------------------------------
# 17. Doctor Regression: Empty or Malformed Results Handled Safely
# -----------------------------------------------------------------------------
def test_doctor_empty_or_malformed_results_handled_safely(gui_dialog):
    import pytest
    dlg, _, _ = gui_dialog

    # 1. Test None report: must not crash, must preserve UNKNOWN
    dlg.render_doctor_report(None)
    for wf_id in ALL_WORKFLOW_IDS:
        assert "[UNKNOWN]" in dlg.wf_labels[wf_id].text()
    assert dlg.doctor_table.rowCount() == 0

    # 2. Test Empty DoctorReport: must not crash
    empty_report = DoctorReport(
        items=[],
        overall_health=True,
        has_critical=False,
        total_checks=0,
        passed_checks=0,
        failed_checks=0,
        workflow_readiness={},
        verified_components={},
    )
    dlg.render_doctor_report(empty_report)
    assert dlg.doctor_table.rowCount() == 0
    # Any workflow not in readiness dictionary must display UNKNOWN
    for wf_id in ALL_WORKFLOW_IDS:
        assert "[UNKNOWN]" in dlg.wf_labels[wf_id].text()

    # 3. Test malformed object (missing items or workflow_readiness)
    with pytest.raises(ValueError, match="Malformed DoctorReport"):
        dlg.render_doctor_report("not_a_report")

