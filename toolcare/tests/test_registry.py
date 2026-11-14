"""
Tests for toolcare.core.registry and toolcare/data/tools.json schema compliance.
"""

import json
import re
from pathlib import Path

import pytest
from toolcare.core.registry import ArchiveSpec, CompatibilitySpec, ToolRegistry, ToolSpec


def test_registry_loads_default_catalog():
    """Assert default tools.json loads successfully and contains the 6 core tools."""
    reg = ToolRegistry()
    assert len(reg) == 6
    assert "ngspice" in reg
    assert "kicad" in reg
    assert "iverilog" in reg
    assert "ghdl" in reg
    assert "verilator" in reg
    assert "llvm" in reg


def test_registry_tool_spec_types():
    """Assert all tools are parsed as valid ToolSpec instances with valid fields."""
    reg = ToolRegistry()
    for tool in reg.get_all_tools():
        assert isinstance(tool, ToolSpec)
        assert isinstance(tool.compatibility, CompatibilitySpec)
        assert len(tool.executables) > 0
        assert len(tool.version_command) > 0
        assert tool.version_regex != ""
        # Verify regex compiles
        compiled = re.compile(tool.version_regex)
        assert compiled is not None


def test_registry_verified_vs_unverified_package_ids():
    """Assert unverified package identifiers are properly flagged and rejected as unverified."""
    reg = ToolRegistry()

    ngspice = reg.get_tool("ngspice")
    assert ngspice is not None
    assert ngspice.is_package_id_verified("winget") is True
    assert ngspice.get_package_id("winget") == "Ngspice.Ngspice"
    assert ngspice.is_package_id_verified("choco") is False
    assert ngspice.get_package_id("choco") is None

    iverilog = reg.get_tool("iverilog")
    assert iverilog is not None
    assert iverilog.is_package_id_verified("winget") is False
    assert iverilog.get_package_id("winget") is None
    assert iverilog.is_package_id_verified("apt") is True
    assert iverilog.get_package_id("apt") == "iverilog"


def test_registry_archive_verification():
    """Assert verified vendor archive specs have valid SHA-256 digests."""
    reg = ToolRegistry()
    ngspice = reg.get_tool("ngspice")
    assert ngspice is not None
    win_archive = ngspice.get_archive("windows_x64")
    assert win_archive is not None
    assert win_archive.is_verified is True
    assert len(win_archive.sha256) == 64
    assert win_archive.url.startswith("https://")

    # Unverified tool archives
    verilator = reg.get_tool("verilator")
    assert verilator is not None
    v_archive = verilator.get_archive("windows_x64")
    assert v_archive is not None
    assert v_archive.is_verified is False


def test_registry_categories():
    """Assert category filtering works accurately."""
    reg = ToolRegistry()
    analog = reg.get_tools_by_category("analog")
    assert any(t.id == "ngspice" for t in analog)

    digital = reg.get_tools_by_category("digital")
    digital_ids = {t.id for t in digital}
    assert "iverilog" in digital_ids
    assert "ghdl" in digital_ids
    assert "verilator" in digital_ids

    core = reg.get_tools_by_category("core")
    assert any(t.id == "kicad" for t in core)


def test_registry_missing_file_raises(tmp_path: Path):
    """Assert attempting to load a non-existent file raises FileNotFoundError."""
    missing = tmp_path / "non_existent.json"
    with pytest.raises(FileNotFoundError):
        ToolRegistry(data_path=missing)


def test_registry_malformed_json_raises(tmp_path: Path):
    """Assert malformed JSON raises ValueError."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{ unquoted_key: 123", encoding="utf-8")
    with pytest.raises(ValueError):
        ToolRegistry(data_path=bad_json)
