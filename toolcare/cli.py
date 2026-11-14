"""
toolcare.cli - Command-Line Interface for eSim ToolCare.

Provides lightweight, robust CLI subcommands:
  - list: Enumerate all registered tools in tools.json catalog.
  - check: Detect installed tools, check version compatibility, and detect available updates.
  - doctor: Run 5-stage health diagnostic audit across simulation workflows.
  - install: Safely stage and install a tool using verified adapters.
  - update: Plan and execute tool updates with interactive confirmation and rollback.
  - configure: Synchronize KiCad symbols and simulation configs, or rollback.
  - logs: View recent audit logs from ~/.esim/logs/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from toolcare import __version__
from toolcare.core.config_handler import ConfigHandler
from toolcare.core.dependency import ALL_WORKFLOW_IDS, DependencyGraph
from toolcare.core.detector import ToolDetector
from toolcare.core.doctor import Severity, SimulationDoctor
from toolcare.core.installer import ToolInstaller
from toolcare.core.registry import ToolRegistry
from toolcare.core.updater import UpdateEngine
from toolcare.core.version import ToolStatus
from toolcare.logger import get_logger
from toolcare.platform_utils import get_esim_config_dir

logger = get_logger("cli")


def _format_table(headers: List[str], rows: List[List[str]]) -> str:
    """Format tabular data into an aligned ASCII table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))

    # Header
    sep_line = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"

    lines = [sep_line, header_line, sep_line]
    for row in rows:
        row_line = "| " + " | ".join(str(row[i]).ljust(col_widths[i]) if i < len(row) else "".ljust(col_widths[i]) for i in range(len(col_widths))) + " |"
        lines.append(row_line)
    lines.append(sep_line)
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Subcommand Handlers
# -----------------------------------------------------------------------------
def handle_list(args: argparse.Namespace) -> int:
    """Enumerate registered tools in eSim ToolCare catalog."""
    registry = ToolRegistry()
    tools = registry.get_all_tools()

    if args.category:
        cat_lower = args.category.strip().lower()
        tools = [t for t in tools if t.category.lower() == cat_lower]

    if not tools:
        print(f"No tools found matching category '{args.category}'.")
        return 0

    dep_graph = DependencyGraph()
    headers = ["Tool ID", "Name", "Category", "Min Ver", "Rec Ver", "Used In Workflows"]
    rows = []
    for t in tools:
        wfs = dep_graph.get_dependent_workflows(t.id)
        wf_str = ", ".join(wfs) if wfs else "Core/General"
        rows.append([
            t.id,
            t.name,
            t.category,
            t.compatibility.min_version or "None",
            t.compatibility.recommended_version,
            wf_str,
        ])

    print("\neSim ToolCare - Registered Tool Catalog")
    print(_format_table(headers, rows))
    print(f"Total tools: {len(tools)}\n")
    return 0


def handle_check(args: argparse.Namespace) -> int:
    """Query tool detection, version compatibility, and update availability."""
    registry = ToolRegistry()
    detector = ToolDetector(registry=registry)
    updater = UpdateEngine(registry=registry, detector=detector)

    tool_ids = [args.tool] if args.tool else [t.id for t in registry.get_all_tools()]

    headers = ["Tool ID", "Status", "Installed Ver", "Recommended", "Compatibility", "Update Avail"]
    rows = []

    for tid in tool_ids:
        spec = registry.get_tool(tid)
        if not spec:
            print(f"Error: Unknown tool '{tid}'. Run 'toolcare list' to see valid tools.", file=sys.stderr)
            return 1

        det = detector.detect_tool(tid)
        plan = updater.plan_update(tid)

        status_str = "INSTALLED" if det.is_installed else "MISSING"
        compat_str = det.evaluation.status.value if det.evaluation else "UNKNOWN"
        update_str = "YES" if plan.is_update_available else "NO"

        rows.append([
            tid,
            status_str,
            det.detected_version or "None",
            spec.compatibility.recommended_version,
            compat_str,
            update_str,
        ])

    print("\neSim ToolCare - Toolchain Compatibility Status")
    print(_format_table(headers, rows))

    if args.verbose and args.tool:
        det = detector.detect_tool(args.tool)
        print(f"\nDetailed Detection for '{args.tool}':")
        print(f"  * Binary Path       : {det.binary_path or 'Not found'}")
        print(f"  * Detection Source  : {det.discovery_source or 'None'}")
        if det.evaluation:
            print(f"  * Compatibility Note: {det.evaluation.message}")
            if not det.evaluation.is_compatible:
                print(f"  * Usable for eSim   : {'Yes' if det.evaluation.is_usable else 'No'}")

    return 0


