import configparser
import contextlib
import glob
import json
import os
import platform
import re
import shutil
import time
import zipfile
from functools import lru_cache
from pathlib import Path
from shutil import disk_usage, move, rmtree
from urllib.parse import urlparse

from pbpy import pbconfig, pbgit, pbhttp, pbinfo, pblog, pbtools, pbuac

# Those variable values are not likely to be changed in the future, it's safe to keep them hardcoded
uev_prefix = "uev:"
uplugin_ext = ".uplugin"
uproject_ext = ".uproject"
uplugin_version_key = "VersionName"
uproject_version_key = "EngineAssociation"
project_version_key = "ProjectVersion="
ddc_folder_name = "DerivedDataCache"
uev_user_config = "ue4v-user"

engine_installation_folder_regex = [r"[0-9].[0-9]{2}.*-", r"-[0-9]{8}"]

p4merge_path = "/p4merge/p4merge.exe"

reg_path = r"HKCU\Software\Epic Games\Unreal Engine\Builds"

long_path = "\\\\?\\"


@lru_cache()
def get_engine_version_prefix():
    return pbconfig.get("engine_prefix")


@lru_cache()
def get_editor_program():
    return "UnrealEditor" if is_ue5() else "UE4Editor"


@lru_cache()
def get_exe_ext():
    return ".exe" if os.name == "nt" else ""


@lru_cache()
def get_dll_ext():
    return ".dll" if os.name == "nt" else ".so"


@lru_cache()
def get_sym_ext(force=False):
    return ".pdb" if os.name == "nt" else ".sym" if force else ""


@lru_cache()
def get_editor_relative_path():
    return (
        f"Engine/Binaries/{get_platform_name()}/{get_editor_program()}{get_exe_ext()}"
    )


@lru_cache()
def get_editor_path():
    return get_engine_base_path() / Path(get_editor_relative_path())


def get_plugin_version(plugin_name):
    plugin_root = f"Plugins/{plugin_name}"
    for uplugin_path in glob.glob(f"{plugin_root}/*{uplugin_ext}"):
        with open(uplugin_path) as uplugin_file:
            data = json.load(uplugin_file)
            version = data[uplugin_version_key]
            # Some plugins have strange versions with only major and minor versions, add patch version for
            # compatibility with package managers
            if version.count(".") == 1:
                version += ".0"
            return version
    return None


@lru_cache()
def get_user_version():
    return pbconfig.get_user("project", "version", "latest")


def is_using_custom_version():
    return get_user_version() != "latest"


@lru_cache()
def get_latest_project_version():
    try:
        with open(pbconfig.get("defaultgame_path")) as ini_file:
            for ln in ini_file:
                if ln.startswith(project_version_key):
                    return ln.replace(project_version_key, "").rstrip()
    except Exception as e:
        pblog.exception(str(e))
        return None
    return None


@lru_cache()
def get_project_version():
    # first check if the user selected their own version
    user_version = get_user_version()
    if user_version != "latest":
        return user_version

    return get_latest_project_version()


def set_project_version(version_string, new_project_version):
    temp_path = "tmpProj.txt"
    # Create a temp file, do the changes there, and replace it with actual file
    try:
        with open(pbconfig.get("defaultgame_path")) as ini_file:
            with open(temp_path, "wt") as fout:
                if new_project_version:
                    for ln in ini_file:
                        if "[/Script/EngineSettings.GeneralProjectSettings]" in ln:
                            fout.write(f"{ln}{project_version_key}{version_string}\n")
                        else:
                            fout.write(ln)
                else:
                    for ln in ini_file:
                        if project_version_key in ln:
                            fout.write(f"{project_version_key}{version_string}\n")
                        else:
                            fout.write(ln)
        os.remove(pbconfig.get("defaultgame_path"))
        move(temp_path, pbconfig.get("defaultgame_path"))
    except Exception as e:
        pblog.exception(str(e))
        return False
    return True


def set_engine_version(version_string):
    temp_path = "tmpEng.txt"
    try:
        # Create a temp file, do the changes there, and replace it with actual file
        with open(pbconfig.get("uproject_name")) as uproject_file:
            with open(temp_path, "wt") as fout:
                for ln in uproject_file:
                    if uproject_version_key in ln:
                        fout.write(
                            f'\t"{uproject_version_key}": "{uev_prefix}{version_string}",\n'
                        )
                    else:
                        fout.write(ln)
        os.remove(pbconfig.get("uproject_name"))
        move(temp_path, pbconfig.get("uproject_name"))
    except Exception as e:
        pblog.exception(str(e))
        return False
    return True


def project_version_increase(increase_type):
    increase_type = increase_type.lower()
    project_version = get_project_version()
    new_project_version = False
    if project_version is None:
        project_version = "0.0.0"
        new_project_version = True

    # Split patch, minor and major versions into an array
    version_split = project_version.split(".")

    if len(version_split) != 3:
        pblog.error("Incorrect project version detected")
        return False
    if increase_type == "patch":
        new_version = (
            f"{version_split[0] }.{version_split[1]}.{str(int(version_split[2]) + 1)}"
        )
    elif increase_type == "minor":
        new_version = f"{version_split[0] }.{str(int(version_split[1]) + 1)}.0"
    elif increase_type == "major":
        new_version = f"{str(int(version_split[2]) + 1)}.0.0"
    else:
        return False

    pblog.info(f"Project version will be increased to {new_version}")
    return set_project_version(new_version, new_project_version)


@lru_cache()
def get_engine_prefix() -> str:
    return f"{pbconfig.get('engine_base_version')}-{get_engine_version_prefix()}"


