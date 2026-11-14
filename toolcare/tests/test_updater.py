"""
Tests for toolcare.core.updater.
Validates update plan generation, workflow dependency tracking, unverified source rejection,
zero-side-effect dry runs, user cancellation, automatic rollback upon verification failure,
and update history logging.
"""

import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch
import pytest

from toolcare.core.config_handler import ConfigHandler
from toolcare.core.dependency import DependencyGraph
from toolcare.core.detector import ToolDetectionResult, ToolDetector
from toolcare.core.installer import InstallResult, ToolInstaller
from toolcare.core.registry import ToolRegistry
from toolcare.core.updater import UpdateEngine, UpdatePlan, UpdateResult
from toolcare.core.version import ToolStatus, VersionEvaluation


def make_eval(tool_id: str, ver: Optional[str], status: ToolStatus) -> VersionEvaluation:
    return VersionEvaluation(
        tool_id=tool_id,
        detected_version=ver,
        status=status,
        message=f"Status: {status.value}",
        min_version="38.0",
        recommended_version="45.2",
        max_version=None,
        is_usable=(status in (ToolStatus.COMPATIBLE, ToolStatus.UPGRADE_AVAILABLE)),
    )


@pytest.fixture
def isolated_update_engine(tmp_path: Path):
    """Fixture providing an isolated UpdateEngine with mocked sub-components."""
    registry = ToolRegistry()
    detector = MagicMock(spec=ToolDetector)
    dep_graph = DependencyGraph()
    config_handler = MagicMock(spec=ConfigHandler)
    installer = MagicMock(spec=ToolInstaller)

    engine = UpdateEngine(
        registry=registry,
        detector=detector,
        dep_graph=dep_graph,
        config_handler=config_handler,
        installer=installer,
    )
    # Redirect history file to tmp_path
    engine.history_file = tmp_path / "logs" / "update_history.json"
    return engine, detector, config_handler, installer


# -----------------------------------------------------------------------------
# Correction 6: Update Planning & Affected Workflows
# -----------------------------------------------------------------------------
def test_plan_update_identifies_affected_workflows(isolated_update_engine):
    engine, detector, _, installer = isolated_update_engine

    # Mock ngspice as installed at version 40.0
    detector.detect_tool.return_value = ToolDetectionResult(
        tool_id="ngspice",
        name="Ngspice Simulator",
        is_installed=True,
        binary_path=Path("C:/dummy/ngspice.exe"),
        detected_version="40.0",
        evaluation=make_eval("ngspice", "40.0", ToolStatus.UPGRADE_AVAILABLE),
        discovery_source="env",
    )
    mock_adapter = MagicMock()
    mock_adapter.name = "archive"
    installer.select_adapter.return_value = mock_adapter

    plan = engine.plan_update("ngspice")
    assert plan.tool_id == "ngspice"
    assert plan.current_version == "40.0"
    assert plan.target_version == "45.2"
    assert plan.is_verified is True
    assert plan.is_update_available is True
    # Ngspice affects simulation, d_cosim, ngveri, nghdl
    assert "simulation" in plan.affected_workflows
    assert "d_cosim" in plan.affected_workflows
    assert "ngveri" in plan.affected_workflows
    assert "nghdl" in plan.affected_workflows


def test_plan_update_rejects_unverified_source(isolated_update_engine):
    engine, detector, _, installer = isolated_update_engine

    # ghdl has unverified archive and package_ids in tools.json
    detector.detect_tool.return_value = ToolDetectionResult(
        tool_id="ghdl",
        name="GHDL VHDL Simulator",
        is_installed=False,
        binary_path=None,
        detected_version=None,
        evaluation=make_eval("ghdl", None, ToolStatus.NOT_INSTALLED),
        discovery_source="none",
    )
    mock_adapter = MagicMock()
    mock_adapter.name = "archive"
    installer.select_adapter.return_value = mock_adapter

    plan = engine.plan_update("ghdl")
    assert plan.is_verified is False
    assert plan.is_update_available is False  # Must NOT be marked available if unverified
    assert any("REJECTED" in action for action in plan.actions)


# -----------------------------------------------------------------------------
# Correction 10: Dry-Run Zero Modifications
# -----------------------------------------------------------------------------
def test_execute_update_dry_run_zero_modifications(isolated_update_engine):
    engine, detector, config_handler, installer = isolated_update_engine

    detector.detect_tool.return_value = ToolDetectionResult(
        tool_id="ngspice",
        name="Ngspice Simulator",
        is_installed=False,
        binary_path=None,
        detected_version=None,
        evaluation=make_eval("ngspice", None, ToolStatus.NOT_INSTALLED),
        discovery_source="none",
    )
    mock_adapter = MagicMock()
    mock_adapter.name = "archive"
    installer.select_adapter.return_value = mock_adapter

    result = engine.execute_update("ngspice", dry_run=True)

    assert result.dry_run is True
    assert result.success is True
    assert "[Dry-Run]" in result.message

    # Verify no adapters or configs were invoked
    config_handler.create_backup.assert_not_called()
    installer.install_tool.assert_not_called()
    assert not engine.history_file.exists()


