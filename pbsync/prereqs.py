from __future__ import annotations

import os
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from pbpy import pbconfig, pbgh, pbgit, pblog, pbtools, pbuac
from pbpy.platform import PlatformSpecificLazyValue, PlatformSpecificValue


class GenericInstaller:
    """Base installer contract."""

    def install(self) -> bool:
        raise NotImplementedError()


@dataclass
class ReleaseSpec:
    host: str  # e.g. "https://github.com"
    repo: str  # e.g. "microsoft/git"
    asset_pattern: PlatformSpecificValue  # platform-specific file name pattern


class ReleaseInstaller(GenericInstaller):
    """Downloads a single release asset and executes it (typically an installer)."""

    def __init__(self, spec: ReleaseSpec, version_tag: Optional[str]):
        # version_tag: like "v3.5.1" or None for latest
        self.spec = spec
        self.version_tag = version_tag

    def _download_and_get_path(self, directory: str) -> Optional[Path]:
        pattern = self.spec.asset_pattern.get()
        if not pattern:
            # Not supported on this platform
            return None
        # Allow a single pattern or a list of patterns
        patterns: Sequence[str] = (
            pattern if isinstance(pattern, (list, tuple)) else [pattern]
        )
        repo_url = f"{self.spec.host}/{self.spec.repo}"

        for pat in patterns:
            res = pbgh.download_release_file(
                (
                    None
                    if (self.version_tag is None or self.version_tag == "latest")
                    else self.version_tag
                ),
                pat,
                directory=directory,
                repo=repo_url,
            )
            if res == 0:
                if "*" in pat:
                    for path in Path(directory).glob(pat):
                        if path.is_file():
                            return path
                    return None
                return Path(directory) / pat
        return None

    def _execute_installer(self, installer_path: Path) -> bool:
        try:
            if os.name == "nt":
                proc = pbtools.run([str(installer_path)])
                return proc.returncode == 0
            elif sys.platform == "darwin":
                # Prefer opening with the system installer
                if installer_path.suffix == ".pkg":
                    proc = pbtools.run(["open", str(installer_path)])
                else:
                    proc = pbtools.run(["open", str(installer_path)])
                return proc.returncode == 0
            else:
                # Linux - try to open with default app (may trigger software installer)
                proc = pbtools.run(["xdg-open", str(installer_path)])
                return proc.returncode == 0
        except Exception as e:
            pblog.exception(str(e))
            return False

    def _open_release_page(self):
        # Open the tag page if a specific tag was requested; otherwise open latest
        base = f"{self.spec.host}/{self.spec.repo}/releases"
        if self.version_tag is None or self.version_tag == "latest":
            url = f"{base}/latest"
        else:
            url = f"{base}/tag/{self.version_tag}"
        try:
            webbrowser.open(url)
        except Exception:
            pass

        pblog.error(f"Please install the supported version from {url}")
        pblog.error(
            f"Visit {pbconfig.get('git_instructions')} for installation instructions"
        )

    def install(self) -> bool:
        directory = "Saved/CliqueSync/Downloads"
        Path(directory).mkdir(parents=True, exist_ok=True)

        pblog.info(f"Downloading {self.spec.repo} {self.version_tag or 'latest'}...")
        installer_path = self._download_and_get_path(directory)
        if installer_path is None:
            return False

        try:
            pblog.info(f"Installing {self.spec.repo}...")
            ok = self._execute_installer(installer_path)
            if not ok:
                pblog.error(
                    f"{self.spec.repo} auto-update failed. Please download and install manually."
                )
                self._open_release_page()
            return ok
        finally:
            # Best-effort cleanup
            try:
                os.remove(installer_path)
            except Exception:
                pass


