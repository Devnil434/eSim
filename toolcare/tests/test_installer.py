"""
Tests for toolcare.adapters and toolcare.core.installer security and verification gates.
"""

import hashlib
import io
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from toolcare.adapters.apt_adapter import AptAdapter
from toolcare.adapters.archive_adapter import (
    ArchiveAdapter,
    SecurityError,
    is_safely_contained,
)
from toolcare.adapters.winget_adapter import WingetAdapter
from toolcare.core.detector import ToolDetectionResult, ToolDetector
from toolcare.core.installer import InstallResult, ToolInstaller
from toolcare.core.registry import ArchiveSpec, CompatibilitySpec, ToolRegistry, ToolSpec
from toolcare.core.version import ToolStatus, VersionEvaluation


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.fixture
def archive_adapter():
    return ArchiveAdapter()


@pytest.fixture
def winget_adapter():
    return WingetAdapter()


@pytest.fixture
def apt_adapter():
    return AptAdapter()


# =============================================================================
# Security Test 1: Canonical Containment & Prefix Collision Prevention
# =============================================================================
def test_path_prefix_collision_prevention(tmp_path):
    """
    Correction 1: Replace string-prefix checks with canonical resolved containment.
    Verifies that a directory named /target_prefix_collision does NOT pass containment
    against /target.
    """
    target_dir = tmp_path / "target"
    target_dir.mkdir()

    # Prefix collision sibling directory
    evil_sibling = tmp_path / "target_evil"
    evil_sibling.mkdir()
    evil_file = evil_sibling / "payload.bat"

    # String startswith would falsely pass if checking str(target_dir):
    # e.g. "C:\...\target_evil\payload.bat".startswith("C:\...\target") is True!
    # But canonical is_safely_contained must be False!
    assert is_safely_contained(target_dir, evil_file) is False

    # A legitimate member inside target must pass
    legit_file = target_dir / "bin" / "tool.exe"
    assert is_safely_contained(target_dir, legit_file) is True


# =============================================================================
# Security Test 2: SHA-256 Checksum Validation & Mismatch Rejection
# =============================================================================
def test_archive_download_sha256_match(archive_adapter, tmp_path):
    """Assert download_staged succeeds when computed SHA-256 matches expected digest."""
    payload = b"Valid EDA binary archive contents"
    expected_hash = hashlib.sha256(payload).hexdigest()

    mock_resp = io.BytesIO(payload)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        staged_file = archive_adapter.download_staged(
            url="https://example.com/tool.zip",
            expected_sha256=expected_hash,
            staging_dir=tmp_path,
        )
        assert staged_file.is_file()
        assert staged_file.read_bytes() == payload


def test_archive_download_sha256_mismatch_raises_security_error(archive_adapter, tmp_path):
    """
    Assert download_staged immediately deletes staging file and raises SecurityError
    when payload is corrupted or hash does not match.
    """
    payload = b"Tampered or corrupted binary payload"
    expected_hash = "0" * 64  # deliberate wrong hash

    mock_resp = io.BytesIO(payload)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(SecurityError) as exc_info:
            archive_adapter.download_staged(
                url="https://example.com/tool.zip",
                expected_sha256=expected_hash,
                staging_dir=tmp_path,
            )
        assert "SHA-256 mismatch" in str(exc_info.value)

    # Verify staging file was deleted
    assert not (tmp_path / "download.tmp").exists()


def test_archive_download_unverified_hash_rejected(archive_adapter, tmp_path):
    """Correction 7: Reject downloads with UNVERIFIED or malformed hashes."""
    with pytest.raises(SecurityError) as exc:
        archive_adapter.download_staged(
            url="https://example.com/tool.zip",
            expected_sha256="UNVERIFIED",
            staging_dir=tmp_path,
        )
    assert "unverified or invalid" in str(exc.value)


# =============================================================================
# Security Test 3: Anti-Zip-Slip & Traversal Rejection
# =============================================================================
def test_safe_extract_zip_traversal_rejected(archive_adapter, tmp_path):
    """
    Correction 2: Zip-Slip path traversal (../../evil.bat) must raise SecurityError.
    """
    zip_path = tmp_path / "traversal.zip"
    target_extract = tmp_path / "extract_dest"
    target_extract.mkdir()

    # Construct malicious zip with traversal path
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../../evil.bat", b"malicious code")

    with pytest.raises(SecurityError) as exc_info:
        archive_adapter.safe_extract_zip(zip_path, target_extract)

    assert "traversal path" in str(exc_info.value).lower() or "containment violation" in str(exc_info.value).lower()
    # Confirm traversal target was never written
    assert not (tmp_path / "evil.bat").exists()