def handle_doctor(args: argparse.Namespace) -> int:
    """Run comprehensive 5-stage health diagnostic audit."""
    doctor = SimulationDoctor()
    report = doctor.run_diagnostics()

    if args.flow:
        print(f"\nTargeting specific workflow: '{args.flow}'")
        wf_status = report.workflow_readiness.get(args.flow)
        if not wf_status:
            print(f"Error: Unknown workflow '{args.flow}'. Valid: {ALL_WORKFLOW_IDS}", file=sys.stderr)
            return 1

        print("=" * 75)
        print(f"Workflow: {wf_status.workflow_name} ({wf_status.workflow_id})")
        print(f"Readiness: {'[READY]' if wf_status.is_runnable else '[BLOCKED]'}")
        if wf_status.diagnostic_messages:
            print("Diagnostic Messages:")
            for msg in wf_status.diagnostic_messages:
                print(f"  * {msg}")
        else:
            print("All required dependencies satisfied.")
        print("=" * 75 + "\n")
        return 0 if wf_status.is_runnable else 1

    # Print full CLI formatted report
    print(report.format_cli_report())

    if report.overall_health:
        print("Doctor Result: ALL WORKFLOWS HEALTHY (Exit 0)\n")
        return 0
    else:
        print("Doctor Result: ISSUES DETECTED (Exit 1)\n")
        return 1


def handle_install(args: argparse.Namespace) -> int:
    """Install or stage a tool using verified adapters."""
    installer = ToolInstaller()
    print(f"ToolCare Installer: Preparing installation for '{args.tool}'...")
    if args.dry_run:
        print("[Dry-Run] Simulation mode enabled. No files will be downloaded or modified.")

    res = installer.install_tool(
        tool_id=args.tool,
        method=args.method or "auto",
        version=args.version,
        dry_run=args.dry_run,
    )

    if res.success:
        print(f"\n[SUCCESS] {res.message}")
        if res.installed_path:
            print(f"Location: {res.installed_path}")
        return 0
    else:
        print(f"\n[FAILURE] {res.message}", file=sys.stderr)
        return 1


def handle_update(args: argparse.Namespace) -> int:
    """Dedicated update workflow with confirmation, safety checks, and rollback."""
    updater = UpdateEngine()

    if args.check:
        # Check-only mode: preview update availability without modifying anything
        print("\neSim ToolCare - Checking Upstream Tool Updates (Zero Modifications)...")
        if args.tool:
            plan = updater.plan_update(args.tool)
            plans = {args.tool: plan}
        else:
            plans = updater.plan_all_updates()

        headers = ["Tool ID", "Current Ver", "Target Ver", "Verified", "Update Needed", "Affected Workflows"]
        rows = []
        for tid, p in plans.items():
            rows.append([
                tid,
                p.current_version or "MISSING",
                p.target_version,
                "YES" if p.is_verified else "NO (UNVERIFIED)",
                "YES" if p.is_update_available else "NO",
                ", ".join(p.affected_workflows) if p.affected_workflows else "None",
            ])
        print(_format_table(headers, rows))
        return 0

    if not args.tool:
        print("Error: Please specify a tool ID to update, or use --check to scan all tools.", file=sys.stderr)
        return 1

    # Execute update
    print(f"ToolCare Update Engine: Preparing update for '{args.tool}'...")
    res = updater.execute_update(
        tool_id=args.tool,
        auto_confirm=args.yes,
        dry_run=args.dry_run,
    )

    if res.dry_run:
        print(f"\n{res.message}")
        return 0

    if res.success:
        print(f"\n[SUCCESS] {res.message}")
        return 0
    else:
        print(f"\n[FAILURE] {res.message}", file=sys.stderr)
        return 1


def handle_configure(args: argparse.Namespace) -> int:
    """Manage configuration files, KiCad symbol registration, and backups."""
    handler = ConfigHandler()

    if args.rollback:
        print(f"ToolCare Config: Rolling back configurations to snapshot '{args.rollback}'...")
        if args.dry_run:
            print("[Dry-Run] Would verify backup integrity and restore files. No changes made.")
            return 0
        try:
            handler.rollback(args.rollback)
            print(f"[SUCCESS] Successfully restored configuration from snapshot {args.rollback}.")
            return 0
        except Exception as e:
            print(f"[FAILURE] Rollback failed: {e}", file=sys.stderr)
            return 1

    if args.sync:
        print("ToolCare Config: Synchronizing eSim KiCad libraries and configuration...")
        if args.dry_run:
            print("[Dry-Run] Would inspect sym-lib-table and update library mappings. No changes made.")
            return 0

        # Snapshot first
        snapshot_id, manifest = handler.create_backup(label="pre_configure_sync")
        print(f"Snapshot created: {snapshot_id} ({len(manifest.files)} files recorded)")

        # Sync KiCad sym-lib-table
        kicad_table = handler.get_kicad_sym_lib_table_path()
        if kicad_table:
            count = handler.sync_kicad_sym_lib_table(kicad_table)
            print(f"[SUCCESS] Synchronized {count} eSim symbol libraries in {kicad_table}.")
        else:
            print("[INFO] KiCad sym-lib-table not detected in standard user locations.")

        return 0

    print("No action specified for configure. Use --sync or --rollback <SNAPSHOT_ID>.")
    return 0


