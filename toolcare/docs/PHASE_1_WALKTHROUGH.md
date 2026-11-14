# Technical Walkthrough: Phase 1 – Foundation, Declarative Schema & Discovery Engine
## eSim ToolCare (Screening Task 5 – Autumn 2026)

**Completion Date:** November 14, 2026  
**Status:** 100% Passed (23/23 Unit Tests Verified on Python 3.14.3)  
**Parent Roadmap:** [`ROADMAP.md`](file:///e:/100xDevs/eSim/ROADMAP.md)  
**Parent Specification:** [`DESIGN_DOCUMENT.md`](file:///e:/100xDevs/eSim/DESIGN_DOCUMENT.md)  

---

## 1. Executive Summary

Phase 1 established the baseline infrastructure for **eSim ToolCare**, an automated external tool and dependency manager designed specifically for eSim. Phase 1 delivers:
1. **Zero-Privilege Leakage & Subprocess Safeguards:** Process isolation on Windows using `CREATE_NO_WINDOW` and `SW_HIDE`, preventing terminal window flashing during background checks.
2. **Declarative Schema 2.0.0 Catalog (`tools.json`):** Strict separation of verified metadata (pinned SHA-256 hashes from `windows/deps-manifest.json`) from unverified upstream package IDs (explicitly marked as `"UNVERIFIED"`).
3. **Semantic Version Comparison Engine (`version.py`):** Arbitrary-depth numeric tuple extraction handling real-world EDA version quirks (`"45.2"`, `"v12-20220611"`, `"9.0.3-rc1"`).
4. **Multi-Tier Non-Invasive Discovery Engine (`detector.py`):** 5-tier resolution hierarchy guaranteeing that user environment variables and existing eSim configuration files take precedence over system paths without altering existing eSim code.

---

## 2. Directory & Component Architecture

```
toolcare/
├── __init__.py                     # Package entry point (exports v2.0.0 & Phase 1 APIs)
├── platform_utils.py               # OS detection, safe subprocess wrapper, package manager probes
├── logger.py                       # Rotating file audit logger (~/.esim/logs/toolcare.log)
├── data/
│   └── tools.json                  # Declarative tool registry (schema 2.0.0)
├── core/
│   ├── __init__.py                 # Core business logic exports
│   ├── registry.py                 # ToolSpec, CompatibilitySpec, ArchiveSpec, ToolRegistry
│   ├── version.py                  # parse_version_tuple, compare_versions, evaluate_compatibility
│   └── detector.py                 # Multi-tier binary detector with environment & config precedence
├── docs/
│   ├── DESIGN_DOCUMENT.md          # Primary architecture specification (v2.0.0)
│   ├── ROADMAP.md                  # 5-Phase, 10-day execution roadmap
│   ├── PHASE_1_IMPLEMENTATION_PLAN.md # Phase 1 approved plan
│   └── PHASE_1_WALKTHROUGH.md      # This walkthrough report
└── tests/
    ├── __init__.py                 # Pytest package
    ├── test_registry.py            # 7 tests: schema, unverified flags, categories, error handling
    ├── test_version.py             # 9 tests: SemVer parsing, EDA quirks, boundary classifications
    └── test_detector.py            # 7 tests: precedence tiers, regex extraction, detection states
```

---

## 3. Deep-Dive: Core Module Mechanisms

### 3.1 Subprocess Isolation & Safety (`platform_utils.py`)
EDA diagnostic checks often spawn multiple CLI processes. On Windows, naive `subprocess.run` calls pop up cmd/powershell windows, disrupting the user.

```python
# toolcare/platform_utils.py snippet
def run_safe_command(cmd: List[str], timeout: int = 10, ...) -> Tuple[int, str, str]:
    if isinstance(cmd, str):
        raise TypeError("run_safe_command requires an argument list (List[str]), not a raw shell string.")

    creationflags = 0
    startupinfo = None
    if IS_WINDOWS:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
        startupinfo=startupinfo,
        timeout=timeout,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
```
* **Security:** Refuses string inputs; strictly enforces tokenized lists, preventing shell injection vulnerabilities.
* **Resilience:** Times out hanging processes and uses UTF-8 decoding with character replacement (`errors="replace"`).

---

### 3.2 Declarative Registry & Metadata Discipline (`data/tools.json` & `core/registry.py`)
In EDA co-simulation, installing a "generic" package frequently causes runtime crashes. For instance:
* Generic Ngspice binaries lack XSPICE code models and the `ivlng` dynamic bridge adapter.
* Generic Icarus Verilog packages omit `libvvp.so` / `libvvp.dll`.
* Ubuntu package managers often ship `ghdl-mcode`, which cannot communicate with NGHDL socket servers.

To prevent silent failures, `tools.json` records verified archive sources and hashes from `windows/deps-manifest.json`, while unverified upstream sources are marked explicitly:

```json
"ghdl": {
  "id": "ghdl",
  "name": "GHDL VHDL Simulator",
  "category": "digital",
  "compatibility": {
    "min_version": "2.0.0",
    "recommended_version": "4.1.0",
    "max_version": "5.5.0",
    "notes": "Must use LLVM backend. The mcode backend crashes eSim socket co-simulation."
  },
  "package_ids": {
    "winget": "UNVERIFIED",
    "apt": "ghdl-llvm",
    "choco": "UNVERIFIED"
  }
}
```

The `ToolSpec` class provides programmatic safety methods:
* `tool.is_package_id_verified("winget")` returns `False`.
* `tool.get_package_id("winget")` safely returns `None`.

---

### 3.3 Semantic Version & Compatibility Engine (`core/version.py`)
EDA version strings vary widely: Ngspice uses `45.2`, Icarus Verilog uses `v12-20220611`, KiCad uses `9.0.3-rc1`, and Verilator uses `Verilator 5.032`.

`parse_version_tuple` extracts numeric sequences and `compare_versions` normalizes tuples with trailing zeros:
* `compare_versions("10.0", "10.0.0") == 0`
* `compare_versions("42.0", "45.2") == -1`
* `compare_versions("9.0.3", "7.0.0") == 1`

`evaluate_compatibility` maps every detected tool into one of six deterministic states:
1. `NOT_INSTALLED`: Binary executable not located on host.
2. `VERSION_UNKNOWN`: Binary exists but version command or regex failed.
3. `INCOMPATIBLE_OLD`: Version $< V_{\text{min}}$ (causes simulation syntax errors).
4. `INCOMPATIBLE_NEW`: Version $> V_{\text{max}}$ (ABI hazard for code models).
5. `UPGRADE_AVAILABLE`: $V_{\text{min}} \le$ Version $< V_{\text{rec}}$ (functional, but newer recommended version exists).
6. `COMPATIBLE`: Version $\ge V_{\text{rec}}$ and $\le V_{\text{max}}$ (fully verified).

---

### 3.4 Multi-Tier Discovery Engine (`core/detector.py`)
eSim allows users to configure custom compiler paths in `~/.nghdl/config.ini` or set environment variables like `ESIM_NGSPICE`. Naively relying on system `$PATH` breaks user configurations.

`detector.py` enforces a 5-tier discovery hierarchy:

```
[Tier 1] Environment Variables (ESIM_NGSPICE, ESIM_IVERILOG, KICAD_PATH, VERILATOR_ROOT)
    │ (if not set)
    ▼
[Tier 2] eSim Configuration Files (~/.nghdl/config.ini [COSIM], ~/.esim/config.ini)
    │ (if not configured)
    ▼
[Tier 3] Bundled eSim & MSYS2 mingw64 Trees (<eSim_Root>/library/bin/, C:\msys64\mingw64\bin)
    │ (if not present)
    ▼
[Tier 4] Operating System Default Locations (C:\Spice64\bin, C:\Program Files\KiCad\*\bin)
    │ (if not found)
    ▼
[Tier 5] System $PATH Fallback (shutil.which)
```

Every discovery result includes `discovery_source` (e.g. `env:ESIM_NGSPICE`, `nghdl_config:COSIM.ngspice`, `system_path`, or `not_found`), providing full traceability for diagnostics.

---

## 4. Test Verification Summary

The complete test suite was executed on the host system using Python 3.14.3:

```text
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.0.3, pluggy-1.6.0 -- E:\Python314\python.exe
cachedir: .pytest_cache
rootdir: E:\100xDevs\eSim
plugins: anyio-4.13.0, asyncio-1.4.0
collecting ... collected 23 items

toolcare/tests/test_detector.py::test_detector_environment_variable_precedence PASSED [  4%]
toolcare/tests/test_detector.py::test_detector_nghdl_config_precedence PASSED [  8%]
toolcare/tests/test_detector.py::test_detector_path_fallback PASSED      [ 13%]
toolcare/tests/test_detector.py::test_probe_version_extraction PASSED    [ 17%]
toolcare/tests/test_detector.py::test_detect_tool_not_installed PASSED   [ 21%]
toolcare/tests/test_detector.py::test_detect_tool_success_compatible PASSED [ 26%]
toolcare/tests/test_detector.py::test_detect_all_returns_all_tools PASSED [ 30%]
toolcare/tests/test_registry.py::test_registry_loads_default_catalog PASSED [ 34%]
toolcare/tests/test_registry.py::test_registry_tool_spec_types PASSED    [ 39%]
toolcare/tests/test_registry.py::test_registry_verified_vs_unverified_package_ids PASSED [ 43%]
toolcare/tests/test_registry.py::test_registry_archive_verification PASSED [ 47%]
toolcare/tests/test_registry.py::test_registry_categories PASSED         [ 52%]
toolcare/tests/test_registry.py::test_registry_missing_file_raises PASSED [ 56%]
toolcare/tests/test_registry.py::test_registry_malformed_json_raises PASSED [ 60%]
toolcare/tests/test_version.py::test_parse_version_tuple_standard PASSED [ 65%]
toolcare/tests/test_version.py::test_parse_version_tuple_eda_quirks PASSED [ 69%]
toolcare/tests/test_version.py::test_parse_version_tuple_invalid_or_empty PASSED [ 73%]
toolcare/tests/test_version.py::test_compare_versions_ordering PASSED    [ 78%]
toolcare/tests/test_version.py::test_evaluate_compatibility_not_installed PASSED [ 82%]
toolcare/tests/test_version.py::test_evaluate_compatibility_incompatible_old PASSED [ 86%]
toolcare/tests/test_version.py::test_evaluate_compatibility_incompatible_new PASSED [ 91%]
toolcare/tests/test_version.py::test_evaluate_compatibility_upgrade_available PASSED [ 95%]
toolcare/tests/test_version.py::test_evaluate_compatibility_compatible PASSED [100%]

============================= 23 passed in 1.11s ==============================
```

---

## 5. Non-Interference Guarantee

Phase 1 code resides strictly inside `toolcare/`. It does not modify, replace, or regress:
* `src/maker/ToolchainCheck.py` (eSim's existing internal diagnostic checks remain untouched).
* `windows/deps-manifest.json` (authoritative reference for bundled packages).
* `windows/windows_bootstrap.py` (monolithic installer bootstrap).
* `Ubuntu/install-eSim.sh` (monolithic Ubuntu installer).