@lru_cache()
def get_engine_association():
    with open(pbconfig.get("uproject_name")) as uproject_file:
        data = json.load(uproject_file)
        engine_association = str(data[uproject_version_key])
        return engine_association


@lru_cache()
def get_engine_version():
    try:
        engine_association = get_engine_association()
        if not engine_association.startswith(uev_prefix):
            # not managed by ueversionator
            return None
        build_version = engine_association.replace(
            f"{uev_prefix}{get_engine_prefix()}-", ""
        )

        if "}" in build_version:
            # Means we're using local build version in .uproject file
            return None

        return build_version
    except Exception as e:
        pblog.exception(str(e))
        return None


@lru_cache()
def get_engine_version_with_prefix():
    engine_ver_number = get_engine_version()
    if engine_ver_number is not None:
        return f"{get_engine_prefix()}-{engine_ver_number}"
    return None


use_source_dir = True


@lru_cache()
def get_engine_install_root(prompt=True) -> str | None:
    engine_version = get_engine_version_with_prefix()
    if not engine_version:
        return None
    source_dir = (
        pbconfig.get_user(uev_user_config, "source_dir") if use_source_dir else None
    )
    root = source_dir or pbconfig.get_user(uev_user_config, "download_dir")
    if root is None and prompt:
        curdir = Path().resolve()

        if pbconfig.get("is_ci"):
            # make sure we actually have a managed install
            engine_ver = get_engine_version()
            if not engine_ver:
                return None
            if os.name == "nt":
                directory = (Path(curdir.anchor) / get_engine_type_folder()).resolve()
            else:
                directory = (curdir.parent / get_engine_type_folder()).resolve()
            directory.mkdir(exist_ok=True)
            return str(directory)

        print(
            "========================================================================="
        )
        print(
            "| A custom Unreal Engine build needs to be downloaded for this project. |"
        )
        print(
            "|   These builds can be quite large. Lots of disk space is required.    |"
        )
        print(
            "=========================================================================\n"
        )
        print(f">>>>> Project path: {curdir}\n")
        print("Which directory should these engine downloads be stored in?\n")

        options = []

        # parent directory
        options.append(curdir.parent)

        # home directory
        options.append(Path.home())

        # drive
        if os.name == "nt":
            options.append(Path(curdir.anchor))

        # go into ue folder
        options = [(path / get_engine_type_folder()).resolve() for path in options]

        # remove duplicates
        options = list(set(options))

        # add custom option
        custom = "custom location"
        options.append(custom)

        for i, option in enumerate(options):
            print(f"{i + 1}) {option}")

        directory = None
        while True:
            response = input(f"\nSelect an option (1-{len(options)}) and press enter: ")
            try:
                choice = int(response) - 1
                if choice >= 0 and choice < len(options):
                    directory = options[choice]
                    if directory == custom:
                        try:
                            response = input("\nCustom location: ")
                            directory = Path(response.strip()).resolve()
                            if directory.is_relative_to(curdir):
                                print(
                                    "download directory cannot reside in the project directory"
                                )
                                continue
                        except Exception as e:
                            pblog.exception(str(e))
                            directory = None

                    if directory:
                        try:
                            directory.mkdir(exist_ok=True)
                            break
                        except Exception as e:
                            pblog.exception(str(e))
                            continue
            except ValueError:
                print("\n")

            pblog.error(f"Invalid option {response}. Try again:\n")
        if directory:
            root = str(directory)
            pbconfig.get_user_config()[uev_user_config]["download_dir"] = root
    return root


@lru_cache()
def is_source_install():
    engine_version = get_engine_version()
    root = get_engine_install_root(prompt=engine_version is not None)
    if root is None:
        return False
    return (Path(root) / ".git").exists()


@lru_cache()
def uses_longtail():
    return pbconfig.get("uses_longtail")


def check_ue_file_association():
    if os.name == "nt":
        file_assoc_result = pbtools.get_combined_output(["assoc", uproject_ext])
        return "Unreal.ProjectFile" in file_assoc_result
    else:
        return True


def check_ddc_folder_created():
    ddc_path = os.path.join(os.getcwd(), ddc_folder_name)
    return os.path.isdir(ddc_path)


def generate_ddc_data():
    pblog.info(
        "Generating DDC data, please wait... (This may take up to one hour only for the initial run)"
    )
    current_version = get_engine_version_with_prefix()
    if current_version is not None:
        installation_dir = str(get_engine_base_path())
        if os.path.isdir(installation_dir):
            ue_editor_executable = os.path.join(
                installation_dir, get_editor_relative_path()
            )
            if os.path.isfile(ue_editor_executable):
                err = pbtools.run(
                    [
                        str(ue_editor_executable),
                        str(Path(pbconfig.get("uproject_name")).resolve()),
                        "-run=DerivedDataCache",
                        "-fill",
                    ]
                ).returncode
                if err == 0:
                    pblog.info(f"DDC generate command has exited with {err}")
                else:
                    pblog.error(f"DDC generate command has exited with {err}")
                if not check_ddc_folder_created():
                    pbtools.error_state(
                        f"DDC folder doesn't exist. Please get support from {pbconfig.get('support_channel')}"
                    )
                pblog.info("DDC data successfully generated!")
                return
        pbtools.error_state(
            f"Engine installation not found. Please get support from {pbconfig.get('support_channel')}"
        )
    pbtools.error_state(
        f"Error occurred while reading project version for DDC data generation. Please get support from {pbconfig.get('support_channel')}"
    )


