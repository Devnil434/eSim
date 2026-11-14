"""
Tests for toolcare.core.dependency simulation flow dependency graph and cascading failure analysis.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from toolcare.core.dependency import (
    ALL_WORKFLOW_IDS,
    WORKFLOW_DCOSIM,
    WORKFLOW_NGHDL,
    WORKFLOW_NGVERI,
    WORKFLOW_SIMULATION,
    WORKFLOW_VERIFIER,
    DependencyGraph,
    WorkflowStatus,
)
from toolcare.core.detector import ToolDetectionResult
from toolcare.core.registry import CompatibilitySpec
from toolcare.core.version import ToolStatus, VersionEvaluation


@pytest.fixture
def dep_graph():
    return DependencyGraph()


def make_mock_detection(
    tool_id: str,
    installed: bool = True,
    version: str = "1.0.0",
    status: ToolStatus = ToolStatus.COMPATIBLE,
) -> ToolDetectionResult:
    """Helper to construct mock ToolDetectionResult objects."""
    eval_res = VersionEvaluation(
        tool_id=tool_id,
        detected_version=version if installed else None,
        status=status,
        message="Mock evaluation",
        min_version="1.0.0",
        recommended_version="1.0.0",
        max_version="2.0.0",
        is_usable=(status in (ToolStatus.COMPATIBLE, ToolStatus.UPGRADE_AVAILABLE)),
    )
    return ToolDetectionResult(
        tool_id=tool_id,
        name=f"Mock {tool_id}",
        is_installed=installed,
        binary_path=Path(f"/mock/bin/{tool_id}") if installed else None,
        detected_version=version if installed else None,
        evaluation=eval_res,
        discovery_source="mock",
    )


def test_standardized_d_cosim_workflow_id(dep_graph):
    """Correction 1: Assert mixed-signal workflow ID is strictly standardized as 'd_cosim'."""
    assert WORKFLOW_DCOSIM == "d_cosim"
    assert "d_cosim" in ALL_WORKFLOW_IDS
    spec = dep_graph.get_workflow("d_cosim")
    assert spec is not None
    assert spec.id == "d_cosim"
    assert "d_cosim" in spec.name


def test_all_five_workflows_defined(dep_graph):
    """Assert all 5 core eSim simulation workflows are defined."""
    workflows = dep_graph.get_all_workflows()
    assert len(workflows) == 5
    ids = {wf.id for wf in workflows}
    assert ids == {"simulation", "verifier", "d_cosim", "ngveri", "nghdl"}


def test_cascading_impact_analysis(dep_graph):
    """Assert get_dependent_workflows correctly traces cascading impacts."""
    # Ngspice is required by 4 workflows
    ngspice_deps = dep_graph.get_dependent_workflows("ngspice")
    assert set(ngspice_deps) == {"simulation", "d_cosim", "ngveri", "nghdl"}

    # Iverilog is required by verifier and d_cosim
    iverilog_deps = dep_graph.get_dependent_workflows("iverilog")
    assert set(iverilog_deps) == {"verifier", "d_cosim"}

    # KiCad is required only by simulation
    kicad_deps = dep_graph.get_dependent_workflows("kicad")
    assert set(kicad_deps) == {"simulation"}

    # GHDL is required only by nghdl
    ghdl_deps = dep_graph.get_dependent_workflows("ghdl")
    assert set(ghdl_deps) == {"nghdl"}

    # Verilator is required only by ngveri
    verilator_deps = dep_graph.get_dependent_workflows("verilator")
    assert set(verilator_deps) == {"ngveri"}


def test_evaluate_workflow_fully_healthy(dep_graph):
    """Assert workflows evaluate as runnable when all mandatory tools and components are present."""
    detections = {
        "kicad": make_mock_detection("kicad", True, "9.0.3"),
        "ngspice": make_mock_detection("ngspice", True, "45.2"),
    }
    components = {
        "kicad": ["eeschema"],
        "ngspice": ["analog.cm", "spice2poly.cm"],
    }
    status = dep_graph.evaluate_workflow("simulation", detections, components)
    assert status.is_runnable is True
    assert len(status.missing_mandatory_tools) == 0
    assert len(status.missing_critical_components) == 0


def test_evaluate_workflow_missing_tool(dep_graph):
    """Assert workflow is blocked when a mandatory tool is missing."""
    detections = {
        "kicad": make_mock_detection("kicad", False, None, ToolStatus.NOT_INSTALLED),
        "ngspice": make_mock_detection("ngspice", True, "45.2"),
    }
    components = {"ngspice": ["analog.cm", "spice2poly.cm"]}
    status = dep_graph.evaluate_workflow("simulation", detections, components)
    assert status.is_runnable is False
    assert "kicad" in status.missing_mandatory_tools


def test_workflow_specific_component_isolation(dep_graph):
    """
    Correction 5: Missing ivlng or libvvp must block d_cosim
    WITHOUT blocking standalone analog simulation.
    """
    detections = {
        "kicad": make_mock_detection("kicad", True, "9.0.3"),
        "ngspice": make_mock_detection("ngspice", True, "45.2"),
        "iverilog": make_mock_detection("iverilog", True, "12.0"),
    }
    # Notice: Ngspice has analog.cm and spice2poly.cm, but NOT ivlng.
    # Iverilog has vvp, but NOT libvvp.
    components = {
        "kicad": ["eeschema"],
        "ngspice": ["analog.cm", "spice2poly.cm"],
        "iverilog": ["vvp"],
    }

    # 1. Analog simulation must be RUNNABLE!
    sim_status = dep_graph.evaluate_workflow("simulation", detections, components)
    assert sim_status.is_runnable is True
    assert len(sim_status.missing_critical_components) == 0

    # 2. Verifier must be RUNNABLE!
    ver_status = dep_graph.evaluate_workflow("verifier", detections, components)
    assert ver_status.is_runnable is True

    # 3. d_cosim must be BLOCKED due to missing ivlng and libvvp!
    dcosim_status = dep_graph.evaluate_workflow("d_cosim", detections, components)
    assert dcosim_status.is_runnable is False
    assert "ngspice:ivlng" in dcosim_status.missing_critical_components
    assert "iverilog:libvvp" in dcosim_status.missing_critical_components


def test_evaluate_workflow_incompatible_tool(dep_graph):
    """Assert workflow is blocked when a mandatory tool is outdated/incompatible."""
    detections = {
        "kicad": make_mock_detection("kicad", True, "5.1.9", ToolStatus.INCOMPATIBLE_OLD),
        "ngspice": make_mock_detection("ngspice", True, "45.2"),
    }
    components = {
        "kicad": ["eeschema"],
        "ngspice": ["analog.cm", "spice2poly.cm"],
    }
    status = dep_graph.evaluate_workflow("simulation", detections, components)
    assert status.is_runnable is False
    assert "kicad" in status.incompatible_tools
