# Technical Walkthrough: Phase 3 – Multi-Tier Safe Installer & Anti-Tamper Extraction Engine
## eSim ToolCare (Screening Task 5 – Autumn 2026)

**Completion Date:** November 14, 2026  
**Status:** 100% Passed (57/57 Unit Tests Verified on Python 3.14.3)  
**Parent Roadmap:** [`ROADMAP.md`](file:///e:/100xDevs/eSim/ROADMAP.md)  
**Parent Specification:** [`DESIGN_DOCUMENT.md`](file:///e:/100xDevs/eSim/DESIGN_DOCUMENT.md)  
**Preceding Walkthroughs:**  
* [`PHASE_1_WALKTHROUGH.md`](file:///e:/100xDevs/eSim/toolcare/docs/PHASE_1_WALKTHROUGH.md) (Discovery, Registry, SemVer – 23 tests passing)  
* [`PHASE_2_WALKTHROUGH.md`](file:///e:/100xDevs/eSim/toolcare/docs/PHASE_2_WALKTHROUGH.md) (Dependency Graph, Doctor, 5 Workflows – 41 tests passing)  

---

## 1. Executive Summary

Phase 3 introduces automated, multi-tier tool installation capabilities to eSim ToolCare. Because installing third-party EDA tools on end-user machines carries high operational and security risks, Phase 3 implements an anti-tamper extraction pipeline with 10 hardened security controls.

Phase 3 delivers:
1. **Extensible Adapter Architecture (`adapters/base.py`):** Abstract polymorphic interface decoupled across system package managers (`winget`, `apt`) and direct vendor archives.
2. **Canonical Containment Path Safety:** Replaced string-prefix checks with `target_resolved.is_relative_to(base_resolved)` to prevent path-prefix collisions.
3. **Anti-Zip-Slip & Unsafe Member Defense:** Explicitly inspects every archive entry, immediately rejecting traversal paths (`..`), absolute paths (`/etc/`, `C:\`), symlinks (`S_IFLNK`), hardlinks, and special device nodes.
4. **Cryptographic Checksum Verification:** Real-time streaming SHA-256 validation against pinned hashes from `windows/deps-manifest.json`, immediately deleting temporary files upon mismatch.
5. **Staged Extraction & Atomic Promotion:** Isolates downloads and extractions inside temporary staging directories (`tempfile.mkdtemp`) and verifies components before promoting them to the target destination.
6. **Preservation & Rollback on Failure:** Automatically snapshots existing installations before promotion and restores them if deployment fails.
7. **Post-Installation Verification Gate:** Immediately invokes `ToolDetector` and `SimulationDoctor` post-install to confirm the binary runs and satisfies eSim compatibility.
8. **Component-Aware Selection Policy:** Prefers verified vendor archives over generic package manager binaries when a tool requires custom eSim code models (e.g. Ngspice XSPICE code models).

---

## 2. Directory & Codebase Architecture

```
toolcare/
├── __init__.py                     # Package entry point (exports v2.0.0 & Phase 1-3 APIs)
├── platform_utils.py               # OS detection, safe subprocess execution, package manager probing
├── logger.py                       # Rotating file audit logger (~/.esim/logs/toolcare.log)
├── data/
│   └── tools.json                  # Declarative tool registry (schema 2.0.0)
├── adapters/                       # [PHASE 3] Multi-tier package adapters
│   ├── __init__.py                 # Adapter exports
│   ├── base.py                     # BaseAdapter abstract class
│   ├── winget_adapter.py           # Windows Package Manager adapter
│   ├── apt_adapter.py              # Debian/Ubuntu APT adapter
│   └── archive_adapter.py          # Direct verified archive downloader & safe unpacker
├── core/
│   ├── __init__.py
│   ├── registry.py                 # ToolSpec, CompatibilitySpec, ArchiveSpec, ToolRegistry
│   ├── version.py                  # parse_version_tuple, compare_versions, evaluate_compatibility
│   ├── detector.py                 # Multi-tier binary detector (5-tier precedence hierarchy)
│   ├── dependency.py               # Simulation flow dependency graph (5 workflows, d_cosim)
│   ├── doctor.py                   # 5-stage diagnostic health auditor (Severity with CRITICAL)
│   └── installer.py                # [PHASE 3] Installation coordinator & post-verification gate
├── docs/
│   ├── DESIGN_DOCUMENT.md
│   ├── ROADMAP.md
│   ├── PHASE_1_IMPLEMENTATION_PLAN.md
│   ├── PHASE_1_WALKTHROUGH.md
│   ├── PHASE_2_IMPLEMENTATION_PLAN.md
│   ├── PHASE_2_WALKTHROUGH.md
│   ├── PHASE_3_IMPLEMENTATION_PLAN.md
│   └── PHASE_3_WALKTHROUGH.md      # This document
└── tests/
    ├── __init__.py
    ├── test_registry.py            # 7 Phase 1 registry tests
    ├── test_version.py             # 9 Phase 1 version parsing tests
    ├── test_detector.py            # 7 Phase 1 detector precedence tests
    ├── test_dependency.py          # 7 Phase 2 workflow dependency tests
    ├── test_doctor.py              # 11 Phase 2 diagnostic doctor tests
    └── test_installer.py           # [PHASE 3] 16 security, adapter, and verification tests
```

---

## 3. Deep-Dive: Security Safeguards in Phase 3

### 3.1 Canonical Containment vs. Prefix-Collision Prevention (`adapters/archive_adapter.py`)
Traditional path traversal checks often use naive string prefix matching:
```python
# VULNERABLE APPROACH:
if str(resolved_member).startswith(str(resolved_target)):
    extract()
```
*Vulnerability:* If `target_dir` is `/home/user/target`, an archive member resolving to `/home/user/target_evil/payload.bat` passes the prefix check because `"/home/user/target_evil/payload.bat".startswith("/home/user/target")` evaluates to `True`!

ToolCare implements strict canonical resolved-path containment:
```python
def is_safely_contained(base_dir: Path, target_path: Path) -> bool:
    base_resolved = base_dir.resolve()
    try:
        target_resolved = target_path.resolve()
        return target_resolved.is_relative_to(base_resolved)
    except (ValueError, RuntimeError, AttributeError):
        try:
            target_resolved = target_path.resolve()
            common = os.path.commonpath([str(base_resolved), str(target_resolved)])
            return common == str(base_resolved)
        except Exception:
            return False
```
This is verified by `test_path_prefix_collision_prevention` in [`test_installer.py`](file:///e:/100xDevs/eSim/toolcare/tests/test_installer.py#L42-L60).

---

### 3.2 Anti-Zip-Slip, Absolute Path & Symlink Defense
Archive members can exploit symlinks or absolute paths to escape target directories:
1. **Absolute Path Rejection:** Rejects any member starting with `/`, `\`, or Windows drive letters (`C:`).
2. **Traversal Path Rejection:** Normalizes path components and rejects any entry containing `..`.
3. **Symlink Rejection:** In ZIP files, inspects the external attribute mode flags (`(mode & 0o170000) == 0o120000`). In TAR files, checks `member.issym()` and `member.islnk()`. Symlinks are rejected with `SecurityError`.
4. **Special Device Rejection:** In TAR files, checks `member.isfifo()`, `member.ischr()`, and `member.isblk()`. Only regular files and directories are permitted.

---

### 3.3 Streaming Cryptographic Hash Verification
Archives are downloaded in 64 KB chunks, hashing the stream in real time:
```python
hasher = hashlib.sha256()
with urllib.request.urlopen(req, timeout=60) as resp:
    with open(temp_archive, "wb") as f_out:
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            f_out.write(chunk)

if hasher.hexdigest().lower() != expected_sha256.lower():
    temp_archive.unlink(missing_ok=True)
    raise SecurityError("Cryptographic SHA-256 mismatch! Staging file deleted.")
```
If the payload is tampered with or corrupted over the network, it is deleted immediately and raises `SecurityError`.

---

### 3.4 Staged Promotion & Rollback Preservation
When deploying a tool to `~/.esim/tools/<tool_id>/`:
1. Extraction occurs strictly inside an ephemeral temporary staging directory (`tempfile.mkdtemp`).
2. If an existing installation is present at the target path, it is moved to a temporary backup directory (`toolcare_backup_...`).
3. The newly extracted files are promoted to the final destination.
4. If staging or promotion throws an error, ToolCare catches the exception and immediately restores the backup copy:
```python
except Exception as move_err:
    if backup_dir and backup_dir.exists():
        logger.error("Failed to promote staged files; rolling back existing installation.")
        if final_dest.exists():
            shutil.rmtree(str(final_dest), ignore_errors=True)
        shutil.move(str(backup_dir), str(final_dest))
    raise move_err
```

---

### 3.5 Post-Installation Verification Gate (`core/installer.py`)
`ToolInstaller.install_tool()` never assumes an installation succeeded. Immediately following execution:
```python
post_detection = self.detector.detect_tool(spec.id)

if not post_detection.is_installed:
    return InstallResult(success=False, message="Binary could not be detected after installation.")

if not post_detection.is_usable:
    return InstallResult(success=False, message=f"Installed version {post_detection.detected_version} is incompatible.")
```
Only when the newly installed tool is found and satisfies SemVer bounds ($V_{\text{min}} \le V \le V_{\text{max}}$) does `InstallResult` report `success=True`.

---

## 4. Test Verification Summary

The complete test suite (Phases 1, 2, and 3) was executed on Python 3.14.3:

```text
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.0.3, pluggy-1.6.0 -- E:\Python314\python.exe
cachedir: .pytest_cache
rootdir: E:\100xDevs\eSim
plugins: anyio-4.13.0, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 57 items

toolcare/tests/test_dependency.py::test_standardized_d_cosim_workflow_id PASSED [  1%]
toolcare/tests/test_dependency.py::test_all_five_workflows_defined PASSED [  3%]
toolcare/tests/test_dependency.py::test_cascading_impact_analysis PASSED [  5%]
toolcare/tests/test_dependency.py::test_evaluate_workflow_fully_healthy PASSED [  7%]
toolcare/tests/test_dependency.py::test_evaluate_workflow_missing_tool PASSED [  8%]
toolcare/tests/test_dependency.py::test_workflow_specific_component_isolation PASSED [ 10%]
toolcare/tests/test_dependency.py::test_evaluate_workflow_incompatible_tool PASSED [ 12%]
toolcare/tests/test_detector.py::test_detector_environment_variable_precedence PASSED [ 14%]
toolcare/tests/test_detector.py::test_detector_nghdl_config_precedence PASSED [ 15%]
toolcare/tests/test_detector.py::test_detector_path_fallback PASSED      [ 17%]
toolcare/tests/test_detector.py::test_probe_version_extraction PASSED    [ 19%]
toolcare/tests/test_detector.py::test_detect_tool_not_installed PASSED   [ 21%]
toolcare/tests/test_detector.py::test_detect_tool_success_compatible PASSED [ 22%]
toolcare/tests/test_detector.py::test_detect_all_returns_all_tools PASSED [ 24%]
toolcare/tests/test_doctor.py::test_severity_levels_include_critical PASSED [ 26%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_llvm PASSED   [ 28%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_mcode PASSED  [ 29%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_gcc PASSED    [ 31%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_unknown_is_not_compatible PASSED [ 33%]
toolcare/tests/test_doctor.py::test_doctor_relative_discovery_ngspice PASSED [ 35%]
toolcare/tests/test_doctor.py::test_doctor_relative_discovery_iverilog PASSED [ 36%]
toolcare/tests/test_doctor.py::test_doctor_fully_healthy PASSED          [ 38%]
toolcare/tests/test_doctor.py::test_doctor_ghdl_mcode_triggers_critical PASSED [ 40%]
toolcare/tests/test_doctor.py::test_doctor_missing_libvvp_blocks_d_cosim_only PASSED [ 42%]
toolcare/tests/test_doctor.py::test_doctor_missing_kicad_symbols_warning PASSED [ 43%]
toolcare/tests/test_installer.py::test_path_prefix_collision_prevention PASSED [ 45%]
toolcare/tests/test_installer.py::test_archive_download_sha256_match PASSED [ 47%]
toolcare/tests/test_installer.py::test_archive_download_sha256_mismatch_raises_security_error PASSED [ 49%]
toolcare/tests/test_installer.py::test_archive_download_unverified_hash_rejected PASSED [ 50%]
toolcare/tests/test_installer.py::test_safe_extract_zip_traversal_rejected PASSED [ 52%]
toolcare/tests/test_installer.py::test_safe_extract_zip_absolute_path_rejected PASSED [ 54%]
toolcare/tests/test_installer.py::test_safe_extract_zip_symlink_rejected PASSED [ 56%]
toolcare/tests/test_installer.py::test_safe_extract_tar_link_rejected PASSED [ 57%]
toolcare/tests/test_installer.py::test_unsupported_archive_format_rejected PASSED [ 59%]
toolcare/tests/test_installer.py::test_installer_dry_run_zero_writes PASSED [ 61%]
toolcare/tests/test_installer.py::test_archive_install_preserves_existing_on_failure PASSED [ 63%]
toolcare/tests/test_installer.py::test_installer_post_verification_success PASSED [ 64%]
toolcare/tests/test_installer.py::test_installer_post_verification_fails_if_binary_missing PASSED [ 66%]
toolcare/tests/test_installer.py::test_winget_adapter_verified_vs_unverified PASSED [ 68%]
toolcare/tests/test_installer.py::test_apt_adapter_verified_and_dry_run PASSED [ 70%]
toolcare/tests/test_installer.py::test_installer_adapter_selection_policy PASSED [ 71%]
toolcare/tests/test_registry.py::test_registry_loads_default_catalog PASSED [ 73%]
toolcare/tests/test_registry.py::test_registry_tool_spec_types PASSED    [ 75%]
toolcare/tests/test_registry.py::test_registry_verified_vs_unverified_package_ids PASSED [ 77%]
toolcare/tests/test_registry.py::test_registry_archive_verification PASSED [ 78%]
toolcare/tests/test_registry.py::test_registry_categories PASSED         [ 80%]
toolcare/tests/test_registry.py::test_registry_missing_file_raises PASSED [ 82%]
toolcare/tests/test_registry.py::test_registry_malformed_json_raises PASSED [ 84%]
toolcare/tests/test_version.py::test_parse_version_tuple_standard PASSED [ 85%]
toolcare/tests/test_version.py::test_parse_version_tuple_eda_quirks PASSED [ 87%]
toolcare/tests/test_version.py::test_parse_version_tuple_invalid_or_empty PASSED [ 89%]
toolcare/tests/test_version.py::test_compare_versions_ordering PASSED    [ 91%]
toolcare/tests/test_version.py::test_evaluate_compatibility_not_installed PASSED [ 92%]
toolcare/tests/test_version.py::test_evaluate_compatibility_incompatible_old PASSED [ 94%]
toolcare/tests/test_version.py::test_evaluate_compatibility_incompatible_new PASSED [ 96%]
toolcare/tests/test_version.py::test_evaluate_compatibility_upgrade_available PASSED [ 98%]
toolcare/tests/test_version.py::test_evaluate_compatibility_compatible PASSED [100%]

============================= 57 passed in 0.98s ==============================
```

---

## 5. Non-Interference Guarantee

Phase 3 introduces no side effects to existing eSim code:
* Zero modifications to existing eSim codebase (`src/maker/ToolchainCheck.py`, `windows/build-windows.ps1`, `Ubuntu/install-eSim.sh`).
* All adapters and installation utilities live strictly in `toolcare/adapters/` and `toolcare/core/`.
* No live installations were executed on the user host machine during testing; all test procedures used mocked endpoints and isolated temporary directories.
