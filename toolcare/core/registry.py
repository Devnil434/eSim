"""
toolcare.core.registry - Declarative tool specifications and catalog loader.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from toolcare.logger import get_logger

logger = get_logger("registry")


@dataclass(frozen=True)
class ArchiveSpec:
    """Specification for direct vendor archive downloads."""
    url: str
    sha256: str
    archive_type: str = "archive"
    strip_components: int = 0

    @property
    def is_verified(self) -> bool:
        """Return True if the archive URL and SHA-256 hash are confirmed."""
        return self.url != "UNVERIFIED" and self.sha256 != "UNVERIFIED" and len(self.sha256) == 64


@dataclass(frozen=True)
class CompatibilitySpec:
    """Version constraints for eSim compatibility."""
    min_version: str
    recommended_version: str
    max_version: Optional[str] = None
    notes: str = ""


@dataclass(frozen=True)
class ToolSpec:
    """Complete declarative specification of an external EDA tool."""
    id: str
    name: str
    category: str
    executables: Dict[str, List[str]]
    version_command: List[str]
    version_regex: str
    compatibility: CompatibilitySpec
    package_ids: Dict[str, str] = field(default_factory=dict)
    archives: Dict[str, ArchiveSpec] = field(default_factory=dict)
    critical_components: List[str] = field(default_factory=list)

    def get_executables(self, os_name: str) -> List[str]:
        """Return the executable binary names for the specified operating system."""
        return self.executables.get(os_name.lower(), [])

    def get_package_id(self, manager: str) -> Optional[str]:
        """
        Get the package manager identifier, returning None if unverified or absent.
        """
        pkg_id = self.package_ids.get(manager.lower())
        if not pkg_id or pkg_id.upper() == "UNVERIFIED":
            return None
        return pkg_id

    def is_package_id_verified(self, manager: str) -> bool:
        """Check whether a package manager package ID is confirmed and verified."""
        pkg_id = self.package_ids.get(manager.lower())
        return pkg_id is not None and pkg_id.upper() != "UNVERIFIED"

    def get_archive(self, arch_key: str = "windows_x64") -> Optional[ArchiveSpec]:
        """Return the archive specification for the requested platform architecture."""
        return self.archives.get(arch_key)


class ToolRegistry:
    """
    Catalog manager for all registered external tools.
    Loads and validates tool definitions from tools.json.
    """

    def __init__(self, data_path: Optional[Path] = None):
        self.data_path = data_path or self.default_registry_path()
        self._tools: Dict[str, ToolSpec] = {}
        self.load()

    @staticmethod
    def default_registry_path() -> Path:
        """Locate the default tools.json catalog file."""
        return Path(__file__).resolve().parent.parent / "data" / "tools.json"

    def load(self) -> None:
        """
        Parse and validate tools.json into immutable ToolSpec objects.
        """
        if not self.data_path.is_file():
            logger.error("Registry file not found at %s", self.data_path)
            raise FileNotFoundError(f"Tool registry JSON not found: {self.data_path}")

        try:
            with open(self.data_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as exc:
            logger.error("Failed to parse registry JSON %s: %s", self.data_path, exc)
            raise ValueError(f"Malformed tools.json: {exc}") from exc

        tools_dict = raw_data.get("tools", {})
        parsed_tools: Dict[str, ToolSpec] = {}

        for tool_id, tdata in tools_dict.items():
            # Validate regex pattern compiles
            regex_str = tdata.get("version_regex", "")
            try:
                re.compile(regex_str)
            except re.error as re_err:
                logger.warning("Tool %s has invalid regex '%s': %s", tool_id, regex_str, re_err)

            # Parse compatibility
            compat_raw = tdata.get("compatibility", {})
            compat_spec = CompatibilitySpec(
                min_version=compat_raw.get("min_version", "0.0.0"),
                recommended_version=compat_raw.get("recommended_version", "0.0.0"),
                max_version=compat_raw.get("max_version"),
                notes=compat_raw.get("notes", ""),
            )

            # Parse archives
            archives_dict: Dict[str, ArchiveSpec] = {}
            for arch_key, arch_raw in tdata.get("archives", {}).items():
                archives_dict[arch_key] = ArchiveSpec(
                    url=arch_raw.get("url", "UNVERIFIED"),
                    sha256=arch_raw.get("sha256", "UNVERIFIED"),
                    archive_type=arch_raw.get("archive_type", "archive"),
                    strip_components=arch_raw.get("strip_components", 0),
                )

            parsed_tools[tool_id] = ToolSpec(
                id=tool_id,
                name=tdata.get("name", tool_id),
                category=tdata.get("category", "general"),
                executables=tdata.get("executables", {}),
                version_command=tdata.get("version_command", ["--version"]),
                version_regex=regex_str,
                compatibility=compat_spec,
                package_ids=tdata.get("package_ids", {}),
                archives=archives_dict,
                critical_components=tdata.get("critical_components", []),
            )

        self._tools = parsed_tools
        logger.debug("Successfully loaded %d tools into registry", len(self._tools))

    def get_tool(self, tool_id: str) -> Optional[ToolSpec]:
        """Retrieve a tool specification by its unique ID."""
        return self._tools.get(tool_id.lower())

    def get_all_tools(self) -> List[ToolSpec]:
        """Return all registered tools."""
        return list(self._tools.values())

    def get_tools_by_category(self, category: str) -> List[ToolSpec]:
        """Filter registered tools by category."""
        target = category.lower()
        return [t for t in self._tools.values() if t.category.lower() == target]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, tool_id: str) -> bool:
        return tool_id.lower() in self._tools
