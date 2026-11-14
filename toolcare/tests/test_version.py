"""
Tests for toolcare.core.version semantic version parsing and boundary evaluation.
"""

import pytest
from toolcare.core.registry import CompatibilitySpec
from toolcare.core.version import (
    ToolStatus,
    VersionEvaluation,
    compare_versions,
    evaluate_compatibility,
    parse_version_tuple,
)


def test_parse_version_tuple_standard():
    """Assert standard dotted decimal versions are parsed into integer tuples."""
    assert parse_version_tuple("45.2") == (45, 2)
    assert parse_version_tuple("9.0.3") == (9, 0, 3)
    assert parse_version_tuple("1.2.3.4") == (1, 2, 3, 4)


def test_parse_version_tuple_eda_quirks():
    """Assert non-standard EDA strings with prefixes, dates, and build tags parse cleanly."""
    assert parse_version_tuple("v12-20220611") == (12, 20220611)
    assert parse_version_tuple("9.0.3-rc1") == (9, 0, 3, 1)
    assert parse_version_tuple("Verilator 5.032") == (5, 32)
    assert parse_version_tuple("GHDL 4.1.0 (Ubuntu)") == (4, 1, 0)


def test_parse_version_tuple_invalid_or_empty():
    """Assert invalid or empty strings return the fallback (0,)."""
    assert parse_version_tuple("") == (0,)
    assert parse_version_tuple(None) == (0,)
    assert parse_version_tuple("unknown_build") == (0,)


def test_compare_versions_ordering():
    """Assert version comparison returns -1, 0, 1 accurately across depths."""
    assert compare_versions("42.0", "45.2") == -1
    assert compare_versions("45.2", "42.0") == 1
    assert compare_versions("45.2", "45.2") == 0
    assert compare_versions("10.0", "10.0.0") == 0
    assert compare_versions("9.0.3", "7.0.0") == 1
    assert compare_versions("5.006", "5.032") == -1


def test_evaluate_compatibility_not_installed():
    """Assert None or empty string evaluates to NOT_INSTALLED."""
    spec = CompatibilitySpec(min_version="38.0", recommended_version="45.2")
    res = evaluate_compatibility("ngspice", None, spec)
    assert res.status == ToolStatus.NOT_INSTALLED
    assert res.is_usable is False
    assert res.is_compatible is False


def test_evaluate_compatibility_incompatible_old():
    """Assert versions below min_version evaluate to INCOMPATIBLE_OLD."""
    spec = CompatibilitySpec(min_version="38.0", recommended_version="45.2")
    res = evaluate_compatibility("ngspice", "34.0", spec)
    assert res.status == ToolStatus.INCOMPATIBLE_OLD
    assert res.is_usable is False
    assert "older than minimum" in res.message


def test_evaluate_compatibility_incompatible_new():
    """Assert versions above max_version evaluate to INCOMPATIBLE_NEW."""
    spec = CompatibilitySpec(min_version="38.0", recommended_version="45.2", max_version="46.0")
    res = evaluate_compatibility("ngspice", "47.0", spec)
    assert res.status == ToolStatus.INCOMPATIBLE_NEW
    assert res.is_usable is False
    assert "exceeds maximum tested" in res.message


def test_evaluate_compatibility_upgrade_available():
    """Assert versions between min and recommended evaluate to UPGRADE_AVAILABLE."""
    spec = CompatibilitySpec(min_version="38.0", recommended_version="45.2")
    res = evaluate_compatibility("ngspice", "42.0", spec)
    assert res.status == ToolStatus.UPGRADE_AVAILABLE
    assert res.is_usable is True
    assert res.is_compatible is False
    assert "upgrade to recommended" in res.message


def test_evaluate_compatibility_compatible():
    """Assert versions meeting or exceeding recommended evaluate to COMPATIBLE."""
    spec = CompatibilitySpec(min_version="38.0", recommended_version="45.2", max_version="46.0")
    res = evaluate_compatibility("ngspice", "45.2", spec)
    assert res.status == ToolStatus.COMPATIBLE
    assert res.is_usable is True
    assert res.is_compatible is True

    # Slightly newer than recommended but <= max
    res2 = evaluate_compatibility("ngspice", "45.5", spec)
    assert res2.status == ToolStatus.COMPATIBLE
    assert res2.is_usable is True