def test_safe_extract_zip_absolute_path_rejected(archive_adapter, tmp_path):
    """
    Correction 2: Reject absolute paths in archive members.
    """
    zip_path = tmp_path / "absolute.zip"
    target_extract = tmp_path / "extract_dest"
    target_extract.mkdir()

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("/etc/evil_config", b"hack")

    with pytest.raises(SecurityError) as exc_info:
        archive_adapter.safe_extract_zip(zip_path, target_extract)

    assert "absolute path" in str(exc_info.value).lower()


def test_safe_extract_zip_symlink_rejected(archive_adapter, tmp_path):
    """
    Correction 2: Reject symlinks in archive members.
    """
    zip_path = tmp_path / "symlink.zip"
    target_extract = tmp_path / "extract_dest"
    target_extract.mkdir()

    with zipfile.ZipFile(zip_path, "w") as zf:
        info = zipfile.ZipInfo("symlink_entry")
        # 0o120000 is S_IFLNK in POSIX file modes
        info.external_attr = 0o120777 << 16
        zf.writestr(info, b"/target/path")

    with pytest.raises(SecurityError) as exc_info:
        archive_adapter.safe_extract_zip(zip_path, target_extract)

    assert "symlink" in str(exc_info.value).lower()


def test_safe_extract_tar_link_rejected(archive_adapter, tmp_path):
    """
    Correction 2: Reject symlinks and hardlinks in TAR archives.
    """
    tar_path = tmp_path / "symlink.tar"
    target_extract = tmp_path / "extract_dest"
    target_extract.mkdir()

    with tarfile.open(tar_path, "w") as tf:
        ti = tarfile.TarInfo(name="evil_link")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/passwd"
        tf.addfile(ti)

    with pytest.raises(SecurityError) as exc_info:
        archive_adapter.safe_extract_tar(tar_path, target_extract)

    assert "link" in str(exc_info.value).lower()


# =============================================================================
# Security Test 4: Unsupported Format Rejection (No Unsafe Shell Fallback)
# =============================================================================
def test_unsupported_archive_format_rejected(archive_adapter, tmp_path):
    """
    Correction 3: Reject unsupported formats safely without running arbitrary shell binaries.
    """
    fake_rar = tmp_path / "tool.rar"
    fake_rar.write_bytes(b"Rar! fake")
    extract_dir = tmp_path / "extract"

    with pytest.raises(ValueError) as exc:
        archive_adapter.extract_archive(fake_rar, extract_dir, archive_type="rar")

    assert "unsupported or unsafe" in str(exc.value).lower()


# =============================================================================
# Security Test 5: Dry-Run Zero Side Effects
# =============================================================================
def test_installer_dry_run_zero_writes(registry, tmp_path):
    """
    Correction 6: Dry-run performs zero downloads, filesystem writes, or process execution.
    """
    installer = ToolInstaller(registry=registry)
    target = tmp_path / "installed_tool"

    with patch("urllib.request.urlopen") as mock_url, \
         patch("toolcare.platform_utils.run_safe_command") as mock_cmd:

        res = installer.install_tool("ngspice", dry_run=True, target_dir=target)

        assert res.success is True
        assert res.dry_run is True
        assert "[Dry-Run]" in res.message
        assert mock_url.call_count == 0
        assert mock_cmd.call_count == 0
        assert not target.exists()


