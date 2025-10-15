import argparse
import multiprocessing
import os
import os.path
import platform
import sys
import shutil
import subprocess
import threading
import time
import webbrowser
from functools import partial
from pathlib import Path

from pbpy import (
    pbbutler,
    pbconfig,
    pbdispatch,
    pbgh,
    pbgit,
    pbhttp,
    pblog,
    pbpy_version,
    pbsteamcmd,
    pbtools,
    pbuac,
    pbunreal,
)
from pbpy.pbtools import error_state

try:
    import pbsync_version
except ImportError:
    from pbsync import pbsync_version

default_config_name = "PBSync.xml"

def check_gh_cli():
    try:
        result = subprocess.run(["gh", "--version"], capture_output=True, text=True, check=True)
        pblog.info(result.stdout.strip())
        return True
    except FileNotFoundError:
        return False

def install_gh_cli():
    pblog.info("GitHub CLI not found. Installing...")
    gh_ver="2.63.0"
    # Download GitHub CLI
    match sys.platform:
        case "win32":
            url = f"https://github.com/cli/cli/releases/download/v{gh_ver}/gh_{gh_ver}_windows_amd64.msi"
            downloaded_file = 'gh.msi'
        case "darwin":
            url = f"https://github.com/cli/cli/releases/download/v{gh_ver}/gh_{gh_ver}_macOS_universal.pkg"
            downloaded_file = 'gh.pkg'
        case _:
            pblog.error("Your operation system is not supported")
            return None

    pbhttp.download(url)

    # Install GitHub CLI
    try:
        match sys.platform:
            case "win32": subprocess.run(['msiexec', '/i', downloaded_file, '/passive'], check=True)
            case "darwin": subprocess.run(['sudo', 'installer', '-pkg', downloaded_file, '-target', '/'], check=True)
    except subprocess.CalledProcessError as e:
        pblog.error(f"Command failed with return code {e.returncode}")
    
    pblog.info("GitHub CLI installed.")
    result = subprocess.run(['gh', '--version'], capture_output=True, text=True, check=True)
    pblog.info(result.stdout.strip())
    # Delete installation file
    os.remove(downloaded_file)

    try:
        subprocess.run(['gh', 'auth', 'login'], check=True)
    except:
        pass

if not check_gh_cli():
    install_gh_cli()

def config_handler(config_var, config_parser_func):
    if not pbconfig.generate_config(config_var, config_parser_func):
        # Logger is not initialized yet, so use print instead
        error_state(
            f"{str(config_var)} config file is not valid or not found. Please check the integrity of the file",
            hush=True,
            term=True,
        )