def clean_old_engine_installations(keep=1):
    current_version = get_engine_version_with_prefix()
    regex_pattern = (
        engine_installation_folder_regex[0]
        + get_engine_version_prefix()
        + engine_installation_folder_regex[1]
    )
    p = re.compile(regex_pattern)
    if current_version is not None:
        engine_install_root = get_engine_install_root()
        # if the user has defined a source dir, find the download_dir instead
        source_dir = (
            pbconfig.get_user(uev_user_config, "source_dir") if use_source_dir else None
        )
        if source_dir is not None:
            engine_install_root = pbconfig.get_user(uev_user_config, "download_dir")
        elif is_source_install():
            return True
        if engine_install_root is not None and os.path.isdir(engine_install_root):
            pblog.info(
                f"Keeping the last {keep} engine versions and removing the rest."
            )
            folders = os.listdir(engine_install_root)
            for i in range(0, len(folders) - keep):
                folder = folders[i]
                # Do not remove folders if they do not match with installation folder name pattern
                # Also do not remove files. Only remove folders
                full_path = os.path.join(engine_install_root, folder)
                if (
                    folder != current_version
                    and p.match(folder) is not None
                    and os.path.isdir(full_path)
                ):
                    pblog.info(f"Removing old engine installation: {str(full_path)}...")
                    try:
                        rmtree(full_path, ignore_errors=True)
                    except Exception as e:
                        pblog.exception(str(e))
                        pblog.error(
                            f"Something went wrong while removing engine folder {str(full_path)}. Please try removing it manually."
                        )
            # also remove lingering 7z files
            for archive in Path(engine_install_root).glob("*.7z"):
                pblog.info(f"Removing unused engine archive: {str(archive)}...")
                try:
                    archive.unlink()
                except Exception as e:
                    pblog.exception(str(e))
                    pblog.error(
                        f"Something went wrong while removing archive folder {str(archive)}. Please try removing it manually."
                    )
            return True

    return False


@lru_cache()
def get_versionator_gs_base(fallback=None):
    if pbconfig.get("uses_gcs") == "True":
        try:
            uev_config = configparser.ConfigParser()
            uev_config.read(".ueversionator")
            baseurl = uev_config.get("ueversionator", "baseurl", fallback=fallback)
            if baseurl:
                domain = urlparse(baseurl).hostname
                return domain
        except Exception as e:
            pblog.exception(str(e))
    return None


@lru_cache()
def get_versionator_gsuri(fallback=None):
    domain = get_versionator_gs_base(fallback)
    if not domain:
        return None
    return f"gs://{domain}/"


@lru_cache
def get_ddc_url(fallback=None, upload=False):
    if pbconfig.get("uses_gcs") == "True":
        try:
            uev_config = configparser.ConfigParser()
            uev_config.read(".ueversionator")
            baseurl = uev_config.get(
                "ddc", "uploadurl" if upload else "baseurl", fallback=fallback
            )
            return baseurl
        except Exception as e:
            pblog.exception(str(e))
    return None


@lru_cache()
def get_ddc_bucket(fallback=None):
    baseurl = get_ddc_url(fallback=fallback)
    if baseurl:
        domain = urlparse(baseurl).hostname
        return domain
    return None


@lru_cache()
def get_ddc_gsuri(fallback=None):
    bucket = get_ddc_bucket(fallback=fallback)
    if bucket:
        return f"gs://{bucket}/"
    return None


@lru_cache()
def is_versionator_symbols_enabled():
    symbols = pbconfig.get_user_config().getboolean(
        uev_user_config, "symbols", fallback=True if pbconfig.get("is_ci") else None
    )
    if symbols is not None:
        return symbols

    # Symbols configuration variable is not on the file, let's add it
    response = input(
        f"Do you want to download debugging symbols for accurate crash logging? You can change this setting later in the {pbconfig.get('user_config')} config file. [y/N] "
    )
    should_use_symbols = len(response) > 0 and response[0].lower() == "y"
    pbconfig.get_user_config()[uev_user_config]["symbols"] = (
        "true" if should_use_symbols else "false"
    )
    return should_use_symbols


@lru_cache()
def get_engine_type():
    return pbconfig.get("engine_type")


@lru_cache()
def is_ue5():
    return get_engine_type() == "ue5"


@lru_cache()
def get_engine_type_folder():
    return "ue" if is_ue5() else "ue4"


@lru_cache()
def get_bundle_verification_file(bundle_name):
    if bundle_name and "engine" in bundle_name:
        unreal_game = "UnrealGame" if is_ue5() else "UE4Game"
        return f"Engine/Binaries/{get_platform_name()}/{unreal_game}"
    else:
        return f"Engine/Binaries/{get_platform_name()}/{get_editor_program()}"


@lru_cache()
def get_engine_base_path() -> Path | None:
    root = get_engine_install_root()
    if root is None:
        installed_path = Path(
            "C:\\ProgramData\\Epic\\UnrealEngineLauncher\\LauncherInstalled.dat"
        )
        with open(str(installed_path)) as f:
            installed = json.load(f)
            for install in installed["InstallationList"]:
                if (
                    install["NamespaceId"] == "ue"
                    and not install["AppVersion"].endswith("UserContent-Windows")
                    and install["AppVersion"].startswith(get_engine_association())
                ):
                    return Path(install["InstallLocation"])
        return None
    root_path = Path(root)
    if is_source_install():
        return root_path
    if uses_longtail():
        return root_path / get_bundle()
    version = get_engine_version()
    if version is not None:
        version = get_engine_version_with_prefix()
        return root_path / version
    return None


@lru_cache()
def get_unreal_version_selector_path():
    if get_engine_version() is None:
        ftype_info = pbtools.get_one_line_output(["ftype", "Unreal.ProjectFile"])
        if ftype_info is not None:
            ftype_split = ftype_info.split('"')
            if len(ftype_split) == 5:
                return Path(ftype_split[1])
        return None
    else:
        base_path = get_engine_base_path()
        return base_path / Path(
            f"Engine/Binaries/{get_platform_name()}/UnrealVersionSelector-{get_platform_name()}-Shipping{get_exe_ext()}"
        )


