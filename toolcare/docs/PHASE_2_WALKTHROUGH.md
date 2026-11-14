# Technical Walkthrough: Phase 2 – Flow Dependency Resolution & Diagnostic Doctor Engine
## eSim ToolCare (Screening Task 5 – Autumn 2026)

**Completion Date:** November 14, 2026  
**Status:** 100% Passed (41/41 Unit Tests Verified on Python 3.14.3)  
**Parent Roadmap:** [`ROADMAP.md`](file:///e:/100xDevs/eSim/ROADMAP.md)  
**Parent Specification:** [`DESIGN_DOCUMENT.md`](file:///e:/100xDevs/eSim/DESIGN_DOCUMENT.md)  
**Preceding Walkthrough:** [`PHASE_1_WALKTHROUGH.md`](file:///e:/100xDevs/eSim/toolcare/docs/PHASE_1_WALKTHROUGH.md)  

---

## 1. Executive Summary

Phase 2 elevates eSim ToolCare from an isolated binary detector into a **workflow-aware simulation diagnostic engine**. 

EDA tools in eSim operate in cooperative pipelines. Rather than evaluating whether tools exist in an abstract sense, Phase 2 implements:
1. **Simulation Flow Dependency Graph (`core/dependency.py`):** Authoritative mapping of eSim's 5 simulation workflows with standardized IDs (`simulation`, `verifier`, `d_cosim`, `ngveri`, `nghdl`), distinguishing mandatory tools from optional ones.
2. **Deep Runtime Diagnostic Doctor (`core/doctor.py`):** Non-destructive 5-stage health auditor mirroring and extending `src/maker/ToolchainCheck.py` without modifying existing eSim source code.
3. **Four Structured Severity Levels:** `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.
4. **Strict GHDL Backend Trap Protection:** Robust parser for `ghdl --version` output; rejects both `mcode` and `unknown` backends to prevent NGHDL socket co-simulation hangs.
5. **Relative Library Discovery:** Dynamic discovery of integration libraries (`analog.cm`, `ivlng`, `ghdl.cm`, `libvvp.dll`) relative to detected binary installation prefixes rather than hardcoded paths.
6. **Workflow-Specific Failure Isolation:** Missing mixed-signal co-simulation libraries (`libvvp`, `ivlng`) block `d_cosim` without incorrectly blocking standalone analog simulation.

---

## 2. Directory & Codebase Architecture

```
toolcare/
├── __init__.py                     # Package entry point (exports v2.0.0 & Phase 1+2 APIs)
├── platform_utils.py               # OS detection, safe subprocess execution, package manager probing
├── logger.py                       # Rotating file audit logger (~/.esim/logs/toolcare.log)
├── data/
│   └── tools.json                  # Declarative tool registry (schema 2.0.0)
├── core/
│   ├── __init__.py                 # Core business logic exports
│   ├── registry.py                 # ToolSpec, CompatibilitySpec, ArchiveSpec, ToolRegistry
│   ├── version.py                  # parse_version_tuple, compare_versions, evaluate_compatibility
│   ├── detector.py                 # Multi-tier binary detector (5-tier precedence hierarchy)
│   ├── dependency.py               # [PHASE 2] Flow dependency graph & cascading impact analysis
│   └── doctor.py                   # [PHASE 2] 5-stage diagnostic health auditor
├── docs/
│   ├── DESIGN_DOCUMENT.md          # Primary architecture specification (v2.0.0)
│   ├── ROADMAP.md                  # 5-Phase, 10-day execution roadmap
│   ├── PHASE_1_IMPLEMENTATION_PLAN.md
│   ├── PHASE_1_WALKTHROUGH.md
│   ├── PHASE_2_IMPLEMENTATION_PLAN.md
│   └── PHASE_2_WALKTHROUGH.md      # This document
└── tests/
    ├── __init__.py
    ├── test_registry.py            # 7 Phase 1 registry tests
    ├── test_version.py             # 9 Phase 1 version parsing tests
    ├── test_detector.py            # 7 Phase 1 detector precedence tests
    ├── test_dependency.py          # [PHASE 2] 7 workflow dependency & isolation tests
    └── test_doctor.py              # [PHASE 2] 11 diagnostic doctor & GHDL backend tests
```

---

## 3. Deep-Dive: Core Module Mechanisms

### 3.1 Standardized Workflow Dependency Graph (`core/dependency.py`)

All mixed-signal references are standardized to **`d_cosim`**:

```python
WORKFLOW_SIMULATION = "simulation"
WORKFLOW_VERIFIER = "verifier"
WORKFLOW_DCOSIM = "d_cosim"        # Standardized mixed-signal workflow ID
WORKFLOW_NGVERI = "ngveri"
WORKFLOW_NGHDL = "nghdl"
```

#### Workflow Specifications & Sub-Dependency Rules:
1. **`simulation` (Analog Simulation):**
   * *Mandatory Tools:* `kicad` (requires `eeschema`), `ngspice` (requires `analog.cm`, `spice2poly.cm`).
2. **`verifier` (Verilog Digital Verifier):**
   * *Mandatory Tools:* `iverilog` (requires `vvp`).
3. **`d_cosim` (Digital/Mixed-Signal Co-Simulation):**
   * *Mandatory Tools:* `ngspice` (requires `ivlng`), `iverilog` (requires `libvvp`).
   * *Critical Sub-Dependency Link:* Ngspice's `ivlng` dynamic adapter dynamically `dlopen`s `libvvp.so` / `libvvp.dll`. Both must be present.
4. **`ngveri` (Verilog Model Synthesis):**
   * *Mandatory Tools:* `verilator` (v5+), `llvm` (clang), `ngspice` (analog.cm).
5. **`nghdl` (VHDL Mixed-Signal Co-Simulation):**
   * *Mandatory Tools:* `ghdl` (LLVM backend ONLY), `llvm` (clang), `ngspice` (ghdl.cm).

#### Cascading Impact Analysis:
`DependencyGraph.get_dependent_workflows(tool_id)` traces failure cascades across workflows:
* `ngspice` missing $\rightarrow$ blocks `["simulation", "d_cosim", "ngveri", "nghdl"]` (4 workflows).
* `iverilog` missing $\rightarrow$ blocks `["verifier", "d_cosim"]`.
* `kicad` missing $\rightarrow$ blocks `["simulation"]`.
* `ghdl` missing $\rightarrow$ blocks `["nghdl"]`.
* `verilator` missing $\rightarrow$ blocks `["ngveri"]`.

---

### 3.2 Diagnostic Doctor Engine (`core/doctor.py`)

#### 1. Severity Enum:
```python
class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
```

#### 2. Robust GHDL Backend Parser:
An unknown or ambiguous backend is **never assumed to be LLVM-compatible**:
```python
@staticmethod
def inspect_ghdl_backend(ghdl_version_output: str) -> Tuple[str, bool]:
    if not ghdl_version_output or not isinstance(ghdl_version_output, str):
        return "unknown", False

    lower = ghdl_version_output.lower()
    if "llvm code generator" in lower or "llvm backend" in lower or "ghdl-llvm" in lower:
        return "llvm", True
    elif "llvm" in lower and "backend" in lower:
        return "llvm", True
    elif "mcode" in lower:
        return "mcode", False
    elif "gcc" in lower:
        return "gcc", False

    return "unknown", False  # Unknown backend strictly rejected
```

#### 3. Relative Discovery for Integration Libraries:
Rather than checking hardcoded paths, `SimulationDoctor` probes paths relative to the detected binary:
* **Ngspice Code Models:** Probes `<detected_bin>/../lib/ngspice`, `<detected_bin>/../../lib/ngspice`, and `<detected_bin>/../share/ngspice`. Asserts presence of `analog.cm`, `spice2poly.cm`, `digital.cm`, `ivlng.dll`/`ivlng.so`, and `ghdl.cm`.
* **Icarus Verilog Libraries:** Probes `<detected_bin>/../lib`, `<detected_bin>/../../lib`, and `<detected_bin>` (for DLLs placed alongside the executable on Windows). Locates `libvvp.dll` / `libvvp.so`.
* **KiCad Symbol Table:** Scans user configuration directories (`%APPDATA%/kicad/<ver>/sym-lib-table` on Windows, `~/.config/kicad/<ver>/sym-lib-table` on Linux) across versions 9.0, 8.0, and 7.0 for `eSim_Subckt`, `eSim_Ngveri`, and `eSim_Nghdl`.

#### 4. Workflow-Specific Failure Isolation:
In `evaluate_workflow`, sub-components are matched strictly against each workflow's requirements:
* If `ivlng` or `libvvp` is missing:
  * `d_cosim.is_runnable` is evaluated as **`False`** (reports `missing_critical_components: ["ngspice:ivlng", "iverilog:libvvp"]`).
  * `simulation.is_runnable` remains **`True`** (because analog simulation only requires `analog.cm` and `spice2poly.cm`).
  * `verifier.is_runnable` remains **`True`** (because standalone Verilog verification only requires `vvp`).

---

## 4. Test Verification Summary

The complete test suite was executed on the host system using Python 3.14.3:

```text
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.0.3, pluggy-1.6.0 -- E:\Python314\python.exe
cachedir: .pytest_cache
rootdir: E:\100xDevs\eSim
plugins: anyio-4.13.0, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 41 items

toolcare/tests/test_dependency.py::test_standardized_d_cosim_workflow_id PASSED [  2%]
toolcare/tests/test_dependency.py::test_all_five_workflows_defined PASSED [  4%]
toolcare/tests/test_dependency.py::test_cascading_impact_analysis PASSED [  7%]
toolcare/tests/test_dependency.py::test_evaluate_workflow_fully_healthy PASSED [  9%]
toolcare/tests/test_dependency.py::test_evaluate_workflow_missing_tool PASSED [ 12%]
toolcare/tests/test_dependency.py::test_workflow_specific_component_isolation PASSED [ 14%]
toolcare/tests/test_dependency.py::test_evaluate_workflow_incompatible_tool PASSED [ 17%]
toolcare/tests/test_detector.py::test_detector_environment_variable_precedence PASSED [ 19%]
toolcare/tests/test_detector.py::test_detector_nghdl_config_precedence PASSED [ 21%]
toolcare/tests/test_detector.py::test_detector_path_fallback PASSED      [ 24%]
toolcare/tests/test_detector.py::test_probe_version_extraction PASSED    [ 26%]
toolcare/tests/test_detector.py::test_detect_tool_not_installed PASSED   [ 29%]
toolcare/tests/test_detector.py::test_detect_tool_success_compatible PASSED [ 31%]
toolcare/tests/test_detector.py::test_detect_all_returns_all_tools PASSED [ 34%]
toolcare/tests/test_doctor.py::test_severity_levels_include_critical PASSED [ 36%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_llvm PASSED   [ 39%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_mcode PASSED  [ 41%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_gcc PASSED    [ 43%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_unknown_is_not_compatible PASSED [ 46%]
toolcare/tests/test_doctor.py::test_doctor_relative_discovery_ngspice PASSED [ 48%]
toolcare/tests/test_doctor.py::test_doctor_relative_discovery_iverilog PASSED [ 51%]
toolcare/tests/test_doctor.py::test_doctor_fully_healthy PASSED          [ 53%]
toolcare/tests/test_doctor.py::test_doctor_ghdl_mcode_triggers_critical PASSED [ 56%]
toolcare/tests/test_doctor.py::test_doctor_missing_libvvp_blocks_d_cosim_only PASSED [ 58%]
toolcare/tests/test_doctor.py::test_doctor_missing_kicad_symbols_warning PASSED [ 60%]
toolcare/tests/test_registry.py::test_registry_loads_default_catalog PASSED [ 63%]
toolcare/tests/test_registry.py::test_registry_tool_spec_types PASSED    [ 65%]
toolcare/tests/test_registry.py::test_registry_verified_vs_unverified_package_ids PASSED [ 68%]
toolcare/tests/test_registry.py::test_registry_archive_verification PASSED [ 70%]
toolcare/tests/test_registry.py::test_registry_categories PASSED         [ 73%]
toolcare/tests/test_registry.py::test_registry_missing_file_raises PASSED [ 75%]
toolcare/tests/test_registry.py::test_registry_malformed_json_raises PASSED [ 78%]
toolcare/tests/test_version.py::test_parse_version_tuple_standard PASSED [ 80%]
toolcare/tests/test_version.py::test_parse_version_tuple_eda_quirks PASSED [ 82%]
toolcare/tests/test_version.py::test_parse_version_tuple_invalid_or_empty PASSED [ 85%]
toolcare/tests/test_version.py::test_compare_versions_ordering PASSED    [ 87%]
toolcare/tests/test_version.py::test_evaluate_compatibility_not_installed PASSED [ 90%]
toolcare/tests/test_version.py::test_evaluate_compatibility_incompatible_old PASSED [ 92%]
toolcare/tests/test_version.py::test_evaluate_compatibility_incompatible_new PASSED [ 95%]
toolcare/tests/test_version.py::test_evaluate_compatibility_upgrade_available PASSED [ 97%]
toolcare/tests/test_version.py::test_evaluate_compatibility_compatible PASSED [100%]

============================= 41 passed in 1.67s ==============================
```

---

## 5. Non-Interference Guarantee

Phase 2 modules operate strictly within `toolcare/core/` and `toolcare/tests/`. All operations are read-only:
* `src/maker/ToolchainCheck.py` remains untouched.
* No system files or configurations were modified during diagnostics.
* Zero third-party runtime dependencies introduced (uses standard library `configparser`, `pathlib`, `dataclasses`, and `enum`).