def sync_handler(sync_val: str, repository_val=None):
    sync_val = sync_val.lower()

    if sync_val == "all" or sync_val == "force" or sync_val == "partial":
        pblog.info(f"Executing {sync_val} sync command")
        pblog.info(f"PBpy Library Version: {pbpy_version.ver}")
        pblog.info(f"PBSync Program Version: {pbsync_version.ver}")

        pblog.info("------------------")

        detected_git_version = pbgit.get_git_version()
        supported_git_version = pbconfig.get("supported_git_version")
        if detected_git_version == supported_git_version:
            pblog.info(f"Current Git version: {detected_git_version}")
        else:
            try:
                match sys.platform:
                    case "win32": subprocess.run(['gh', 'release', 'download', supported_git_version, '-p', 'Git*.exe', '-R', 'microsoft/git'], check=True)
                    case "darwin": subprocess.run(['gh', 'release', 'download', supported_git_version, '-p', 'git*.pkg', '-R', 'microsoft/git'], check=True)
            except subprocess.CalledProcessError as e:
                pblog.error(f"Command failed with return code {e.returncode}")

            git_installer = [file for file in os.listdir() if file.startswith("Git") or file.startswith("git")][0]

            # Install Git
            try:
                match sys.platform:
                    case "win32":  subprocess.run([git_installer], '/VERYSILENT', check=True)
                    case "darwin": subprocess.run(['sudo', 'installer', '-pkg', git_installer, '-target', '/'], check=True)
                pblog.info(f'Installing Git {supported_git_version}...')
            except subprocess.CalledProcessError as e:
                pblog.error(f"Command failed with return code {e.returncode}")

            pblog.info(f'Git {supported_git_version} installed successfully.')
            # Delete installation file
            os.remove(git_installer)

        if (
            os.name == "nt"
            and pbgit.get_git_executable() == "git"
            and pbgit.get_lfs_executable() == "git-lfs"
        ):
            # find Git/cmd/git.exe
            git_paths = [path for path in pbtools.whereis("git") if "cmd" in path.parts]

            if len(git_paths) > 0:
                bundled_git_lfs = False

                is_admin = pbuac.isUserAdmin()

                delete_paths = []

                for git_path in git_paths:
                    # find Git from Git/cmd/git.exe
                    git_root = git_path.parents[1]
                    possible_lfs_paths = [
                        "cmd/git-lfs.exe",
                        "mingw64/bin/git-lfs.exe",
                        "mingw64/libexec/git-core/git-lfs.exe",
                    ]
                    for possible_lfs_path in possible_lfs_paths:
                        path = git_root / possible_lfs_path
                        if path.exists():
                            try:
                                if is_admin:
                                    path.unlink()
                                else:
                                    delete_paths.append(str(path))
                            except FileNotFoundError:
                                pass
                            except OSError:
                                pblog.error(
                                    f"Git LFS is bundled with Git, overriding your installed version. Please remove {path}."
                                )
                                bundled_git_lfs = True

                if not is_admin and len(delete_paths) > 0:
                    pblog.info(
                        "Requesting admin permission to delete bundled Git LFS which is overriding your installed version..."
                    )
                    time.sleep(1)
                    quoted_paths = [f'"{path}"' for path in delete_paths]
                    delete_cmdline = ["cmd.exe", "/c", "DEL", "/q", "/f"] + quoted_paths
                    try:
                        ret = pbuac.runAsAdmin(delete_cmdline)
                    except OSError:
                        pblog.error(
                            "User declined permission. Automatic delete failed."
                        )

                for delete_path in delete_paths:
                    path = Path(delete_path)
                    if path.exists():
                        bundled_git_lfs = True
                        pblog.error(
                            f"Git LFS is bundled with Git, overriding your installed version. Please remove {path}."
                        )

                if bundled_git_lfs:
                    error_state()

        detected_lfs_version = pbgit.get_lfs_version()
        supported_lfs_version = pbconfig.get("supported_lfs_version")
        if detected_lfs_version == supported_lfs_version:
            pblog.info(f"Current Git LFS version: {detected_lfs_version}")
        else:
            # Download Git LFS
            try:
                match sys.platform:
                    case "win32": subprocess.run(['gh', 'release', 'download', supported_lfs_version, '-p', '*.exe', '-R', 'git-lfs/git-lfs'], check=True)
                    case "darwin":
                        if platform.machine() == "AMD64":
                            subprocess.run(['gh', 'release', 'download', supported_lfs_version, '-p', '*darwin-amd64*', '-R', 'git-lfs/git-lfs'], check=True)
                        else:
                            subprocess.run(['gh', 'release', 'download', supported_lfs_version, '-p', '*darwin-arm64*', '-R', 'git-lfs/git-lfs'], check=True)
            except subprocess.CalledProcessError as e:
                pblog.error(f"Command failed with return code {e.returncode}")
    
            # Find the downloaded .exe (Windows) or folder (Mac OS)
            lfs_installer = [file for file in os.listdir() if file.startswith("git-lfs")][0]

            try:
                match sys.platform:
                    case "win32": subprocess.run([lfs_installer], '/VERYSILENT', check=True)
                    case "darwin":
                        subprocess.run(['unzip', lfs_installer], check=True)
                        subprocess.run(['sudo', f"./{lfs_installer}/install.sh"], check=True)
                pblog.info(f"Installing Git LFS {supported_lfs_version}...")
            except subprocess.CalledProcessError as e:
                pblog.error(f"Command failed with return code {e.returncode}")

            pblog.info(f'Git LFS {supported_lfs_version} installed successfully.')

            # Delete installation file/folder
            if path.isfile(lfs_installer):
                os.remove(lfs_installer)
            elif path.isdir(lfs_installer):
                shutil.rmtree(lfs_installer)

        # check if Git LFS was installed to a different path
        if os.name == "nt" and pbgit.get_lfs_executable() == "git-lfs":
            git_lfs_paths = [path for path in pbtools.whereis("git-lfs")]
            if len(git_lfs_paths) > 1:
                index = 0
                main_lfs_path = git_lfs_paths[0]
                for git_lfs_path in git_lfs_paths:
                    if supported_lfs_version == pbgit.get_lfs_version(git_lfs_path):
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
                                ret = pbuac.runAsAdmin(move_cmdline)
                            except OSError:
                                pblog.error(
                                    "User declined permission. Automatic move failed."
                                )
                                pblog.error(
                                    f"Git LFS is installed to a different location, overriding your installed version. Please install Git LFS to {main_lfs_path.parents[1]}."
                                )
                                error_state()
                        break
                    index += 1

        detected_gcm_version = pbgit.get_gcm_version()
        supported_gcm_version_raw = pbconfig.get("supported_gcm_version")
        supported_gcm_version = f"{supported_gcm_version_raw}"
        if detected_gcm_version == supported_gcm_version:
            pblog.info(
                f"Current Git Credential Manager version: {detected_gcm_version}"
            )
        else:
            pblog.warning(
                "Git Credential Manager is not updated to the supported version in your system"
            )
            pblog.warning(
                f"Supported Git Credential Manager Version: {supported_gcm_version}"
            )
            pblog.warning(
                f"Current Git Credential Manager Version: {detected_gcm_version}"
            )
            needs_git_update = True
            if detected_gcm_version.startswith("diff"):
                # remove the old credential helper (it may get stuck, and GCM won't be able to install)
                pbtools.run_with_combined_output(
                    [
                        pbgit.get_git_executable(),
                        "config",
                        "--unset-all",
                        "credential.helper",
                    ]
                )
                pbtools.run_with_combined_output(
                    [
                        pbgit.get_git_executable(),
                        "config",
                        "--global",
                        "--unset-all",
                        "credential.helper",
                    ]
                )
                exe_location = detected_gcm_version.split(".", 1)[1]
                # if they actually have a Windows program installed, inform them.
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
            else:
                if os.name == "nt":
                    pblog.info("Auto-updating Git Credential Manager...")
                    version = f"v{supported_gcm_version}"
                    directory = "Saved/PBSyncDownloads"
                    download = f"gcm-win-x86-{supported_gcm_version_raw}.exe"
                    repo = "git-ecosystem/git-credential-manager"
                    if (
                        pbgh.download_release_file(
                            version,
                            download,
                            directory=directory,
                            repo=f"https://github.com/{repo}",
                        )
                        != 0
                    ):
                        pblog.error(
                            "Git Credential Manager auto-update failed, please download and install manually."
                        )
                        webbrowser.open(
                            f"https://github.com/{repo}/releases/download/{version}/{download}"
                        )
                    else:
                        download_path = f"Saved\\PBSyncDownloads\\{download}"
                        proc = pbtools.run([download_path])
                        if proc.returncode:
                            pblog.error(
                                "Git Credential Manager auto-update failed, please download and install manually."
                            )
                            webbrowser.open(
                                f"https://github.com/{repo}/releases/download/{version}/{download}"
                            )
                        else:
                            # reconfigure credential manager to make sure we have the proper path
                            pbtools.run([*pbgit.get_gcm_executable(), "configure"])
                            needs_git_update = False
                        os.remove(download_path)

            if not needs_git_update:
                # this handles a case where GCM is installed by Git itself, and blocks GCM from installing the new one
                needs_git_update = pbgit.get_gcm_version() != supported_gcm_version
                if needs_git_update:
                    # remove the old credential helper (it may get stuck, and GCM won't be able to install)
                    pbtools.run_with_combined_output(
                        [
                            pbgit.get_git_executable(),
                            "config",
                            "--unset-all",
                            "credential.helper",
                        ]
                    )
                    pbtools.run_with_combined_output(
                        [
                            pbgit.get_git_executable(),
                            "config",
                            "--global",
                            "--unset-all",
                            "credential.helper",
                        ]
                    )
                    pblog.error(
                        "Git Credential Manager failed due to an installation conflict, please launch UpdateProject again to finalize the installation."
                    )
            else:
                pblog.error(
                    "Please install the supported Git Credential Manager version from https://github.com/git-ecosystem/git-credential-manager/releases"
                )

        if needs_git_update:
            error_state()

        pblog.info("------------------")

        # Check our remote connection before doing anything
        remote_state, remote_url = pbgit.check_remote_connection()
        if not remote_state:
            error_state(
                f"Remote connection was not successful. Please verify that you have an internet connection. Current git remote URL: {remote_url}"
            )
        else:
            pblog.info("Remote connection is up")

        pblog.info("------------------")

        # Do some housekeeping for git configuration
        pbgit.setup_config()

        # Check if we have correct credentials
        pbgit.check_credentials()

        partial_sync = sync_val == "partial"
        is_ci = pbconfig.get("is_ci")

        status_out = pbtools.run_with_combined_output(
            [pbgit.get_git_executable(), "status", "-uno"]
        ).stdout
        # continue a trivial rebase
        if "rebase" in status_out:
            if pbtools.it_has_any(
                status_out,
                "nothing to commit",
                "git rebase --continue",
                "all conflicts fixed",
            ):
                pbunreal.ensure_ue_closed()
                rebase_out = pbtools.run_with_combined_output(
                    [pbgit.get_git_executable(), "rebase", "--continue"]
                ).stdout
                if pbtools.it_has_any(rebase_out, "must edit all merge conflicts"):
                    # this is an improper state, since git told us otherwise before. abort all.
                    pbgit.abort_all()
            else:
                error_state(
                    f"You are in the middle of a rebase. Changes on one of your commits will be overridden by incoming changes. Please request help in {pbconfig.get('support_channel')} to resolve conflicts, and please do not run UpdateProject until the issue is resolved.",
                    fatal_error=True,
                )

        # undo single branch clone
        if not is_ci:
            pbtools.run(
                [
                    pbgit.get_git_executable(),
                    "config",
                    "remote.origin.fetch",
                    "+refs/heads/*:refs/remotes/origin/*",
                ]
            )

        # Execute synchronization part of script if we're on the expected branch, or force sync is enabled
        if sync_val == "force" or pbgit.is_on_expected_branch():
            if partial_sync:
                pbtools.maintain_repo()
            else:
                pbtools.resolve_conflicts_and_pull()

                pblog.info("------------------")

            project_version = pbunreal.get_project_version()
            is_custom_version = pbunreal.is_using_custom_version()
            needs_binaries_pull = pbgh.is_pull_binaries_required()
            if project_version is not None:
                if is_custom_version:
                    pblog.info(f"User selected project version: {project_version}")
                else:
                    pblog.info(f"Current project version: {project_version}")
            elif needs_binaries_pull:
                error_state(
                    f"Something went wrong while fetching project version. Please request help in {pbconfig.get('support_channel')}."
                )

            checksum_json_path = pbconfig.get("checksum_file")
            if is_custom_version:
                # checkout old checksum file from tag
                pbgit.sync_file(checksum_json_path, project_version)

            if needs_binaries_pull:
                pblog.info("Binaries are not up to date, pulling new binaries...")
                ret = pbgh.pull_binaries(project_version)
                if ret == 0:
                    pblog.success("Binaries were pulled successfully!")
                elif ret < 0:
                    error_state(
                        "Binaries pull failed, please view log for instructions."
                    )
                elif ret > 0:
                    error_state(
                        f"An error occurred while pulling binaries. Please request help in {pbconfig.get('support_channel')} to resolve it, and please do not run UpdateProject until the issue is resolved.",
                        True,
                    )
            else:
                pblog.success("Binaries are up to date!")

            # restore checksum file
            if is_custom_version:
                pbgit.sync_file(checksum_json_path, "HEAD")
        elif pbconfig.get_user_config().getboolean(
            "project", "autosync", fallback=True
        ):
            pbtools.resolve_conflicts_and_pull()
        else:
            pblog.info(
                f"Current branch does not need auto synchronization: {pbgit.get_current_branch_name()}."
            )
            pbtools.maintain_repo()

        symbols_needed = pbunreal.is_versionator_symbols_enabled()
        pbunreal.clean_binaries_folder(not symbols_needed)

        pblog.info("------------------")

        pblog.info("Checking for engine updates...")
        uproject_file = pbconfig.get("uproject_name")
        if pbgit.sync_file(uproject_file) != 0:
            error_state(
                f"Something went wrong while updating the uproject file. Please request help in {pbconfig.get('support_channel')}."
            )

        configured_branches = pbconfig.get("branches")
        should_unlock_unmodified = (
            pbgit.get_current_branch_name() in configured_branches
        )
        fix_attr_thread = threading.Thread(
            target=pbgit.fix_lfs_ro_attr, args=(should_unlock_unmodified,)
        )
        fix_attr_thread.start()

        engine_version = pbunreal.get_engine_version_with_prefix()
        if engine_version is not None:
            pblog.info(
                "Registering current engine build if it exists. Otherwise, the build will be downloaded..."
            )

            bundle_name = pbunreal.get_bundle()

            if pbunreal.download_engine(bundle_name, symbols_needed):
                pblog.info(
                    f"Engine build {bundle_name}-{engine_version} successfully registered"
                )
            else:
                error_state(
                    f"Something went wrong while registering engine build {bundle_name}-{engine_version}. Please request help in {pbconfig.get('support_channel')}."
                )

            # Clean old engine installations
            if pbconfig.get_user_config().getboolean(
                pbunreal.uev_user_config, "clean", fallback=True
            ):
                if pbunreal.clean_old_engine_installations():
                    pblog.info("Successfully cleaned old engine installations.")
                else:
                    pblog.warning(
                        "Something went wrong while cleaning old engine installations. You may want to clean them manually."
                    )

        binaries_mode = pbgit.get_binaries_mode()
        if binaries_mode == "build":
            pbunreal.generate_project_files()
            pbunreal.build_source(for_distribution=False)

        pblog.info("------------------")

        pblog.info("Updating Unreal configuration settings")
        pbunreal.update_source_control()

        pblog.info("Finishing LFS locks cleanup...")
        fix_attr_thread.join()
        pblog.info("Finished LFS locks cleanup.")

        launch_pref = (
            pbconfig.get_user("project", "launch", "none")
            if is_ci
            else pbconfig.get_user("project", "launch", "editor")
        )
        if launch_pref == "vs":
            os.startfile(pbunreal.get_sln_path())
        elif launch_pref == "rider":
            rider_bin = pbtools.get_one_line_output(
                ["echo", "%Rider for Unreal Engine%"]
            )
            rider_bin = rider_bin.replace(";", "")
            rider_bin = rider_bin.replace('"', "")
            pbtools.run_non_blocking(
                f'"{rider_bin}\\rider64.exe" "{str(pbunreal.get_sln_path().resolve())}"'
            )
        elif pbunreal.is_ue_closed():
            if launch_pref == "editor":
                extra_args = pbconfig.get_user(
                    "project", "editor_args", default=""
                ).split()
                if extra_args:
                    launch_args = [pbunreal.get_editor_path(), path]
                    launch_args.extend(extra_args)
                    pbtools.run_non_blocking_ex(launch_args)
                else:
                    launched_editor = False
                    if not pbunreal.check_ue_file_association():
                        pblog.warning(
                            "PBSync failed to find a valid file association to launch the editor, attempting to resolve..."
                        )
                        pbunreal.run_unreal_setup()
                    if pbunreal.check_ue_file_association():
                        uproject_file = pbconfig.get("uproject_name")
                        path = str(Path(uproject_file).resolve())
                        try:
                            os.startfile(path)
                            launched_editor = True
                        except OSError:
                            # files are associated, but the executable is not found
                            pass
                        except NotImplementedError:
                            if sys.platform.startswith("linux"):
                                pbtools.run_non_blocking(f"xdg-open {path}")
                                launched_editor = True

                    if not launched_editor:
                        pblog.warning(
                            f"PBSync failed to find a valid file association to launch the editor, and will attempt to launch the editor directly as a workaround."
                        )
                        pbtools.run_non_blocking_ex([pbunreal.get_editor_path(), path])
                        pblog.warning(
                            f"If PBSync failed to launch the directly directly, please launch {uproject_file} manually for now."
                        )
                        error_state(
                            f"For a permanent fix, try clearing out file associations for the .uproject file type and launching PBSync again. Please get help in {pbconfig.get('support_channel')} if the issue continues."
                        )

            # TODO
            # elif launch_pref == "debug":
            #    pbtools.run_non_blocking(f"\"{str(pbunreal.get_devenv_path())}\" \"{str(pbunreal.get_sln_path())}\" /DebugExe \"{str(pbunreal.get_editor_path())}\" \"{str(pbunreal.get_uproject_path())}\" -skipcompile")

    elif sync_val == "binaries":
        project_version = pbunreal.get_project_version()
        ret = pbgh.pull_binaries(project_version, True)
        if ret == 0:
            pblog.info(
                f"Binaries for {project_version} pulled and extracted successfully"
            )
        else:
            error_state(f"Failed to pull binaries for {project_version}")

    elif sync_val == "engine":
        # Pull engine build with ueversionator and register it
        bundle_name = pbunreal.get_bundle()

        engine_version = pbunreal.get_engine_version_with_prefix()
        symbols_needed = pbunreal.is_versionator_symbols_enabled()
        if engine_version is not None:
            if pbunreal.download_engine(bundle_name, symbols_needed):
                pblog.info(
                    f"Engine build {bundle_name}-{engine_version} successfully registered"
                )
                if pbconfig.get("is_ci"):
                    pbunreal.clean_old_engine_installations(keep=3)
            else:
                error_state(
                    f"Something went wrong while registering engine build {bundle_name}-{engine_version}"
                )


