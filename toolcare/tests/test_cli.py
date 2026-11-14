"""
Tests for toolcare.cli and toolcare.__main__.
Validates argument parsing, subcommand execution, table formatting, exit codes,
and error handling.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from toolcare.cli import build_parser, main
from toolcare.core.doctor import DoctorReport, Severity
from toolcare.core.installer import InstallResult
from toolcare.core.updater import UpdatePlan, UpdateResult


def test_cli_help(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "eSim ToolCare" in captured.out


def test_cli_version(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "toolcare 2.0.0" in captured.out


def test_cli_no_subcommand(capsys):
    exit_code = main([])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "usage: toolcare" in captured.out


def test_cli_list(capsys):
    exit_code = main(["list"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "ngspice" in captured.out
    assert "kicad" in captured.out
    assert "iverilog" in captured.out
    assert "Total tools: 6" in captured.out


def test_cli_list_category_filter(capsys):
    exit_code = main(["list", "-c", "digital"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "iverilog" in captured.out
    assert "ghdl" in captured.out
    assert "ngspice" not in captured.out


def test_cli_check_all(capsys):
    exit_code = main(["check"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "ngspice" in captured.out
    assert "Toolchain Compatibility Status" in captured.out


def test_cli_check_single_tool_verbose(capsys):
    exit_code = main(["check", "ngspice", "-v"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Detailed Detection for 'ngspice':" in captured.out
    assert "Binary Path" in captured.out


def test_cli_check_unknown_tool(capsys):
    exit_code = main(["check", "invalid_nonexistent_tool"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error: Unknown tool" in captured.err


def test_cli_doctor_healthy(monkeypatch, capsys):
    mock_doctor = MagicMock()
    mock_report = MagicMock(spec=DoctorReport)
    mock_report.overall_health = True
    mock_report.format_cli_report.return_value = "All diagnostic checks passed."
    mock_doctor.run_diagnostics.return_value = mock_report

    monkeypatch.setattr("toolcare.cli.SimulationDoctor", lambda: mock_doctor)

    exit_code = main(["doctor"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "All diagnostic checks passed." in captured.out
    assert "Doctor Result: ALL WORKFLOWS HEALTHY" in captured.out


def test_cli_doctor_target_flow_blocked(capsys):
    exit_code = main(["doctor", "--flow", "d_cosim"])
    # On clean test machine without ngspice/iverilog, d_cosim will be blocked
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Targeting specific workflow: 'd_cosim'" in captured.out
    assert "Readiness: [BLOCKED]" in captured.out


def test_cli_install_dry_run(capsys):
    exit_code = main(["install", "ngspice", "--dry-run"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[Dry-Run]" in captured.out
    assert "Simulation mode enabled" in captured.out


def test_cli_update_check(capsys):
    exit_code = main(["update", "--check"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Checking Upstream Tool Updates (Zero Modifications)" in captured.out
    assert "ngspice" in captured.out
    assert "Affected Workflows" in captured.out


def test_cli_update_dry_run(capsys):
    exit_code = main(["update", "ngspice", "--dry-run"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[Dry-Run] Update Plan for 'ngspice':" in captured.out


def test_cli_update_missing_tool_no_check(capsys):
    exit_code = main(["update"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error: Please specify a tool ID to update" in captured.err


def test_cli_configure_sync_dry_run(capsys):
    exit_code = main(["configure", "--sync", "--dry-run"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[Dry-Run] Would inspect sym-lib-table" in captured.out


def test_cli_configure_rollback_dry_run(capsys):
    exit_code = main(["configure", "--rollback", "20261114_120000", "--dry-run"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[Dry-Run] Would verify backup integrity" in captured.out


def test_cli_logs(capsys):
    exit_code = main(["logs", "-n", "10"])
    assert exit_code == 0
