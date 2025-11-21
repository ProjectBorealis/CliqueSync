import argparse
import json
import multiprocessing
import os
import sys
from functools import partial
from pathlib import Path

from pbpy import (
    pbbutler,
    pbconfig,
    pbdispatch,
    pbgh,
    pbgit,
    pblog,
    pbpy_version,
    pbsteamcmd,
    pbtools,
    pbunreal,
)
from pbpy.pbtools import error_state
from pbsync import actions

try:
    import pbsync_version
except ImportError:
    from pbsync import pbsync_version

default_config_name = "CliqueSync.xml"


def config_handler(config_var, config_parser_func):
    if not pbconfig.generate_config(config_var, config_parser_func):
        # Logger is not initialized yet, so use print instead
        error_state(
            f"{str(config_var)} config file is not valid or not found. Please check the integrity of the file",
            hush=True,
            term=True,
        )


def sync_handler(sync_val: str):
    sync_val = sync_val.lower()

    pblog.info(f"Executing {sync_val} sync command")
    pblog.info(f"CliqueSync Program Version: {pbsync_version.ver}")
    pblog.info(f"CliqueSync Utilities Version: {pbpy_version.ver}")

    sync_workflow = []

    if sync_val == "all" or sync_val == "force" or sync_val == "partial":
        sync_workflow.append(actions.git_prereqs)
        sync_workflow.append(actions.git_check)
        sync_workflow.append(actions.git_ensure_clean)

        is_ci = pbconfig.get("is_ci")
        if not is_ci:
            sync_workflow.append(actions.git_fill_branches)

        partial_sync = sync_val == "partial"
        # Execute synchronization part of script if we're on the expected branch, or force sync is enabled
        if sync_val == "force" or pbgit.is_on_expected_branch():
            if partial_sync:
                sync_workflow.append(actions.git_maintain)
            else:
                sync_workflow.append(actions.git_sync)

            sync_workflow.append(actions.pull_binaries)
        elif pbconfig.get_user_config().getboolean(
            "project", "autosync", fallback=True
        ):
            sync_workflow.append(actions.git_sync)
        else:
            pblog.info(
                f"Current branch does not need auto synchronization: {pbgit.get_current_branch_name()}."
            )
            sync_workflow.append(actions.git_maintain)

        sync_workflow.append(actions.tidy_binaries)
        sync_workflow.append(actions.ensure_project_file)
        sync_workflow.append(actions.download_engine)
        sync_workflow.append(actions.lfs_unlock_thread)

        binaries_mode = pbgit.get_binaries_mode()
        if binaries_mode == "build":
            sync_workflow.append(actions.build_local)

        sync_workflow.append(actions.setup_unreal_git)
        sync_workflow.append(actions.lfs_unlock_thread)
        sync_workflow.append(actions.launch_project)
    elif sync_val == "binaries":
        sync_workflow.append(actions.pull_binaries)
    elif sync_val == "engine":
        sync_workflow.append(actions.download_engine)
    else:
        with open("cliqueworkflows.json") as f:
            workflows = json.load(f)
        if sync_val in workflows:
            actions.create_workflow(sync_val, workflows[sync_val])
            actions.run_workflow(sync_val)
        else:
            error_state(f"Unknown workflow: {sync_val}")
        return

    actions.create_workflow("sync_workflow", sync_workflow)
    actions.run_workflow("sync_workflow")


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


def printversion_handler(print_val):
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
            if os.getenv("CLIQUESYNC_STEAMDRM_USECLOUD")
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
        if publisher == "":
            error_state("Empty publisher configured, please configure a publisher")
        fn = PUBLISHERS.get(publisher)
        if not fn:
            error_state(f"Unknown publisher: {publisher}")
            return
        result = fn(publish_val.lower(), publisher)
        if result != 0:
            error_state(
                f"Something went wrong while publishing a new build. Error code {result}"
            )


