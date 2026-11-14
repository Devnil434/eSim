"""
Tests for toolcare.core.detector multi-tier binary discovery and safe execution.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from toolcare.core.detector import ToolDetectionResult, ToolDetector
from toolcare.core.registry import ToolRegistry
from toolcare.core.version import ToolStatus


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.fixture
def detector(registry):
    return ToolDetector(registry=registry)


def test_detector_environment_variable_precedence(detector, tmp_path, monkeypatch):
    """Assert Tier 1 (environment variables) takes highest discovery precedence."""
    fake_ngspice = tmp_path / "custom_ngspice.exe"
    fake_ngspice.write_text("echo fake", encoding="utf-8")

    monkeypatch.setenv("ESIM_NGSPICE", str(fake_ngspice))

    with patch("os.access", return_value=True):
        spec = detector.registry.get_tool("ngspice")
        binary_path, source = detector.find_binary(spec)

        assert binary_path == fake_ngspice.resolve()
        assert source == "env:ESIM_NGSPICE"


def test_detector_nghdl_config_precedence(detector, tmp_path, monkeypatch):
    """Assert Tier 2 (eSim nghdl config.ini) takes precedence over standard system dirs."""
    monkeypatch.delenv("ESIM_NGSPICE", raising=False)

    fake_bin = tmp_path / "nghdl_ngspice.exe"
    fake_bin.write_text("fake", encoding="utf-8")

    fake_nghdl_dir = tmp_path / ".nghdl"
    fake_nghdl_dir.mkdir()
    ini_file = fake_nghdl_dir / "config.ini"
    ini_file.write_text(f"[COSIM]\nngspice = {fake_bin}\n", encoding="utf-8")

    with patch("toolcare.core.detector.get_nghdl_config_dir", return_value=fake_nghdl_dir):
        with patch("os.access", return_value=True):
            spec = detector.registry.get_tool("ngspice")
            binary_path, source = detector.find_binary(spec)

            assert binary_path == fake_bin.resolve()
            assert "nghdl_config:COSIM" in source


def test_detector_path_fallback(detector, monkeypatch):
    """Assert Tier 5 (System PATH via shutil.which) works as fallback when configs absent."""
    monkeypatch.delenv("ESIM_NGSPICE", raising=False)

    fake_path = Path("C:/System/ngspice.exe")

    with patch("toolcare.core.detector.get_nghdl_config_dir", return_value=Path("/non_existent")):
        with patch("shutil.which", return_value=str(fake_path)):
            with patch.object(Path, "is_file", return_value=True):
                spec = detector.registry.get_tool("ngspice")
                binary_path, source = detector.find_binary(spec)

                assert binary_path == fake_path.resolve()
                assert source == "system_path"


def test_probe_version_extraction(detector):
    """Assert probe_version successfully extracts version string via regex."""
    spec = detector.registry.get_tool("ngspice")

    mock_output = "ngspice-45.2 : Circuit level simulation compiler\nRelease date: 2024"
    with patch("toolcare.core.detector.run_safe_command", return_value=(0, mock_output, "")):
        ver = detector.probe_version(Path("C:/Spice64/bin/ngspice.exe"), spec)
        assert ver == "45.2"


def test_detect_tool_not_installed(detector, monkeypatch):
    """Assert detect_tool returns NOT_INSTALLED when binary cannot be located anywhere."""
    monkeypatch.delenv("ESIM_NGSPICE", raising=False)

    with patch.object(detector, "find_binary", return_value=(None, "not_found")):
        result = detector.detect_tool("ngspice")
        assert isinstance(result, ToolDetectionResult)
        assert result.is_installed is False
        assert result.status == ToolStatus.NOT_INSTALLED
        assert result.binary_path is None


def test_detect_tool_success_compatible(detector):
    """Assert detect_tool returns COMPATIBLE when tool meets recommended version."""
    fake_bin = Path("C:/Spice64/bin/ngspice.exe")
    with patch.object(detector, "find_binary", return_value=(fake_bin, "system_dir:C:/Spice64/bin")):
        with patch.object(detector, "probe_version", return_value="45.2"):
            res = detector.detect_tool("ngspice")
            assert res.is_installed is True
            assert res.detected_version == "45.2"
            assert res.status == ToolStatus.COMPATIBLE
            assert res.is_compatible is True
            assert res.is_usable is True


def test_detect_all_returns_all_tools(detector):
    """Assert detect_all returns entries for every tool in the registry."""
    with patch.object(detector, "detect_tool") as mock_dt:
        mock_dt.return_value = MagicMock(spec=ToolDetectionResult)
        all_results = detector.detect_all()
        assert len(all_results) == 6
        assert set(all_results.keys()) == {"ngspice", "kicad", "iverilog", "ghdl", "verilator", "llvm"}