class PosixPackageInstaller(GenericInstaller):
    """Installs packages via available POSIX package manager (Linux/macOS).

    Supports apt, dnf, yum, zypper, pacman on Linux and Homebrew on macOS.
    Accepts a list of candidate package names; tries each across detected managers until one succeeds.
    """

    def __init__(self, package_candidates: Sequence[str]):
        self.package_candidates = list(package_candidates)

    def _is_root(self) -> bool:
        # Use os.getuid where available (POSIX). Fallback to False on non-POSIX.
        try:
            getuid = getattr(os, "getuid", None)
            if getuid is None:
                return False
            return getuid() == 0
        except Exception:
            return False

    def _prefix_sudo(self, cmd: list[str]) -> list[str]:
        if self._is_root():
            return cmd
        return ["sudo", "-n"] + cmd

    def _has(self, exe: str) -> bool:
        return len(pbtools.whereis(exe)) > 0

    def install(self) -> bool:
        if os.name != "posix":
            return False

        managers: list[str] = []
        # macOS tries brew first
        if sys.platform == "darwin" and self._has("brew"):
            managers.append("brew")
        # Linux managers (ordered by prevalence)
        if self._has("apt-get"):
            managers.append("apt")
        if self._has("dnf"):
            managers.append("dnf")
        if self._has("yum"):
            managers.append("yum")
        if self._has("zypper"):
            managers.append("zypper")
        if self._has("pacman"):
            managers.append("pacman")
        # Homebrew last on Linux
        if sys.platform != "darwin" and self._has("brew"):
            managers.append("brew")

        for mgr in managers:
            for pkg in self.package_candidates:
                try:
                    if mgr == "brew":
                        # Homebrew does not use sudo
                        pbtools.run(["brew", "update"])  # best effort
                        proc = pbtools.run(["brew", "install", pkg])
                        if proc.returncode == 0:
                            return True
                    elif mgr == "apt":
                        # refresh then install
                        env = {"DEBIAN_FRONTEND": "noninteractive"}
                        pbtools.run(self._prefix_sudo(["apt-get", "update"]))
                        proc = pbtools.run(
                            self._prefix_sudo(["apt-get", "install", "-y", pkg]),
                            env=env,
                        )
                        if proc.returncode == 0:
                            return True
                    elif mgr == "dnf":
                        proc = pbtools.run(
                            self._prefix_sudo(["dnf", "install", "-y", pkg])
                        )
                        if proc.returncode == 0:
                            return True
                    elif mgr == "yum":
                        proc = pbtools.run(
                            self._prefix_sudo(["yum", "install", "-y", pkg])
                        )
                        if proc.returncode == 0:
                            return True
                    elif mgr == "zypper":
                        proc = pbtools.run(
                            self._prefix_sudo(
                                ["zypper", "--non-interactive", "install", pkg]
                            )
                        )
                        if proc.returncode == 0:
                            return True
                    elif mgr == "pacman":
                        # refresh then install
                        pbtools.run(self._prefix_sudo(["pacman", "-Sy"]))
                        proc = pbtools.run(
                            self._prefix_sudo(["pacman", "-S", "--noconfirm", pkg])
                        )
                        if proc.returncode == 0:
                            return True
                except Exception as e:
                    pblog.exception(str(e))
                    continue
        return False


class GenericPrereq:
    """Base prerequisite contract."""

    def __init__(self, display: str):
        self.display = display

    def is_met(self) -> bool:
        return True

    def install(self) -> bool:
        raise NotImplementedError()


class VersionedPrereq(GenericPrereq):
    """Prereq that has a supported version and an installed version."""

    def __init__(self, display: str, match_mode: str = "exact"):
        super().__init__(display)
        self.match_mode = match_mode

    def get_supported_version(self) -> Optional[str]:
        return None

    def get_installed_version(self) -> Optional[str]:
        return None

    def is_met(self, hush=False) -> bool:
        supported_version = self.get_supported_version()
        installed_version = self.get_installed_version()

        ret = False

        if self.match_mode == "exact":
            if installed_version and (
                not supported_version or installed_version == supported_version
            ):
                ret = True
        elif self.match_mode == "minimum":
            # TODO: parse semver if needed
            raise NotImplementedError("Minimum version checking not implemented yet.")
        elif self.match_mode == "compat":
            # TODO: implement major compat matching with semver parsing
            raise NotImplementedError(
                "Compatibility version checking not implemented yet."
            )
        else:
            raise ValueError(f"Unknown match mode: {self.match_mode}")

        if not hush:
            if ret:
                pblog.info(
                    f"Current {self.__class__.__name__} version: {self.get_installed_version()}"
                )
            else:
                if supported_version and installed_version:
                    pblog.warning(
                        f"{self.__class__.__name__} does not match the supported version"
                    )
                else:
                    pblog.warning(f"{self.__class__.__name__} is not installed")
                if supported_version:
                    pblog.warning(f"Supported version: {supported_version}")
                if installed_version:
                    pblog.warning(f"Installed version: {installed_version}")

        return ret


# Concrete prereqs


class GitPrereq(VersionedPrereq):
    """Ensures Git is installed at the supported version"""

    def __init__(self):
        super().__init__("Git")

    def get_supported_version(self) -> Optional[str]:
        return pbconfig.get("supported_git_version")

    def get_installed_version(self) -> Optional[str]:
        return pbgit.get_git_version()

    def install(self) -> bool:
        supported = self.get_supported_version()

        version_tag = f"v{supported}" if supported else None

        spec = ReleaseSpec(
            host="https://github.com",
            repo="microsoft/git",
            asset_pattern=PlatformSpecificValue(
                platform_values={
                    "win32": "Git-*-64-bit.exe",
                    "darwin": "git-*-universal.pkg",
                },
            ),
        )

        installer = ReleaseInstaller(spec, version_tag)
        linstall = PlatformSpecificLazyValue(
            platform_values={
                "win32": installer.install,
                "darwin": installer.install,
                "linux": PosixPackageInstaller(["git"]).install,
            }
        )

        ok = linstall() is True
        if ok:
            if os.name == "nt":
                # Reconfigure GCM path
                gcm_bin = pbgit.get_gcm_executable()
                if gcm_bin:
                    pbtools.run([*gcm_bin, "configure"])
        return ok