def handle_logs(args: argparse.Namespace) -> int:
    """View recent ToolCare audit logs."""
    log_file = get_esim_config_dir() / "logs" / "toolcare.log"
    if not log_file.is_file():
        print(f"No log file found at {log_file}.")
        return 0

    lines_to_read = args.lines or 50
    try:
        content = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = content[-lines_to_read:]
        print(f"\n--- ToolCare Audit Log (Last {len(tail)} lines from {log_file}) ---")
        for line in tail:
            print(line)
        print("--- End of Log ---\n")
        return 0
    except Exception as e:
        print(f"Error reading log file: {e}", file=sys.stderr)
        return 1


# -----------------------------------------------------------------------------
# Parser Construction & Entrypoint
# -----------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="toolcare",
        description="eSim ToolCare: Automated Tool, Dependency & Configuration Manager for eSim.",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the ToolCare graphical management interface (PyQt5/PyQt6)",
    )

    subparsers = parser.add_subparsers(dest="subcommand", title="subcommands", description="valid operations")

    # list
    p_list = subparsers.add_parser("list", help="List registered tools in the eSim catalog")
    p_list.add_argument("-c", "--category", help="Filter tools by category (e.g. EDA, Simulator, Waveform)")

    # check
    p_check = subparsers.add_parser("check", help="Detect installed tools, versions, and compatibility")
    p_check.add_argument("tool", nargs="?", help="Optional specific tool ID to check")
    p_check.add_argument("-v", "--verbose", action="store_true", help="Display verbose path and source information")

    # doctor
    p_doctor = subparsers.add_parser("doctor", help="Run 5-stage health diagnostic audit for simulation flows")
    p_doctor.add_argument(
        "-f", "--flow",
        choices=ALL_WORKFLOW_IDS,
        help="Target a specific simulation workflow (simulation, verifier, d_cosim, ngveri, nghdl)",
    )

    # install
    p_install = subparsers.add_parser("install", help="Install or stage a tool using verified adapters")
    p_install.add_argument("tool", help="Tool ID to install (e.g., ngspice, kicad, ghdl, iverilog)")
    p_install.add_argument("-m", "--method", choices=["archive", "winget", "apt", "auto"], help="Installation adapter")
    p_install.add_argument("--version", help="Specific version to install (defaults to recommended)")
    p_install.add_argument("--dry-run", action="store_true", help="Simulate installation without touching the system")

    # update
    p_update = subparsers.add_parser("update", help="Update tools with verification, confirmation, and rollback")
    p_update.add_argument("tool", nargs="?", help="Tool ID to update (e.g., ngspice, kicad)")
    p_update.add_argument("--check", action="store_true", help="Check for available updates without making changes")
    p_update.add_argument("--dry-run", action="store_true", help="Preview update plan without making changes")
    p_update.add_argument("-y", "--yes", action="store_true", help="Automatically confirm update prompt")

    # configure
    p_configure = subparsers.add_parser("configure", help="Manage KiCad symbol registration and configuration synchronization")
    p_configure.add_argument("--sync", action="store_true", help="Synchronize KiCad eSim symbol libraries")
    p_configure.add_argument("--rollback", help="Restore configuration files from snapshot timestamp")
    p_configure.add_argument("--dry-run", action="store_true", help="Simulate configuration changes without writing")

    # logs
    p_logs = subparsers.add_parser("logs", help="Display recent entries from the ToolCare audit log")
    p_logs.add_argument("-n", "--lines", type=int, default=50, help="Number of lines to display (default: 50)")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.gui:
        from toolcare.gui import launch_gui
        return launch_gui()

    if not args.subcommand:
        parser.print_help()
        return 0

    handlers = {
        "list": handle_list,
        "check": handle_check,
        "doctor": handle_doctor,
        "install": handle_install,
        "update": handle_update,
        "configure": handle_configure,
        "logs": handle_logs,
    }

    handler = handlers.get(args.subcommand)
    if handler:
        return handler(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
