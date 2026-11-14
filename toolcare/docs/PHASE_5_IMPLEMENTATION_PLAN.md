# Phase 5 Implementation Plan: PyQt GUI Integration & eSim Menu Hooks

## 1. Overview & Objectives

Phase 5 delivers the visual and interactive user interface for **eSim ToolCare (Task 5: Automated Tool Manager)**, seamlessly integrating with eSim's existing desktop architecture. 

The objectives of Phase 5 are:
1. Provide a modern, clean, tabbed graphical management window (`ToolCareDialog`) supporting both PyQt5 and PyQt6 with graceful headless fallback.
2. Ensure **non-blocking asynchronous operations** using a dedicated background worker (`WorkerThread`) that emits progress and log signals, preventing GUI freezing during archive downloads or deep Doctor audits.
3. Expose interactive diagnostic results with workflow-readiness visual badges (`simulation`, `verifier`, `d_cosim`, `ngveri`, `nghdl`).
4. Provide one-click KiCad symbol synchronization and snapshot-based configuration rollback.
5. Provide non-intrusive integration hooks for eSim's menu bar (`Help` / `Tools` menu) without mutating core eSim source code.
6. Support comprehensive automated testing using `pytest-qt` / headless PyQt fixtures without requiring an active X11/Wayland/Windows display server.

---

## 2. Architecture & Component Design

```
toolcare/gui/
├── __init__.py           # GUI entry point & PyQt availability checker
├── worker.py             # QThread background worker emitting progress & log signals
├── dialog.py             # Tabbed ToolCareDialog (Status, Doctor, Config, Logs)
├── hooks.py              # Non-intrusive eSim menu bar attachment hook
└── tests/
    └── test_gui.py       # Headless GUI tests (dialog init, worker signals, tabs)
```

### 2.1 Tab Layout in `ToolCareDialog`
1. **Tab 1: Toolchain Status**
   - Interactive table: Tool ID, Tool Name, Category, Detected Version, Recommended Version, Status chip (`[OK]`, `[UPGRADE AVAILABLE]`, `[MISSING]`, `[INCOMPATIBLE]`).
   - Action buttons: "Check Upstream Updates", "Update Selected Tool", "Install Tool".
   - Safe confirmation modal on update action (defaulting to No).
2. **Tab 2: Simulation Doctor (Health Diagnostics)**
   - "Run Diagnostics" button with progress spinner.
   - Top workflow readiness cards: visual green/red chips for all 5 simulation flows (`simulation`, `verifier`, `d_cosim`, `ngveri`, `nghdl`).
   - Tree widget categorizing 5 diagnostic stages with severity tags (`[INFO]`, `[WARNING]`, `[ERROR]`, `[CRITICAL]`) and collapsible remediation suggestions.
3. **Tab 3: Configuration & KiCad Libraries**
   - KiCad `sym-lib-table` synchronization status and registered eSim symbol count.
   - Ngspice `spinit` code model path inspector.
   - Backup snapshots list: timestamp, label, file count, and SHA-256 integrity indicator.
   - "Rollback to Selected Snapshot" button with safety confirmation.
4. **Tab 4: Audit & Update Logs**
   - Live view of `~/.esim/logs/toolcare.log` and `update_history.json`.
   - "Refresh" and "Clear View" buttons.

### 2.2 Background Worker (`WorkerThread`)
- Inherits from `QThread`.
- Custom Qt signals:
  - `started_task(str)`: Name of active task.
  - `progress(int, str)`: Percentage complete and status message.
  - `log_message(str)`: Real-time stream of audit events.
  - `finished_task(bool, str)`: Boolean success flag and summary message.
- Wraps long-running operations (`SimulationDoctor.run_diagnostics`, `ToolInstaller.install_tool`, `UpdateEngine.execute_update`).

### 2.3 Non-Intrusive eSim Menu Hook (`hooks.py`)
- Provides `attach_toolcare_to_menu(main_window)` helper.
- Dynamically checks if PyQt5/PyQt6 is loaded.
- Locates eSim `Help` or `Tools` menu and appends a `ToolCare Toolchain Manager...` action.
- Does not modify `src/frontEnd/helpMenu.py` or `src/frontEnd/main.py` directly; can be invoked during eSim bootstrap or launched standalone via `python -m toolcare --gui`.

---

## 3. Implementation Steps

1. **Step 5.1: Create `toolcare/gui/worker.py`**
   - Implement `WorkerThread` with Qt signals and exception isolation.
2. **Step 5.2: Create `toolcare/gui/dialog.py`**
   - Implement `ToolCareDialog` with 4 tabs, styling, and signal/slot bindings.
3. **Step 5.3: Create `toolcare/gui/hooks.py` and `toolcare/gui/__init__.py`**
   - Expose `launch_gui()` and menu injection helpers.
4. **Step 5.4: Connect CLI flag `--gui`**
   - Add `toolcare --gui` to `toolcare/cli.py` to launch the GUI dialog.
5. **Step 5.5: Comprehensive Unit & Headless GUI Testing**
   - Implement `toolcare/tests/test_gui.py` using `pytest` with `os.environ["QT_QPA_PLATFORM"] = "offscreen"`.
   - Test dialog construction, tab population, worker signal emission, and error handling.
6. **Step 5.6: Regression Testing**
   - Run complete test suite (Phases 1–5).

---

## 4. Testing Plan

- **Headless GUI Initialization**: Verify `ToolCareDialog` initializes without crashing under offscreen platform.
- **Table Data Population**: Verify Tab 1 displays all tools from `tools.json`.
- **Doctor Diagnostic Trigger**: Verify Tab 2 triggers background diagnostics and updates workflow status chips.
- **Worker Signal Integrity**: Verify progress and finish signals are emitted accurately.
- **Menu Hook Resilience**: Verify `attach_toolcare_to_menu` handles `None` or non-Qt menu bars gracefully without raising exceptions.