class GitLFSPrereq(VersionedPrereq):
    """Ensures Git LFS is installed at supported version"""

    def __init__(self):
        super().__init__("Git LFS")

    def get_supported_version(self) -> Optional[str]:
        return pbconfig.get("supported_lfs_version")

    def get_installed_version(self) -> Optional[str]:
        return pbgit.get_lfs_version()

    def _cleanup_bundled_windows_lfs(self) -> bool:
        """Remove Git-bundled LFS binaries that can override installed version on Windows.

        Returns True if cleanup succeeded or not needed; False if conflicting binaries remain.
        """
        if os.name != "nt":
            return True
        # Only attempt cleanup when using PATH-based executables (no custom overrides)
        if (
            pbgit.get_git_executable() != "git"
            or pbgit.get_lfs_executable() != "git-lfs"
        ):
            return True

        # Find git.exe under .../Git/cmd/git.exe to infer install root(s)
        git_paths = [path for path in pbtools.whereis("git") if "cmd" in path.parts]
        if not git_paths:
            return True

        bundled_git_lfs = False
        is_admin = pbuac.isUserAdmin()
        delete_paths: list[str] = []

        for git_path in git_paths:
            # find Git from Git/cmd/git.exe
            try:
                git_root = git_path.parents[1]
            except IndexError:
                continue
            possible_lfs_paths = [
                "cmd/git-lfs.exe",
                "mingw64/bin/git-lfs.exe",
                "mingw64/libexec/git-core/git-lfs.exe",
            ]
            for rel in possible_lfs_paths:
                path = git_root / rel
                if path.exists():
                    try:
                        if is_admin:
                            path.unlink(missing_ok=True)
                        else:
                            delete_paths.append(str(path))
                    except FileNotFoundError:
                        pass
                    except OSError:
                        bundled_git_lfs = True
                        delete_paths.append(str(path))

        if not bundled_git_lfs and not is_admin and delete_paths:
            pblog.info(
                "Requesting admin permission to delete bundled Git LFS which is overriding your installed version..."
            )
            time.sleep(1)
            quoted_paths = [f'"{p}"' for p in delete_paths]
            delete_cmdline = ["cmd.exe", "/c", "DEL", "/q", "/f"] + quoted_paths
            try:
                pbuac.runAsAdmin(delete_cmdline)
            except OSError:
                pblog.error("User declined permission. Automatic delete failed.")

        # Verify deletion; if any remain, we must stop and ask user to remove
        for delete_path in delete_paths:
            path = Path(delete_path)
            if path.exists():
                bundled_git_lfs = True
                pblog.error(
                    f"Git LFS is bundled with Git, overriding your installed version. Please remove {path}."
                )

        return not bundled_git_lfs

    def install(self) -> bool:
        # On Windows, ensure bundled Git LFS binaries are removed so they don't shadow the supported version
        if not self._cleanup_bundled_windows_lfs():
            pbtools.error_state()
        supported = self.get_supported_version()

        version_tag = f"v{supported}" if supported else None

        # Use wildcards so 'latest' works across platforms
        spec = ReleaseSpec(
            host="https://github.com",
            repo="git-lfs/git-lfs",
            asset_pattern=PlatformSpecificValue(
                platform_values={
                    "win32": "git-lfs-windows-*.exe",
                    "darwin": "git-lfs-darwin-*.zip",
                    "linux": "git-lfs-linux-*.tar.gz",
                },
            ),
        )
        installer = ReleaseInstaller(spec, version_tag)

        linstall = PlatformSpecificLazyValue(
            platform_values={
                "win32": installer.install,
                "darwin": installer.install,
                "linux": PosixPackageInstaller(["git-lfs"]).install,
            }
        )
        ok = linstall() is True
        if ok:
            # Configure LFS hooks for the user
            current_drive = Path().resolve()
            drive_root = current_drive.drive or current_drive.root
            pbtools.run([pbgit.get_lfs_executable(), "install"], cwd=drive_root)

            # Check if Git LFS was installed to a different path (Windows only)
            if os.name == "nt" and pbgit.get_lfs_executable() == "git-lfs":
                git_lfs_paths = [path for path in pbtools.whereis("git-lfs")]
                if len(git_lfs_paths) > 1:
                    index = 0
                    main_lfs_path = git_lfs_paths[0]
                    for git_lfs_path in git_lfs_paths:
                        if supported == pbgit.get_lfs_version(git_lfs_path):
                            if index != 0:
                                pblog.info(
                                    "Requesting admin permission to move installed Git LFS which is being overridden..."
                                )
                                time.sleep(1)
                                move_cmdline = [
                                    "cmd.exe",
                                    "/c",
                                    "MOVE",
                                    "/Y",
                                    f'"{git_lfs_path}"',
                                    f'"{main_lfs_path}"',
                                ]
                                try:
                                    pbuac.runAsAdmin(move_cmdline)
                                except OSError:
                                    pblog.error(
                                        "User declined permission. Automatic move failed."
                                    )
                                    pblog.error(
                                        f"Git LFS is installed to a different location, overriding your installed version. Please install Git LFS to {main_lfs_path.parents[1]}."
                                    )
                                    pbtools.error_state()
                            break
                        index += 1

        return ok


