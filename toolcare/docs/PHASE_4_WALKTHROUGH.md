# Phase 4 Walkthrough: Configuration Safety, Rollback & Dedicated Update Engine, and Lightweight CLI

## 1. Executive Summary

Phase 4 completes the operational core of **eSim ToolCare (Task 5: Automated Tool Manager)** by establishing:
1. **Comment-Preserving Configuration Management**: Modifies `.ini` and KiCad S-expression configurations in place without clobbering user comments, custom keys, or formatting.
2. **Atomic Writes with Failure Cleanup**: Employs unique temporary sibling files (`.{target}.tmp_{uuid}`) with permission copying, descriptor flushing (`fsync`), atomic replacement (`os.replace`), and deterministic cleanup upon failure.
3. **Cryptographically Verified Backup & Rollback**: Generates `manifest.json` snapshots tracking original paths, UTC timestamps, file sizes, and streaming SHA-256 digests. Rollback pre-verifies all backup files and aborts with `ConfigSecurityError` if tampering or corruption is detected.
4. **Targeted KiCad S-Expression Editing**: Safely inspects, updates, and appends `(sym_lib_table ...)` entries idempotently, completely preventing duplicate libraries.
5. **Dedicated Update Coordinator (`UpdateEngine`)**: Pre-computes immutable execution plans, traces affected simulation workflows (`simulation`, `verifier`, `d_cosim`, `ngveri`, `nghdl`), rejects unverified packages/hashes, enforces interactive `[y/N]` confirmation (defaulting to No), and automatically restores configurations upon verification failure.
6. **Standard Library CLI (`toolcare.cli`)**: Implements clean, self-contained subcommands (`list`, `check`, `doctor`, `install`, `update`, `configure`, `logs`) requiring zero heavy external dependencies.

All 90 unit tests across Phases 1–4 pass with 100% success in 3.01 seconds.

---

## 2. Applied Security Corrections

| # | User Requirement / Correction | Implementation in Phase 4 | Verification Test |
|---|---|---|---|
| **1** | Preserve configuration comments & formatting | Implemented `update_ini_section_in_place()` doing line-by-line scanning; never uses destructive `configparser.write()`. | `test_update_ini_preserves_comments_and_formatting` |
| **2** | Safe targeted S-expression editing for KiCad `sym-lib-table` | Balanced parenthetical regex matching `(lib (name "...") ...)`; updates existing entries in place or inserts right before the root closing parenthesis. | `test_kicad_sym_lib_table_targeted_sexpr_update` |
| **3** | Store backup manifests with paths, timestamps, sizes & SHA-256 | `create_backup()` generates `manifest.json` recording `BackupFileEntry` records with SHA-256 and byte sizes. | `test_create_backup_manifest_integrity` |
| **4** | Verify backup integrity before rollback | `rollback()` computes SHA-256 of all backup copies prior to touching active files; raises `ConfigSecurityError` on mismatch. | `test_rollback_tamper_detection_rejection` |
| **5** | Atomic writes via unique temporary siblings & cleanup | `atomic_write()` writes to sibling `.{name}.tmp_{uuid}`, fsyncs, preserves permissions with `shutil.copymode`, and unlinks temporary files in `finally`. | `test_atomic_write_success_and_sibling_cleanup`, `test_atomic_write_failure_cleans_up_tmp` |
| **6** | Reject unverified target versions, package IDs, or hashes | `plan_update()` marks `is_verified=False` for unverified entries (e.g. `ghdl`, `verilator`); `execute_update()` rejects unconfirmed sources. | `test_plan_update_rejects_unverified_source`, `test_execute_update_unverified_rejection` |
| **7** | Automatic rollback if post-install verification fails | If `installer.install_tool()` fails post-check, `UpdateEngine` immediately triggers `config_handler.rollback()` and logs `FAILED_ROLLED_BACK`. | `test_execute_update_failed_post_install_rollback` |
| **8** | Explicit interactive confirmation (default No) | Requires explicit prompt `[y/N]`; enters cancellation unless user types `y` or `yes`. Bypassable via `--yes` in headless automation. | `test_execute_update_user_cancellation` |
| **9** | Validate KiCad library entries against actual eSim source | `get_actual_esim_symbol_libraries()` validates `library/kicadLibrary/eSim-symbols/` and `~/.esim/kicad_symbols/` paths before registration. | `test_kicad_sym_lib_table_targeted_sexpr_update`, `test_kicad_sym_lib_table_idempotent_no_duplicates` |
| **10**| Zero side effects for `--check` and `--dry-run` | `--check` and `--dry-run` execute plan generation and preview with zero disk writes, zero downloads, and zero package manager commands. | `test_execute_update_dry_run_zero_modifications`, `test_cli_update_check`, `test_cli_configure_dry_run` |