build_hooks = {
    "sln": pbunreal.generate_project_files,
    "source": pbunreal.build_source,
    "local": partial(pbunreal.build_source, False),
    "debuggame": partial(pbunreal.build_game, "DebugGame"),
    "development": partial(pbunreal.build_game, "Development"),
    "internal": partial(pbunreal.build_game, "Test"),
    "game": pbunreal.build_game,
    "shaders": pbunreal.build_shaders,
    "shaders_vulkan": partial(pbunreal.build_shaders, "SF_VULKAN_SM6"),
    "installedbuild": pbunreal.build_installed_build,
    "package": pbunreal.package_binaries,
    "release": pbgh.generate_release,
    "inspect": pbunreal.inspect_source,
    "inspectall": partial(pbunreal.inspect_source, all=True),
    "fillddc": pbunreal.fill_ddc,
    "s3ddc": pbunreal.upload_cloud_ddc,
    "ddc": pbunreal.generate_ddc_data,
    "clearcook": pbunreal.clear_cook_cache,
}


def build_handler(build_val):
    for build_action in build_val:
        build_func = build_hooks.get(build_action)
        if build_func:
            build_func()


def clean_handler(clean_val):
    if clean_val == "workspace":
        if pbtools.wipe_workspace():
            pblog.info("Workspace wipe successful")
        else:
            error_state("Something went wrong while wiping the workspace")

    elif clean_val == "engine":
        if not pbunreal.clean_old_engine_installations():
            error_state(
                "Something went wrong while cleaning old engine installations. You may want to clean them manually."
            )


