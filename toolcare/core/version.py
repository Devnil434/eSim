"""
toolcare.core.version - Semantic version parsing, comparison, and compatibility evaluation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from toolcare.core.registry import CompatibilitySpec


class ToolStatus(str, Enum):
    """Categorical status of a detected external EDA tool."""
    NOT_INSTALLED = "NOT_INSTALLED"
    VERSION_UNKNOWN = "VERSION_UNKNOWN"
    INCOMPATIBLE_OLD = "INCOMPATIBLE_OLD"
    INCOMPATIBLE_NEW = "INCOMPATIBLE_NEW"
    UPGRADE_AVAILABLE = "UPGRADE_AVAILABLE"
    COMPATIBLE = "COMPATIBLE"


@dataclass(frozen=True)
class VersionEvaluation:
    """Detailed version evaluation result against eSim compatibility bounds."""
    tool_id: str
    detected_version: Optional[str]
    status: ToolStatus
    message: str
    min_version: str
    recommended_version: str
    max_version: Optional[str]
    is_usable: bool

    @property
    def is_compatible(self) -> bool:
        """True if the tool is strictly compatible with eSim recommended bounds."""
        return self.status == ToolStatus.COMPATIBLE


def parse_version_tuple(v_str: str) -> Tuple[int, ...]:
    """
    Extract a tuple of integers from an arbitrary version string.

    Handles non-standard EDA strings:
    - "45.2" -> (45, 2)
    - "v12-20220611" -> (12, 20220611)
    - "9.0.3" -> (9, 0, 3)
    - "9.0.3-rc1" -> (9, 0, 3, 1)
    - "Verilator 5.032" -> (5, 32)
    """
    if not v_str or not isinstance(v_str, str):
        return (0,)

    # Find all contiguous digit sequences
    matches = re.findall(r"\d+", v_str)
    if not matches:
        return (0,)

    return tuple(int(m) for m in matches)


def compare_versions(v1: str, v2: str) -> int:
    """
    Compare two version strings.

    Returns:
        -1 if v1 < v2
         0 if v1 == v2
         1 if v1 > v2
    """
    t1 = list(parse_version_tuple(v1))
    t2 = list(parse_version_tuple(v2))

    # Pad with zeros to match component length
    max_len = max(len(t1), len(t2))
    t1.extend([0] * (max_len - len(t1)))
    t2.extend([0] * (max_len - len(t2)))

    if t1 < t2:
        return -1
    elif t1 > t2:
        return 1
    return 0


def evaluate_compatibility(
    tool_id: str,
    detected_version: Optional[str],
    spec: CompatibilitySpec,
) -> VersionEvaluation:
    """
    Evaluate detected version against minimum, recommended, and maximum bounds.
    """
    if detected_version is None or detected_version.strip() == "":
        return VersionEvaluation(
            tool_id=tool_id,
            detected_version=None,
            status=ToolStatus.NOT_INSTALLED,
            message=f"{tool_id} is not installed on the system.",
            min_version=spec.min_version,
            recommended_version=spec.recommended_version,
            max_version=spec.max_version,
            is_usable=False,
        )

    t_detected = parse_version_tuple(detected_version)
    if t_detected == (0,) and detected_version.strip() not in ("0", "0.0"):
        return VersionEvaluation(
            tool_id=tool_id,
            detected_version=detected_version,
            status=ToolStatus.VERSION_UNKNOWN,
            message=f"{tool_id} binary located but version string '{detected_version}' could not be parsed.",
            min_version=spec.min_version,
            recommended_version=spec.recommended_version,
            max_version=spec.max_version,
            is_usable=False,
        )

    # Check minimum bound
    if compare_versions(detected_version, spec.min_version) < 0:
        return VersionEvaluation(
            tool_id=tool_id,
            detected_version=detected_version,
            status=ToolStatus.INCOMPATIBLE_OLD,
            message=(
                f"Installed version {detected_version} is older than minimum supported {spec.min_version}. "
                f"Recommended: {spec.recommended_version}."
            ),
            min_version=spec.min_version,
            recommended_version=spec.recommended_version,
            max_version=spec.max_version,
            is_usable=False,
        )

    # Check maximum bound (if defined)
    if spec.max_version and compare_versions(detected_version, spec.max_version) > 0:
        return VersionEvaluation(
            tool_id=tool_id,
            detected_version=detected_version,
            status=ToolStatus.INCOMPATIBLE_NEW,
            message=(
                f"Installed version {detected_version} exceeds maximum tested {spec.max_version}. "
                "Potential ABI breakage with eSim co-simulation models."
            ),
            min_version=spec.min_version,
            recommended_version=spec.recommended_version,
            max_version=spec.max_version,
            is_usable=False,
        )

    # Check upgrade availability
    if compare_versions(detected_version, spec.recommended_version) < 0:
        return VersionEvaluation(
            tool_id=tool_id,
            detected_version=detected_version,
            status=ToolStatus.UPGRADE_AVAILABLE,
            message=(
                f"Installed version {detected_version} is compatible, but an upgrade to recommended "
                f"{spec.recommended_version} is available."
            ),
            min_version=spec.min_version,
            recommended_version=spec.recommended_version,
            max_version=spec.max_version,
            is_usable=True,
        )

    # Compatible
    return VersionEvaluation(
        tool_id=tool_id,
        detected_version=detected_version,
        status=ToolStatus.COMPATIBLE,
        message=f"Installed version {detected_version} satisfies eSim compatibility requirements.",
        min_version=spec.min_version,
        recommended_version=spec.recommended_version,
        max_version=spec.max_version,
        is_usable=True,
    )