---

## 3. Architecture & File Structure

```
toolcare/
├── __init__.py                # Exports ConfigHandler, UpdateEngine, Doctor, Installer, Detector
├── __main__.py                # Entrypoint for python -m toolcare
├── cli.py                     # Lightweight argparse CLI (list, check, doctor, install, update, configure, logs)
├── logger.py                  # Central rotating audit logger (~/.esim/logs/toolcare.log)
├── platform_utils.py          # Process isolation (CREATE_NO_WINDOW, SW_HIDE)
├── data/
│   └── tools.json             # Declarative schema 2.0.0 catalog
├── core/
│   ├── registry.py            # ToolSpec, CompatibilitySpec, ArchiveSpec
│   ├── version.py             # Semantic version parsing & compatibility engine
│   ├── detector.py            # 5-tier discovery hierarchy
│   ├── dependency.py          # 5 simulation workflows & cascading impact analysis
│   ├── doctor.py              # 5-stage health auditor & GHDL backend inspector
│   ├── installer.py           # Staging coordinator & post-installation gate
│   ├── config_handler.py      # Atomic writes, comment preservation, manifest backups, KiCad S-expr editing
│   └── updater.py             # Update planning, confirmation prompt, rollback, update_history.json
├── adapters/
│   ├── base.py                # BaseAdapter interface
│   ├── winget_adapter.py      # Windows Package Manager adapter
│   ├── apt_adapter.py         # Debian/Ubuntu APT adapter
│   └── archive_adapter.py     # Canonical containment Anti-Zip-Slip, staged downloader
├── docs/
│   ├── DESIGN_DOCUMENT.md
│   ├── ROADMAP.md
│   ├── PHASE_1_WALKTHROUGH.md
│   ├── PHASE_2_WALKTHROUGH.md
│   ├── PHASE_3_WALKTHROUGH.md
│   └── PHASE_4_WALKTHROUGH.md # This document
└── tests/
    ├── test_registry.py       # 7 tests (catalog parsing & schema)
    ├── test_version.py        # 9 tests (version comparisons)
    ├── test_detector.py       # 7 tests (discovery tiers)
    ├── test_dependency.py     # 7 tests (workflows & cascading impact)
    ├── test_doctor.py         # 11 tests (health checks & backend inspection)
    ├── test_installer.py      # 16 tests (security, containment, rollback)
    ├── test_config_handler.py # 9 tests (atomic write, manifests, INI & KiCad S-expr)
    ├── test_updater.py        # 7 tests (update engine, confirmation, history)
    └── test_cli.py            # 17 tests (CLI commands, flags, exit codes)
```

---

## 4. Test Suite Execution & Output

Executed command:
```powershell
& "E:\Python314\python.exe" -m pytest toolcare/tests/ -v
```

