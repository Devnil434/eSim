"""
toolcare.core.doctor - 5-stage simulation health diagnostics and runtime integration auditor.
"""

from __future__ import annotations

import configparser
import os
import platform
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from toolcare.core.dependency import (
    ALL_WORKFLOW_IDS,
    WORKFLOW_DCOSIM,
    WORKFLOW_SIMULATION,
    DependencyGraph,
    WorkflowStatus,
)
from toolcare.core.detector import ToolDetectionResult, ToolDetector
from toolcare.core.registry import ToolRegistry
from toolcare.core.version import ToolStatus
from toolcare.logger import get_logger
from toolcare.platform_utils import (
    CPU_ARCH,
    IS_LINUX,
    IS_MAC,
    IS_WINDOWS,
    SYSTEM_OS,
    get_available_package_managers,
    get_esim_config_dir,
    get_home_dir,
    get_nghdl_config_dir,
    run_safe_command,
)

logger = get_logger("doctor")


class Severity(str, Enum):
    """Diagnostic check severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class DoctorCheckItem:
    """Individual diagnostic inspection result."""
    stage: str
    check_name: str
    passed: bool
    severity: Severity
    details: str
    remediation: Optional[str] = None


@dataclass(frozen=True)
class DoctorReport:
    """Consolidated 5-stage health diagnostic report."""
    items: List[DoctorCheckItem]
    overall_health: bool
    has_critical: bool
    total_checks: int
    passed_checks: int
    failed_checks: int
    workflow_readiness: Dict[str, WorkflowStatus]
    verified_components: Dict[str, List[str]]

    def get_items_by_severity(self, severity: Severity) -> List[DoctorCheckItem]:
        return [it for it in self.items if it.severity == severity]

    def format_cli_report(self) -> str:
        """Format an aligned text report suitable for CLI and documentation."""
        lines = []
        lines.append("=" * 80)
        lines.append("           eSim ToolCare Simulation Health & Diagnostic Audit")
        lines.append("=" * 80)

        # Group by stage
        stages: Dict[str, List[DoctorCheckItem]] = {}
        for it in self.items:
            stages.setdefault(it.stage, []).append(it)

        for stage_name, stage_items in stages.items():
            lines.append(f"\n[{stage_name}]")
            for it in stage_items:
                marker = "[OK]" if it.passed else f"[{it.severity.value}]"
                lines.append(f"  {marker:<10} {it.check_name}: {it.details}")
                if not it.passed and it.remediation:
                    lines.append(f"             --> Remediation: {it.remediation}")

        # Workflow readiness summary
        lines.append("\n" + "-" * 80)
        lines.append("Simulation Workflow Readiness Summary:")
        lines.append("-" * 80)
        for wf_id in ALL_WORKFLOW_IDS:
            status = self.workflow_readiness.get(wf_id)
            if not status:
                continue
            run_tag = "[RUNNABLE]" if status.is_runnable else "[BLOCKED] "
            lines.append(f"  {run_tag:<12} {status.workflow_name} ({status.workflow_id})")
            if not status.is_runnable:
                for diag in status.diagnostic_messages:
                    lines.append(f"               * {diag}")

        lines.append("\n" + "=" * 80)
        lines.append(
            f"Overall Status: {'HEALTHY' if self.overall_health else 'ISSUES DETECTED'} | "
            f"Passed: {self.passed_checks}/{self.total_checks} checks | "
            f"Critical Failures: {len(self.get_items_by_severity(Severity.CRITICAL))}"
        )
        lines.append("=" * 80)
        return "\n".join(lines)


class SimulationDoctor:
    """
    Deep runtime diagnostic auditor for eSim.
    Executes non-destructive, read-only inspection across 5 stages.
    """

    def __init__(
        self,
        detector: Optional[ToolDetector] = None,
        dep_graph: Optional[DependencyGraph] = None,
    ):
        self.detector = detector or ToolDetector()
        self.dep_graph = dep_graph or DependencyGraph()

    # -------------------------------------------------------------------------
    # GHDL Backend Detection (Strict & Robust)
    # -------------------------------------------------------------------------
    @staticmethod
    def inspect_ghdl_backend(ghdl_version_output: str) -> Tuple[str, bool]:
        """
        Detect GHDL backend from command output.
        Correction 3: An unknown backend MUST NOT be treated as LLVM-compatible.

        Returns:
            Tuple of (backend_name, is_llvm_compatible).
            Backends: 'llvm' (True), 'mcode' (False), 'gcc' (False), 'unknown' (False).
        """
        if not ghdl_version_output or not isinstance(ghdl_version_output, str):
            return "unknown", False

        lower = ghdl_version_output.lower()

        # Check for explicit LLVM backend signatures
        if "llvm code generator" in lower or "llvm backend" in lower or "ghdl-llvm" in lower:
            return "llvm", True
        elif "llvm" in lower and "backend" in lower:
            return "llvm", True
        elif "mcode" in lower:
            return "mcode", False
        elif "gcc" in lower:
            return "gcc", False

        return "unknown", False

    # -------------------------------------------------------------------------
    # Relative Path Discovery for Integration Libraries
    # -------------------------------------------------------------------------
    @staticmethod
    def find_ngspice_code_models_dir(binary_path: Optional[Path]) -> Optional[Path]:
        """
        Discover Ngspice code-models directory relative to detected binary installation prefix.
        Correction 4: Discovers integration libraries relative to detected prefixes.
        """
        candidates: List[Path] = []
        if binary_path and binary_path.is_file():
            b_parent = binary_path.parent  # e.g. C:/Spice64/bin or /usr/bin
            candidates.extend([
                b_parent.parent / "lib" / "ngspice",
                b_parent / "lib" / "ngspice",
                b_parent.parent / "share" / "ngspice",
                b_parent / "share" / "ngspice",
                b_parent / "lib",
            ])

        # Platform default fallbacks
        if IS_WINDOWS:
            candidates.extend([
                Path("C:/Spice64/lib/ngspice"),
                Path("C:/msys64/mingw64/lib/ngspice"),
            ])
        else:
            candidates.extend([
                Path("/usr/lib/ngspice"),
                Path("/usr/local/lib/ngspice"),
                Path("/usr/lib/x86_64-linux-gnu/ngspice"),
            ])

        for c in candidates:
            if c.is_dir():
                return c.resolve()
        return None

    @staticmethod
    def find_iverilog_lib_dir(binary_path: Optional[Path]) -> Optional[Path]:
        """
        Discover Icarus Verilog library directory relative to detected binary installation prefix.
        """
        candidates: List[Path] = []
        if binary_path and binary_path.is_file():
            b_parent = binary_path.parent
            candidates.extend([
                b_parent.parent / "lib",
                b_parent / "lib",
                b_parent,  # on Windows, DLLs are often placed in the bin directory
                b_parent.parent / "lib" / "ivl",
            ])

        if IS_WINDOWS:
            candidates.extend([
                Path("C:/iverilog/lib"),
                Path("C:/iverilog/bin"),
                Path("C:/msys64/mingw64/lib"),
            ])
        else:
            candidates.extend([
                Path("/usr/lib/x86_64-linux-gnu"),
                Path("/usr/lib"),
                Path("/usr/local/lib"),
            ])

        for c in candidates:
            if c.is_dir():
                return c.resolve()
        return None

    @staticmethod
    def find_kicad_sym_lib_table() -> Optional[Path]:
        """
        Locate global KiCad sym-lib-table across version folders and operating systems.
        """
        home = get_home_dir()
        candidates: List[Path] = []

        if IS_WINDOWS:
            appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
            kicad_root = appdata / "kicad"
            if kicad_root.is_dir():
                # Check version directories 9.0 down to 7.0
                for ver in ["9.0", "8.0", "7.0", ""]:
                    candidates.append(kicad_root / ver / "sym-lib-table" if ver else kicad_root / "sym-lib-table")
        else:
            config_root = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "kicad"
            if config_root.is_dir():
                for ver in ["9.0", "8.0", "7.0", ""]:
                    candidates.append(config_root / ver / "sym-lib-table" if ver else config_root / "sym-lib-table")

        for c in candidates:
            if c.is_file():
                return c.resolve()
        return None

    # -------------------------------------------------------------------------
    # Diagnostic Execution (5 Stages)
    # -------------------------------------------------------------------------
    def run_diagnostics(self) -> DoctorReport:
        """
        Execute comprehensive read-only 5-stage health audit.
        """
        items: List[DoctorCheckItem] = []
        verified_components: Dict[str, List[str]] = {
            "ngspice": [],
            "iverilog": [],
            "kicad": [],
            "ghdl": [],
            "verilator": [],
            "llvm": [],
        }

        # Query all tools via detector
        detection_results = self.detector.detect_all()

        # =====================================================================
        # Stage 1: Host Environment Audit
        # =====================================================================
        # 1.1 Operating system
        os_info = f"{SYSTEM_OS.capitalize()} ({platform.release()}, {CPU_ARCH})"
        items.append(
            DoctorCheckItem(
                stage="Stage 1: Host Environment",
                check_name="Operating System & Architecture",
                passed=True,
                severity=Severity.INFO,
                details=f"Detected {os_info}.",
            )
        )

        # 1.2 Bitness check
        is_64bit = sys.maxsize > 2**32
        items.append(
            DoctorCheckItem(
                stage="Stage 1: Host Environment",
                check_name="64-Bit Architecture",
                passed=is_64bit,
                severity=Severity.CRITICAL if not is_64bit else Severity.INFO,
                details="64-bit architecture confirmed." if is_64bit else "32-bit system detected; eSim 2.5+ requires 64-bit OS.",
                remediation="Upgrade to a 64-bit operating system." if not is_64bit else None,
            )
        )

        # 1.3 Python runtime
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        py_compat = sys.version_info >= (3, 10)
        items.append(
            DoctorCheckItem(
                stage="Stage 1: Host Environment",
                check_name="Python Runtime",
                passed=py_compat,
                severity=Severity.ERROR if not py_compat else Severity.INFO,
                details=f"Python {py_ver} ({sys.executable}).",
                remediation="Install Python 3.10 or higher." if not py_compat else None,
            )
        )

        # 1.4 Package manager availability
        pkg_managers = get_available_package_managers()
        available_pms = [pm for pm, avail in pkg_managers.items() if avail]
        pm_details = f"Available: {', '.join(available_pms)}" if available_pms else "No package manager on PATH"
        items.append(
            DoctorCheckItem(
                stage="Stage 1: Host Environment",
                check_name="Host Package Managers",
                passed=len(available_pms) > 0,
                severity=Severity.WARNING if not available_pms else Severity.INFO,
                details=pm_details,
                remediation="Ensure winget/choco (Windows) or apt (Linux) is installed and in PATH." if not available_pms else None,
            )
        )

        # =====================================================================
        # Stage 2: Analog Simulation Audit (Ngspice)
        # =====================================================================
        det_ngspice = detection_results.get("ngspice")
        if not det_ngspice or not det_ngspice.is_installed:
            items.append(
                DoctorCheckItem(
                    stage="Stage 2: Analog Simulation",
                    check_name="Ngspice Executable",
                    passed=False,
                    severity=Severity.CRITICAL,
                    details="Ngspice binary not found on system.",
                    remediation="Install Ngspice 45.2 (e.g. via ToolCare archive or winget).",
                )
            )
        else:
            items.append(
                DoctorCheckItem(
                    stage="Stage 2: Analog Simulation",
                    check_name="Ngspice Executable",
                    passed=det_ngspice.is_usable,
                    severity=Severity.ERROR if not det_ngspice.is_usable else Severity.INFO,
                    details=f"Located at {det_ngspice.binary_path} (Version: {det_ngspice.detected_version}).",
                    remediation=det_ngspice.evaluation.message if not det_ngspice.is_usable else None,
                )
            )

            # Discover code models directory relative to binary prefix
            cm_dir = self.find_ngspice_code_models_dir(det_ngspice.binary_path)
            if not cm_dir:
                items.append(
                    DoctorCheckItem(
                        stage="Stage 2: Analog Simulation",
                        check_name="Ngspice Code Models Directory",
                        passed=False,
                        severity=Severity.ERROR,
                        details="Could not locate /lib/ngspice code-models directory.",
                        remediation="Ensure Ngspice XSPICE models are placed in ../lib/ngspice relative to ngspice binary.",
                    )
                )
            else:
                items.append(
                    DoctorCheckItem(
                        stage="Stage 2: Analog Simulation",
                        check_name="Ngspice Code Models Directory",
                        passed=True,
                        severity=Severity.INFO,
                        details=f"Found code models directory at {cm_dir}.",
                    )
                )

                # Probe standard analog code models (analog.cm, spice2poly.cm, digital.cm)
                for cm_name in ["analog.cm", "spice2poly.cm", "digital.cm"]:
                    cm_path = cm_dir / cm_name
                    # Also check for .so or .dll if extension varies
                    found = cm_path.is_file() or (cm_dir / f"{cm_name}.dll").is_file() or (cm_dir / f"{cm_name}.so").is_file()
                    if found:
                        verified_components["ngspice"].append(cm_name)
                    items.append(
                        DoctorCheckItem(
                            stage="Stage 2: Analog Simulation",
                            check_name=f"Code Model: {cm_name}",
                            passed=found,
                            severity=Severity.ERROR if not found else Severity.INFO,
                            details=f"Present at {cm_path}." if found else f"Missing {cm_name} in {cm_dir}.",
                            remediation=f"Ensure {cm_name} is compiled and placed in {cm_dir}." if not found else None,
                        )
                    )

                # Probe ivlng bridge (needed for d_cosim)
                ivlng_candidates = [
                    cm_dir / "ivlng.dll",
                    cm_dir / "ivlng.so",
                    cm_dir / "ivlng.cm",
                    cm_dir / "ivlng",
                ]
                has_ivlng = any(p.is_file() for p in ivlng_candidates)
                if has_ivlng:
                    verified_components["ngspice"].append("ivlng")
                items.append(
                    DoctorCheckItem(
                        stage="Stage 2: Analog Simulation",
                        check_name="Verilog Bridge (ivlng)",
                        passed=has_ivlng,
                        severity=Severity.WARNING if not has_ivlng else Severity.INFO,
                        details="ivlng adapter found." if has_ivlng else f"ivlng dynamic bridge missing in {cm_dir}. (Blocks d_cosim mixed-signal flow).",
                        remediation="Compile or copy ivlng.dll / ivlng.so into Ngspice code models folder for mixed-signal co-simulation." if not has_ivlng else None,
                    )
                )

                # Probe ghdl.cm (needed for nghdl)
                ghdl_cm_candidates = [
                    cm_dir / "ghdl.cm",
                    cm_dir / "ghdl.dll",
                    cm_dir / "ghdl.so",
                ]
                has_ghdl_cm = any(p.is_file() for p in ghdl_cm_candidates)
                if has_ghdl_cm:
                    verified_components["ngspice"].append("ghdl.cm")
                items.append(
                    DoctorCheckItem(
                        stage="Stage 2: Analog Simulation",
                        check_name="VHDL Code Model (ghdl.cm)",
                        passed=has_ghdl_cm,
                        severity=Severity.WARNING if not has_ghdl_cm else Severity.INFO,
                        details="ghdl.cm found." if has_ghdl_cm else f"ghdl.cm missing in {cm_dir}. (Blocks nghdl VHDL co-simulation).",
                        remediation="Build ghdl.cm and place in Ngspice code models directory for VHDL co-simulation." if not has_ghdl_cm else None,
                    )
                )

        # =====================================================================
        # Stage 3: Digital Simulation Audit (Icarus Verilog & Verilator)
        # =====================================================================
        det_iverilog = detection_results.get("iverilog")
        if not det_iverilog or not det_iverilog.is_installed:
            items.append(
                DoctorCheckItem(
                    stage="Stage 3: Digital Simulation",
                    check_name="Icarus Verilog Executable",
                    passed=False,
                    severity=Severity.ERROR,
                    details="iverilog binary not found.",
                    remediation="Install Icarus Verilog v12 (e.g. bleyer setup on Windows, apt on Ubuntu).",
                )
            )
        else:
            items.append(
                DoctorCheckItem(
                    stage="Stage 3: Digital Simulation",
                    check_name="Icarus Verilog Executable",
                    passed=det_iverilog.is_usable,
                    severity=Severity.ERROR if not det_iverilog.is_usable else Severity.INFO,
                    details=f"Located at {det_iverilog.binary_path} (Version: {det_iverilog.detected_version}).",
                    remediation=det_iverilog.evaluation.message if not det_iverilog.is_usable else None,
                )
            )
            verified_components["iverilog"].append("vvp")

            # Discover libvvp relative to binary prefix
            ivl_lib_dir = self.find_iverilog_lib_dir(det_iverilog.binary_path)
            has_libvvp = False
            libvvp_found_path: Optional[Path] = None

            if ivl_lib_dir:
                for lib_name in ["libvvp.dll", "libvvp.so", "libvvp.dylib", "libvvp.a"]:
                    cand = ivl_lib_dir / lib_name
                    if cand.is_file():
                        has_libvvp = True
                        libvvp_found_path = cand
                        break

            if has_libvvp:
                verified_components["iverilog"].append("libvvp")

            items.append(
                DoctorCheckItem(
                    stage="Stage 3: Digital Simulation",
                    check_name="libvvp Dynamic Linkage",
                    passed=has_libvvp,
                    severity=Severity.WARNING if not has_libvvp else Severity.INFO,
                    details=f"libvvp located at {libvvp_found_path}." if has_libvvp else "libvvp shared library not found in Icarus library paths. (Blocks d_cosim mixed-signal flow).",
                    remediation="Install Icarus Verilog compiled with --enable-libvvp so Ngspice ivlng can dynamically dlopen libvvp." if not has_libvvp else None,
                )
            )

        # Verilator
        det_verilator = detection_results.get("verilator")
        if not det_verilator or not det_verilator.is_installed:
            items.append(
                DoctorCheckItem(
                    stage="Stage 3: Digital Simulation",
                    check_name="Verilator Executable",
                    passed=False,
                    severity=Severity.WARNING,
                    details="Verilator binary not installed. (Optional for standalone simulation; required for ngveri).",
                    remediation="Install Verilator v5+ for C++ testbench generation.",
                )
            )
        else:
            ver_ok = det_verilator.is_usable
            if ver_ok:
                verified_components["verilator"].append("verilator_bin")
            items.append(
                DoctorCheckItem(
                    stage="Stage 3: Digital Simulation",
                    check_name="Verilator Executable",
                    passed=ver_ok,
                    severity=Severity.ERROR if not ver_ok else Severity.INFO,
                    details=f"Located at {det_verilator.binary_path} (Version: {det_verilator.detected_version}).",
                    remediation=det_verilator.evaluation.message if not ver_ok else None,
                )
            )

        # =====================================================================
        # Stage 4: VHDL & Compiler Audit (GHDL & LLVM)
        # =====================================================================
        det_ghdl = detection_results.get("ghdl")
        if not det_ghdl or not det_ghdl.is_installed:
            items.append(
                DoctorCheckItem(
                    stage="Stage 4: VHDL & Compiler",
                    check_name="GHDL Executable",
                    passed=False,
                    severity=Severity.WARNING,
                    details="GHDL binary not installed. (Optional for analog simulation; required for nghdl).",
                    remediation="Install GHDL with LLVM backend (ghdl-llvm).",
                )
            )
        else:
            # Query GHDL version output for backend inspection
            ghdl_cmd = [str(det_ghdl.binary_path), "--version"]
            _, ghdl_stdout, ghdl_stderr = run_safe_command(ghdl_cmd, timeout=5)
            ghdl_out = f"{ghdl_stdout}\n{ghdl_stderr}"

            backend_name, is_llvm = self.inspect_ghdl_backend(ghdl_out)

            if is_llvm:
                verified_components["ghdl"].append("ghdl-llvm")
                items.append(
                    DoctorCheckItem(
                        stage="Stage 4: VHDL & Compiler",
                        check_name="GHDL Compiler Backend",
                        passed=True,
                        severity=Severity.INFO,
                        details=f"GHDL version {det_ghdl.detected_version} with verified LLVM backend.",
                    )
                )
            else:
                # Backend is mcode, gcc, or unknown (Correction 3: unknown backend triggers CRITICAL)
                if backend_name == "mcode":
                    sev = Severity.CRITICAL
                    reason = "GHDL is configured with 'mcode' backend. eSim NGHDL socket IPC crashes on mcode."
                    remed = "Replace GHDL with ghdl-llvm (e.g. mingw-w64-x86_64-ghdl-llvm on Windows, ghdl-llvm on Ubuntu)."
                else:
                    sev = Severity.CRITICAL
                    reason = f"GHDL backend could not be verified as LLVM (Detected: '{backend_name}'). An unknown backend cannot be assumed compatible."
                    remed = "Install verified ghdl-llvm package."

                items.append(
                    DoctorCheckItem(
                        stage="Stage 4: VHDL & Compiler",
                        check_name="GHDL Compiler Backend",
                        passed=False,
                        severity=sev,
                        details=reason,
                        remediation=remed,
                    )
                )

        # LLVM / Clang compiler
        det_llvm = detection_results.get("llvm")
        if not det_llvm or not det_llvm.is_installed:
            items.append(
                DoctorCheckItem(
                    stage="Stage 4: VHDL & Compiler",
                    check_name="LLVM / Clang Toolchain",
                    passed=False,
                    severity=Severity.WARNING,
                    details="LLVM/Clang compiler not located. (Required for compiling dynamic models in ngveri/nghdl).",
                    remediation="Install LLVM/Clang or ensure GCC/Clang is in PATH.",
                )
            )
        else:
            verified_components["llvm"].append("clang")
            items.append(
                DoctorCheckItem(
                    stage="Stage 4: VHDL & Compiler",
                    check_name="LLVM / Clang Toolchain",
                    passed=det_llvm.is_usable,
                    severity=Severity.WARNING if not det_llvm.is_usable else Severity.INFO,
                    details=f"Located at {det_llvm.binary_path} (Version: {det_llvm.detected_version}).",
                    remediation=det_llvm.evaluation.message if not det_llvm.is_usable else None,
                )
            )

        # =====================================================================
        # Stage 5: Configuration Files & Symbol Integration Audit
        # =====================================================================
        # 5.1 KiCad sym-lib-table
        det_kicad = detection_results.get("kicad")
        if det_kicad and det_kicad.is_installed:
            verified_components["kicad"].append("eeschema")

        kicad_table = self.find_kicad_sym_lib_table()
        if not kicad_table:
            items.append(
                DoctorCheckItem(
                    stage="Stage 5: Configuration & Integration",
                    check_name="KiCad Symbol Library Table",
                    passed=False,
                    severity=Severity.WARNING,
                    details="Could not locate global KiCad sym-lib-table.",
                    remediation="Launch KiCad once to initialize default symbol library table, or run 'toolcare configure --sync'.",
                )
            )
        else:
            try:
                table_text = kicad_table.read_text(encoding="utf-8", errors="replace")
                has_esim_syms = any(
                    sym in table_text
                    for sym in ["eSim_Subckt", "eSim_Ngveri", "eSim_Nghdl", "eSim_Devices"]
                )
                items.append(
                    DoctorCheckItem(
                        stage="Stage 5: Configuration & Integration",
                        check_name="KiCad eSim Symbols Integration",
                        passed=has_esim_syms,
                        severity=Severity.WARNING if not has_esim_syms else Severity.INFO,
                        details=f"Checked {kicad_table}." if has_esim_syms else f"eSim custom symbol entries missing in {kicad_table}.",
                        remediation="Run 'toolcare configure --sync' to register eSim symbol libraries into sym-lib-table without duplicates." if not has_esim_syms else None,
                    )
                )
            except Exception as e:
                items.append(
                    DoctorCheckItem(
                        stage="Stage 5: Configuration & Integration",
                        check_name="KiCad Symbol Library Table",
                        passed=False,
                        severity=Severity.WARNING,
                        details=f"Failed to read {kicad_table}: {e}",
                    )
                )

        # 5.2 ~/.nghdl/config.ini
        nghdl_ini = get_nghdl_config_dir() / "config.ini"
        if not nghdl_ini.is_file():
            items.append(
                DoctorCheckItem(
                    stage="Stage 5: Configuration & Integration",
                    check_name="NGHDL Configuration File",
                    passed=False,
                    severity=Severity.WARNING,
                    details="~/.nghdl/config.ini not found.",
                    remediation="Run 'toolcare configure --sync' or start eSim once to seed ~/.nghdl configuration.",
                )
            )
        else:
            try:
                cp = configparser.ConfigParser()
                cp.read(str(nghdl_ini), encoding="utf-8")
                has_cosim = cp.has_section("COSIM") or cp.has_section("NGHDL")
                items.append(
                    DoctorCheckItem(
                        stage="Stage 5: Configuration & Integration",
                        check_name="NGHDL Configuration File",
                        passed=has_cosim,
                        severity=Severity.WARNING if not has_cosim else Severity.INFO,
                        details=f"Valid config found at {nghdl_ini} with sections: {', '.join(cp.sections())}.",
                        remediation="Add [COSIM] and [COMPILER] sections to ~/.nghdl/config.ini." if not has_cosim else None,
                    )
                )
            except Exception as e:
                items.append(
                    DoctorCheckItem(
                        stage="Stage 5: Configuration & Integration",
                        check_name="NGHDL Configuration File",
                        passed=False,
                        severity=Severity.ERROR,
                        details=f"Malformed ~/.nghdl/config.ini: {e}",
                        remediation="Restore valid ~/.nghdl/config.ini or re-sync with toolcare.",
                    )
                )

        # 5.3 ~/.esim/config.ini
        esim_ini = get_esim_config_dir() / "config.ini"
        if not esim_ini.is_file():
            items.append(
                DoctorCheckItem(
                    stage="Stage 5: Configuration & Integration",
                    check_name="eSim Main Configuration File",
                    passed=True,
                    severity=Severity.INFO,
                    details="~/.esim/config.ini not present (will be initialized on first eSim launch).",
                )
            )
        else:
            items.append(
                DoctorCheckItem(
                    stage="Stage 5: Configuration & Integration",
                    check_name="eSim Main Configuration File",
                    passed=True,
                    severity=Severity.INFO,
                    details="Found ~/.esim/config.ini.",
                )
            )

        # =====================================================================
        # Workflow Readiness Evaluation (Correction 5: Workflow-specific isolation)
        # =====================================================================
        workflow_readiness = self.dep_graph.evaluate_all_workflows(
            detection_results=detection_results,
            verified_components=verified_components,
        )

        # Compute summary metrics
        passed_count = sum(1 for it in items if it.passed)
        failed_count = sum(1 for it in items if not it.passed)
        has_critical = any(it.severity == Severity.CRITICAL for it in items if not it.passed)
        has_errors = any(it.severity in (Severity.ERROR, Severity.CRITICAL) for it in items if not it.passed)
        overall_health = not has_errors and all(wf.is_runnable for wf in workflow_readiness.values())

        return DoctorReport(
            items=items,
            overall_health=overall_health,
            has_critical=has_critical,
            total_checks=len(items),
            passed_checks=passed_count,
            failed_checks=failed_count,
            workflow_readiness=workflow_readiness,
            verified_components=verified_components,
        )
