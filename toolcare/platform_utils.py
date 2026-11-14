"""
toolcare.platform_utils - Cross-platform identification and secure subprocess execution.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SYSTEM_OS = platform.system().lower()
IS_WINDOWS = SYSTEM_OS == "windows"
IS_LINUX = SYSTEM_OS == "linux"
IS_MAC = SYSTEM_OS == "darwin"
CPU_ARCH = platform.machine().lower()


def get_home_dir() -> Path:
    """Return the user's home directory across operating systems."""
    return Path.home()


def get_esim_config_dir() -> Path:
    """Return the ~/.esim configuration directory."""
    return get_home_dir() / ".esim"


def get_nghdl_config_dir() -> Path:
    """Return the ~/.nghdl configuration directory."""
    return get_home_dir() / ".nghdl"


def run_safe_command(
    cmd: List[str],
    timeout: int = 10,
    cwd: Optional[Path | str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    """
    Execute a system command securely using argument lists.

    Safeguards:
    - Never uses shell=True to protect against shell injection.
    - On Windows, enforces CREATE_NO_WINDOW and SW_HIDE to prevent flashing console windows.
    - Enforces execution timeouts to avoid hanging background processes.
    - Decodes output safely with UTF-8 and replacement of unknown characters.

    Returns:
        Tuple of (returncode, stdout, stderr).
    """
    if isinstance(cmd, str):
        raise TypeError("run_safe_command requires an argument list (List[str]), not a raw shell string.")

    creationflags = 0
    startupinfo = None

    if IS_WINDOWS:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            startupinfo=startupinfo,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout} seconds: {' '.join(cmd)}"
    except FileNotFoundError:
        return 127, "", f"Executable not found: {cmd[0]}"
    except Exception as exc:
        return -1, "", f"Execution failure: {exc}"


def get_available_package_managers() -> Dict[str, bool]:
    """
    Detect available system package managers on the host machine.
    """
    managers = {
        "winget": False,
        "choco": False,
        "apt": False,
        "brew": False,
    }

    if IS_WINDOWS:
        managers["winget"] = shutil.which("winget") is not None
        managers["choco"] = shutil.which("choco") is not None
    elif IS_LINUX:
        managers["apt"] = shutil.which("apt-get") is not None
    elif IS_MAC:
        managers["brew"] = shutil.which("brew") is not None

    return managers
