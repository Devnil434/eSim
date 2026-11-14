"""
eSim ToolCare - Automated Tool, Dependency & Configuration Manager for eSim.
"""

__version__ = "2.0.0"
__author__ = "eSim Team / FOSSEE Task 5 Submission"

from toolcare.core.registry import ToolRegistry, ToolSpec
from toolcare.core.detector import ToolDetector, ToolDetectionResult
from toolcare.core.version import ToolStatus, VersionEvaluation
from toolcare.core.dependency import (
    DependencyGraph,
    WorkflowSpec,
    WorkflowStatus,
    WORKFLOW_SIMULATION,
    WORKFLOW_VERIFIER,
    WORKFLOW_DCOSIM,
    WORKFLOW_NGVERI,
    WORKFLOW_NGHDL,
)
from toolcare.core.doctor import SimulationDoctor, DoctorReport, Severity
from toolcare.core.installer import ToolInstaller, InstallResult
from toolcare.core.config_handler import ConfigHandler
from toolcare.core.updater import UpdateEngine, UpdatePlan

__all__ = [
    "__version__",
    "ToolRegistry",
    "ToolSpec",
    "ToolDetector",
    "ToolDetectionResult",
    "ToolStatus",
    "VersionEvaluation",
    "DependencyGraph",
    "WorkflowSpec",
    "WorkflowStatus",
    "WORKFLOW_SIMULATION",
    "WORKFLOW_VERIFIER",
    "WORKFLOW_DCOSIM",
    "WORKFLOW_NGVERI",
    "WORKFLOW_NGHDL",
    "SimulationDoctor",
    "DoctorReport",
    "Severity",
    "ToolInstaller",
    "InstallResult",
    "ConfigHandler",
    "UpdateEngine",
    "UpdatePlan",
]
