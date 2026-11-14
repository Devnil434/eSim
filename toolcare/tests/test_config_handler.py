"""
Tests for toolcare.core.config_handler.
Validates atomic writes, sibling cleanup, manifest-backed backups, hash verification,
comment-preserving INI editing, KiCad S-expression manipulation, and idempotency.
"""

import hashlib
import os
import shutil
from pathlib import Path
import pytest

from toolcare.core.config_handler import (
    ConfigHandler,
    ConfigSecurityError,
    atomic_write,
    compute_file_sha256,
)


@pytest.fixture
def temp_workspace(tmp_path: Path):
    """Fixture providing isolated temporary directories for configs and backups."""
    cfg_dir = tmp_path / "config"
    backup_dir = tmp_path / "backups"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path, cfg_dir, backup_dir


# -----------------------------------------------------------------------------
# Correction 5: Atomic Writes and Failure Cleanup
# -----------------------------------------------------------------------------
def test_atomic_write_success_and_sibling_cleanup(temp_workspace):
    _, cfg_dir, _ = temp_workspace
    target_file = cfg_dir / "test_target.txt"

    atomic_write(target_file, "Initial content line 1\nLine 2")
    assert target_file.is_file()
    assert target_file.read_text(encoding="utf-8") == "Initial content line 1\nLine 2"

    # Verify no temporary files remain in directory
    temp_files = list(cfg_dir.glob(".*.tmp_*"))
    assert len(temp_files) == 0

    # Overwrite atomically
    atomic_write(target_file, "Updated content")
    assert target_file.read_text(encoding="utf-8") == "Updated content"
    assert len(list(cfg_dir.glob(".*.tmp_*"))) == 0


def test_atomic_write_failure_cleans_up_tmp(temp_workspace, monkeypatch):
    _, cfg_dir, _ = temp_workspace
    target_file = cfg_dir / "fail_target.txt"
    target_file.write_text("Original pristine content", encoding="utf-8")

    # Force os.replace to raise an exception
    def broken_replace(src, dst):
        raise OSError("Simulated filesystem I/O error during replace")

    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(OSError, match="Simulated filesystem I/O error"):
        atomic_write(target_file, "New corrupting content")

    # Ensure original content is untouched
    assert target_file.read_text(encoding="utf-8") == "Original pristine content"

    # Ensure temporary sibling files were cleaned up in finally block
    temp_files = list(cfg_dir.glob(".*.tmp_*"))
    assert len(temp_files) == 0


# -----------------------------------------------------------------------------
# Corrections 3 & 4: Backup Manifests and Verified Rollback
# -----------------------------------------------------------------------------
def test_create_backup_manifest_integrity(temp_workspace):
    _, cfg_dir, backup_dir = temp_workspace
    file_a = cfg_dir / "app_config.ini"
    file_b = cfg_dir / "user_rules.txt"

    file_a.write_text("[section]\nkey = value\n", encoding="utf-8")
    file_b.write_text("Rule 1: safe defaults\n", encoding="utf-8")

    handler = ConfigHandler(backup_root=backup_dir)
    snapshot_id, manifest = handler.create_backup([file_a, file_b], label="unit_test")

    assert snapshot_id is not None
    assert manifest.label == "unit_test"
    assert len(manifest.files) == 2

    # Verify manifest file on disk
    manifest_disk_file = backup_dir / snapshot_id / "manifest.json"
    assert manifest_disk_file.is_file()

    # Check that SHA-256 digests in manifest match actual files
    for entry in manifest.files:
        assert entry.sha256 == compute_file_sha256(Path(entry.original_path))
        assert entry.size_bytes > 0
        backup_copy = backup_dir / snapshot_id / entry.backup_file
        assert backup_copy.is_file()
        assert compute_file_sha256(backup_copy) == entry.sha256


