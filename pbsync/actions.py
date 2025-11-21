import os
import sys
import threading
from pathlib import Path

from pbpy import pbconfig, pbgh, pbgit, pblog, pbtools, pbunreal
from pbsync import prereqs


actions = {}
action_pairs = {}
active_pairs = {}


def register_action():
    def decorator(func):
        actions[func.__name__] = func
        return func

    return decorator


def register_action_pair(pop_func):
    def decorator(func):
        action_name = func.__name__

        def pop_wrapper():
            if action_name in active_pairs:
                result = pop_func(active_pairs[action_name])
                del active_pairs[action_name]
                return result
            return True

        def wrapper():
            if action_name in active_pairs:
                return pop_wrapper()
            result = func()
            if result:
                active_pairs[action_name] = result
            return result

        action_pairs[action_name] = wrapper
        action_pairs[f"pop_{action_name}"] = pop_wrapper
        return func

    return decorator


workflows = {}


def create_workflow(workflow_name: str, workflow_actions):
    def workflow():
        pblog.info("------------------")
        for action in workflow_actions:
            if callable(action):
                action = action.__name__
            if action in actions:
                if not actions[action]():
                    break
            elif action in action_pairs:
                if not action_pairs[action]():
                    break
            else:
                raise ValueError(f"Action {action} not registered.")

            pblog.info("------------------")

        for action in active_pairs.keys():
            pop_action_name = f"pop_{action}"
            action_pairs[pop_action_name]()

    workflows[workflow_name] = workflow
    return workflow


def run_workflow(workflow_name):
    if workflow_name in workflows:
        workflows[workflow_name]()
    else:
        raise ValueError(f"Workflow {workflow_name} not registered.")


@register_action()
def git_prereqs():
    return prereqs.ensure_prereqs()


@register_action()
def git_check():
    # Check our remote connection before doing anything
    remote_state, remote_url = pbgit.check_remote_connection()
    if not remote_state:
        pbtools.error_state(
            f"Remote connection was not successful. Please verify that you have an internet connection. Current git remote URL: {remote_url}"
        )
        return False
    else:
        pblog.info("Remote connection is up")

    # Do some housekeeping for git configuration
    pbgit.setup_config()

    # Check if we have correct credentials
    pbgit.check_credentials()

    return True


@register_action()
def git_ensure_clean():
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
            pbtools.error_state(
                f"You are in the middle of a rebase. Changes on one of your commits will be overridden by incoming changes. Please request help from {pbconfig.get('support_channel')} to resolve conflicts, and please do not run UpdateProject until the issue is resolved.",
                fatal_error=True,
            )
            return False

    return True


@register_action()
def git_fill_branches():
    # undo single branch clone
    pbtools.run(
        [
            pbgit.get_git_executable(),
            "config",
            "remote.origin.fetch",
            "+refs/heads/*:refs/remotes/origin/*",
        ]
    )
    return True


@register_action()
def git_maintain():
    pbtools.maintain_repo()
    return True


@register_action()
def git_sync():
    pbtools.resolve_conflicts_and_pull()
    return True


@register_action()
def pull_binaries():
    project_version = pbunreal.get_project_version()
    is_custom_version = pbunreal.is_using_custom_version()
    needs_binaries_pull = pbgh.is_pull_binaries_required()
    if project_version is not None:
        if is_custom_version:
            pblog.info(f"User selected project version: {project_version}")
        else:
            pblog.info(f"Current project version: {project_version}")
    elif needs_binaries_pull:
        pbtools.error_state(
            f"Something went wrong while fetching project version. Please request help from {pbconfig.get('support_channel')}."
        )

    checksum_json_path = pbconfig.get("checksum_file")
    if is_custom_version:
        # checkout old checksum file from tag
        pbgit.sync_file(checksum_json_path, project_version)

    if needs_binaries_pull and project_version:
        pblog.info("Binaries are not up to date, pulling new binaries...")
        ret = pbgh.pull_binaries(project_version)
        if ret == 0:
            pblog.success("Binaries were pulled successfully!")
        elif ret < 0:
            pbtools.error_state(
                "Binaries pull failed, please view log for instructions."
            )
        elif ret > 0:
            pbtools.error_state(
                f"An error occurred while pulling binaries. Please request help from {pbconfig.get('support_channel')} to resolve it, and please do not run UpdateProject until the issue is resolved.",
                True,
            )
    else:
        pblog.success("Binaries are up to date!")

    # restore checksum file
    if is_custom_version:
        pbgit.sync_file(checksum_json_path, "HEAD")

    return True


@register_action()
def tidy_binaries():
    symbols_needed = pbunreal.is_versionator_symbols_enabled()
    pbunreal.clean_binaries_folder(not symbols_needed)
    return True


