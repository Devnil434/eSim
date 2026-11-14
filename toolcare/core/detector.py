"""
toolcare.core.detector - Multi-tier binary discovery and version detection engine.
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from toolcare.core.registry import ToolRegistry, ToolSpec
from toolcare.core.version import ToolStatus, VersionEvaluation, evaluate_compatibility
from toolcare.logger import get_logger
from toolcare.platform_utils import (
    IS_LINUX,
    IS_MAC,
    IS_WINDOWS,
    SYSTEM_OS,
    get_esim_config_dir,
    get_nghdl_config_dir,
    run_safe_command,
)

logger = get_logger("detector")

# Known environment variables mapping to specific tools
ENV_OVERRIDE_VARS = {
    "ngspice": ["ESIM_NGSPICE", "NGSPICE_PATH"],
    "iverilog": ["ESIM_IVERILOG", "IVERILOG_PATH"],
    "kicad": ["KICAD_PATH", "ESIM_KICAD"],
    "ghdl": ["GHDL_PATH", "ESIM_GHDL"],
    "verilator": ["VERILATOR_ROOT", "VERILATOR_PATH"],
    "llvm": ["LLVM_PATH", "CLANG_PATH"],
}


@dataclass(frozen=True)
class ToolDetectionResult:
    """Complete result of detecting an external tool binary and its version."""
    tool_id: str
    name: str
    is_installed: bool
    binary_path: Optional[Path]
    detected_version: Optional[str]
    evaluation: VersionEvaluation
    discovery_source: str

    @property
    def status(self) -> ToolStatus:
        return self.evaluation.status

    @property
    def is_compatible(self) -> bool:
        return self.evaluation.is_compatible

    @property
    def is_usable(self) -> bool:
        return self.evaluation.is_usable


class ToolDetector:
    """
    Non-invasive EDA tool discovery engine following eSim's established resolution hierarchy.
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        esim_root: Optional[Path] = None,
    ):
        self.registry = registry or ToolRegistry()
        self.esim_root = esim_root or self._find_esim_root()

    @staticmethod
    def _find_esim_root() -> Optional[Path]:
        """Detect the root repository path of eSim if running within an eSim checkout."""
        candidate = Path(__file__).resolve().parent.parent.parent
        if (candidate / "src").is_dir() and (candidate / "library").is_dir():
            return candidate
        return None

    def find_binary(self, spec: ToolSpec) -> Tuple[Optional[Path], str]:
        """
        Locate executable binary following 5-tier discovery hierarchy:
        1. Explicit Environment Variable Overrides
        2. eSim Configuration Files (~/.nghdl/config.ini, ~/.esim/config.ini)
        3. Bundled eSim & MSYS2 mingw64 Trees
        4. Standard Operating System Install Directories
        5. System PATH via shutil.which
        """
        tool_id = spec.id.lower()
        executables = spec.get_executables(SYSTEM_OS)
        if not executables:
            # Fallback for cross-platform matching
            executables = [f"{tool_id}.exe"] if IS_WINDOWS else [tool_id]

        # ---------------------------------------------------------------------
        # Tier 1: Explicit Environment Variable Overrides
        # ---------------------------------------------------------------------
        env_vars = ENV_OVERRIDE_VARS.get(tool_id, [])
        for ev in env_vars:
            val = os.environ.get(ev)
            if val:
                p = Path(val).resolve()
                if p.is_file() and os.access(p, os.X_OK):
                    logger.debug("Found %s via environment variable %s: %s", tool_id, ev, p)
                    return p, f"env:{ev}"
                elif p.is_dir():
                    for exe_name in executables:
                        candidate = p / exe_name
                        if candidate.is_file() and os.access(candidate, os.X_OK):
                            logger.debug("Found %s in env dir %s: %s", tool_id, ev, candidate)
                            return candidate, f"env:{ev}"

        # ---------------------------------------------------------------------
        # Tier 2: eSim Configuration Files (~/.nghdl/config.ini, ~/.esim/config.ini)
        # ---------------------------------------------------------------------
        nghdl_ini = get_nghdl_config_dir() / "config.ini"
        if nghdl_ini.is_file():
            try:
                cp = configparser.ConfigParser()
                cp.read(str(nghdl_ini), encoding="utf-8")
                # Check [COSIM], [NGHDL], [COMPILER] sections
                for sec in cp.sections():
                    for key, val in cp.items(sec):
                        if tool_id in key.lower() or any(e.lower() in val.lower() for e in executables):
                            cand = Path(val).resolve()
                            if cand.is_file() and os.access(cand, os.X_OK):
                                return cand, f"nghdl_config:{sec}.{key}"
                            elif cand.is_dir():
                                for exe_name in executables:
                                    sub_cand = cand / exe_name
                                    if sub_cand.is_file() and os.access(sub_cand, os.X_OK):
                                        return sub_cand, f"nghdl_config:{sec}.{key}"
            except Exception as e:
                logger.debug("Error parsing nghdl config: %s", e)

        # ---------------------------------------------------------------------
        # Tier 3: Bundled eSim & MSYS2 mingw64 Trees
        # ---------------------------------------------------------------------
        bundled_dirs: List[Path] = []
        if self.esim_root:
            bundled_dirs.extend([
                self.esim_root / "library" / "bin",
                self.esim_root / "tools",
                self.esim_root / "tools" / tool_id / "bin",
            ])

        if IS_WINDOWS:
            bundled_dirs.append(Path("C:/msys64/mingw64/bin"))

        for b_dir in bundled_dirs:
            if b_dir.is_dir():
                for exe_name in executables:
                    cand = b_dir / exe_name
                    if cand.is_file() and os.access(cand, os.X_OK):
                        logger.debug("Found %s in bundled tree: %s", tool_id, cand)
                        return cand, f"bundled:{b_dir}"

        # ---------------------------------------------------------------------
        # Tier 4: Standard Operating System Install Directories
        # ---------------------------------------------------------------------
        system_dirs: List[Path] = []
        if IS_WINDOWS:
            # Check Spice64, KiCad, Icarus standard folders
            system_dirs.extend([
                Path("C:/Spice64/bin"),
                Path("C:/iverilog/bin"),
                Path("C:/Program Files/KiCad/9.0/bin"),
                Path("C:/Program Files/KiCad/8.0/bin"),
                Path("C:/Program Files/KiCad/7.0/bin"),
                Path("C:/Program Files/KiCad/bin"),
                Path("C:/Program Files/LLVM/bin"),
            ])
            # Glob wildcard KiCad versions in Program Files if present
            pf = Path("C:/Program Files/KiCad")
            if pf.is_dir():
                for sub in pf.glob("*/bin"):
                    system_dirs.append(sub)
        elif IS_LINUX:
            system_dirs.extend([
                Path("/usr/bin"),
                Path("/usr/local/bin"),
                Path("/opt/kicad/bin"),
                Path("/usr/lib/ngspice"),
            ])
        elif IS_MAC:
            system_dirs.extend([
                Path("/opt/homebrew/bin"),
                Path("/usr/local/bin"),
                Path("/Applications/KiCad/KiCad.app/Contents/MacOS"),
            ])

        for s_dir in system_dirs:
            if s_dir.is_dir():
                for exe_name in executables:
                    cand = s_dir / exe_name
                    if cand.is_file() and os.access(cand, os.X_OK):
                        logger.debug("Found %s in standard OS dir: %s", tool_id, cand)
                        return cand, f"system_dir:{s_dir}"

        # ---------------------------------------------------------------------
        # Tier 5: System PATH Fallback
        # ---------------------------------------------------------------------
        for exe_name in executables:
            which_res = shutil.which(exe_name)
            if which_res:
                cand = Path(which_res).resolve()
                if cand.is_file():
                    logger.debug("Found %s on PATH: %s", tool_id, cand)
                    return cand, "system_path"

        return None, "not_found"

    def probe_version(self, binary_path: Path, spec: ToolSpec) -> Optional[str]:
        """
        Run the tool's version command safely and extract the version string via regex.
        """
        cmd = [str(binary_path)] + spec.version_command
        retcode, stdout, stderr = run_safe_command(cmd, timeout=5)
        combined = f"{stdout}\n{stderr}"

        if not spec.version_regex:
            return None

        match = re.search(spec.version_regex, combined, re.IGNORECASE)
        if match:
            # Prefer group 1 if captured, otherwise full match
            return match.group(1) if match.groups() else match.group(0)

        # Fallback to general SemVer match
        semver_match = re.search(r"(\d+\.\d+(?:\.\d+)?)", combined)
        if semver_match:
            return semver_match.group(1)

        return None

    def detect_tool(self, tool_id: str) -> ToolDetectionResult:
        """
        Execute full detection, version probing, and compatibility evaluation for a tool.
        """
        spec = self.registry.get_tool(tool_id)
        if not spec:
            raise KeyError(f"Tool '{tool_id}' is not defined in registry tools.json.")

        binary_path, source = self.find_binary(spec)

        if not binary_path:
            eval_res = evaluate_compatibility(
                tool_id=spec.id,
                detected_version=None,
                spec=spec.compatibility,
            )
            return ToolDetectionResult(
                tool_id=spec.id,
                name=spec.name,
                is_installed=False,
                binary_path=None,
                detected_version=None,
                evaluation=eval_res,
                discovery_source="not_found",
            )

        detected_ver = self.probe_version(binary_path, spec)
        eval_res = evaluate_compatibility(
            tool_id=spec.id,
            detected_version=detected_ver,
            spec=spec.compatibility,
        )

        return ToolDetectionResult(
            tool_id=spec.id,
            name=spec.name,
            is_installed=True,
            binary_path=binary_path,
            detected_version=detected_ver,
            evaluation=eval_res,
            discovery_source=source,
        )

    def detect_all(self) -> Dict[str, ToolDetectionResult]:
        """
        Detect all tools registered in the catalog.
        """
        results: Dict[str, ToolDetectionResult] = {}
        for tool in self.registry.get_all_tools():
            results[tool.id] = self.detect_tool(tool.id)
        return results