Output:
```
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.0.3, pluggy-1.6.0 -- E:\Python314\python.exe
cachedir: .pytest_cache
rootdir: E:\100xDevs\eSim
plugins: anyio-4.13.0, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False
collected 90 items

toolcare/tests/test_cli.py::test_cli_help PASSED                         [  1%]
toolcare/tests/test_cli.py::test_cli_version PASSED                      [  2%]
toolcare/tests/test_cli.py::test_cli_no_subcommand PASSED                [  3%]
toolcare/tests/test_cli.py::test_cli_list PASSED                         [  4%]
toolcare/tests/test_cli.py::test_cli_list_category_filter PASSED         [  5%]
toolcare/tests/test_cli.py::test_cli_check_all PASSED                    [  6%]
toolcare/tests/test_cli.py::test_cli_check_single_tool_verbose PASSED    [  7%]
toolcare/tests/test_cli.py::test_cli_check_unknown_tool PASSED           [  8%]
toolcare/tests/test_cli.py::test_cli_doctor_healthy PASSED               [ 10%]
toolcare/tests/test_cli.py::test_cli_doctor_target_flow_blocked PASSED   [ 11%]
toolcare/tests/test_cli.py::test_cli_install_dry_run PASSED              [ 12%]
toolcare/tests/test_cli.py::test_cli_update_check PASSED                 [ 13%]
toolcare/tests/test_cli.py::test_cli_update_dry_run PASSED               [ 14%]
toolcare/tests/test_cli.py::test_cli_update_missing_tool_no_check PASSED [ 15%]
toolcare/tests/test_cli.py::test_cli_configure_sync_dry_run PASSED       [ 16%]
toolcare/tests/test_cli.py::test_cli_configure_rollback_dry_run PASSED   [ 17%]
toolcare/tests/test_cli.py::test_cli_logs PASSED                         [ 18%]
toolcare/tests/test_config_handler.py::test_atomic_write_success_and_sibling_cleanup PASSED [ 20%]
toolcare/tests/test_config_handler.py::test_atomic_write_failure_cleans_up_tmp PASSED [ 21%]
toolcare/tests/test_config_handler.py::test_create_backup_manifest_integrity PASSED [ 22%]
toolcare/tests/test_config_handler.py::test_rollback_verified_restoration PASSED [ 23%]
toolcare/tests/test_config_handler.py::test_rollback_tamper_detection_rejection PASSED [ 24%]
toolcare/tests/test_config_handler.py::test_update_ini_preserves_comments_and_formatting PASSED [ 25%]
toolcare/tests/test_config_handler.py::test_kicad_sym_lib_table_targeted_sexpr_update PASSED [ 26%]
toolcare/tests/test_config_handler.py::test_kicad_sym_lib_table_idempotent_no_duplicates PASSED [ 27%]
toolcare/tests/test_config_handler.py::test_ngspice_spinit_codemodel_sync PASSED [ 28%]
toolcare/tests/test_dependency.py::test_standardized_d_cosim_workflow_id PASSED [ 30%]
toolcare/tests/test_dependency.py::test_all_five_workflows_defined PASSED [ 31%]
toolcare/tests/test_dependency.py::test_cascading_impact_analysis PASSED [ 32%]
toolcare/tests/test_dependency.py::test_evaluate_workflow_fully_healthy PASSED [ 33%]
toolcare/tests/test_dependency.py::test_evaluate_workflow_missing_tool PASSED [ 34%]
toolcare/tests/test_dependency.py::test_workflow_specific_component_isolation PASSED [ 35%]
toolcare/tests/test_dependency.py::test_evaluate_workflow_incompatible_tool PASSED [ 36%]
toolcare/tests/test_detector.py::test_detector_environment_variable_precedence PASSED [ 37%]
toolcare/tests/test_detector.py::test_detector_nghdl_config_precedence PASSED [ 38%]
toolcare/tests/test_detector.py::test_detector_path_fallback PASSED      [ 40%]
toolcare/tests/test_detector.py::test_probe_version_extraction PASSED    [ 41%]
toolcare/tests/test_detector.py::test_detect_tool_not_installed PASSED   [ 42%]
toolcare/tests/test_detector.py::test_detect_tool_success_compatible PASSED [ 43%]
toolcare/tests/test_detector.py::test_detect_all_returns_all_tools PASSED [ 44%]
toolcare/tests/test_doctor.py::test_severity_levels_include_critical PASSED [ 45%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_llvm PASSED   [ 46%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_mcode PASSED  [ 47%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_gcc PASSED    [ 48%]
toolcare/tests/test_doctor.py::test_ghdl_backend_detection_unknown_is_not_compatible PASSED [ 50%]
toolcare/tests/test_doctor.py::test_doctor_relative_discovery_ngspice PASSED [ 51%]
toolcare/tests/test_doctor.py::test_doctor_relative_discovery_iverilog PASSED [ 52%]
toolcare/tests/test_doctor.py::test_doctor_fully_healthy PASSED          [ 53%]
toolcare/tests/test_doctor.py::test_doctor_ghdl_mcode_triggers_critical PASSED [ 54%]
toolcare/tests/test_doctor.py::test_doctor_missing_libvvp_blocks_d_cosim_only PASSED [ 55%]
toolcare/tests/test_doctor.py::test_doctor_missing_kicad_symbols_warning PASSED [ 56%]
toolcare/tests/test_installer.py::test_path_prefix_collision_prevention PASSED [ 57%]
toolcare/tests/test_installer.py::test_archive_download_sha256_match PASSED [ 58%]
toolcare/tests/test_installer.py::test_archive_download_sha256_mismatch_raises_security_error PASSED [ 60%]
toolcare/tests/test_installer.py::test_archive_download_unverified_hash_rejected PASSED [ 61%]
toolcare/tests/test_installer.py::test_safe_extract_zip_traversal_rejected PASSED [ 62%]
toolcare/tests/test_installer.py::test_safe_extract_zip_absolute_path_rejected PASSED [ 63%]
toolcare/tests/test_installer.py::test_safe_extract_zip_symlink_rejected PASSED [ 64%]
toolcare/tests/test_installer.py::test_safe_extract_tar_link_rejected PASSED [ 65%]
toolcare/tests/test_installer.py::test_unsupported_archive_format_rejected PASSED [ 66%]
toolcare/tests/test_installer.py::test_installer_dry_run_zero_writes PASSED [ 67%]
toolcare/tests/test_archive_install_preserves_existing_on_failure PASSED [ 68%]
toolcare/tests/test_installer.py::test_installer_post_verification_success PASSED [ 70%]
toolcare/tests/test_installer.py::test_installer_post_verification_fails_if_binary_missing PASSED [ 71%]
toolcare/tests/test_installer.py::test_winget_adapter_verified_vs_unverified PASSED [ 72%]
toolcare/tests/test_installer.py::test_apt_adapter_verified_and_dry_run PASSED [ 73%]
toolcare/tests/test_installer.py::test_installer_adapter_selection_policy PASSED [ 74%]
toolcare/tests/test_registry.py::test_registry_loads_default_catalog PASSED [ 75%]
toolcare/tests/test_registry.py::test_registry_tool_spec_types PASSED    [ 76%]
toolcare/tests/test_registry.py::test_registry_verified_vs_unverified_package_ids PASSED [ 77%]
toolcare/tests/test_registry.py::test_registry_archive_verification PASSED [ 78%]
toolcare/tests/test_registry.py::test_registry_categories PASSED         [ 80%]
toolcare/tests/test_registry.py::test_registry_missing_file_raises PASSED [ 81%]
toolcare/tests/test_registry.py::test_registry_malformed_json_raises PASSED [ 82%]
toolcare/tests/test_updater.py::test_plan_update_identifies_affected_workflows PASSED [ 83%]
toolcare/tests/test_updater.py::test_plan_update_rejects_unverified_source PASSED [ 84%]
toolcare/tests/test_updater.py::test_execute_update_dry_run_zero_modifications PASSED [ 85%]
toolcare/tests/test_updater.py::test_execute_update_unverified_rejection PASSED [ 86%]
toolcare/tests/test_updater.py::test_execute_update_user_cancellation PASSED [ 87%]
toolcare/tests/test_updater.py::test_execute_update_failed_post_install_rollback PASSED [ 88%]
toolcare/tests/test_updater.py::test_execute_update_success_and_history_logging PASSED [ 90%]
toolcare/tests/test_version.py::test_parse_version_tuple_standard PASSED [ 91%]
toolcare/tests/test_version.py::test_parse_version_tuple_eda_quirks PASSED [ 92%]
toolcare/tests/test_version.py::test_parse_version_tuple_invalid_or_empty PASSED [ 93%]
toolcare/tests/test_version.py::test_compare_versions_ordering PASSED    [ 94%]
toolcare/tests/test_version.py::test_evaluate_compatibility_not_installed PASSED [ 95%]
toolcare/tests/test_version.py::test_evaluate_compatibility_incompatible_old PASSED [ 96%]
toolcare/tests/test_version.py::test_evaluate_compatibility_incompatible_new PASSED [ 97%]
toolcare/tests/test_version.py::test_evaluate_compatibility_upgrade_available PASSED [ 98%]
toolcare/tests/test_version.py::test_evaluate_compatibility_compatible PASSED [100%]

============================= 90 passed in 3.01s ==============================
```