@register_action()
def ensure_project_file():
    uproject_file = pbunreal.get_uproject_name()
    if pbgit.sync_file(uproject_file) != 0:
        pbtools.error_state(
            f"Something went wrong while updating the uproject file. Please request help from {pbconfig.get('support_channel')}."
        )
    return True


def pop_lfs_unlock_thread(fix_attr_thread):
    pblog.info("Finishing LFS locks cleanup...")
    fix_attr_thread.join()
    pblog.info("Finished LFS locks cleanup.")
    return True


@register_action_pair(pop_func=pop_lfs_unlock_thread)
def lfs_unlock_thread():
    configured_branches = pbconfig.get("branches")
    should_unlock_unmodified = pbgit.get_current_branch_name() in configured_branches
    fix_attr_thread = threading.Thread(
        target=pbgit.fix_lfs_ro_attr, args=(should_unlock_unmodified,)
    )
    fix_attr_thread.start()
    return fix_attr_thread


@register_action()
def download_engine():
    engine_version = pbunreal.get_engine_version_with_prefix()
    if engine_version is not None:
        pblog.info(
            "Registering current engine build if it exists. Otherwise, the build will be downloaded..."
        )

        bundle_name = pbunreal.get_bundle()
        symbols_needed = pbunreal.is_versionator_symbols_enabled()
        if pbunreal.download_engine(bundle_name, symbols_needed):
            pblog.info(
                f"Engine build {bundle_name}-{engine_version} successfully registered"
            )
        else:
            pbtools.error_state(
                f"Something went wrong while registering engine build {bundle_name}-{engine_version}. Please request help from {pbconfig.get('support_channel')}."
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
    else:
        pblog.info("Using unmanaged standard engine.")
    return True


@register_action()
def build_local():
    pbunreal.generate_project_files()
    pbunreal.build_source(for_distribution=False)
    return True


@register_action()
def setup_unreal_git():
    pblog.info("Updating Unreal configuration settings")
    pbunreal.update_source_control()
    return True


@register_action()
def launch_project():
    is_ci = pbconfig.get("is_ci")

    launch_pref = (
        pbconfig.get_user("project", "launch", "none")
        if is_ci
        else pbconfig.get_user("project", "launch", "editor")
    )
    if launch_pref == "vs":
        pblog.info("Launching Visual Studio...")
        os.startfile(pbunreal.get_sln_path())
    elif launch_pref == "rider":
        pblog.info("Launching Rider...")
        rider_bin = pbtools.get_one_line_output(["echo", "%Rider for Unreal Engine%"])
        rider_bin = rider_bin.replace(";", "")
        rider_bin = rider_bin.replace('"', "")
        pbtools.run_non_blocking(
            f'"{rider_bin}\\rider64.exe" "{str(pbunreal.get_sln_path().resolve())}"'
        )
    elif launch_pref == "editor":
        if pbunreal.is_ue_closed():
            pblog.info("Launching Unreal Editor...")
            uproject_file = pbunreal.get_uproject_name()
            path = str(Path(uproject_file).resolve())

            extra_args = pbconfig.get_user("project", "editor_args", default="").split()

            if extra_args:
                launch_args = [pbunreal.get_editor_path(), path]
                launch_args.extend(extra_args)
                pbtools.run_non_blocking_ex(launch_args)
            else:
                launched_editor = False
                if not pbunreal.check_ue_file_association():
                    pblog.warning(
                        "CliqueSync failed to find a valid file association to launch the editor, attempting to resolve..."
                    )
                    pbunreal.run_unreal_setup()
                if pbunreal.check_ue_file_association():
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
                        f"CliqueSync failed to find a valid file association to launch the editor, and will attempt to launch the editor directly as a workaround."
                    )
                    pbtools.run_non_blocking_ex([pbunreal.get_editor_path(), path])
                    pblog.warning(
                        f"If CliqueSync failed to launch the directly directly, please launch {uproject_file} manually for now."
                    )
                    pbtools.error_state(
                        f"For a permanent fix, try clearing out file associations for the .uproject file type and launching CliqueSync again. Please get help from {pbconfig.get('support_channel')} if the issue continues."
                    )
        else:
            pblog.info("Unreal Editor is already running, skipping launch.")
    # TODO
    # elif launch_pref == "debug":
    #    pbtools.run_non_blocking(f"\"{str(pbunreal.get_devenv_path())}\" \"{str(pbunreal.get_sln_path())}\" /DebugExe \"{str(pbunreal.get_editor_path())}\" \"{str(pbunreal.get_uproject_path())}\" -skipcompile")
    else:
        pblog.info("No launch action selected, skipping launch.")
    return True
