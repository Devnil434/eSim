"""
eSim ToolCare - Automated Tool, Dependency & Configuration Manager for eSim.
"""

__version__ = "2.0.0"
__author__ = "eSim Team / FOSSEE Task 5 Submission"

from toolcare.core.registry import ToolRegistry, ToolSpec
from toolcare.core.detector import ToolDetector, ToolDetectionResult
from toolcare.core.version import ToolStatus, VersionEvaluation

# Gracefully import later phases if available
try:
    from toolcare.core.dependency import DependencyGraph, FlowStatus
    from toolcare.core.doctor import SimulationDoctor, DoctorReport
    from toolcare.core.updater import UpdateEngine, UpdatePlan
    from toolcare.core.config_handler import ConfigHandler
    from toolcare.core.installer import ToolInstaller, InstallResult
except ImportError:
    pass

__all__ = [
    "__version__",
    "ToolRegistry",
    "ToolSpec",
    "ToolDetector",
    "ToolDetectionResult",
    "ToolStatus",
    "VersionEvaluation",
]
