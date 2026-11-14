"""
toolcare.core.config_handler - Safe configuration management with atomic writes,
comment preservation, backup manifests, integrity verification, and rollback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from toolcare.logger import get_logger
from toolcare.platform_utils import (
    IS_LINUX,
    IS_WINDOWS,
    get_esim_config_dir,
    get_home_dir,
    get_nghdl_config_dir,
)

logger = get_logger("config_handler")


class ConfigSecurityError(Exception):
    """Raised when configuration integrity or backup validation fails."""
    pass


# -----------------------------------------------------------------------------
# Correction 5: Atomic Writes with Unique Sibling & Permission Preservation
# -----------------------------------------------------------------------------
def atomic_write(filepath: Path | str, content: str, encoding: str = "utf-8") -> None:
    """
    Write content to a unique temporary sibling file, flush and sync descriptors to disk,
    preserve permissions, and replace target atomically.
    Cleans up temporary files if an error occurs.
    """
    target = Path(filepath).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    # Unique temporary sibling file
    unique_id = uuid.uuid4().hex
    tmp_path = target.parent / f".{target.name}.tmp_{unique_id}"

    try:
        with open(tmp_path, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        # Preserve file permissions if target exists
        if target.exists():
            try:
                shutil.copymode(str(target), str(tmp_path))
            except Exception:
                pass

        # Atomic replacement
        os.replace(str(tmp_path), str(target))
    except Exception as exc:
        logger.error("Atomic write failed for %s: %s", target, exc)
        raise exc
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def compute_file_sha256(filepath: Path) -> str:
    """Compute SHA-256 digest of a local file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(64 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


# -----------------------------------------------------------------------------
# Correction 3: Backup Manifest & Metadata Tracking
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class BackupFileEntry:
    original_path: str
    backup_file: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class BackupManifest:
    timestamp: str
    created_at_utc: str
    files: List[BackupFileEntry]


class ConfigHandler:
    """
    Configuration manager for eSim ToolCare.
    Safely edits INI and S-expression configurations while preserving comments,
    manages manifest-verified backups, and provides verified rollbacks.
    """

    def __init__(self, esim_root: Optional[Path] = None):
        self.esim_root = esim_root or self._find_esim_root()
        self.backups_dir = get_esim_config_dir() / "backups"

    @staticmethod
    def _find_esim_root() -> Optional[Path]:
        cand = Path(__file__).resolve().parent.parent.parent
        if (cand / "src").is_dir() and (cand / "library").is_dir():
            return cand
        return None

    # -------------------------------------------------------------------------
    # Backups and Verified Rollback (Corrections 3 & 4)
    # -------------------------------------------------------------------------
    def create_backup(self, target_files: List[Path], label: str = "") -> Tuple[str, Path]:
        """
        Create a manifest-backed snapshot of target configuration files.
        Stores original paths, timestamps, file sizes, and SHA-256 digests.
        """
        now = datetime.now(timezone.utc)
        ts_id = now.strftime("%Y%m%dT%H%M%SZ")
        if label:
            ts_id = f"{ts_id}_{label}"

        snapshot_dir = self.backups_dir / ts_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        manifest_entries: List[BackupFileEntry] = []

        for target in target_files:
            target_path = Path(target).resolve()
            if not target_path.is_file():
                continue

            sha = compute_file_sha256(target_path)
            size = target_path.stat().st_size
            b_name = f"{target_path.name}_{uuid.uuid4().hex[:8]}.bak"
            dest_backup = snapshot_dir / b_name

            shutil.copy2(str(target_path), str(dest_backup))

            manifest_entries.append(
                BackupFileEntry(
                    original_path=str(target_path),
                    backup_file=b_name,
                    sha256=sha,
                    size_bytes=size,
                )
            )

        manifest = BackupManifest(
            timestamp=ts_id,
            created_at_utc=now.isoformat(),
            files=manifest_entries,
        )

        manifest_file = snapshot_dir / "manifest.json"
        atomic_write(manifest_file, json.dumps(asdict(manifest), indent=2))
        logger.info("Created backup manifest %s with %d files", ts_id, len(manifest_entries))

        return ts_id, snapshot_dir

    def rollback(self, timestamp: str) -> Tuple[bool, str]:
        """
        Roll back configuration files using a timestamped backup manifest.
        Correction 4: Verifies backup file integrity (SHA-256) before restoring.
        """
        snapshot_dir = self.backups_dir / timestamp
        if not snapshot_dir.is_dir():
            return False, f"Backup snapshot '{timestamp}' not found in {self.backups_dir}."

        manifest_path = snapshot_dir / "manifest.json"
        if not manifest_path.is_file():
            return False, f"Backup manifest corrupted or missing in {snapshot_dir}."

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            files = [BackupFileEntry(**item) for item in data.get("files", [])]
        except Exception as exc:
            return False, f"Failed to parse backup manifest: {exc}"

        # Step 1: Pre-verification of all backup files (Correction 4)
        for entry in files:
            b_path = snapshot_dir / entry.backup_file
            if not b_path.is_file():
                raise ConfigSecurityError(
                    f"Rollback aborted: Missing backup file '{entry.backup_file}' in snapshot {timestamp}."
                )
            current_sha = compute_file_sha256(b_path)
            if current_sha.lower() != entry.sha256.lower():
                raise ConfigSecurityError(
                    f"Rollback aborted: Backup file '{entry.backup_file}' failed integrity check! "
                    f"Expected SHA-256: {entry.sha256}, Computed: {current_sha}."
                )

        # Step 2: Restore verified files
        restored = []
        for entry in files:
            b_path = snapshot_dir / entry.backup_file
            orig_target = Path(entry.original_path)
            orig_target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(orig_target, b_path.read_text(encoding="utf-8", errors="replace"))
            restored.append(str(orig_target))

        logger.info("Successfully rolled back %d files from snapshot %s", len(restored), timestamp)
        return True, f"Successfully restored {len(restored)} configuration files from {timestamp}."

    # -------------------------------------------------------------------------
    # Targeted In-Place INI Updating (Correction 1: Preserve Comments & Format)
    # -------------------------------------------------------------------------
    @staticmethod
    def update_ini_section_in_place(
        ini_path: Path,
        section: str,
        updates: Dict[str, str],
    ) -> None:
        """
        Update key/value pairs in an INI file while preserving comments and structure.
        Correction 1: Does NOT use configparser.write() to avoid stripping comments.
        """
        lines: List[str] = []
        if ini_path.is_file():
            lines = ini_path.read_text(encoding="utf-8", errors="replace").splitlines()

        current_sec: Optional[str] = None
        sec_header_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")
        kv_re = re.compile(r"^(\s*)([a-zA-Z0-9_\-\.]+)(\s*=\s*)(.*)$")

        remaining_updates = dict(updates)
        new_lines: List[str] = []
        in_target_section = False
        section_found = False

        for line in lines:
            sec_match = sec_header_re.match(line)
            if sec_match:
                # Leaving target section; flush any new keys that weren't present
                if in_target_section:
                    for k, v in list(remaining_updates.items()):
                        new_lines.append(f"{k} = {v}")
                        del remaining_updates[k]
                    in_target_section = False

                current_sec = sec_match.group(1).strip()
                if current_sec.lower() == section.lower():
                    in_target_section = True
                    section_found = True
                new_lines.append(line)
                continue

            if in_target_section:
                kv_match = kv_re.match(line)
                if kv_match:
                    indent, key, equals, val = kv_match.groups()
                    key_lower = key.strip().lower()
                    # Check if this key should be updated
                    matching_key = next((k for k in remaining_updates if k.lower() == key_lower), None)
                    if matching_key:
                        new_lines.append(f"{indent}{key}{equals}{remaining_updates[matching_key]}")
                        del remaining_updates[matching_key]
                        continue

            new_lines.append(line)

        # If file ended while still in target section, flush remaining keys
        if in_target_section:
            for k, v in list(remaining_updates.items()):
                new_lines.append(f"{k} = {v}")
                del remaining_updates[k]

        # If section didn't exist at all, create it
        if not section_found:
            if new_lines and new_lines[-1].strip() != "":
                new_lines.append("")
            new_lines.append(f"[{section}]")
            for k, v in remaining_updates.items():
                new_lines.append(f"{k} = {v}")

        atomic_write(ini_path, "\n".join(new_lines) + "\n")

    # -------------------------------------------------------------------------
    # KiCad sym-lib-table S-Expression Synchronization (Corrections 2 & 9)
    # -------------------------------------------------------------------------
    def get_actual_esim_symbol_libraries(self) -> Dict[str, Tuple[Path, str]]:
        """
        Correction 9: Validate proposed KiCad library entries against actual eSim source.
        Returns mapping: libname -> (path, description).
        """
        libs: Dict[str, Tuple[Path, str]] = {}
        home = get_home_dir()

        # 1. Generated libraries in ~/.esim/kicad_symbols
        gen_dir = home / ".esim" / "kicad_symbols"
        for lib_name in ["eSim_Ngveri", "eSim_NgVeriCosim", "eSim_Nghdl"]:
            p = gen_dir / f"{lib_name}.kicad_sym"
            libs[lib_name] = (p, f"eSim generated co-simulation models ({lib_name})")

        # 2. Static libraries from eSim source checkout if present
        if self.esim_root:
            static_dir = self.esim_root / "library" / "kicadLibrary" / "eSim-symbols"
            if static_dir.is_dir():
                for f in sorted(static_dir.glob("eSim_*.kicad_sym")):
                    lib_name = f.stem
                    libs[lib_name] = (f.resolve(), f"eSim static symbol library ({lib_name})")

        return libs

    @staticmethod
    def sync_kicad_sym_lib_table(table_path: Path, libraries: Dict[str, Tuple[Path, str]]) -> int:
        """
        Correction 2: Safe targeted S-expression editing for KiCad sym-lib-table.
        Avoids broad regex surgery. Inserts or updates libraries in place and avoids duplicates.
        """
        if not table_path.parent.is_dir():
            table_path.parent.mkdir(parents=True, exist_ok=True)

        content = ""
        if table_path.is_file():
            content = table_path.read_text(encoding="utf-8", errors="replace").strip()

        if not content:
            content = "(sym_lib_table\n)"

        # Locate closing parenthesis of top-level sexpr
        if not content.startswith("(sym_lib_table") or not content.endswith(")"):
            content = "(sym_lib_table\n)"

        updated_count = 0

        for lib_name, (lib_path, descr) in libraries.items():
            path_str = str(lib_path).replace("\\", "/")
            escaped_name = re.escape(lib_name)

            # Match balanced single (lib (name "...") ... )
            pattern = re.compile(
                rf'\(\s*lib\s+\(\s*name\s+"{escaped_name}"\s*\)'
                rf'\s+\(\s*type\s+"[^"]*"\s*\)'
                rf'\s+\(\s*uri\s+"[^"]*"\s*\)'
                rf'\s+\(\s*options\s+"[^"]*"\s*\)'
                rf'\s+\(\s*descr\s+"[^"]*"\s*\)\s*\)',
                re.DOTALL,
            )

            new_lib_sexpr = (
                f'  (lib (name "{lib_name}")(type "KiCad")'
                f'(uri "{path_str}")(options "")(descr "{descr}"))'
            )

            if pattern.search(content):
                # Update in place if URI has relocated
                content, count = pattern.subn(new_lib_sexpr, content)
                if count > 0:
                    updated_count += count
            else:
                # Check simple name match
                simple_match = re.search(rf'\(\s*lib\s+\(\s*name\s+"{escaped_name}"\s*\)[^\)]*\)', content)
                if simple_match:
                    content = content[: simple_match.start()] + new_lib_sexpr + content[simple_match.end() :]
                    updated_count += 1
                else:
                    # Append right before closing paren
                    insert_pos = content.rfind(")")
                    content = content[:insert_pos] + f"{new_lib_sexpr}\n)"
                    updated_count += 1

        atomic_write(table_path, content)
        return updated_count

    # -------------------------------------------------------------------------
    # Ngspice spinit / .spiceinit Synchronization
    # -------------------------------------------------------------------------
    @staticmethod
    def sync_ngspice_spinit(spinit_path: Path, codemodel_dir: Path) -> bool:
        """
        Synchronize Ngspice spinit with codemodel directives pointing to detected code models.
        Correction 1: Line-based update preventing duplicate directives.
        """
        cm_posix = str(codemodel_dir).replace("\\", "/")
        required_models = ["spice2poly.cm", "analog.cm", "digital.cm"]

        lines: List[str] = []
        if spinit_path.is_file():
            lines = spinit_path.read_text(encoding="utf-8", errors="replace").splitlines()

        new_lines: List[str] = []
        existing_models_handled = set()

        for line in lines:
            line_str = line.strip()
            # If line is codemodel directive, update path in place
            if line_str.lower().startswith("codemodel"):
                for m in required_models:
                    if m.lower() in line_str.lower():
                        new_lines.append(f'codemodel "{cm_posix}/{m}"')
                        existing_models_handled.add(m)
                        break
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # Append missing codemodel lines
        for m in required_models:
            if m not in existing_models_handled:
                new_lines.append(f'codemodel "{cm_posix}/{m}"')

        atomic_write(spinit_path, "\n".join(new_lines) + "\n")
        return True