class GitCredentialManagerPrereq(VersionedPrereq):
    """Ensures Git Credential Manager is installed at supported version"""

    def __init__(self):
        super().__init__("Git Credential Manager")

    def get_supported_version(self) -> Optional[str]:
        return pbconfig.get("supported_gcm_version")

    def get_installed_version(self) -> Optional[str]:
        return pbgit.get_gcm_version()

    def _unset_all_credential_helpers(self):
        """Remove any configured credential.helper entries (local and global)."""
        try:
            pbtools.run_with_combined_output(
                [
                    pbgit.get_git_executable(),
                    "config",
                    "--unset-all",
                    "credential.helper",
                ]
            )
        except Exception:
            pass
        try:
            pbtools.run_with_combined_output(
                [
                    pbgit.get_git_executable(),
                    "config",
                    "--global",
                    "--unset-all",
                    "credential.helper",
                ]
            )
        except Exception:
            pass

    def install(self) -> bool:
        supported = self.get_supported_version()
        detected = self.get_installed_version()

        # If a conflicting credential helper is configured, clear it first
        if isinstance(detected, str) and detected.startswith("diff"):
            exe_location = detected.split(".", 1)[1]
            if exe_location.endswith(".exe"):
                pblog.error(
                    f"It seems like you have another Git credential helper installed at: {exe_location}."
                )
                pblog.error(
                    'Please uninstall this and Git Credential Manager if you have it in "Add or remove programs" and then install Git Credential Manager again.'
                )
            else:
                pblog.error(
                    'Please uninstall Git Credential Manager if you have it in "Add or remove programs" and then install Git Credential Manager again.'
                )
            self._unset_all_credential_helpers()

        version_tag = f"v{supported}" if supported else None

        # Provide broad wildcard patterns; platform package managers may handle install after open
        spec = ReleaseSpec(
            host="https://github.com",
            repo="git-ecosystem/git-credential-manager",
            asset_pattern=PlatformSpecificValue(
                platform_values={
                    "win32": "gcm-win-x86-*.exe",
                    "darwin": "gcm-osx*.pkg",
                },
            ),
        )
        installer = ReleaseInstaller(spec, version_tag)
        linstall = PlatformSpecificLazyValue(
            platform_values={
                "win32": installer.install,
                "darwin": installer.install,
                "linux": PosixPackageInstaller(
                    ["git-credential-manager", "git-credential-manager-core"]
                ).install,
            }
        )
        need_install = (
            bool(supported)
            or detected == pbgit.missing_version
            or (isinstance(detected, str) and detected.startswith("diff"))
        )
        ok = linstall() is True if need_install else True

        # (Re)configure GCM and verify
        gcm_bin = pbgit.get_gcm_executable()
        if gcm_bin:
            pbtools.run([*gcm_bin, "configure"])

        # If a specific version is required, ensure it matches; otherwise just ensure some GCM is active
        if supported:
            final_version = self.get_installed_version()
            if final_version != supported:
                # This handles a case where GCM is installed by Git itself and blocks new install
                self._unset_all_credential_helpers()
                pblog.error(
                    "Git Credential Manager failed due to an installation conflict, please launch UpdateProject again to finalize the installation."
                )
                return False

        return ok


def ensure_prereqs(prereqs: Optional[Sequence[GenericPrereq]] = None) -> bool:
    """Checks and installs required prerequisites.

    Returns True if all prereqs are met or successfully installed.
    """
    if prereqs is None:
        prereqs = [GitPrereq(), GitLFSPrereq(), GitCredentialManagerPrereq()]

    all_ok = True
    for prereq in prereqs:
        try:
            if prereq.is_met():
                continue
            name = prereq.__class__.__name__
            pblog.info(f"Installing prerequisite: {name}...")
            ok = prereq.install()
            if not ok:
                pblog.error(f"Failed to install prerequisite: {name}.")
                all_ok = False
        except Exception as e:
            pblog.exception(str(e))
            all_ok = False
    return all_ok