---

## 5. Live CLI Demonstrations

### 5.1 `python -m toolcare list`
```
eSim ToolCare - Registered Tool Catalog
+-----------+------------------------+----------+---------+---------+------------------------------------+
| Tool ID   | Name                   | Category | Min Ver | Rec Ver | Used In Workflows                  |
+-----------+------------------------+----------+---------+---------+------------------------------------+
| ngspice   | Ngspice Simulator      | analog   | 38.0    | 45.2    | simulation, d_cosim, ngveri, nghdl |
| kicad     | KiCad EDA Suite        | core     | 7.0.0   | 9.0.3   | simulation                         |
| iverilog  | Icarus Verilog         | digital  | 10.0    | 12.0    | verifier, d_cosim                  |
| ghdl      | GHDL VHDL Simulator    | digital  | 2.0.0   | 4.1.0   | nghdl                              |
| verilator | Verilator Simulator    | digital  | 5.006   | 5.032   | ngveri                             |
| llvm      | LLVM / Clang Toolchain | system   | 14.0    | 18.1.8  | ngveri, nghdl                      |
+-----------+------------------------+----------+---------+---------+------------------------------------+
Total tools: 6
```

### 5.2 `python -m toolcare check`
```
eSim ToolCare - Toolchain Compatibility Status
+-----------+---------+---------------+-------------+---------------+--------------+
| Tool ID   | Status  | Installed Ver | Recommended | Compatibility | Update Avail |
+-----------+---------+---------------+-------------+---------------+--------------+
| ngspice   | MISSING | None          | 45.2        | NOT_INSTALLED | YES          |
| kicad     | MISSING | None          | 9.0.3       | NOT_INSTALLED | YES          |
| iverilog  | MISSING | None          | 12.0        | NOT_INSTALLED | YES          |
| ghdl      | MISSING | None          | 4.1.0       | NOT_INSTALLED | NO           |
| verilator | MISSING | None          | 5.032       | NOT_INSTALLED | NO           |
| llvm      | MISSING | None          | 18.1.8      | NOT_INSTALLED | NO           |
+-----------+---------+---------------+-------------+---------------+--------------+
```