def run_unreal_setup():
    base_path = get_engine_base_path()
    pblog.info("Installing Unreal Engine prerequisites (requires admin permission)")
    prereq_exe = "UEPrereqSetup_x64" if is_ue5() else "UE4PrereqSetup_x64"
    prereq_path = base_path / Path(
        f"Engine/Extras/Redist/en-us/{prereq_exe}{get_exe_ext()}"
    )
    pbtools.run([str(prereq_path), "/quiet"])
    pblog.info("Registering Unreal Engine file associations")
    selector_path = get_unreal_version_selector_path()
    cmdline = [selector_path, "/fileassociations"]
    pblog.info(
        "Requesting admin permission to register Unreal Engine file associations..."
    )
    if not pbuac.isUserAdmin():
        time.sleep(1)
        try:
            pbuac.runAsAdmin(cmdline)
        except OSError:
            pblog.error("User declined permission. Automatic install failed.")
    else:
        pbtools.run(cmdline)


@lru_cache()
def get_uproject_path():
    return Path(pbconfig.get("uproject_name")).resolve()


def generate_project_files():
    pbtools.run(
        [
            str(get_unreal_version_selector_path()),
            "/projectfiles",
            str(get_uproject_path()),
        ]
    )
    # TODO: grab latest UnrealVersionSelector log from Saved\Logs, and print it out?


gb_multiplier = 1000 * 1000 * 1000
gb_div = 1.0 / gb_multiplier


def parse_reg_query(proc):
    query = proc.stdout.splitlines()
    for res in query:
        if res.startswith("    "):
            key, rtype, value = res.split("    ")[1:]
            yield key, rtype, value


def register_engine(version, path):
    if os.name == "nt":
        # query if this path is used elsewhere, if so, we delete it
        for key, rtype, value in parse_reg_query(
            pbtools.run_with_combined_output(
                ["reg", "query", reg_path, "/f", path, "/e", "/t", "REG_SZ"]
            )
        ):
            # if we already have this version, no need to reregister it
            if key == version:
                return False
            pbtools.run(["reg", "delete", reg_path, "/v", key, "/f"])
        pbtools.run(
            ["reg", "add", reg_path, "/f", "/v", version, "/t", "REG_SZ", "/d", path]
        )
        return True


longtail_path = "\\longtail\\longtail.exe"


@lru_cache()
def get_bundle():
    bundle_name = (
        pbconfig.get("uev_ci_bundle")
        if pbconfig.get("is_ci")
        else pbconfig.get("uev_default_bundle")
    )
    bundle_name = pbconfig.get_user("project", "bundle", default=bundle_name)
    return bundle_name