def printversion_handler(print_val, repository_val=None):
    if print_val == "current-engine":
        engine_version = pbunreal.get_engine_version()
        if engine_version is None:
            error_state("Could not find current engine version.")
        print(engine_version, end="")

    elif print_val == "project":
        project_version = pbunreal.get_project_version()
        if project_version is None:
            error_state("Could not find project version.")
        print(project_version, end="")

    elif print_val == "latest-project":
        project_version = pbunreal.get_latest_project_version()
        if project_version is None:
            error_state("Could not find project version.")
        print(project_version, end="")


def autoversion_handler(autoversion_val):
    if pbunreal.project_version_increase(autoversion_val):
        pblog.info("Successfully increased project version")
    else:
        error_state("Error occurred while increasing project version")


PUBLISHERS = {
    "dispatch": lambda publish_val, pubexe: pbdispatch.publish_build(
        publish_val,
        pubexe,
        pbconfig.get("publish_stagedir"),
        pbconfig.get("dispatch_config"),
    ),
    "steamcmd": lambda publish_val, pubexe: pbsteamcmd.publish_build(
        publish_val,
        pubexe,
        pbconfig.get("publish_stagedir"),
        pbconfig.get("steamcmd_script"),
        pbconfig.get("steamdrm_appid"),
        pbconfig.get("steamdrm_targetbinary"),
        (
            True
            if os.getenv("PBSYNC_STEAMDRM_USECLOUD")
            else pbconfig.get("steamdrm_useonprem")
        ),
    ),
    "butler": lambda publish_val, pubexe: pbbutler.publish_build(
        publish_val,
        pubexe,
        pbconfig.get("publish_stagedir"),
        pbconfig.get("butler_project"),
        pbconfig.get("butler_manifest"),
    ),
}