### 5.3 `python -m toolcare update --check`
```
eSim ToolCare - Checking Upstream Tool Updates (Zero Modifications)...
+-----------+-------------+------------+-----------------+---------------+------------------------------------+
| Tool ID   | Current Ver | Target Ver | Verified        | Update Needed | Affected Workflows                 |
+-----------+-------------+------------+-----------------+---------------+------------------------------------+
| ngspice   | MISSING     | 45.2       | YES             | YES           | simulation, d_cosim, ngveri, nghdl |
| kicad     | MISSING     | 9.0.3      | YES             | YES           | simulation                         |
| iverilog  | MISSING     | 12.0       | YES             | YES           | verifier, d_cosim                  |
| ghdl      | MISSING     | 4.1.0      | NO (UNVERIFIED) | NO            | nghdl                              |
| verilator | MISSING     | 5.032      | NO (UNVERIFIED) | NO            | ngveri                             |
| llvm      | MISSING     | 18.1.8     | NO (UNVERIFIED) | NO            | ngveri, nghdl                      |
+-----------+-------------+------------+-----------------+---------------+------------------------------------+
```

### 5.4 `python -m toolcare update ngspice --dry-run`
```
ToolCare Update Engine: Preparing update for 'ngspice'...

[Dry-Run] Update Plan for 'ngspice':
  * Current Version   : NOT INSTALLED
  * Target Version    : 45.2
  * Method            : archive
  * Verified Source   : https://downloads.sourceforge.net/project/ngspice/ng-spice-rework/45.2/ngspice-45.2_64.7z
  * Affected Flows    : simulation, d_cosim, ngveri, nghdl
  * Backups Required  : 0 files
  * Action: 1. Snapshot 0 configuration files to ~/.esim/backups/.
  * Action: 2. Deploy ngspice v45.2 using archive adapter.
  * Action: 3. Execute post-installation verification check.
  * Action: 4. Re-synchronize eSim configuration files.
```