def download_engine(bundle_name=None, download_symbols=False):
    version = get_engine_version_with_prefix()
    legacy_archives = not uses_longtail()

    if version is None:
        return True

    engine_id = f"{uev_prefix}{version}"

    root = get_engine_install_root()

    if is_source_install():
        branch = pbtools.get_combined_output(
            [pbgit.get_git_executable(), "-C", str(root), "branch", "--show-current"]
        )
        base_branch = get_engine_prefix()
        if not branch.startswith(base_branch):
            pbtools.run(
                [pbgit.get_git_executable(), "-C", str(root), "switch", base_branch]
            )
        pbtools.run(
            [
                pbgit.get_git_executable(),
                "-C",
                str(root),
                "pull",
                "--rebase",
                "--autostash",
            ]
        )
        pbtools.run(
            [
                pbgit.get_git_executable(),
                "-C",
                str(root),
                "submodule",
                "update",
                "--init",
                "--recursive",
            ]
        )
        registered = register_engine(engine_id, root)
        if registered or not check_ue_file_association():
            run_unreal_setup()
        if registered or not get_sln_path().exists():
            generate_project_files()
        return True

    is_ci = pbconfig.get("is_ci")

    if root is not None:
        # create install dir if doesn't exist
        os.makedirs(root, exist_ok=True)

        root_path = Path(root)
        base_path = get_engine_base_path()

        if not legacy_archives:
            download_symbols = True
            pblog.success(
                "Using new Longtail incremental delivery method for engine update."
            )

        verification_file = get_bundle_verification_file(bundle_name)
        editor_verification = get_bundle_verification_file("editor")
        engine_verification = get_bundle_verification_file("engine")
        symbols_path = base_path / Path(f"{editor_verification}{get_sym_ext()}")
        needs_symbols = download_symbols and not symbols_path.exists()
        exe_path = base_path / Path(f"{verification_file}{get_exe_ext()}")
        needs_exe = not exe_path.exists()

        is_upgrade = False
        if legacy_archives:
            # handle downgrading to non-engine bundles
            if "engine" not in bundle_name:
                game_exe_path = base_path / Path(
                    f"{engine_verification}{get_exe_ext()}"
                )
                if game_exe_path.exists():
                    needs_exe = True
                    needs_symbols = download_symbols
                    shutil.rmtree(str(base_path), ignore_errors=True)
        else:
            # if we already have the files we need, we're only doing an in-place upgrade.
            is_upgrade = not needs_exe and not needs_symbols
            # reset because for longtail we always try to download latest to check for updates
            needs_exe = True
            needs_symbols = download_symbols

        if needs_exe or needs_symbols:
            if not is_ci and os.path.isdir(root):
                # 1GB for in-place upgrade (scratch space?), maybe not correct but should be close enough
                required_free_gb = (
                    30 if legacy_archives else 1 if is_upgrade else 22
                )  # extracted
                required_free_gb += (
                    9 if legacy_archives else 0 if is_upgrade else 7
                )  # archive

                if needs_symbols:
                    required_free_gb += (
                        80 if legacy_archives else 0 if is_upgrade else 57
                    )  # extracted
                    required_free_gb += (
                        23 if legacy_archives else 0 if is_upgrade else 17
                    )  # archive

                required_free_space = required_free_gb * gb_multiplier

                total, used, free = disk_usage(root)

                if free < required_free_space:
                    pblog.warning(
                        "Not enough free space. Cleaning old engine installations before download."
                    )
                    clean_old_engine_installations()
                    total, used, free = disk_usage(root)
                    if free < required_free_space:
                        pblog.error(
                            f"You do not have enough available space to install the engine. Please free up space on {Path(root).anchor}"
                        )
                        available_gb = free * gb_div
                        pblog.error(f"Available space: {available_gb:.2f}GB")
                        pblog.error(f"Total install size: {required_free_gb}GB")
                        must_free = required_free_gb - available_gb
                        pblog.error(f"Required space: {must_free:.2f}GB")
                        pbtools.error_state()

    # Extract with ueversionator
    if (needs_exe or needs_symbols) and legacy_archives:
        command_set = [f"ueversionator{get_exe_ext()}"]

        command_set.append("-assume-valid")
        command_set.append("-user-config")
        command_set.append(pbconfig.get("user_config"))

        if bundle_name is not None:
            command_set.append("-bundle")
            command_set.append(str(bundle_name))

        if is_ue5():
            command_set.append("-ue5")
            command_set.append("-basedir")
            command_set.append("ue5")

        if is_ci:
            # If we're CI, write our environment variable to user config
            user_config = pbconfig.get_user_config()
            for section in user_config.sections():
                for key in list(user_config[section].keys()):
                    val = pbconfig.get_user(section, key)
                    if val:
                        user_config[section][key] = val
                    else:
                        user_config.remove_option(section, key)
            with open(pbconfig.get("user_config"), "w") as user_config_file:
                pbconfig.get_user_config().write(user_config_file)

        if pbtools.run(command_set).returncode != 0:
            return False
    else:
        ensure_ue_closed()
        gcs_bucket = get_versionator_gsuri()
        # TODO: maybe cache out Saved and Intermediate folders?
        # current legacy archive behavior obviously doesn't keep them for new installs, but we could now
        # have to copy them out and then copy them back in

        # query build version so we can bump it up
        build_version_path = base_path / "Engine" / "Build" / "Build.version"

        branch_version = None
        if build_version_path.exists():
            with open(build_version_path) as f:
                build_version = json.load(f)
                branch_version = build_version.get("BranchName")

        if branch_version:
            pblog.info(
                f"Comparing target engine version {get_engine_version_with_prefix()} with local engine version {branch_version}"
            )

        if get_engine_version_with_prefix() == branch_version:
            # fast version
            proc = pbtools.run_stream(
                [
                    pbinfo.format_repo_folder(longtail_path),
                    "get",
                    "--source-path",
                    f"{gcs_bucket}lt/{bundle_name}/{version}.json",
                    "--target-path",
                    str(base_path),
                    "--cache-path",
                    f"Saved/longtail/cache/{bundle_name}",
                    "--enable-file-mapping",
                ],
                env={"GOOGLE_APPLICATION_CREDENTIALS": "Build/credentials.json"},
                logfunc=pbtools.progress_stream_log,
            )
        else:
            # verify a new install
            proc = pbtools.run_stream(
                [
                    pbinfo.format_repo_folder(longtail_path),
                    "get",
                    "--source-path",
                    f"{gcs_bucket}lt/{bundle_name}/{version}.json",
                    "--target-path",
                    str(base_path),
                    "--cache-path",
                    f"Saved/longtail/cache/{bundle_name}",
                    "--no-cache-target-index",
                    "--validate",
                    "--enable-file-mapping",
                ],
                env={"GOOGLE_APPLICATION_CREDENTIALS": "Build/credentials.json"},
                logfunc=pbtools.progress_stream_log,
            )
        # print out a newline
        print("")
        if proc.returncode:
            pbtools.error_state(
                f"Failed to download engine update. Make sure your system time is synced. If this issue persists, please request help in {pbconfig.get('support_channel')}."
            )
        # TODO: similarly, have to copy PDBs out into a store so longtail doesn't touch the engine and delete everything but symbols
        if download_symbols:
            pblog.warning(
                "Symbols download not supported with incremental delivery at this time."
            )
        if not register_engine(engine_id, get_engine_base_path()):
            needs_exe = False

    # if not CI, run the setup tasks
    if root is not None and not is_ci and needs_exe:
        run_unreal_setup()
        # generate project files for developers
        if not pbgit.is_on_expected_branch():
            generate_project_files()

    return True


class multi_dict(dict):
    def __setitem__(self, key, value):
        if isinstance(value, list) and key in self:
            self[key].extend(value)
        else:
            super().__setitem__(key, value)

    def force_set(self, key, value):
        super().__setitem__(key, value)


@contextlib.contextmanager
def ue_config(path):
    config = pbconfig.MultiConfigParser(
        allow_no_value=True,
        delimiters=("=",),
        strict=False,
        comment_prefixes=(";",),
        dict_type=multi_dict,
        interpolation=configparser.Interpolation(),
    )
    # case sensitive
    config.optionxform = lambda option: option
    write_config = True
    if os.path.exists(path):
        try:
            config.read(path)
        except UnicodeDecodeError:
            try:
                with open(path, "r", encoding="utf-16") as f:
                    config.readfp(f)
            except Exception as e:
                pblog.error(f"Unreal config parsing failed for {path}. Skipping.")
                pblog.exception(str(e))
                write_config = False
    try:
        yield config
    finally:
        if write_config:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w+") as ini_file:
                config.write(ini_file, space_around_delimiters=False)


