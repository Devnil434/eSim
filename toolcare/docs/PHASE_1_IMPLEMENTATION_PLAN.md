# Implementation Plan: Phase 1 – Foundation, Declarative Schema & Discovery Engine
## eSim ToolCare (Screening Task 5 – Autumn 2026)

**Target Window:** November 14 – 15, 2026 (Days 1–2)  
**Parent Document:** [`DESIGN_DOCUMENT.md`](file:///e:/100xDevs/eSim/DESIGN_DOCUMENT.md)  
**Parent Roadmap:** [`ROADMAP.md`](file:///e:/100xDevs/eSim/ROADMAP.md)  
**Status:** Ready for Review & User Approval  

---

## 1. Phase 1 Objective & Scope

The objective of **Phase 1** is to build the immutable baseline of eSim ToolCare without altering any existing eSim source code (`src/maker/ToolchainCheck.py`, `windows/deps-manifest.json`, `Ubuntu/install-eSim.sh`).

Phase 1 establishes:
1. **Platform & Process Safeguards:** OS identification, package manager presence probing, and safe subprocess invocation on Windows (`CREATE_NO_WINDOW` and `SW_HIDE`).
2. **Central Declarative Registry:** Schema 2.0.0 tool specifications in `toolcare/data/tools.json` with verified metadata extracted from eSim manifests and explicit `UNVERIFIED` flags for unconfirmed upstream identifiers.
3. **Semantic Version & Compatibility Engine:** Parsing non-standard EDA version strings into numeric tuples, boundary comparison ($V_{\text{min}} \le V \le V_{\text{max}}$), and 6-state status classification.
4. **Multi-Tier Binary Discovery Engine:** Non-invasive path discovery following eSim's established 5-tier resolution hierarchy.
5. **Phase 1 Automated Test Suite:** 100% mocked, deterministic `pytest` tests validating registry schema, SemVer edge cases, and path precedence.

---

## 2. File Deliverables & Module Breakdown

```
toolcare/
├── __init__.py                     # Package entry point & version export (v2.0.0)
├── platform_utils.py               # OS detection, safe subprocess execution, package manager probing
├── logger.py                       # Rotating file audit logger (~/.esim/logs/toolcare.log)
├── data/
│   └── tools.json                  # Declarative tool registry (schema 2.0.0)
├── core/
│   ├── __init__.py                 # Core package exports
│   ├── registry.py                 # ToolSpec, CompatibilitySpec, ArchiveSpec, ToolRegistry
│   ├── version.py                  # parse_version_tuple, compare_versions, evaluate_compatibility
│   └── detector.py                 # Multi-tier binary detector with environment & config precedence
├── docs/
│   └── PHASE_1_IMPLEMENTATION_PLAN.md # Implementation plan mirror
└── tests/
    ├── __init__.py                 # Test suite package
    ├── test_registry.py            # Registry schema & unverified flags validation
    ├── test_version.py             # SemVer parsing & compatibility boundary tests
    └── test_detector.py            # Precedence discovery & mocked execution tests
```

---

## 3. Detailed Component Specifications

### 3.1 Platform Utilities (`toolcare/platform_utils.py`)
* **OS Constants:** `IS_WINDOWS`, `IS_LINUX`, `IS_MAC`, `SYSTEM_OS`, `CPU_ARCH`.
* **Safe Subprocess Wrapper (`run_safe_command`):**
  * Requires command arguments as `List[str]` (strictly forbids `shell=True` for untrusted input).
  * Applies `creationflags=subprocess.CREATE_NO_WINDOW` and `startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW` on Windows so background checks never flash console windows.
  * Captures `stdout` and `stderr` safely with UTF-8 decoding and timeout enforcement (default 10 seconds).
* **Package Manager Probing (`get_available_package_managers`):**
  * Checks presence of `winget`, `choco` on Windows; `apt-get` on Linux; `brew` on macOS.

---

### 3.2 Audit Logger (`toolcare/logger.py`)
* **Log Location:** `~/.esim/logs/toolcare.log` (aligned with eSim runtime log directory).
* **Handler:** `logging.handlers.RotatingFileHandler` (max 5 MB, 3 backup files).
* **Format:** `%(asctime)s [%(levelname)s] [%(name)s] %(message)s`.

---

### 3.3 Declarative Tool Catalog (`toolcare/data/tools.json`)
The registry adheres to Schema 2.0.0. Every tool definition includes:

| Tool ID | Category | Executables (Win / Linux) | Version Probe Command | Regex Pattern | Minimum | Recommended | Maximum Tested | Verified Package ID | Archive URL & SHA-256 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `ngspice` | `analog` | `ngspice.exe` / `ngspice` | `--version` | `ngspice.*?([0-9]+(?:\.[0-9]+)*)` | `38.0` | `45.2` | `46.0` | Winget: `Ngspice.Ngspice`<br>APT: `ngspice` | SourceForge Ngspice 45.2 64-bit (`6a0c44056e7f...` from `deps-manifest.json`) |
| `kicad` | `core` | `kicad.exe` / `kicad` | `--version` | `([0-9]+\.[0-9]+(?:\.[0-9]+)?)` | `7.0.0` | `9.0.3` | `9.9.9` | Winget: `KiCad.KiCad`<br>APT: `kicad` | Official KiCad mirror (`582635ced855...` from `deps-manifest.json`) |
| `iverilog` | `digital` | `iverilog.exe` / `iverilog` | `-V` | `version ([0-9]+(?:\.[0-9]+)*)` | `10.0` | `12.0` | `13.0` | Winget: `UNVERIFIED`<br>APT: `iverilog` | Bleyer Windows Setup (`a614057374df...`) |
| `ghdl` | `digital` | `ghdl.exe` / `ghdl` | `--version` | `GHDL\s+([0-9]+(?:\.[0-9]+)*)` | `2.0.0` | `4.1.0` | `5.5.0` | Winget: `UNVERIFIED`<br>APT: `ghdl-llvm` | MSYS2 mingw-w64 ghdl-llvm package |
| `verilator` | `digital` | `verilator.exe` / `verilator` | `--version` | `Verilator\s+([0-9]+\.[0-9]+)` | `5.006` | `5.032` | `5.999` | Winget: `UNVERIFIED`<br>APT: `verilator` | MSYS2 mingw-w64 verilator package |
| `llvm` | `system` | `clang.exe` / `clang` | `--version` | `clang version ([0-9]+(?:\.[0-9]+)*)` | `14.0` | `18.1.8` | `20.0` | Winget: `LLVM.LLVM`<br>APT: `clang` | LLVM official binary archive |

> [!IMPORTANT]
> Unverified package identifiers are marked as `"UNVERIFIED"` rather than inventing non-existent package names.

---

### 3.4 Registry Parser (`toolcare/core/registry.py`)
* **Dataclasses:**
  * `ArchiveSpec`: `url: str`, `sha256: str`, `archive_type: str`, `strip_components: int`.
  * `CompatibilitySpec`: `min_version: str`, `recommended_version: str`, `max_version: Optional[str]`, `notes: str`.
  * `ToolSpec`: `id: str`, `name: str`, `category: str`, `executables: Dict[str, List[str]]`, `version_command: List[str]`, `version_regex: str`, `compatibility: CompatibilitySpec`, `package_ids: Dict[str, str]`, `archives: Dict[str, ArchiveSpec]`, `critical_components: List[str]`.
* **Class `ToolRegistry`:**
  * `load(path: Optional[Path])`: Loads and validates `tools.json`.
  * `get_tool(tool_id: str) -> Optional[ToolSpec]`.
  * `get_all_tools() -> List[ToolSpec]`.
  * `get_tools_by_category(category: str) -> List[ToolSpec]`.

---

### 3.5 Semantic Version Comparator & Boundary Evaluator (`toolcare/core/version.py`)
* **`parse_version_tuple(v_str: str) -> Tuple[int, ...]`:
  * Extracts numeric components using regex `\d+`.
  * Handles EDA version quirks: `"45.2"` $\rightarrow$ `(45, 2)`, `"v12-20220611"` $\rightarrow$ `(12, 20220611)`, `"9.0.3-rc1"` $\rightarrow$ `(9, 0, 3, 1)`.
* **`compare_versions(v1: str, v2: str) -> int`:
  * Normalizes tuples with trailing zeros to match lengths.
  * Returns `-1` if $v_1 < v_2$, `0` if $v_1 == v_2$, `1` if $v_1 > v_2$.
* **`ToolStatus` Enum:**
  * `NOT_INSTALLED`: Binary was not located on host.
  * `VERSION_UNKNOWN`: Binary exists but version command or regex failed.
  * `INCOMPATIBLE_OLD`: Detected version $< V_{\text{min}}$.
  * `INCOMPATIBLE_NEW`: Detected version $> V_{\text{max}}$ (ABI incompatibility risk).
  * `UPGRADE_AVAILABLE`: $V_{\text{min}} \le$ Detected version $< V_{\text{rec}}$.
  * `COMPATIBLE`: Detected version $\ge V_{\text{rec}}$ and $\le V_{\text{max}}$.
* **`evaluate_compatibility(detected: Optional[str], spec: CompatibilitySpec) -> VersionEvaluation`:
  * Returns structured evaluation with status enum, explanation, and upgrade recommendation.

---

### 3.6 Multi-Tier Binary Discovery Engine (`toolcare/core/detector.py`)
Implements strict discovery precedence order:
1. **Tier 1: Explicit Environment Overrides:**
   * Ngspice: `ESIM_NGSPICE`
   * Icarus Verilog: `ESIM_IVERILOG`
   * KiCad: `KICAD_PATH`
   * Verilator: `VERILATOR_ROOT`
2. **Tier 2: eSim Configuration Files:**
   * Reads `~/.nghdl/config.ini` (`[COSIM]` and `[COMPILER]` sections).
   * Reads `~/.esim/config.ini`.
3. **Tier 3: Bundled eSim Directories:**
   * Checks `<eSim_Root>/library/bin/` and `<eSim_Root>/tools/`.
   * Checks MSYS2 Mingw64 paths (`C:\msys64\mingw64\bin`).
4. **Tier 4: Operating System Default Locations:**
   * Windows: `C:\Spice64\bin`, `C:\Program Files\KiCad\*\bin`, `C:\iverilog\bin`.
   * Linux: `/usr/bin`, `/usr/local/bin`, `/opt/kicad/bin`.
5. **Tier 5: System PATH Fallback:**
   * Searches system `$PATH` via `shutil.which`.

For every discovered binary, `detector.py` invokes its version command using `run_safe_command`, extracts the version string via regex, and evaluates its status via `version.py`.

---

## 4. Phase 1 Test Plan (`toolcare/tests/`)

| Test File | Target Functionality | Key Assertions |
| :--- | :--- | :--- |
| `test_registry.py` | `ToolRegistry` & `tools.json` | • JSON loads without schema errors.<br>• All required tools (`ngspice`, `kicad`, `iverilog`, `ghdl`, `verilator`, `llvm`) exist.<br>• All regex strings compile with `re.compile`.<br>• Correctly handles `UNVERIFIED` package ID flags.<br>• Filter by category works (`analog`, `digital`, `core`, `system`). |
| `test_version.py` | `version.py` | • `parse_version_tuple` handles clean versions (`"45.2"`), prefixed versions (`"v12"`), dates (`"20220611"`), and RCs.<br>• `compare_versions` orders `42.0 < 45.2`, `10.0 == 10.0.0`, `9.0.3 > 7.0.0`.<br>• `evaluate_compatibility` asserts all 6 status states with boundary values. |
| `test_detector.py` | `detector.py` | • Precedence order respects environment variables over system `$PATH`.<br>• Precedence order respects `~/.nghdl/config.ini` over defaults.<br>• Fallback to PATH works when configs are absent.<br>• Safe command execution passes `CREATE_NO_WINDOW` on Windows. |

---

## 5. Execution & Verification Steps

Upon user approval, the following sequence will be executed:
1. Implement `toolcare/platform_utils.py` and `toolcare/logger.py`.
2. Populate verified declarative catalog in `toolcare/data/tools.json`.
3. Implement `toolcare/core/registry.py`.
4. Implement `toolcare/core/version.py`.
5. Implement `toolcare/core/detector.py`.
6. Implement `toolcare/tests/test_registry.py`, `test_version.py`, `test_detector.py`.
7. Execute tests via `E:\Python314\python.exe -m pytest toolcare/tests/ -v` and record output.
8. Verify that importing `toolcare` works cleanly without missing module errors.

---

## 6. Review & Alignment Questions for User

Before code implementation commences, please confirm:
1. **Primary Demonstration Target:** Confirm that Windows 10/11 x64 is the active platform for running and verifying Phase 1 tests.
2. **Metadata Scope:** Confirm that the 6 core tools (`ngspice`, `kicad`, `iverilog`, `ghdl`, `verilator`, `llvm`) are the priority catalog set for Phase 1.
3. **Execution Approval:** Should we proceed with creating these Phase 1 files and running the test suite?