### 5.5 `python -m toolcare doctor --flow d_cosim`
```
Targeting specific workflow: 'd_cosim'
===========================================================================
Workflow: Digital/Mixed-Signal Co-Simulation (d_cosim) (d_cosim)
Readiness: [BLOCKED]
Diagnostic Messages:
  * Mandatory tool 'ngspice' is not installed.
  * Mandatory tool 'iverilog' is not installed.
===========================================================================
```

---

## 6. Changed & Created Files in Phase 4

| File Path | Nature | Purpose |
|---|---|---|
| `toolcare/core/config_handler.py` | Created | Atomic writes, comment preservation, manifest backups, KiCad S-expression manipulation |
| `toolcare/core/updater.py` | Created | Pre-update planning, workflow tracing, confirmation prompt, rollback, history logging |
| `toolcare/cli.py` | Created | Lightweight CLI entry points and tabular formatters |
| `toolcare/__main__.py` | Created | Module invocation hook (`python -m toolcare`) |
| `toolcare/core/installer.py` | Modified | Added support for `preferred_method="auto"` |
| `toolcare/core/__init__.py` | Modified | Exported `ConfigHandler`, `atomic_write`, `UpdateEngine`, `UpdatePlan` |
| `toolcare/__init__.py` | Modified | Exported `ConfigHandler`, `UpdateEngine`, `UpdatePlan` |
| `toolcare/tests/test_config_handler.py` | Created | 9 tests covering atomic writes, failure cleanup, manifests, tampering, INI comments, and S-exprs |
| `toolcare/tests/test_updater.py` | Created | 7 tests covering plan generation, affected flows, unverified rejection, cancellation, rollback, and history |
| `toolcare/tests/test_cli.py` | Created | 17 tests covering argument parsing, formatters, commands, dry-run, and exit codes |

---

## 7. Limitations & Open Assumptions

1. **Active KiCad GUI Concurrency**: If KiCad is running simultaneously while `toolcare configure --sync` executes, KiCad holds its in-memory symbol library table and may overwrite disk changes on exit. A future enhancement in Phase 5 GUI could inspect active KiCad processes and warn the user.
2. **Interactive TTY Detection**: In headless CI environments where standard input (`sys.stdin`) is not attached to an interactive terminal, `toolcare update <tool>` requires the `--yes` (`-y`) flag to proceed; otherwise, an `EOFError` triggers a safe abort.
3. **No Network Access in Default Sandbox**: The test suite uses comprehensive mocked fixtures for network and package manager executions to ensure complete offline determinism and zero machine side effects.

---

## 8. Next Step: Awaiting Phase 5 Review

In compliance with the internship submission workflow:
- Implementation of Phase 4 is complete and fully verified.
- **Phase 5 (PyQt5/PyQt6 GUI Integration & eSim Menu Hooks) has NOT been started.**
- We pause here for the user's review and approval.
