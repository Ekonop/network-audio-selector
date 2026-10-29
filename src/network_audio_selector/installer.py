from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class SnapcastInstallError(RuntimeError):
    """Snapcast could not be installed through the detected package manager."""


def snapcast_package_manager() -> tuple[str, list[str]] | None:
    if shutil.which("yay"):
        return "yay", ["yay", "-S", "--needed", "--noconfirm", "snapcast"]
    if shutil.which("paru"):
        return "paru", ["paru", "-S", "--needed", "--noconfirm", "snapcast"]
    if shutil.which("pacman"):
        return "pacman", ["pacman", "-S", "--needed", "snapcast"]
    if shutil.which("apt-get"):
        return "apt", ["apt-get", "install", "-y", "snapcast"]
    if shutil.which("dnf"):
        return "dnf", ["dnf", "install", "-y", "snapcast"]
    if shutil.which("zypper"):
        return "zypper", ["zypper", "--non-interactive", "install", "snapcast"]
    return None


def install_snapcast() -> str:
    manager = snapcast_package_manager()
    if manager is None:
        raise SnapcastInstallError(
            "No supported package manager found. Install the Snapcast package manually."
        )
    name, command = manager
    if shutil.which(command[0]) is None:
        raise SnapcastInstallError(f"{name} is not available on this system.")
    if name in {"yay", "paru"}:
        privileged_command = command
    elif shutil.which("pkexec"):
        privileged_command = ["pkexec", *command]
    elif shutil.which("sudo"):
        privileged_command = ["sudo", *command]
    else:
        raise SnapcastInstallError(
            "Snapcast needs administrator privileges, but neither pkexec nor sudo is available."
        )
    try:
        subprocess.run(privileged_command, check=True)
    except subprocess.CalledProcessError as exc:
        if name not in {"yay", "paru"} or not shutil.which("pkexec"):
            raise SnapcastInstallError(
                f"Snapcast installation failed with exit code {exc.returncode}."
            ) from exc
        packages = sorted(
            Path.home().glob(".cache/yay/snapcast/snapcast-*.pkg.tar.zst"),
            key=lambda path: path.stat().st_mtime,
        )
        if not packages:
            raise SnapcastInstallError(
                f"Snapcast installation failed with exit code {exc.returncode}."
            ) from exc
        try:
            subprocess.run(
                ["pkexec", "pacman", "-U", "--noconfirm", str(packages[-1])],
                check=True,
            )
        except subprocess.CalledProcessError as fallback_exc:
            raise SnapcastInstallError(
                "Snapcast was built, but installing the package failed."
            ) from fallback_exc
    if not shutil.which("snapserver"):
        raise SnapcastInstallError(
            "The package manager completed, but snapserver is still unavailable."
        )
    return "Snapcast installed successfully."
