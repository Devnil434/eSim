"""
toolcare.adapters.archive_adapter - Direct verified archive deployment with anti-tamper safeguards.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

from toolcare.adapters.base import BaseAdapter
from toolcare.core.registry import ArchiveSpec, ToolSpec
from toolcare.logger import get_logger
from toolcare.platform_utils import get_esim_config_dir

logger = get_logger("adapters.archive")


class SecurityError(Exception):
    """Raised when an archive fails cryptographic or path containment security checks."""
    pass


def is_safely_contained(base_dir: Path, target_path: Path) -> bool:
    """
    Correction 1: Strict canonical resolved-path containment.
    Replaces string-prefix checks to prevent prefix-collision vulnerabilities
    (e.g. '/target_evil' falsely passing prefix check on '/target').
    """
    base_resolved = base_dir.resolve()
    try:
        target_resolved = target_path.resolve()
        return target_resolved.is_relative_to(base_resolved)
    except (ValueError, RuntimeError, AttributeError):
        # Fallback for Python versions where is_relative_to might raise
        try:
            target_resolved = target_path.resolve()
            common = os.path.commonpath([str(base_resolved), str(target_resolved)])
            return common == str(base_resolved)
        except Exception:
            return False


class ArchiveAdapter(BaseAdapter):
    """
    Adapter for downloading and safely extracting verified vendor archives.
    Enforces streaming SHA-256 validation, Anti-Zip-Slip containment, symlink rejection,
    and staging directory isolation.
    """

    name: str = "archive"

    def is_available(self) -> bool:
        """Always available as it utilizes Python standard library."""
        return True

    @staticmethod
    def get_default_install_dir(tool_id: str) -> Path:
        """
        Correction 9: Default target directory is ~/.esim/tools/<tool_id>/
        Ensures non-administrative user portability across Windows and Linux.
        """
        return get_esim_config_dir() / "tools" / tool_id.lower()

    @staticmethod
    def validate_sha256_format(digest: str) -> bool:
        """
        Correction 7: Verify SHA-256 is exactly 64 hexadecimal characters.
        """
        if not digest or digest.upper() == "UNVERIFIED" or len(digest) != 64:
            return False
        return all(c in "0123456789abcdefABCDEF" for c in digest)

    def download_staged(
        self,
        url: str,
        expected_sha256: str,
        staging_dir: Path,
    ) -> Path:
        """
        Stream archive download to temporary file and verify SHA-256 in real time.
        Correction 4: Downloads into temporary staging directory.
        Correction 7: Rejects invalid or unverified hashes.
        """
        if not self.validate_sha256_format(expected_sha256):
            raise SecurityError(
                f"Cannot download archive from '{url}': SHA-256 hash is unverified or invalid ('{expected_sha256}')."
            )

        temp_archive = staging_dir / "download.tmp"
        hasher = hashlib.sha256()

        logger.info("Starting staged download from %s", url)
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "eSim-ToolCare/2.0.0"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(temp_archive, "wb") as f_out:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        hasher.update(chunk)
                        f_out.write(chunk)
        except Exception as exc:
            if temp_archive.is_file():
                temp_archive.unlink(missing_ok=True)
            raise IOError(f"Network download failure from '{url}': {exc}") from exc

        computed_sha256 = hasher.hexdigest().lower()
        if computed_sha256 != expected_sha256.lower():
            temp_archive.unlink(missing_ok=True)
            raise SecurityError(
                f"Cryptographic SHA-256 mismatch for download from {url}!\n"
                f"Expected: {expected_sha256.lower()}\n"
                f"Computed: {computed_sha256}\n"
                "The downloaded payload may be corrupted or tampered with. Staging file deleted."
            )

        logger.info("Cryptographic verification passed (SHA-256: %s)", computed_sha256)
        return temp_archive

    def safe_extract_zip(self, archive_path: Path, target_dir: Path) -> None:
        """
        Extract a ZIP archive with strict Anti-Zip-Slip, absolute path,
        and symlink rejection.
        Corrections 1 & 2.
        """
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.infolist():
                name = member.filename

                # Correction 2: Reject absolute paths (POSIX and Windows drive letters)
                if name.startswith("/") or name.startswith("\\") or (len(name) > 1 and name[1] == ":"):
                    raise SecurityError(f"Rejected absolute path in ZIP archive member: {name}")

                # Correction 2: Reject traversal paths
                norm_parts = Path(name).parts
                if ".." in norm_parts:
                    raise SecurityError(f"Rejected traversal path in ZIP archive member: {name}")

                # Correction 2: Reject symlinks
                # External attributes: top 16 bits contain file mode in Unix
                mode = member.external_attr >> 16
                if (mode & 0o170000) == 0o120000:
                    raise SecurityError(f"Rejected symlink entry in ZIP archive member: {name}")

                dest_path = target_dir / name

                # Correction 1: Canonical resolved-path containment
                if not is_safely_contained(target_dir, dest_path):
                    raise SecurityError(
                        f"Zip-Slip path containment violation detected: member '{name}' "
                        f"resolves to '{dest_path.resolve()}' which escapes target directory '{target_dir.resolve()}'."
                    )

                if member.is_dir():
                    dest_path.mkdir(parents=True, exist_ok=True)
                else:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member, "r") as source, open(dest_path, "wb") as target:
                        shutil.copyfileobj(source, target)

    def safe_extract_tar(self, archive_path: Path, target_dir: Path) -> None:
        """
        Extract a TAR archive with strict Anti-Zip-Slip, special device,
        and link rejection.
        Corrections 1 & 2.
        """
        with tarfile.open(archive_path, "r:*") as tf:
            for member in tf.getmembers():
                name = member.name

                # Correction 2: Reject absolute paths
                if name.startswith("/") or name.startswith("\\") or (len(name) > 1 and name[1] == ":"):
                    raise SecurityError(f"Rejected absolute path in TAR archive member: {name}")

                # Correction 2: Reject traversal paths
                if ".." in Path(name).parts:
                    raise SecurityError(f"Rejected traversal path in TAR archive member: {name}")

                # Correction 2: Reject symlinks, hardlinks, and device nodes
                if member.issym() or member.islnk():
                    raise SecurityError(f"Rejected link entry in TAR archive member: {name}")
                if member.isfifo() or member.ischr() or member.isblk():
                    raise SecurityError(f"Rejected special device entry in TAR archive member: {name}")

                if not (member.isreg() or member.isdir()):
                    raise SecurityError(f"Rejected non-regular archive member: {name}")

                dest_path = target_dir / name

                # Correction 1: Canonical containment
                if not is_safely_contained(target_dir, dest_path):
                    raise SecurityError(
                        f"Tar-Slip path containment violation: member '{name}' "
                        f"resolves to '{dest_path.resolve()}' which escapes target '{target_dir.resolve()}'."
                    )

                if member.isdir():
                    dest_path.mkdir(parents=True, exist_ok=True)
                else:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    f_in = tf.extractfile(member)
                    if f_in:
                        with f_in, open(dest_path, "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)

    def extract_archive(
        self,
        archive_path: Path,
        target_dir: Path,
        archive_type: str = "archive",
    ) -> None:
        """
        Route archive to safe extractor.
        Correction 3: Rejects unsupported formats safely without executing arbitrary shell fallbacks.
        """
        ext = archive_path.suffix.lower()
        target_dir.mkdir(parents=True, exist_ok=True)

        if zipfile.is_zipfile(archive_path) or ext in (".zip", ".jar"):
            self.safe_extract_zip(archive_path, target_dir)
        elif tarfile.is_tarfile(archive_path) or ext in (".tar", ".gz", ".tgz", ".bz2", ".xz"):
            self.safe_extract_tar(archive_path, target_dir)
        elif ext == ".7z" or archive_type == "7z":
            # Correction 3: Check for py7zr; safely reject if not available
            try:
                import py7zr  # type: ignore
                with py7zr.SevenZipFile(archive_path, mode="r") as z:
                    for name in z.getnames():
                        if name.startswith("/") or ".." in Path(name).parts:
                            raise SecurityError(f"Unsafe entry in 7z: {name}")
                        dest = target_dir / name
                        if not is_safely_contained(target_dir, dest):
                            raise SecurityError(f"7z path traversal detected: {name}")
                    z.extractall(path=target_dir)
            except ImportError:
                raise ValueError(
                    f"Extraction of 7z format ({archive_path.name}) requires py7zr. "
                    "Unsafe shell fallbacks are prohibited by security policy. "
                    "Please provide a standard .zip or .tar archive or install py7zr."
                )
        else:
            raise ValueError(
                f"Unsupported or unsafe archive format '{ext}' for {archive_path.name}. "
                "Supported formats: .zip, .tar, .tar.gz, .tar.xz, .tar.bz2."
            )

    def install(
        self,
        spec: ToolSpec,
        version: Optional[str] = None,
        dry_run: bool = False,
        target_dir: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """
        Execute staged download and verified extraction.
        Correction 4: Staged isolation prior to target promotion.
        Correction 5: Preserves existing target if verification fails.
        Correction 6: Dry-run performs zero network downloads or disk modifications.
        """
        archive_spec = spec.get_archive("windows_x64")
        if not archive_spec or not archive_spec.is_verified:
            return False, f"Tool '{spec.id}' does not have a verified vendor archive specification."

        final_dest = (target_dir or self.get_default_install_dir(spec.id)).resolve()

        if dry_run:
            preview = (
                f"[Dry-Run] Archive Installation Plan for '{spec.id}':\n"
                f"  * Source URL    : {archive_spec.url}\n"
                f"  * Expected SHA  : {archive_spec.sha256}\n"
                f"  * Format        : {archive_spec.archive_type}\n"
                f"  * Destination   : {final_dest}\n"
                f"  * Action        : Zero downloads or disk writes performed in dry-run mode."
            )
            logger.info(preview)
            return True, preview

        # Staged execution
        staging_root = Path(tempfile.mkdtemp(prefix="toolcare_stage_"))
        try:
            # 1. Download & verify checksum
            archive_file = self.download_staged(
                url=archive_spec.url,
                expected_sha256=archive_spec.sha256,
                staging_dir=staging_root,
            )

            # 2. Extract into staging directory
            stage_extract_dir = staging_root / "extracted"
            stage_extract_dir.mkdir(parents=True, exist_ok=True)
            self.extract_archive(archive_file, stage_extract_dir, archive_spec.archive_type)

            # Handle strip_components
            extracted_source = stage_extract_dir
            if archive_spec.strip_components > 0:
                subs = [p for p in stage_extract_dir.iterdir() if p.is_dir()]
                if len(subs) == 1:
                    extracted_source = subs[0]

            # 3. Target promotion with preservation of existing installation (Correction 5)
            backup_dir: Optional[Path] = None
            if final_dest.exists():
                backup_dir = Path(tempfile.mkdtemp(prefix="toolcare_backup_")) / spec.id
                logger.info("Preserving existing installation by backing up to %s", backup_dir)
                shutil.move(str(final_dest), str(backup_dir))

            try:
                final_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(extracted_source), str(final_dest))
                if backup_dir and backup_dir.exists():
                    shutil.rmtree(str(backup_dir.parent), ignore_errors=True)
                return True, f"Successfully deployed {spec.id} to {final_dest}."
            except Exception as move_err:
                # Rollback to preserved backup (Correction 5)
                if backup_dir and backup_dir.exists():
                    logger.error("Failed to promote staged files; rolling back existing installation.")
                    if final_dest.exists():
                        shutil.rmtree(str(final_dest), ignore_errors=True)
                    shutil.move(str(backup_dir), str(final_dest))
                raise move_err

        except Exception as exc:
            logger.error("Archive deployment failed: %s", exc)
            return False, f"Archive deployment failed: {exc}"
        finally:
            shutil.rmtree(str(staging_root), ignore_errors=True)

    def uninstall(self, spec: ToolSpec, dry_run: bool = False) -> Tuple[bool, str]:
        """Uninstall tool by removing its directory."""
        target = self.get_default_install_dir(spec.id)
        if dry_run:
            return True, f"[Dry-Run] Would remove directory {target}"
        if target.is_dir():
            shutil.rmtree(str(target), ignore_errors=True)
            return True, f"Removed {target}"
        return False, f"Directory not found: {target}"