# -----------------------------------------------------------------------------
# Correction 6: Unverified Update Rejection
# -----------------------------------------------------------------------------
def test_execute_update_unverified_rejection(isolated_update_engine):
    engine, detector, config_handler, installer = isolated_update_engine

    detector.detect_tool.return_value = ToolDetectionResult(
        tool_id="ghdl",
        name="GHDL VHDL Simulator",
        is_installed=False,
        binary_path=None,
        detected_version=None,
        evaluation=make_eval("ghdl", None, ToolStatus.NOT_INSTALLED),
        discovery_source="none",
    )
    mock_adapter = MagicMock()
    mock_adapter.name = "archive"
    installer.select_adapter.return_value = mock_adapter

    result = engine.execute_update("ghdl", auto_confirm=True, dry_run=False)

    assert result.success is False
    assert "Update rejected for 'ghdl'" in result.message
    config_handler.create_backup.assert_not_called()
    installer.install_tool.assert_not_called()

    # Verify history recorded rejection
    assert engine.history_file.is_file()
    history = json.loads(engine.history_file.read_text(encoding="utf-8"))
    assert len(history) == 1
    assert history[0]["status"] == "REJECTED_UNVERIFIED"


# -----------------------------------------------------------------------------
# Correction 8: Interactive Confirmation (Default No)
# -----------------------------------------------------------------------------
def test_execute_update_user_cancellation(isolated_update_engine, monkeypatch):
    engine, detector, config_handler, installer = isolated_update_engine

    detector.detect_tool.return_value = ToolDetectionResult(
        tool_id="ngspice",
        name="Ngspice Simulator",
        is_installed=True,
        binary_path=Path("C:/dummy/ngspice.exe"),
        detected_version="40.0",
        evaluation=make_eval("ngspice", "40.0", ToolStatus.UPGRADE_AVAILABLE),
        discovery_source="env",
    )
    mock_adapter = MagicMock()
    mock_adapter.name = "archive"
    installer.select_adapter.return_value = mock_adapter

    # User inputs 'n' or enters empty string (default No)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    result = engine.execute_update("ngspice", auto_confirm=False, dry_run=False)

    assert result.success is False
    assert "Update cancelled by user" in result.message
    config_handler.create_backup.assert_not_called()
    installer.install_tool.assert_not_called()


# -----------------------------------------------------------------------------
# Correction 7: Failed Update Verification & Automatic Rollback
# -----------------------------------------------------------------------------
def test_execute_update_failed_post_install_rollback(isolated_update_engine):
    engine, detector, config_handler, installer = isolated_update_engine

    detector.detect_tool.return_value = ToolDetectionResult(
        tool_id="ngspice",
        name="Ngspice Simulator",
        is_installed=True,
        binary_path=Path("C:/dummy/ngspice.exe"),
        detected_version="40.0",
        evaluation=make_eval("ngspice", "40.0", ToolStatus.UPGRADE_AVAILABLE),
        discovery_source="env",
    )
    mock_adapter = MagicMock()
    mock_adapter.name = "archive"
    installer.select_adapter.return_value = mock_adapter

    # Backup returns mock snapshot id
    mock_manifest = MagicMock()
    config_handler.create_backup.return_value = ("20261114_120000", mock_manifest)

    # Installation fails post-verification
    installer.install_tool.return_value = InstallResult(
        tool_id="ngspice",
        success=False,
        method_used="archive",
        installed_path=None,
        post_install_version=None,
        is_compatible=False,
        message="Post-installation verification failed: binary not executable.",
        dry_run=False,
    )

    result = engine.execute_update("ngspice", auto_confirm=True, dry_run=False)

    assert result.success is False
    assert result.rolled_back is True
    assert "Rolled back configurations to snapshot 20261114_120000" in result.message

    # Verify rollback was invoked on config_handler
    config_handler.rollback.assert_called_once_with("20261114_120000")

    # Verify history recorded failure & rollback
    history = json.loads(engine.history_file.read_text(encoding="utf-8"))
    assert history[-1]["status"] == "FAILED_ROLLED_BACK"


# -----------------------------------------------------------------------------
# Happy Path & History Logging
# -----------------------------------------------------------------------------
def test_execute_update_success_and_history_logging(isolated_update_engine):
    engine, detector, config_handler, installer = isolated_update_engine

    detector.detect_tool.return_value = ToolDetectionResult(
        tool_id="ngspice",
        name="Ngspice Simulator",
        is_installed=True,
        binary_path=Path("C:/dummy/ngspice.exe"),
        detected_version="40.0",
        evaluation=make_eval("ngspice", "40.0", ToolStatus.UPGRADE_AVAILABLE),
        discovery_source="env",
    )
    mock_adapter = MagicMock()
    mock_adapter.name = "archive"
    installer.select_adapter.return_value = mock_adapter

    mock_manifest = MagicMock()
    config_handler.create_backup.return_value = ("20261114_123000", mock_manifest)

    installer.install_tool.return_value = InstallResult(
        tool_id="ngspice",
        success=True,
        method_used="archive",
        installed_path=Path("C:/dummy/ngspice-45.2/bin/ngspice.exe"),
        post_install_version="45.2",
        is_compatible=True,
        message="Installed successfully.",
        dry_run=False,
    )

    result = engine.execute_update("ngspice", auto_confirm=True, dry_run=False)

    assert result.success is True
    assert result.new_version == "45.2"
    assert "Successfully updated and verified ngspice to v45.2" in result.message

    # Rollback must NOT have been called
    config_handler.rollback.assert_not_called()

    # History entry recorded
    history = json.loads(engine.history_file.read_text(encoding="utf-8"))
    assert len(history) == 1
    assert history[0]["status"] == "SUCCESS"
    assert history[0]["prior_version"] == "40.0"
    assert history[0]["target_version"] == "45.2"
