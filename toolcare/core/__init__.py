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

# Gracefully import later phases if available
try:
    from toolcare.core.dependency import DependencyGraph, FlowStatus, ALL_FLOWS, FLOW_LABELS
    from toolcare.core.doctor import SimulationDoctor, DoctorReport, DoctorCheckItem
    from toolcare.core.updater import UpdateEngine, UpdatePlan
    from toolcare.core.config_handler import ConfigHandler, atomic_write
    from toolcare.core.installer import ToolInstaller, InstallResult
except ImportError:
    pass

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
]
