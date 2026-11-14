"""
toolcare.core - Core business logic and engine modules.
"""

from toolcare.core.registry import ToolRegistry, ToolSpec, CompatibilitySpec, ArchiveSpec
from toolcare.core.version import (
    ToolStatus,
    VersionEvaluation,
    parse_version_tuple,
    compare_versions,
    evaluate_compatibility,
)
from toolcare.core.detector import ToolDetector, ToolDetectionResult
from toolcare.core.dependency import (
    DependencyGraph,
    WorkflowSpec,
    WorkflowStatus,
    ToolRequirement,
    ALL_WORKFLOW_IDS,
    WORKFLOW_SIMULATION,
    WORKFLOW_VERIFIER,
    WORKFLOW_DCOSIM,
    WORKFLOW_NGVERI,
    WORKFLOW_NGHDL,
)
from toolcare.core.doctor import (
    SimulationDoctor,
    DoctorReport,
    DoctorCheckItem,
    Severity,
)
from toolcare.core.installer import ToolInstaller, InstallResult
from toolcare.core.config_handler import ConfigHandler, atomic_write
from toolcare.core.updater import UpdateEngine, UpdatePlan

__all__ = [
    "ToolRegistry",
    "ToolSpec",
    "CompatibilitySpec",
    "ArchiveSpec",
    "ToolStatus",
    "VersionEvaluation",
    "parse_version_tuple",
    "compare_versions",
    "evaluate_compatibility",
    "ToolDetector",
    "ToolDetectionResult",
    "DependencyGraph",
    "WorkflowSpec",
    "WorkflowStatus",
    "ToolRequirement",
    "ALL_WORKFLOW_IDS",
    "WORKFLOW_SIMULATION",
    "WORKFLOW_VERIFIER",
    "WORKFLOW_DCOSIM",
    "WORKFLOW_NGVERI",
    "WORKFLOW_NGHDL",
    "SimulationDoctor",
    "DoctorReport",
    "DoctorCheckItem",
    "Severity",
    "ToolInstaller",
    "InstallResult",
    "ConfigHandler",
    "atomic_write",
    "UpdateEngine",
    "UpdatePlan",
]