def test_rollback_verified_restoration(temp_workspace):
    _, cfg_dir, backup_dir = temp_workspace
    file_a = cfg_dir / "settings.ini"
    original_text = "; original comment\n[engine]\ntimeout = 30\n"
    file_a.write_text(original_text, encoding="utf-8")

    handler = ConfigHandler(backup_root=backup_dir)
    snapshot_id, _ = handler.create_backup([file_a], label="pre_change")

    # Corrupt or modify the active file
    file_a.write_text("[engine]\ntimeout = 9999\nbroken = true\n", encoding="utf-8")
    assert file_a.read_text(encoding="utf-8") != original_text

    # Perform rollback
    restored = handler.rollback(snapshot_id)
    assert len(restored) == 1
    assert restored[0] == file_a
    assert file_a.read_text(encoding="utf-8") == original_text


def test_rollback_tamper_detection_rejection(temp_workspace):
    _, cfg_dir, backup_dir = temp_workspace
    file_a = cfg_dir / "secure_table.txt"
    file_a.write_text("Original authentic settings", encoding="utf-8")

    handler = ConfigHandler(backup_root=backup_dir)
    snapshot_id, manifest = handler.create_backup([file_a])

    # Tamper with the backup copy stored in backup_root
    backup_copy = backup_dir / snapshot_id / manifest.files[0].backup_file
    backup_copy.write_text("TAMPERED_MALICIOUS_PAYLOAD", encoding="utf-8")

    # Modify the active file
    file_a.write_text("Changed before rollback", encoding="utf-8")

    # Rollback must reject due to SHA-256 mismatch (Correction 4)
    with pytest.raises(ConfigSecurityError, match="Integrity verification failed for backup"):
        handler.rollback(snapshot_id)

    # Active file must NOT have been replaced by the tampered backup
    assert file_a.read_text(encoding="utf-8") == "Changed before rollback"


# -----------------------------------------------------------------------------
# Correction 1: Comment and Formatting Preservation
# -----------------------------------------------------------------------------
def test_update_ini_preserves_comments_and_formatting(temp_workspace):
    _, cfg_dir, _ = temp_workspace
    ini_path = cfg_dir / "config.ini"

    raw_ini = (
        "# Top-level header comment\n"
        "# Another comment line\n"
        "\n"
        "[General]\n"
        "; User custom setting\n"
        "theme = dark\n"
        "font_size = 12\n"
        "\n"
        "[Simulation]\n"
        "# Default simulator path\n"
        "ngspice_path = C:/old/ngspice.exe\n"
        "threads = 4\n"
    )
    ini_path.write_text(raw_ini, encoding="utf-8")

    handler = ConfigHandler()
    handler.update_ini_section_in_place(
        ini_path,
        section="Simulation",
        updates={"ngspice_path": "C:/new/ngspice-45.2/bin/ngspice.exe"},
    )

    result_content = ini_path.read_text(encoding="utf-8")

    # Verify comments are completely preserved
    assert "# Top-level header comment" in result_content
    assert "; User custom setting" in result_content
    assert "theme = dark" in result_content
    assert "font_size = 12" in result_content
    assert "# Default simulator path" in result_content
    assert "threads = 4" in result_content
    # Verify the specific key was updated
    assert "ngspice_path = C:/new/ngspice-45.2/bin/ngspice.exe" in result_content
    assert "C:/old/ngspice.exe" not in result_content