def update_source_control():
    with ue_config(
        "Saved/Config/Windows/SourceControlSettings.ini"
    ) as source_control_config:
        source_control_config["SourceControl.SourceControlSettings"][
            "Provider"
        ] = "Git LFS 2"
        git_lfs_2 = source_control_config["GitSourceControl.GitSourceControlSettings"]
        binary_path = pbgit.get_git_executable()
        if binary_path != "git":
            git_lfs_2["BinaryPath"] = binary_path
        else:
            git_paths = [path for path in pbtools.whereis("git") if "cmd" in path.parts]
            if len(git_paths) > 0:
                git_lfs_2["BinaryPath"] = str(git_paths[0].resolve())
        git_lfs_2["UsingGitLfsLocking"] = "True"
        username, _ = pbgit.get_credentials()
        if username:
            git_lfs_2["LfsUserName"] = username
        else:
            pblog.warning(
                f"Credential retrieval failed. Please get help from {pbconfig.get('support_channel')}."
            )
    with ue_config(
        "Saved/Config/Windows/EditorPerProjectUserSettings.ini"
    ) as editor_config:
        p4merge = str(Path(pbinfo.format_repo_folder(p4merge_path)).resolve())
        editor_config["/Script/UnrealEd.EditorLoadingSavingSettings"][
            "TextDiffToolPath"
        ] = f'(FilePath="{p4merge}")'


# we will either error out, or succeed, so this won't matter
@lru_cache()
def is_ue_closed():
    # check if there is a UE running at all
    p = pbtools.get_running_process(get_editor_program())
    if p is None:
        # ue is not running at all
        return True
    # cheap check for our engine
    version = get_engine_version()
    if version is not None:
        root = get_engine_install_root()
        if root is None:
            root = get_engine_base_path()
        if root is not None:
            exe = Path(p.info["exe"])
            root = Path(root)
            if not exe.is_relative_to(root):
                # not our engine
                return True
    # finally, do an expensive open files check to ensure the project is open
    files = p.open_files()
    project_path = Path().resolve()
    found_project = False
    for file in files:
        path = Path(file.path)
        if path.is_relative_to(project_path):
            found_project = True
            break
    # if we didn't get any files, something went wrong, so take the safe route
    if len(files) < 1 or found_project:
        return False
    return True


def ensure_ue_closed():
    if not is_ue_closed():
        pbtools.error_state(
            "Unreal Editor is currently running. Please close it before running PBSync. It may be listed only in Task Manager as a background process. As a last resort, you should log off and log in again."
        )


@lru_cache()
def get_base_name():
    project_path = get_uproject_path()
    return project_path.stem


@lru_cache()
def get_sln_path():
    return Path(get_base_name() + ".sln")


@lru_cache()
def get_vs_basepath():
    return pbtools.get_one_line_output(
        [
            "%ProgramFiles(x86)%\\Microsoft Visual Studio\\Installer\\vswhere.exe",
            "-prerelease",
            "-latest",
            "-products",
            "*",
            "-property",
            "installationPath",
        ]
    )


@lru_cache()
def get_devenv_path():
    vs_basepath = get_vs_basepath()
    if vs_basepath:
        return Path(vs_basepath, "Common7", "IDE", "devenv.exe")
    return None


@lru_cache()
def get_uat_path():
    base = get_engine_base_path()
    if base is not None:
        return base / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat"
    return None


def fill_ddc():
    pbtools.run(
        [
            get_editor_path(),
            get_uproject_path(),
            "-DDC=EnumerateForS3DDC",
            "-execcmds=Automation RunTests FillDDCForPIETest",
        ]
    )


def upload_cloud_ddc():
    uat_path = get_uat_path()
    if uat_path is None:
        pbtools.error_state("Unable to determine location of Unreal Automation Tool.")
        return

    credentials = str(Path("Build/credentials").resolve())
    access_logs = str(Path("Saved/AccessLogs").resolve())
    cache = Path("DerivedDataCache")
    root = get_engine_base_path()
    cache = str(cache.resolve())
    manifest = str(Path("Build/DDC.json").resolve())
    manifest = os.path.relpath(manifest, start=root)
    # for some reason https://storage.googleapis.com doesn't work, so we have to settle for domain-named nesting...
    # we upload to a ServiceURL bucket.com -> storage.googleapis.com/bucket.com
    # but AWS takes a Bucket and ServiceURL, we use the bucket.com's "bucket": bucket.com/bucket.com -> storage.googleapis.com/bucket.com/bucket.com
    proc = pbtools.run_stream(
        [
            uat_path,
            "UploadDDCToAWS",
            f"-Bucket={get_ddc_bucket()}",
            f"-CredentialsFile={credentials}",
            "-CredentialsKey=default",
            f"-CacheDir={cache}",
            f"-FilterDir={access_logs}",
            f"-Manifest={manifest}",
            f"-ServiceURL={get_ddc_url(upload=True)}",
            "-utf8output",
        ]
    )
    if proc.returncode:
        pbtools.error_state("Upload failed.")