def main(argv):
    parser = argparse.ArgumentParser(
        description=f"CliqueSync | CliqueSync Program Version: {pbsync_version.ver} | CliqueSync Utilities Version: {pbpy_version.ver}"
    )

    parser.add_argument(
        "--sync",
        help="""
        Main command for CliqueSync, runs a sync workflow. By default synchronizes the project with latest changes from the repo, and does some housekeeping. Default options:
        all (default): Full sync, syncs git repo, pulls binaries, downloads engine, builds if needed, and launches project
        force: Forces a full sync even if not on expected branch
        partial: Does a partial sync, only syncing git repo and pulling binaries
        binaries: Only pulls binaries
        engine: Only downloads engine

        Otherwise, if a custom workflow name is provided, CliqueSync will attempt to load the workflow from cliqueworkflows.json file and execute it.
        """,
        const="all",
        nargs="?",
    )
    parser.add_argument(
        "--printversion",
        help="Prints requested version information into console.",
        choices=["current-engine", "latest-project", "project"],
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
        help="Publishes a playable build with the provided build type",
        const="default",
        nargs="?",
    )
    parser.add_argument(
        "--debugpath", help="If provided, CliqueSync will run in the provided path"
    )
    parser.add_argument(
        "--debugbranch",
        help="If provided, CliqueSync will use the provided branch as expected branch",
    )

    if len(argv) > 0:
        args = parser.parse_args(argv)
    else:
        pblog.error("At least one valid argument should be passed!")
        pblog.error("Did you mean to launch UpdateProject?")
        input("Press enter to continue...")
        error_state(hush=True, term=True)
        return

    if not (args.debugpath is None):
        # Work on provided debug path
        os.chdir(str(args.debugpath))

    # Parser function object for CliqueSync config file
    def pbsync_config_parser_func(root):
        config_args_map = {
            # config key : xml location | forced override | default | is single
            "supported_git_version": ("git/version", None, "", True),
            "supported_lfs_version": ("git/lfsversion", None, "", True),
            "supported_gcm_version": ("git/gcmversion", None, "", True),
            "expected_branch_names": (
                "git/expectedbranch",
                None if args.debugbranch is None else [str(args.debugbranch)],
                ["main"],
                False,
            ),
            "git_url": ("git/url", None, "", True),
            "branches": ("git/branches/branch", None, ["main"], False),
            "log_file_path": ("log/file", None, "cliquesync_log.txt", True),
            "user_config": ("project/userconfig", None, ".user-sync", True),
            "ci_config": ("project/ciconfig", None, ".ci-sync", True),
            "uev_default_bundle": ("versionator/defaultbundle", None, "editor", True),
            "uev_ci_bundle": ("versionator/cibundle", None, "engine", True),
            "engine_base_version": ("project/enginebaseversion", None, "", True),
            "uproject_name": ("project/uprojectname", None, None, True),
            "package_pdbs": ("project/packagepdbs", None, False, True),
            "repo_folder": ("project/repo_folder", None, "default", True),
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
            "engine_type": ("versionator/enginetype", None, "ue5", True),
            "cloud_storage": ("versionator/cloud_storage", None, False, True),
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
                el = [e.text.strip() if e.text else "" for e in root.findall(tag)]
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

    uproject_name = pbconfig.get("uproject_name")
    if not uproject_name.endswith(".uproject"):

        projects_folder = Path(uproject_name).resolve()
        project_files = list(projects_folder.glob("*/*.uproject"))

        if not project_files:
            error_state(
                f"Could not find any Unreal projects in the provided folder: {projects_folder}"
            )
            return

        print(
            "========================================================================="
        )
        print(
            "|        This is a multi-project directory.                             |"
        )
        print(
            "|        You need to select the project you'd like to sync.             |"
        )
        print(
            "=========================================================================\n"
        )
        print(f">>>>> Multi-project path: {projects_folder}\n")
        print("Which project would you like to sync?\n")

        options = [file.stem for file in project_files]

        for i, option in enumerate(options):
            print(f"{i + 1}) {option}")

        uproject_file = None
        while True:
            response = input(f"\nSelect an option (1-{len(options)}) and press enter: ")
            try:
                choice = int(response) - 1
                if choice >= 0 and choice < len(options):
                    uproject_file = project_files[choice].relative_to(Path.cwd())
                    print("")
                    pblog.success(f"Syncing project {options[choice]}.")
                    break
            except ValueError:
                print("\n")

            pblog.error(f"Invalid option {response}. Try again:\n")

        if uproject_file:
            pbunreal.select_uproject_name(str(uproject_file))

    # Do not process further if we're in an error state
    if pbtools.check_error_state():
        error_state(
            f"""Repository is currently in an error state. Please fix the issues in your workspace
        before running CliqueSync.\nIf you have already fixed the problem, you may remove {pbtools.error_file} from your project folder and
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
        printversion_handler(args.printversion)
    if not (args.clean is None):
        clean_handler(args.clean)
    if not (args.sync is None):
        sync_handler(args.sync)
    if not (args.autoversion is None):
        autoversion_handler(args.autoversion)
    if not (args.build is None):
        build_handler(args.build)
    if not (args.publish is None):
        publish_handler(args.publish)

    pbconfig.shutdown()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "Script" in os.getcwd():
        # Working directory fix for scripts calling CliqueSync from Script/Scripts folder
        os.chdir("..")
    main(sys.argv[1:])
