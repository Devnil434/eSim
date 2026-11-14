"""
toolcare.core.dependency - Simulation flow dependency graph and workflow readiness engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from toolcare.core.detector import ToolDetectionResult
from toolcare.logger import get_logger

logger = get_logger("dependency")

# Standardized workflow IDs
WORKFLOW_SIMULATION = "simulation"
WORKFLOW_VERIFIER = "verifier"
WORKFLOW_DCOSIM = "d_cosim"  # Standardized mixed-signal workflow ID
WORKFLOW_NGVERI = "ngveri"
WORKFLOW_NGHDL = "nghdl"

ALL_WORKFLOW_IDS = [
    WORKFLOW_SIMULATION,
    WORKFLOW_VERIFIER,
    WORKFLOW_DCOSIM,
    WORKFLOW_NGVERI,
    WORKFLOW_NGHDL,
]


@dataclass(frozen=True)
class ToolRequirement:
    """Specific tool and component requirement for a simulation workflow."""
    tool_id: str
    is_mandatory: bool = True
    critical_components: List[str] = field(default_factory=list)
    description: str = ""


@dataclass(frozen=True)
class WorkflowSpec:
    """Specification of an eSim simulation workflow."""
    id: str
    name: str
    description: str
    requirements: List[ToolRequirement] = field(default_factory=list)

    @property
    def mandatory_tool_ids(self) -> List[str]:
        return [req.tool_id for req in self.requirements if req.is_mandatory]

    @property
    def optional_tool_ids(self) -> List[str]:
        return [req.tool_id for req in self.requirements if not req.is_mandatory]

    def get_requirement(self, tool_id: str) -> Optional[ToolRequirement]:
        for req in self.requirements:
            if req.tool_id.lower() == tool_id.lower():
                return req
        return None


@dataclass(frozen=True)
class WorkflowStatus:
    """Comprehensive readiness evaluation of an eSim simulation workflow."""
    workflow_id: str
    workflow_name: str
    is_runnable: bool
    missing_mandatory_tools: List[str]
    missing_optional_tools: List[str]
    incompatible_tools: List[str]
    missing_critical_components: List[str]
    diagnostic_messages: List[str]

    @property
    def has_issues(self) -> bool:
        return (
            bool(self.missing_mandatory_tools)
            or bool(self.incompatible_tools)
            or bool(self.missing_critical_components)
        )


class DependencyGraph:
    """
    Simulation flow dependency graph for eSim ToolCare.
    Maps simulation workflows to tool requirements and performs cascading impact analysis.
    """

    def __init__(self):
        self._workflows: Dict[str, WorkflowSpec] = {}
        self._build_default_workflow_graph()

    def _build_default_workflow_graph(self) -> None:
        """
        Define the 5 authoritative eSim simulation workflows with explicit
        mandatory/optional tools and workflow-specific component requirements.
        """
        # 1. Analog Simulation
        self._workflows[WORKFLOW_SIMULATION] = WorkflowSpec(
            id=WORKFLOW_SIMULATION,
            name="Analog Simulation",
            description="Schematic capture and SPICE circuit simulation.",
            requirements=[
                ToolRequirement(
                    tool_id="kicad",
                    is_mandatory=True,
                    critical_components=["eeschema"],
                    description="Schematic editor for circuit capture and netlist generation.",
                ),
                ToolRequirement(
                    tool_id="ngspice",
                    is_mandatory=True,
                    critical_components=["analog.cm", "spice2poly.cm"],
                    description="SPICE solver with standard analog code models.",
                ),
            ],
        )

        # 2. Verilog Digital Verifier
        self._workflows[WORKFLOW_VERIFIER] = WorkflowSpec(
            id=WORKFLOW_VERIFIER,
            name="Verilog Digital Verifier",
            description="Standalone Verilog HDL compilation and verification.",
            requirements=[
                ToolRequirement(
                    tool_id="iverilog",
                    is_mandatory=True,
                    critical_components=["vvp"],
                    description="Icarus Verilog compiler and VVP simulation runtime.",
                ),
            ],
        )

        # 3. Digital / Mixed-Signal Co-Simulation (d_cosim)
        self._workflows[WORKFLOW_DCOSIM] = WorkflowSpec(
            id=WORKFLOW_DCOSIM,
            name="Digital/Mixed-Signal Co-Simulation (d_cosim)",
            description="Mixed-signal co-simulation bridging Ngspice and Icarus Verilog via ivlng.",
            requirements=[
                ToolRequirement(
                    tool_id="ngspice",
                    is_mandatory=True,
                    critical_components=["ivlng"],
                    description="Ngspice engine with ivlng dynamic bridge adapter.",
                ),
                ToolRequirement(
                    tool_id="iverilog",
                    is_mandatory=True,
                    critical_components=["libvvp"],
                    description="Icarus Verilog with exported libvvp shared library.",
                ),
            ],
        )

        # 4. Verilog Model Synthesis (ngveri)
        self._workflows[WORKFLOW_NGVERI] = WorkflowSpec(
            id=WORKFLOW_NGVERI,
            name="Verilog Model Synthesis (ngveri)",
            description="Synthesizes Verilog modules into C++ XSPICE code models for Ngspice.",
            requirements=[
                ToolRequirement(
                    tool_id="verilator",
                    is_mandatory=True,
                    critical_components=["verilator_bin"],
                    description="Verilator v5+ C++ model compiler.",
                ),
                ToolRequirement(
                    tool_id="llvm",
                    is_mandatory=True,
                    critical_components=["clang"],
                    description="C++ compilation toolchain (clang/gcc) and make.",
                ),
                ToolRequirement(
                    tool_id="ngspice",
                    is_mandatory=True,
                    critical_components=["analog.cm"],
                    description="Ngspice simulation engine.",
                ),
            ],
        )

        # 5. VHDL Mixed-Signal Co-Simulation (nghdl)
        self._workflows[WORKFLOW_NGHDL] = WorkflowSpec(
            id=WORKFLOW_NGHDL,
            name="VHDL Mixed-Signal Co-Simulation (nghdl)",
            description="Mixed-signal VHDL co-simulation using GHDL LLVM backend and IPC sockets.",
            requirements=[
                ToolRequirement(
                    tool_id="ghdl",
                    is_mandatory=True,
                    critical_components=["ghdl-llvm"],
                    description="GHDL simulator compiled strictly with LLVM backend.",
                ),
                ToolRequirement(
                    tool_id="llvm",
                    is_mandatory=True,
                    critical_components=["clang"],
                    description="LLVM/Clang compiler toolchain.",
                ),
                ToolRequirement(
                    tool_id="ngspice",
                    is_mandatory=True,
                    critical_components=["ghdl.cm"],
                    description="Ngspice with ghdl.cm code model.",
                ),
            ],
        )

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowSpec]:
        """Retrieve workflow specification by ID."""
        return self._workflows.get(workflow_id.lower())

    def get_all_workflows(self) -> List[WorkflowSpec]:
        """Return all supported workflow specifications."""
        return list(self._workflows.values())

    def get_required_tools(self, workflow_id: str, mandatory_only: bool = True) -> List[str]:
        """Return the list of tool IDs required by a specific workflow."""
        wf = self.get_workflow(workflow_id)
        if not wf:
            raise KeyError(f"Unknown simulation workflow: '{workflow_id}'")
        if mandatory_only:
            return wf.mandatory_tool_ids
        return [req.tool_id for req in wf.requirements]

    def get_dependent_workflows(self, tool_id: str) -> List[str]:
        """
        Cascading impact analysis: returns all workflows that depend on the specified tool.
        """
        tid = tool_id.lower()
        dependent = []
        for wf_id, spec in self._workflows.items():
            if any(req.tool_id.lower() == tid for req in spec.requirements):
                dependent.append(wf_id)
        return dependent

    def evaluate_workflow(
        self,
        workflow_id: str,
        detection_results: Dict[str, ToolDetectionResult],
        verified_components: Optional[Dict[str, List[str]]] = None,
    ) -> WorkflowStatus:
        """
        Evaluate workflow readiness against detected tools and verified sub-components.
        Component failures are strictly workflow-specific: missing components only
        block workflows that explicitly require them.
        """
        wf = self.get_workflow(workflow_id)
        if not wf:
            raise KeyError(f"Unknown simulation workflow: '{workflow_id}'")

        missing_mandatory: List[str] = []
        missing_optional: List[str] = []
        incompatible_tools: List[str] = []
        missing_components: List[str] = []
        diagnostics: List[str] = []

        components_map = verified_components or {}

        for req in wf.requirements:
            tid = req.tool_id.lower()
            det = detection_results.get(tid)

            # Check binary installation
            if not det or not det.is_installed:
                if req.is_mandatory:
                    missing_mandatory.append(tid)
                    diagnostics.append(f"Mandatory tool '{tid}' is not installed.")
                else:
                    missing_optional.append(tid)
                    diagnostics.append(f"Optional tool '{tid}' is not installed.")
                continue

            # Check version compatibility
            if not det.is_usable:
                incompatible_tools.append(tid)
                diagnostics.append(
                    f"Tool '{tid}' version {det.detected_version} is incompatible: {det.evaluation.message}"
                )

            # Check critical components (isolated per tool requirement)
            tool_verified = components_map.get(tid, [])
            for comp in req.critical_components:
                if comp not in tool_verified:
                    comp_tag = f"{tid}:{comp}"
                    missing_components.append(comp_tag)
                    diagnostics.append(f"Required sub-component '{comp}' for '{tid}' was not found.")

        is_runnable = (
            len(missing_mandatory) == 0
            and len(incompatible_tools) == 0
            and len(missing_components) == 0
        )

        return WorkflowStatus(
            workflow_id=wf.id,
            workflow_name=wf.name,
            is_runnable=is_runnable,
            missing_mandatory_tools=missing_mandatory,
            missing_optional_tools=missing_optional,
            incompatible_tools=incompatible_tools,
            missing_critical_components=missing_components,
            diagnostic_messages=diagnostics,
        )

    def evaluate_all_workflows(
        self,
        detection_results: Dict[str, ToolDetectionResult],
        verified_components: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, WorkflowStatus]:
        """Evaluate readiness for all 5 simulation workflows."""
        results: Dict[str, WorkflowStatus] = {}
        for wf_id in self._workflows:
            results[wf_id] = self.evaluate_workflow(
                wf_id,
                detection_results,
                verified_components,
            )
        return results
