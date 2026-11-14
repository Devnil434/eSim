"""
Tests for toolcare.core.doctor 5-stage health auditor, GHDL backend detection, and relative component discovery.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from toolcare.core.dependency import DependencyGraph
from toolcare.core.detector import ToolDetectionResult, ToolDetector
from toolcare.core.doctor import DoctorCheckItem, DoctorReport, Severity, SimulationDoctor
from toolcare.core.version import ToolStatus, VersionEvaluation


@pytest.fixture
def mock_detector():
    return MagicMock(spec=ToolDetector)


@pytest.fixture
def doctor(mock_detector):
    dep_graph = DependencyGraph()
    return SimulationDoctor(detector=mock_detector, dep_graph=dep_graph)


def make_mock_result(
    tool_id: str,
    installed: bool = True,
    version: str = "1.0.0",
    status: ToolStatus = ToolStatus.COMPATIBLE,
    bin_path: Optional[Path] = None,
) -> ToolDetectionResult:
    eval_res = VersionEvaluation(
        tool_id=tool_id,
        detected_version=version if installed else None,
        status=status,
        message="Mock OK",
        min_version="1.0.0",
        recommended_version="1.0.0",
        max_version="2.0.0",
        is_usable=(status in (ToolStatus.COMPATIBLE, ToolStatus.UPGRADE_AVAILABLE)),
    )
    return ToolDetectionResult(
        tool_id=tool_id,
        name=f"Mock {tool_id}",
        is_installed=installed,
        binary_path=bin_path or (Path(f"/mock/bin/{tool_id}") if installed else None),
        detected_version=version if installed else None,
        evaluation=eval_res,
        discovery_source="mock",
    )


def test_severity_levels_include_critical():
    """Correction 2: Assert CRITICAL is an explicit member of Severity."""
    assert Severity.CRITICAL == "CRITICAL"
    assert Severity.ERROR == "ERROR"
    assert Severity.WARNING == "WARNING"
    assert Severity.INFO == "INFO"


def test_ghdl_backend_detection_llvm():
    """Assert LLVM backend is correctly identified as compatible."""
    output = "GHDL 4.1.0 (Ubuntu 4.1.0+dfsg-1ubuntu1) [Dunoon edition]\n Compiled with GNAT Version: 13.2.0\n llvm code generator\n"
    backend, is_compat = SimulationDoctor.inspect_ghdl_backend(output)
    assert backend == "llvm"
    assert is_compat is True


def test_ghdl_backend_detection_mcode():
    """Assert mcode backend is identified and rejected as incompatible."""
    output = "GHDL 0.37 (tarball) [Dunoon edition]\n Compiled with GNAT Version: 9.3.0\n mcode code generator\n"
    backend, is_compat = SimulationDoctor.inspect_ghdl_backend(output)
    assert backend == "mcode"
    assert is_compat is False


def test_ghdl_backend_detection_gcc():
    """Assert gcc backend is identified and rejected as incompatible."""
    output = "GHDL 4.0.0\n GCC code generator\n"
    backend, is_compat = SimulationDoctor.inspect_ghdl_backend(output)
    assert backend == "gcc"
    assert is_compat is False


def test_ghdl_backend_detection_unknown_is_not_compatible():
    """
    Correction 3: An unknown backend MUST NOT be treated as LLVM-compatible.
    """
    output = "GHDL 3.0.0 (Custom Build)\n Copyright (C) 2002-2023 Tristan Gingold\n"
    backend, is_compat = SimulationDoctor.inspect_ghdl_backend(output)
    assert backend == "unknown"
    assert is_compat is False


def test_doctor_relative_discovery_ngspice(tmp_path):
    """
    Correction 4: Discover integration libraries relative to detected installation prefix.
    """
    ngspice_bin = tmp_path / "bin" / "ngspice.exe"
    ngspice_bin.parent.mkdir(parents=True)
    ngspice_bin.write_text("fake bin")

    cm_dir = tmp_path / "lib" / "ngspice"
    cm_dir.mkdir(parents=True)
    (cm_dir / "analog.cm").write_text("fake cm")

    found_dir = SimulationDoctor.find_ngspice_code_models_dir(ngspice_bin)
    assert found_dir == cm_dir.resolve()


def test_doctor_relative_discovery_iverilog(tmp_path):
    """
    Correction 4: Discover Icarus Verilog libraries relative to detected installation prefix.
    """
    ivl_bin = tmp_path / "bin" / "iverilog.exe"
    ivl_bin.parent.mkdir(parents=True)
    ivl_bin.write_text("fake bin")

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir(parents=True)
    (lib_dir / "libvvp.dll").write_text("fake dll")

    found_lib = SimulationDoctor.find_iverilog_lib_dir(ivl_bin)
    assert found_lib == lib_dir.resolve()


def test_doctor_fully_healthy(doctor, tmp_path):
    """Assert fully healthy installation passes all checks and marks all 5 workflows runnable."""
    fake_ngspice = tmp_path / "Spice64" / "bin" / "ngspice.exe"
    fake_ngspice.parent.mkdir(parents=True)
    fake_ngspice.write_text("bin")

    cm_dir = tmp_path / "Spice64" / "lib" / "ngspice"
    cm_dir.mkdir(parents=True)
    for f in ["analog.cm", "spice2poly.cm", "digital.cm", "ivlng.dll", "ghdl.cm"]:
        (cm_dir / f).write_text("cm")

    fake_ivl = tmp_path / "iverilog" / "bin" / "iverilog.exe"
    fake_ivl.parent.mkdir(parents=True)
    fake_ivl.write_text("bin")
    (fake_ivl.parent / "libvvp.dll").write_text("dll")

    fake_kicad_table = tmp_path / "sym-lib-table"
    fake_kicad_table.write_text('(sym_lib_table (lib (name "eSim_Subckt")))')

    fake_nghdl_ini = tmp_path / "config.ini"
    fake_nghdl_ini.write_text("[COSIM]\nngspice = test\n[COMPILER]\ngcc = test\n")

    detections = {
        "ngspice": make_mock_result("ngspice", True, "45.2", ToolStatus.COMPATIBLE, fake_ngspice),
        "kicad": make_mock_result("kicad", True, "9.0.3", ToolStatus.COMPATIBLE),
        "iverilog": make_mock_result("iverilog", True, "12.0", ToolStatus.COMPATIBLE, fake_ivl),
        "ghdl": make_mock_result("ghdl", True, "4.1.0", ToolStatus.COMPATIBLE),
        "verilator": make_mock_result("verilator", True, "5.032", ToolStatus.COMPATIBLE),
        "llvm": make_mock_result("llvm", True, "18.1.8", ToolStatus.COMPATIBLE),
    }
    doctor.detector.detect_all.return_value = detections

    with patch.object(SimulationDoctor, "find_ngspice_code_models_dir", return_value=cm_dir), \
         patch.object(SimulationDoctor, "find_iverilog_lib_dir", return_value=fake_ivl.parent), \
         patch.object(SimulationDoctor, "find_kicad_sym_lib_table", return_value=fake_kicad_table), \
         patch("toolcare.core.doctor.get_nghdl_config_dir", return_value=tmp_path), \
         patch("toolcare.core.doctor.run_safe_command", return_value=(0, "llvm code generator", "")):

        report = doctor.run_diagnostics()

        assert report.overall_health is True
        assert report.has_critical is False
        assert all(wf.is_runnable for wf in report.workflow_readiness.values())


def test_doctor_ghdl_mcode_triggers_critical(doctor, tmp_path):
    """Assert GHDL mcode backend triggers CRITICAL severity and blocks nghdl."""
    detections = {
        "ngspice": make_mock_result("ngspice", True, "45.2"),
        "kicad": make_mock_result("kicad", True, "9.0.3"),
        "iverilog": make_mock_result("iverilog", True, "12.0"),
        "ghdl": make_mock_result("ghdl", True, "0.37"),
        "verilator": make_mock_result("verilator", True, "5.032"),
        "llvm": make_mock_result("llvm", True, "18.1.8"),
    }
    doctor.detector.detect_all.return_value = detections

    with patch.object(SimulationDoctor, "find_ngspice_code_models_dir", return_value=None), \
         patch.object(SimulationDoctor, "find_iverilog_lib_dir", return_value=None), \
         patch.object(SimulationDoctor, "find_kicad_sym_lib_table", return_value=None), \
         patch("toolcare.core.doctor.run_safe_command", return_value=(0, "mcode code generator", "")):

        report = doctor.run_diagnostics()

        critical_items = report.get_items_by_severity(Severity.CRITICAL)
        assert len(critical_items) >= 1
        assert any("mcode" in it.details for it in critical_items)
        assert report.workflow_readiness["nghdl"].is_runnable is False


def test_doctor_missing_libvvp_blocks_d_cosim_only(doctor, tmp_path):
    """
    Correction 5: Missing libvvp must block d_cosim while standalone simulation remains runnable.
    """
    cm_dir = tmp_path / "lib" / "ngspice"
    cm_dir.mkdir(parents=True)
    # Ngspice has analog.cm, spice2poly.cm, digital.cm, and ivlng.dll
    for f in ["analog.cm", "spice2poly.cm", "digital.cm", "ivlng.dll"]:
        (cm_dir / f).write_text("cm")

    # Empty iverilog lib dir (libvvp is missing!)
    empty_ivl_lib = tmp_path / "ivl_empty_lib"
    empty_ivl_lib.mkdir(parents=True)

    fake_kicad_table = tmp_path / "sym-lib-table"
    fake_kicad_table.write_text('(sym_lib_table (lib (name "eSim_Subckt")))')

    detections = {
        "ngspice": make_mock_result("ngspice", True, "45.2"),
        "kicad": make_mock_result("kicad", True, "9.0.3"),
        "iverilog": make_mock_result("iverilog", True, "12.0"),
        "ghdl": make_mock_result("ghdl", False),
        "verilator": make_mock_result("verilator", False),
        "llvm": make_mock_result("llvm", False),
    }
    doctor.detector.detect_all.return_value = detections

    with patch.object(SimulationDoctor, "find_ngspice_code_models_dir", return_value=cm_dir), \
         patch.object(SimulationDoctor, "find_iverilog_lib_dir", return_value=empty_ivl_lib), \
         patch.object(SimulationDoctor, "find_kicad_sym_lib_table", return_value=fake_kicad_table):

        report = doctor.run_diagnostics()

        # Standalone analog simulation is RUNNABLE!
        assert report.workflow_readiness["simulation"].is_runnable is True

        # Standalone verifier is RUNNABLE!
        assert report.workflow_readiness["verifier"].is_runnable is True

        # d_cosim is BLOCKED because libvvp is missing!
        assert report.workflow_readiness["d_cosim"].is_runnable is False
        assert "iverilog:libvvp" in report.workflow_readiness["d_cosim"].missing_critical_components


def test_doctor_missing_kicad_symbols_warning(doctor, tmp_path):
    """Assert missing eSim symbol entries in sym-lib-table triggers a WARNING."""
    empty_kicad_table = tmp_path / "sym-lib-table"
    empty_kicad_table.write_text("(sym_lib_table)")

    detections = {
        "ngspice": make_mock_result("ngspice", True, "45.2"),
        "kicad": make_mock_result("kicad", True, "9.0.3"),
        "iverilog": make_mock_result("iverilog", True, "12.0"),
        "ghdl": make_mock_result("ghdl", False),
        "verilator": make_mock_result("verilator", False),
        "llvm": make_mock_result("llvm", False),
    }
    doctor.detector.detect_all.return_value = detections

    with patch.object(SimulationDoctor, "find_kicad_sym_lib_table", return_value=empty_kicad_table):
        report = doctor.run_diagnostics()
        warnings = report.get_items_by_severity(Severity.WARNING)
        assert any("KiCad eSim Symbols Integration" in w.check_name for w in warnings)