def build_source(for_distribution=True):
    global use_source_dir
    engine_version = get_engine_version()
    if engine_version:
        bundle_name = (
            pbconfig.get("uev_ci_bundle")
            if pbconfig.get("is_ci")
            else pbconfig.get("uev_default_bundle")
        )
        bundle_name = pbconfig.get_user("project", "bundle", default=bundle_name)
        symbols_needed = is_versionator_symbols_enabled()
        if for_distribution:
            use_source_dir = False
            pblog.info("Setting installed engine for distribution binaries.")
            engine_version = get_engine_version_with_prefix()
            if engine_version:
                download_engine(bundle_name, symbols_needed)
    base = get_engine_base_path()
    ubt = base / "Engine" / "Build" / "BatchFiles"
    platform = get_platform_name()
    if platform == "Linux" or platform == "Mac":
        ubt = ubt / platform / "Build.sh"
    else:
        ubt = ubt / "Build.bat"
    proc = pbtools.run_stream(
        [
            ubt,
            platform,
            "Development",
            f"-project={str(get_uproject_path())}",
            "-TargetType=Editor",
        ],
        logfunc=lambda x: pbtools.checked_stream_log(
            x, error="error ", warning="warning "
        ),
    )
    if not use_source_dir:
        pblog.warning(
            "You will need to run --sync engine in a new session to restore your original engine."
        )
    if proc.returncode:
        pbtools.error_state("Build failed.")


def clear_cook_cache():
    shutil.rmtree("Saved/Cooked", ignore_errors=True)


def build_shaders(platform: str = "PCD3D_SM6"):
    uproject_path = get_uproject_path()
    project_name = uproject_path.stem
    project_path = str(uproject_path.parent)
    args = [
        str(get_editor_path()),
        str(get_uproject_path()),
        "-run=ShaderPipelineCacheTools",
        "expand",
        f"{project_path}/Saved/StagedBuilds/Windows/{project_name}/Saved/CollectedPSOs/*.rec.upipelinecache",
        f"{project_path}/Saved/Shaders/{platform}/*.shk",
        f"{project_path}/Build/Windows/PipelineCaches/PSO_{project_name}_{platform}.spc",
    ]
    pbtools.run_stream(
        args,
        logfunc=lambda x: pbtools.checked_stream_log(
            x, error="Error: ", warning="Warning: "
        ),
    )


def build_game(configuration="Shipping"):
    uat_path = get_uat_path()
    if uat_path is None:
        pbtools.error_state("Unable to determine location of Unreal Automation Tool.")
        return

    args = [
        str(uat_path),
        f"-ScriptsForProject={str(get_uproject_path())}",
        "BuildCookRun",
        "-nop4",
        "-utf8output",
        f"-project={str(get_uproject_path())}",
        f"-clientconfig={configuration}",
        "-unattended",
        "-NoCodeSign",
        "-CrashReporter",
        "-build",
        "-cook",
        "-stage",
        "-pak",
        "-iostore",
        "-compressed",
        "-iterate",
        "-iterativecooking",
        "-distribution",
        "-manifests",
        "-package",
    ]
    publishers = pbconfig.get("publish_publishers")
    if "steamcmd" in publishers:
        args.extend(["-patchpaddingalign=1048576", "-blocksize=1048576"])
    proc = pbtools.run_stream(
        args,
        logfunc=lambda x: pbtools.checked_stream_log(
            x, error="Error: ", warning="Warning: "
        ),
    )
    if proc.returncode:
        pbtools.error_state("Build failed.")


platform_names = {"Windows": "Win64", "Darwin": "Mac", "Linux": "Linux"}


@lru_cache()
def get_platform_name():
    return platform_names[platform.system()]


binaries_paths = ["Binaries", "Plugins/**/Binaries"]


def package_binaries():
    binaries_zip = Path("Binaries.zip")
    binaries_zip.unlink(missing_ok=True)
    base_path = Path(".")

    clean_binaries_folder(pbconfig.get("package_pdbs") != "True")

    hashes = dict()
    with zipfile.ZipFile("Binaries.zip", "x") as zipf:
        for binaries_path in binaries_paths:
            for file in base_path.glob(f"{binaries_path}/**/*"):
                if not file.is_file():
                    continue
                filename = str(file)
                zipf.write(filename, filename)
                hashes[filename] = pbtools.get_hash(filename)

    hashes["Binaries.zip"] = pbtools.get_hash("Binaries.zip")

    pbtools.make_json_from_dict(hashes, pbconfig.get("checksum_file"))


def inspect_source(all=False):
    if all:
        modified_files_list = "Source/**/*"
    else:
        modified_paths = pbgit.get_modified_files()
        if len(modified_paths) < 1:
            pblog.info(
                "No modified files to inspect, done. Use --build inspectall if you'd like to inspect the entire project."
            )
            return
        modified_files = [
            str(path) for path in modified_paths if str(path).startswith("Source")
        ]
        modified_files_list = ";".join(modified_files)
    version = pbconfig.get("resharper_version")
    saved_dir = Path("Saved")
    zip_name = f"JetBrains.ReSharper.CommandLineTools.{version}.zip"
    zip_path = saved_dir / Path(zip_name)
    if not zip_path.exists():
        pblog.info(f"Downloading Resharper {version}")
        url = f"https://download-cdn.jetbrains.com/resharper/dotUltimate.{version}/{zip_name}"
        pbhttp.download(url, str(zip_path))
    resharper_dir = saved_dir / Path("ResharperCLI")
    pblog.info(f"Unpacking Resharper {version}")
    shutil.unpack_archive(str(zip_path), str(resharper_dir))
    resharper_exe = resharper_dir / Path("inspectcode.exe")
    inspect_file = "Saved/InspectionResults.txt"
    pblog.info(f"Running Resharper {version}")
    proc = pbtools.run_stream(
        [
            str(resharper_exe),
            str(get_sln_path()),
            "--no-swea",
            "--properties:Platform=Win64;Configuration=Development Editor",
            f"--include={modified_files_list}",
            f"--project={get_base_name()}",
            "-f=Text",  # TODO: maybe switch to XML for more robust parsing?
            f"-o={inspect_file}",
        ]
    )
    if proc.returncode:
        pbtools.error_state("Resharper inspectcode failed.")
    has_error = False
    # TODO: make this configurable
    non_errors = [
        "Possibly unused #include directive",
        " is hidden in derived class ",
        " hides a non-virtual function from class ",
        "Possibly unintended object slicing",
        "Cannot resolve symbol",
        " can be made const",
        " does not have a 'virtual' specifier",
        "style cast is used instead of",
        "Member function can be made static",
        "can be moved to inner scope",
        "Unreachable code",
    ]
    # it is UTF-8 BOM
    with open(inspect_file, encoding="utf-8-sig") as f:
        lines = f.readlines()
        for line in lines:
            # if blank, skip
            line_strip = line.strip()
            if not line_strip:
                continue
            if line_strip.startswith("Solution ") or line_strip.startswith("Project "):
                pblog.info(line)
            elif pbtools.it_has_any(line, *non_errors):
                pblog.warning(line)
            else:
                pblog.error(line)
                has_error = True
    if has_error:
        pbtools.error_state("Resharper inspectcode found errors.")
    os.remove(inspect_file)
    shutil.rmtree(str(resharper_dir))