# =============================================================================
# Security Test 6: Preservation & Rollback of Existing Installation
# =============================================================================
def test_archive_install_preserves_existing_on_failure(archive_adapter, tmp_path):
    """
    Correction 5: Preserve existing installations if promotion/extraction fails.
    """
    final_dest = tmp_path / "ngspice_install"
    final_dest.mkdir()
    marker = final_dest / "original_file.txt"
    marker.write_text("pre-existing installation")

    # Mock tool spec with valid archive
    mock_spec = MagicMock(spec=ToolSpec)
    mock_spec.id = "ngspice"
    mock_spec.get_archive.return_value = ArchiveSpec(
        url="https://example.com/fake.zip",
        sha256="a" * 64,
        archive_type="zip",
        strip_components=0,
    )

    # Force download to fail
    with patch.object(archive_adapter, "download_staged", side_effect=SecurityError("Payload tampered")):
        success, msg = archive_adapter.install(mock_spec, dry_run=False, target_dir=final_dest)

        assert success is False
        assert "Payload tampered" in msg
        # Assert pre-existing file was NOT deleted or corrupted!
        assert final_dest.is_dir()
        assert marker.is_file()
        assert marker.read_text() == "pre-existing installation"


# =============================================================================
# Security Test 7: Post-Installation Verification Gate
# =============================================================================
def test_installer_post_verification_success(registry, tmp_path):
    """Assert successful install when post-verification detector finds compatible binary."""
    mock_detector = MagicMock(spec=ToolDetector)
    # After install, detector reports compatible version
    mock_eval = VersionEvaluation("ngspice", "45.2", ToolStatus.COMPATIBLE, "OK", "38.0", "45.2", "46.0", True)
    mock_detection = ToolDetectionResult("ngspice", "Ngspice", True, tmp_path / "bin" / "ngspice.exe", "45.2", mock_eval, "test")
    mock_detector.detect_tool.return_value = mock_detection

    installer = ToolInstaller(registry=registry, detector=mock_detector)

    # Mock adapter install to succeed
    mock_adapter = MagicMock()
    mock_adapter.name = "mock_adapter"
    mock_adapter.install.return_value = (True, "Installed OK")
    installer.adapters["archive"] = mock_adapter

    res = installer.install_tool("ngspice", method="archive", dry_run=False)

    assert res.success is True
    assert res.is_compatible is True
    assert res.post_install_version == "45.2"
    assert "verified" in res.message.lower()


def test_installer_post_verification_fails_if_binary_missing(registry):
    """Assert install fails if post-verification cannot locate executable."""
    mock_detector = MagicMock(spec=ToolDetector)
    mock_eval = VersionEvaluation("ngspice", None, ToolStatus.NOT_INSTALLED, "Not installed", "38.0", "45.2", None, False)
    mock_detection = ToolDetectionResult("ngspice", "Ngspice", False, None, None, mock_eval, "not_found")
    mock_detector.detect_tool.return_value = mock_detection

    installer = ToolInstaller(registry=registry, detector=mock_detector)

    mock_adapter = MagicMock()
    mock_adapter.name = "archive"
    mock_adapter.install.return_value = (True, "Unpacked OK")
    installer.adapters["archive"] = mock_adapter

    res = installer.install_tool("ngspice", method="archive", dry_run=False)

    assert res.success is False
    assert "could not be detected" in res.message


# =============================================================================
# Package Manager Adapters (Winget & APT)
# =============================================================================
def test_winget_adapter_verified_vs_unverified(winget_adapter, registry):
    """Correction 7: Reject tools with unverified Winget package IDs."""
    ngspice = registry.get_tool("ngspice")  # verified
    iverilog = registry.get_tool("iverilog")  # unverified

    # Ngspice dry run succeeds
    success, msg = winget_adapter.install(ngspice, dry_run=True)
    assert success is True
    assert "Ngspice.Ngspice" in msg

    # Iverilog installation is rejected
    success, msg = winget_adapter.install(iverilog, dry_run=True)
    assert success is False
    assert "verified Winget package identifier" in msg


def test_apt_adapter_verified_and_dry_run(apt_adapter, registry):
    """Assert APT adapter creates argument list and supports dry-run."""
    ghdl = registry.get_tool("ghdl")  # apt package is ghdl-llvm

    success, msg = apt_adapter.install(ghdl, dry_run=True)
    assert success is True
    assert "apt-get install -y ghdl-llvm" in msg


def test_installer_adapter_selection_policy(registry):
    """
    Correction 10: Prefer verified archives over generic package-manager binaries
    when eSim-specific components are required (e.g. for Ngspice).
    """
    installer = ToolInstaller(registry=registry)
    ngspice = registry.get_tool("ngspice")
    adapter = installer.select_adapter(ngspice)
    # Ngspice has critical components, so it must select ArchiveAdapter
    assert isinstance(adapter, ArchiveAdapter)