def publish_handler(publish_val):
    publishers = pbconfig.get("publish_publishers")
    for publisher in publishers:
        fn = PUBLISHERS.get(publisher)
        if not fn:
            error_state(f"Unknown publisher: {publisher}")
        result = fn(publish_val.lower(), publisher)
        if result != 0:
            error_state(
                f"Something went wrong while publishing a new build. Error code {result}"
            )


def main(argv):
    parser = argparse.ArgumentParser(
        description=f"PBSync | PBpy Library Version: {pbpy_version.ver} | PBSync Program Version: {pbsync_version.ver}"
    )

    parser.add_argument(
        "--sync",
        help="Main command for the PBSync, synchronizes the project with latest changes from the repo, and does some housekeeping",
        choices=[
            "all",
            "partial",
            "binaries",
            "engineversion",
            "engine",
            "force",
        ],
        const="all",
        nargs="?",
    )
    parser.add_argument(
        "--printversion",
        help="Prints requested version information into console.",
        choices=["current-engine", "latest-engine", "project"],
    )
    parser.add_argument(
        "--repository",
        help="gcloud repository url for --printversion latest-engine and --sync engine commands",
    )
    parser.add_argument(
        "--autoversion",
        help="Automatic version update for project version",
        choices=["patch", "minor", "major"],
    )
    parser.add_argument(
        "--build",
        help="Does build task according to the specified argument.",
        action="append",
        choices=list(build_hooks.keys()),
    )
    parser.add_argument(
        "--clean",
        help="""Do cleanup according to specified argument. If engine is provided, old engine installations will be cleared
    If workspace is provided, workspace will be reset with latest changes from current branch (not revertible)""",
        choices=["engine", "workspace"],
    )
    parser.add_argument(
        "--config",
        help=f"Path of config XML file. If not provided, ./{default_config_name} is used as default",
        default=default_config_name,
    )
    parser.add_argument(
        "--publish",
        help="Publishes a playable build with provided build type",
        const="default",
        nargs="?",
    )
    parser.add_argument(
        "--debugpath", help="If provided, PBSync will run in provided path"
    )
    parser.add_argument(
        "--debugbranch",
        help="If provided, PBSync will use provided branch as expected branch",
    )

    if len(argv) > 0:
        args = parser.parse_args(argv)
    else:
        pblog.error("At least one valid argument should be passed!")
        pblog.error("Did you mean to launch UpdateProject?")
        input("Press enter to continue...")
        error_state(hush=True, term=True)

    if not (args.debugpath is None):
        # Work on provided debug path
        os.chdir(str(args.debugpath))

    # Parser function object for PBSync config file
    def pbsync_config_parser_func(root):
        config_args_map = {
            "supported_git_version": ("git/version", None, None, True),
            "supported_lfs_version": ("git/lfsversion", None, None, True),
            "supported_gcm_version": ("git/gcmversion", None, None, True),
            "expected_branch_names": (
                "git/expectedbranch",
                None if args.debugbranch is None else [str(args.debugbranch)],
                ["main"],
                False,
            ),
            "git_url": ("git/url", None, None, True),
            "branches": ("git/branches/branch", None, ["main"], False),
            "log_file_path": ("log/file", None, "pbsync_log.txt", True),
            "user_config": ("project/userconfig", None, ".user-sync", True),
            "ci_config": ("project/ciconfig", None, ".ci-sync", True),
            "uev_default_bundle": ("versionator/defaultbundle", None, "editor", True),
            "uev_ci_bundle": ("versionator/cibundle", None, "engine", True),
            "engine_base_version": ("project/enginebaseversion", None, "", True),
            "uproject_name": ("project/uprojectname", None, None, True),
            "defaultgame_path": (
                "project/defaultgameinipath",
                None,
                "Config/DefaultGame.ini",
                True,
            ),
            "package_pdbs": ("project/packagepdbs", None, False, True),
            "publish_publishers": ("publish/publisher", None, [], False),
            "publish_stagedir": ("publish/stagedir", None, "Saved/StagedBuilds", True),
            "dispatch_config": ("dispatch/config", None, "", True),
            "butler_project": ("butler/project", None, "", True),
            "butler_manifest": ("butler/manifest", None, "", True),
            "steamcmd_script": ("steamcmd/script", None, "", True),
            "steamdrm_appid": ("steamcmd/drm/appid", None, "", True),
            "steamdrm_targetbinary": ("steamcmd/drm/targetbinary", None, "", True),
            "steamdrm_useonprem": ("steamcmd/drm/useonprem", None, False, True),
            "resharper_version": ("resharper/version", None, "", True),
            "engine_prefix": ("versionator/engineprefix", None, "", True),
            "engine_type": ("versionator/enginetype", None, None, True),
            "uses_gcs": ("versionator/uses_gcs", None, False, True),
            "uses_longtail": ("versionator/uses_longtail", None, False, True),
            "git_instructions": (
                "msg/git_instructions",
                None,
                "https://github.com/ProjectBorealis/PBCore/wiki/Prerequisites",
                True,
            ),
            "support_channel": ("msg/support_channel", None, None, True),
        }

        missing_keys = []
        config_map = {}
        for key, val in config_args_map.items():
            tag, override, default, is_single = val
            if override is not None:
                config_map[key] = override
                continue
            el = root.findall(tag)
            if el:
                el = [e.text if e.text else "" for e in root.findall(tag)]
                size = len(el)
                optional = size > 0
                if size == 1 and is_single:
                    # if there is just one key, use it
                    el = el[0]
            else:
                el = default
                optional = default is not None
            if el or optional:
                config_map[key] = el
            else:
                missing_keys.append(tag)

        if missing_keys:
            raise KeyError("Missing keys: %s" % ", ".join(missing_keys))

        return config_map

    # Preparation
    config_handler(args.config, pbsync_config_parser_func)
    pblog.setup_logger(pbconfig.get("log_file_path"))

    # Do not process further if we're in an error state
    if pbtools.check_error_state():
        error_state(
            f"""Repository is currently in an error state. Please fix the issues in your workspace
        before running PBSync.\nIf you have already fixed the problem, you may remove {pbtools.error_file} from your project folder and
        run UpdateProject again.""",
            True,
        )

    if len(sys.argv) < 2:
        pblog.error("At least one valid argument should be passed!")
        pblog.error("Did you mean to launch UpdateProject?")
        input("Press enter to continue...")
        error_state(hush=True)

    # Parse args
    if not (args.printversion is None):
        printversion_handler(args.printversion, args.repository)
    if not (args.clean is None):
        clean_handler(args.clean)
    if not (args.sync is None):
        sync_handler(args.sync, args.repository)
    if not (args.autoversion is None):
        autoversion_handler(args.autoversion)
    if not (args.build is None):
        build_handler(args.build)
    if not (args.publish is None):
        publish_handler(args.publish)

    pbconfig.shutdown()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "Scripts" in os.getcwd():
        # Working directory fix for scripts calling PBSync from Scripts folder
        os.chdir("..")
    main(sys.argv[1:])