clean_binaries_globs = [
    f"*-*-*{get_dll_ext()}",
    f"*-*-*{get_sym_ext(True)}",
    "*.patch_*",
    "*.ilk",
]


def clean_binaries_folder(clean_pdbs):
    base_path = Path(".")
    for binaries_path in binaries_paths:
        for glob in clean_binaries_globs:
            for file in base_path.glob(f"{binaries_path}/{get_platform_name()}/{glob}"):
                file.unlink()

    if clean_pdbs:
        for binaries_path in binaries_paths:
            for pdb in base_path.glob(
                f"{binaries_path}/{get_platform_name()}/*{get_sym_ext(True)}"
            ):
                pdb.unlink()


def build_installed_build():
    if not is_source_install():
        pbtools.error_state(
            "Engine builds are only supported for source installs.", fatal_error=False
        )

    uat_path = get_uat_path()
    if uat_path is None:
        pbtools.error_state("Unable to determine location of Unreal Automation Tool.")
        return

    engine_path = get_engine_base_path()

    # query build version so we can bump it up
    build_version_path = engine_path / "Engine" / "Build" / "Build.version"

    with open(build_version_path) as f:
        build_version = json.load(f)

    changelist = build_version["Changelist"] + 1
    code_changelist = build_version["CompatibleChangelist"] + 1

    # clean up old archives
    local_builds_path = engine_path / "LocalBuilds"
    local_build_archives = local_builds_path / "Archives"
    if local_build_archives.exists():
        shutil.rmtree(local_build_archives)

    # build the installed engine
    proc = pbtools.run_stream(
        [
            str(uat_path),
            "BuildGraph",
            f"-Target=Make Installed Build {get_platform_name()}",
            "-Script=Engine/Build/InstalledEngineBuild.xml",
            "-NoP4",
            "-NoCodeSign",
            "-Set:EditorTarget=editor",
            "-Set:HostPlatformEditorOnly=true",
            "-Set:WithLinuxAArch64=false",
            "-Set:WithFeaturePacks=false",
            "-Set:WithDDC=false",
            "-Set:WithFullDebugInfo=false",
            "-utf8output",
        ],
        env={
            "IsBuildMachine": "1",
            "CI": "1",
            "uebp_CL": str(changelist),
            "uebp_CodeCL": str(code_changelist),
        },
        logfunc=lambda x: pbtools.checked_stream_log(
            x, error="Error: ", warning="Warning: "
        ),
    )

    if proc.returncode:
        pbtools.error_state("Failed to build installed engine.")

    with open(build_version_path) as f:
        build_version = json.load(f)
    # UE4 has a branch prefix, UE5 does not
    version = build_version["BranchName"].replace("++UE4+", "")

    if pbconfig.get("uses_gcs") == "True":
        if uses_longtail():
            bundle_name = pbconfig.get("uev_default_bundle")
            project_path = get_uproject_path().parent
            proc = pbtools.run_stream(
                [
                    str(project_path / pbinfo.format_repo_folder(longtail_path)),
                    "put",
                    "--source-path",
                    "Windows",
                    "--target-path",
                    f"{get_versionator_gsuri()}lt/{bundle_name}/{version}.json",
                    "--compression-algorithm",
                    "zstd_max",
                ],
                cwd=str(local_builds_path / "Engine"),
                env={
                    "GOOGLE_APPLICATION_CREDENTIALS": str(
                        project_path / "Build" / "credentials.json"
                    )
                },
                logfunc=pbtools.progress_stream_log,
            )
            # print out a new line
            print("")
        else:
            proc = pbtools.run_stream(
                [
                    "gsutil",
                    "-m",
                    "-o",
                    "GSUtil:parallel_composite_upload_threshold=100M",
                    "cp",
                    "*.7z",
                    get_versionator_gsuri(),
                ],
                cwd=str(local_build_archives),
            )

            download_dir = pbconfig.get_user(uev_user_config, "download_dir")
            if download_dir:
                download_dir = Path(download_dir)
                if not download_dir.exists():
                    download_dir.mkdir(parents=True)
                for file in local_build_archives.glob("*.7z"):
                    shutil.copy(file, download_dir)

        if proc.returncode:
            pbtools.error_state("Failed to upload installed engine.")

    if not set_engine_version(version):
        pbtools.error_state("Error while updating engine version in .uproject file")
    pblog.info(f"Successfully changed engine version as {str(version)}")