# -----------------------------------------------------------------------------
# Correction 2 & 9: KiCad S-Expression Editing & Duplicate Prevention
# -----------------------------------------------------------------------------
def test_kicad_sym_lib_table_targeted_sexpr_update(temp_workspace):
    _, cfg_dir, _ = temp_workspace
    table_path = cfg_dir / "sym-lib-table"

    initial_table = (
        "(sym_lib_table\n"
        "  (version 7)\n"
        '  (lib (name "Device")(type "KiCad")(uri "${KICAD_SYMBOL_DIR}/Device.kicad_sym")(options "")(descr "Devices"))\n'
        '  (lib (name "eSim_Analog")(type "KiCad")(uri "C:/old_esim/library/eSim-symbols/eSim_Analog.kicad_sym")(options "")(descr "Old"))\n'
        ")\n"
    )
    table_path.write_text(initial_table, encoding="utf-8")

    # Create dummy eSim library files
    esim_lib_dir = cfg_dir / "library" / "kicadLibrary" / "eSim-symbols"
    esim_lib_dir.mkdir(parents=True, exist_ok=True)
    (esim_lib_dir / "eSim_Analog.kicad_sym").touch()
    (esim_lib_dir / "eSim_Digital.kicad_sym").touch()

    handler = ConfigHandler(esim_root=cfg_dir)
    updated_count = handler.sync_kicad_sym_lib_table(table_path)

    new_content = table_path.read_text(encoding="utf-8")

    # Standard Device lib must remain untouched
    assert '(name "Device")' in new_content
    # Existing eSim_Analog URI must be updated to new location
    expected_uri = str(esim_lib_dir / "eSim_Analog.kicad_sym").replace("\\", "/")
    assert f'(name "eSim_Analog")(type "KiCad")(uri "{expected_uri}")' in new_content
    # Missing eSim_Digital must be appended
    expected_dig_uri = str(esim_lib_dir / "eSim_Digital.kicad_sym").replace("\\", "/")
    assert f'(name "eSim_Digital")(type "KiCad")(uri "{expected_dig_uri}")' in new_content
    # Must remain a valid S-expression ending in )
    assert new_content.strip().endswith(")")


def test_kicad_sym_lib_table_idempotent_no_duplicates(temp_workspace):
    _, cfg_dir, _ = temp_workspace
    table_path = cfg_dir / "sym-lib-table"
    table_path.write_text("(sym_lib_table\n  (version 7)\n)\n", encoding="utf-8")

    esim_lib_dir = cfg_dir / "library" / "kicadLibrary" / "eSim-symbols"
    esim_lib_dir.mkdir(parents=True, exist_ok=True)
    (esim_lib_dir / "eSim_Subckt.kicad_sym").touch()

    handler = ConfigHandler(esim_root=cfg_dir)

    # First sync
    handler.sync_kicad_sym_lib_table(table_path)
    content_pass_1 = table_path.read_text(encoding="utf-8")
    assert content_pass_1.count('(name "eSim_Subckt")') == 1

    # Second sync (must be idempotent, no duplicates)
    handler.sync_kicad_sym_lib_table(table_path)
    content_pass_2 = table_path.read_text(encoding="utf-8")
    assert content_pass_2.count('(name "eSim_Subckt")') == 1


def test_ngspice_spinit_codemodel_sync(temp_workspace):
    _, cfg_dir, _ = temp_workspace
    spinit_path = cfg_dir / "spinit"
    cm_dir = cfg_dir / "lib" / "ngspice"
    cm_dir.mkdir(parents=True, exist_ok=True)

    initial_spinit = (
        "* Ngspice initialization file\n"
        'codemodel "C:/old/path/spice2poly.cm"\n'
        "set nomoremode\n"
    )
    spinit_path.write_text(initial_spinit, encoding="utf-8")

    handler = ConfigHandler()
    handler.sync_ngspice_spinit(spinit_path, cm_dir)

    updated_spinit = spinit_path.read_text(encoding="utf-8")
    cm_posix = str(cm_dir).replace("\\", "/")

    # spice2poly.cm path should be updated in place
    assert f'codemodel "{cm_posix}/spice2poly.cm"' in updated_spinit
    # missing models should be appended
    assert f'codemodel "{cm_posix}/analog.cm"' in updated_spinit
    assert f'codemodel "{cm_posix}/digital.cm"' in updated_spinit
    # existing command lines preserved
    assert "set nomoremode" in updated_spinit
    # no duplicates of spice2poly
    assert updated_spinit.count("spice2poly.cm") == 1
