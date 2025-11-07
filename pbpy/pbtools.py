import hashlib
import json
import math
import multiprocessing
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from subprocess import CalledProcessError

import psutil

# PBSync Imports
from pbpy import pbconfig, pbgit, pblog, pbuac, pbunreal

error_file = ".pbsync_err"


def handle_env(env):
    if env is None:
        return os.environ
    else:
        return os.environ | env


def handle_env_out(cmd, env_out):
    if env_out:
        for env_var in env_out:
            if os.name == "posix":
                cmd.extend(["&&", "echo", f"{env_var}=${env_var}"])
            else:
                cmd.extend(["&&", "set", env_var])


def parse_environment(stdout, env_out):
    if env_out is None:
        return
    for line in stdout.splitlines():
        # if not a valid line for environment echo, skip it
        if line.startswith("?") or line.startswith("Environment variable "):
            continue
        k, _, v = line.partition("=")
        # v is empty if no partition (not a key=value) or no value set (key=)
        # must check to see if we actually requested this key
        # then, check if this was a set variable in posix
        if v and k in env_out and not v.startswith("$"):
            os.environ[k] = v.strip('"')


def run(cmd, env=None, cwd=None):
    if os.name == "posix":
        cmd = " ".join(cmd) if isinstance(cmd, list) else cmd

    env = handle_env(env)
    return subprocess.run(cmd, shell=True, env=env, cwd=cwd)


def run_with_output(cmd, env=None, env_out=None):
    handle_env_out(cmd, env_out)
    if os.name == "posix":
        cmd = " ".join(cmd) if isinstance(cmd, list) else cmd

    env = handle_env(env)
    proc = subprocess.run(cmd, capture_output=True, text=True, shell=True, env=env)
    parse_environment(proc.stdout, env_out)
    return proc


def default_stream_log(msg):
    pblog.info(msg)


def checked_stream_log(msg, error="error", warning="warning"):
    if error in msg:
        pblog.error(msg)
    elif warning in msg:
        pblog.warning(msg)
    else:
        pblog.info(msg)


def raised_stream_log(msg, error="error", warning="warning"):
    if error in msg:
        pblog.error(msg)
    elif warning in msg:
        pblog.warning(msg)
    else:
        print(msg)


def progress_stream_log(msg, error="error", warning="warning"):
    if error in msg:
        pblog.error(msg)
    elif warning in msg:
        pblog.warning(msg)
    else:
        msg = msg.rstrip("\n")
        print(f"{msg}", end="\r", flush=True)


def run_stream(cmd, env=None, logfunc=None, cwd=None):
    if logfunc is None:
        logfunc = default_stream_log

    startupinfo = None

    if os.name == "posix":
        cmd = " ".join(cmd) if isinstance(cmd, list) else cmd
    elif os.name == "nt":
        startupinfo = subprocess.STARTUPINFO(dwFlags=subprocess.CREATE_NEW_CONSOLE)

    env = handle_env(env)
    proc = subprocess.Popen(
        cmd,
        text=True,
        shell=True,
        bufsize=1,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=cwd,
        startupinfo=startupinfo,
        encoding="utf8",
    )
    returncode = None
    while True:
        try:
            for line in iter(lambda: proc.stdout.readline(), ""):
                logfunc(line)
        except:
            continue
        returncode = proc.poll()
        if returncode is not None:
            break
    return proc


def run_with_stdin(cmd, input, env=None, env_out=None):
    handle_env_out(cmd, env_out)
    if os.name == "posix":
        cmd = " ".join(cmd) if isinstance(cmd, list) else cmd

    env = handle_env(env)
    proc = subprocess.run(
        cmd, input=input, capture_output=True, text=True, shell=True, env=env
    )
    parse_environment(proc.stdout, env_out)
    return proc


def run_with_combined_output(cmd, env=None, env_out=None):
    handle_env_out(cmd, env_out)
    if os.name == "posix":
        cmd = " ".join(cmd) if isinstance(cmd, list) else cmd

    env = handle_env(env)
    proc = subprocess.run(
        cmd,
        text=True,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    parse_environment(proc.stdout, env_out)
    return proc


def run_non_blocking(*commands):
    if os.name == "nt":
        cmdline = " & ".join(commands)
        subprocess.Popen(
            cmdline,
            shell=True,
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    elif os.name == "posix":
        forked_commands = [f"nohup {command}" for command in commands]
        cmdline = " || ".join(forked_commands)
        subprocess.Popen(
            cmdline,
            shell=True,
            start_new_session=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


def run_non_blocking_ex(cmd, env=None):
    if os.name == "posix":
        cmd.insert("nohup", 0)
        cmd = " ".join(cmd) if isinstance(cmd, list) else cmd

    env = handle_env(env)
    if os.name == "nt":
        subprocess.Popen(
            cmd,
            shell=True,
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    elif os.name == "posix":
        subprocess.Popen(
            cmd,
            shell=True,
            start_new_session=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


def get_combined_output(cmd, env=None, env_out=None):
    return run_with_combined_output(cmd, env=env, env_out=env_out).stdout


def get_one_line_output(cmd, env=None, env_out=None):
    return run_with_output(cmd, env=env, env_out=env_out).stdout.rstrip()


def it_has_any(it, *args):
    return any([el in it for el in args])


def it_has_all(it, *args):
    return all([el in it for el in args])


def whereis(app):
    result = None

    command = "where"
    if os.name != "nt":
        command = "which"

    try:
        result = subprocess.run(
            f"{command} {app}",
            text=True,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        ).stdout
    except CalledProcessError:
        pass

    if result is None:
        return []

    result = result.splitlines()
    return [Path(line) for line in result if len(line)]


def get_hash(file_path):
    hash_reader = hashlib.blake2b()
    try:
        with open(file_path, "rb") as f:
            hash_reader.update(f.read())
            return str(hash_reader.hexdigest())
    except FileNotFoundError:
        return None
    except Exception as e:
        pblog.exception(str(e))
        return None


def compare_hash_single(compared_file_path, hash_json_file_path):
    current_hash = get_hash(compared_file_path)
    if current_hash is None:
        return False

    dict_search_string = f"{compared_file_path}"
    hash_dict = get_dict_from_json(hash_json_file_path)

    if hash_dict is None or not (dict_search_string in hash_dict):
        pblog.error(f"Key {dict_search_string} not found in {hash_json_file_path}")
        return False

    if hash_dict[dict_search_string] == current_hash:
        pblog.info(f"Checksum successful for {compared_file_path}")
        return True
    else:
        pblog.error(f"Checksum failed for {compared_file_path}")
        pblog.error(f"Expected hash: {hash_dict[dict_search_string]}")
        pblog.error(f"Current hash: {str(current_hash)}")
        return False


def compare_hash_all(hash_json_file_path, print_log=False, ignored_extension=".zip"):
    hash_dict = get_dict_from_json(hash_json_file_path)
    if hash_dict is None or len(hash_dict) == 0:
        return False

    for file_path in hash_dict:
        if file_path.endswith(ignored_extension):
            continue

        current_hash = get_hash(file_path)
        if hash_dict[file_path] == current_hash:
            if print_log:
                pblog.info(f"Checksum successful for {file_path}")
        else:
            if print_log:
                pblog.error(f"Checksum failed for {file_path}")
                if current_hash is not None:
                    pblog.error(f"Expected hash: {hash_dict[file_path]}")
                    pblog.error(f"Current hash: {str(current_hash)}")
                else:
                    pblog.error("File does not exist")
            return False
    return True


def make_json_from_dict(dictionary, json_file_path):
    with open(json_file_path, "w") as f:
        json.dump(dictionary, f)


def get_dict_from_json(json_file_path):
    try:
        with open(json_file_path, "rb") as json_file:
            json_text = json_file.read()
            return json.loads(json_text)
    except Exception as e:
        pblog.exception(str(e))
        return None


def is_junction(file_path: str) -> bool:
    try:
        return bool(os.readlink(file_path))
    except OSError:
        return False
    except ValueError:
        return False
    except NotImplementedError:
        return False


def remove_junction(destination):
    if os.path.isdir(destination):
        try:
            shutil.rmtree(destination)
        except Exception:
            try:
                os.remove(destination)
            except Exception:
                return False
    return True


def check_error_state():
    # True: Error on last run, False: No errors
    if not os.path.exists(error_file):
        return False

    try:
        with open(error_file) as error_state_file:
            error_code = error_state_file.readline(1)
            if int(error_code) == 0:
                return False
            elif int(error_code) == 1:
                return True
            else:
                return False
    except Exception:
        return False


def remove_file(file_path):
    try:
        os.remove(file_path)
    except Exception:
        os.chmod(file_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # 0777
        try:
            os.remove(file_path)
        except Exception as e:
            pblog.exception(str(e))
            pass
    return not os.path.isfile(file_path)


def error_state(msg=None, fatal_error=False, hush=False, term=False):
    if msg is not None:
        pblog.error(msg)
    if fatal_error:
        # Log status for more information during tech support
        pblog.info(
            run_with_combined_output([pbgit.get_git_executable(), "status"]).stdout
        )
        pblog.info(
            run_with_combined_output(
                [pbgit.get_git_executable(), "reflog", "-10"]
            ).stdout
        )
        # This is a fatal error, so do not let user run PBSync until issue is fixed
        if False:
            with open(error_file, "w") as error_state_file:
                error_state_file.write("1")
    if not hush:
        pblog.info(f"Logs are saved in {pbconfig.get('log_file_path')}.")
    if not term:
        pbconfig.shutdown()
    sys.exit(1)


def get_running_process(process_name):
    if os.name == "nt":
        process_name += ".exe"
    try:
        for p in psutil.process_iter(["name", "exe"]):
            if process_name == p.info["name"]:
                return p
    except Exception:
        # An exception occurred while checking, assume the program is not running
        pass
    return None


def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def wipe_workspace():
    current_branch = pbgit.get_current_branch_name()
    response = input(
        f"This command will wipe your workspace and get latest changes from {current_branch}. Are you sure? [y/N] "
    )

    if len(response) < 1 or response[0].lower() != "y":
        return False

    pbgit.abort_all()
    output = get_combined_output(
        [pbgit.get_git_executable(), "fetch", "origin", current_branch]
    )
    pblog.info(output)
    proc = run_with_combined_output(
        [pbgit.get_git_executable(), "reset", "--hard", f"origin/{current_branch}"]
    )
    result = proc.returncode
    pblog.info(proc.stdout)
    output = get_combined_output([pbgit.get_git_executable(), "clean", "-fd"])
    pblog.info(output)
    output = get_combined_output([pbgit.get_git_executable(), "pull"])
    pblog.info(output)
    return result == 0


def maintain_repo():
    pblog.info("Starting repo maintenance...")

    commands = [f"{pbgit.get_lfs_executable()} prune -fc"]

    if os.name == "nt" and pbgit.get_git_executable() == "git":
        proc = run_with_combined_output(
            ["schtasks" "/query", "/TN", "Git for Windows Updater"]
        )
        # if exists
        if proc.returncode == 0:
            cmdline = ["schtasks", "/delete", "/F", "/TN", '"Git for Windows Updater"']
            pblog.info(
                "Requesting admin permission to delete the Git for Windows Updater..."
            )
            if not pbuac.isUserAdmin():
                time.sleep(1)
                try:
                    pbuac.runAsAdmin(cmdline)
                except OSError:
                    pblog.error("User declined permission. Automatic delete failed.")
            else:
                proc = run_with_combined_output(cmdline)

    does_maintainence = (
        get_one_line_output(
            [pbgit.get_git_executable(), "config", "maintenance.prefetch.schedule"]
        )
        == "hourly"
    )
    if not does_maintainence:
        commands.insert(0, f"scalar register .")
    else:
        commands.insert(0, f"scalar reconfigure .")

    # fill in the git repo optionally
    is_shallow = get_one_line_output(
        [pbgit.get_git_executable(), "rev-parse", "--is-shallow-repository"]
    )
    # add in the front, so everything else can clean up after the fetch
    if is_shallow == "true":
        pblog.info(
            "Shallow clone detected. PBSync will fill in history in the background."
        )
        commands.insert(0, f"{pbgit.get_git_executable()} fetch --unshallow")
    else:
        # repo was already fetched in UpdateProject for the current branch
        current_branch = pbgit.get_current_branch_name()
        fetch_base = [pbgit.get_git_executable(), "fetch", "--no-tags", "origin"]
        # sync other branches, but we already synced our own in UpdateProject.bat
        configured_branches = pbconfig.get("branches")
        branches = []
        if configured_branches:
            for branch in configured_branches:
                if branch == current_branch:
                    continue
                branches.append(branch)
            fetch_base.extend(branches)
        commands.insert(0, " ".join(fetch_base))

    run_non_blocking(*commands)


lfs_fetch_thread = None


def do_lfs_fetch(files):
    branch_name = pbgit.get_current_branch_name()
    fetch = [
        pbgit.get_lfs_executable(),
        "fetch",
        "origin",
        f"origin/{branch_name}",
        "-I",
        ",".join(files),
    ]
    run(fetch)


def start_lfs_fetch(files):
    global lfs_fetch_thread
    lfs_fetch_thread = threading.Thread(target=do_lfs_fetch, args=(files,))
    lfs_fetch_thread.start()


def finish_lfs_fetch():
    if lfs_fetch_thread is not None:
        lfs_fetch_thread.join()


def do_lfs_checkout(files):
    # todo: is not using Git LFS user exe ok?
    lfs_checkout = ["git-lfs", "checkout", "--"]
    lfs_checkout.extend(files)
    run_with_combined_output(lfs_checkout)


def resolve_conflicts_and_pull(retry_count=0, max_retries=1):
    branch_name = pbgit.get_current_branch_name()
    on_expected_branch = pbgit.is_on_expected_branch()
    configured_branches = pbconfig.get("branches")
    if not on_expected_branch and branch_name not in configured_branches:
        pblog.info(f"Branch {branch_name} is not an auto-synced branch. Skipping pull.")
        return

    def should_attempt_auto_resolve():
        return retry_count <= max_retries

    if retry_count:
        # wait a little bit if retrying (exponential)
        time.sleep(0.25 * (1 << retry_count))

    def handle_success():
        pblog.success(
            "Success! You are now on the latest changes without any conflicts."
        )

    def handle_error(msg=None):
        error_state(msg, fatal_error=True)

    index_lock = Path(".git/index.lock")
    if index_lock.exists():
        success = False
        try:
            assert os.name == "nt"
            # rename to itself
            index_lock.rename(".git/index.lock")
            index_lock.unlink(missing_ok=True)
            success = True
        except OSError:
            if should_attempt_auto_resolve():
                pblog.error(
                    "Git is currently being used by another process. Retrying..."
                )
                retry_count += 1
                resolve_conflicts_and_pull(retry_count, 2)
                return
        except AssertionError:
            # just fall back to full error
            pass
        if not success:
            handle_error(
                f"Git is currently being used by another process. Please try again later or request help in {pbconfig.get('support_channel')} to resolve it, and please do not run UpdateProject until the issue is resolved."
            )

    out = get_combined_output(
        [pbgit.get_git_executable(), "status", "--porcelain=2", "--branch"]
    )

    if not it_has_any(out, "-0"):
        pbunreal.ensure_ue_closed()
        pblog.info(
            "Please wait while getting the latest changes from the repository. It may take a while..."
        )

        if False:
            # reset plugin submodules
            if pbgit.is_on_expected_branch():
                shutil.rmtree("Plugins", ignore_errors=True)

        res_out = ""

        def res_log(log):
            nonlocal res_out
            log = log.rstrip()
            if not log:
                return
            pblog.info(log)
            res_out += log

        use_new_sync = False
        if use_new_sync:
            # Check what files we are going to change
            diff_proc = run_with_combined_output(
                [
                    pbgit.get_git_executable(),
                    "diff",
                    "--name-only",
                    f"HEAD...origin/{branch_name}",
                ]
            )
            if diff_proc.returncode == 0:
                changed_files = diff_proc.stdout.splitlines()
            else:
                changed_files = []

            changed_files = [file for file in changed_files if pbgit.is_lfs_file(file)]

            cpus = os.cpu_count() or 1
            total = len(changed_files)
            fetch_processes = min(cpus, math.ceil(total / 50))
            fetch_batch_size = min(50, math.ceil(total / fetch_processes))
            if fetch_batch_size == total:
                start_lfs_fetch(changed_files)
            else:
                fetch_batched = chunks(changed_files, fetch_batch_size)
                with multiprocessing.Pool(fetch_processes) as pool:
                    pool.map(do_lfs_fetch, fetch_batched)

            # Get the latest files, but skip smudge so we can super charge a LFS pull as one batch
            cmdline = [
                pbgit.get_git_executable(),
                "-c",
                "filter.lfs.smudge=",
                "-c",
                "filter.lfs.process=",
                "-c",
                "filter.lfs.required=false",
            ]
            # if we can fast forward merge, do that instead of a rebase (faster, safer)
            if it_has_any(out, "+0"):
                pblog.info(
                    "Fast forwarding workspace to the latest changes from the repository..."
                )
                cmdline.extend(["merge", "--ff-only"])
            else:
                pblog.info(
                    "Rebasing workspace with the latest changes from the repository..."
                )
                cmdline.extend(["rebase", "--autostash"])
            cmdline.append(f"origin/{branch_name}")
            result = run_with_combined_output(cmdline)

            # update plugin submodules
            if run_with_combined_output(
                [pbgit.get_git_executable(), "ls-files", "--", "Plugins"]
            ).stdout:
                run_with_combined_output(
                    [
                        pbgit.get_git_executable(),
                        "submodule",
                        "update",
                        "--init",
                        "--",
                        "Plugins",
                    ]
                )
            else:
                shutil.rmtree("Plugins", ignore_errors=True)

            processes = min(cpus, math.ceil(total / 5))
            batch_size = min(50, math.ceil(total / processes))
            chunked_files = chunks(changed_files, batch_size)

            # make index.lock to block LFS from updating index
            with open(".git/index.lock", "w") as f:
                pass

            # Update index without FS monitor
            run(
                [
                    pbgit.get_git_executable(),
                    "config",
                    "-f",
                    ".gitconfig",
                    "core.useBuiltinFSMonitor",
                    "false",
                ]
            )

            # split it out to multiprocess
            with multiprocessing.Pool(processes) as pool:
                # Checkout LFS in one go since we skipped smudge and fetched in the background
                finish_lfs_fetch()
                pool.map(do_lfs_checkout, chunked_files)

            os.remove(".git/index.lock")

            update_index = [
                pbgit.get_git_executable(),
                "update-index",
                "-q",
                "--refresh",
                "--unmerged",
                "--",
            ]
            update_index.extend(changed_files)
            run(update_index)

            # Revert back to using FS monitor
            run(
                [
                    pbgit.get_git_executable(),
                    "config",
                    "-f",
                    ".gitconfig",
                    "core.useBuiltinFSMonitor",
                    "true",
                ]
            )
        else:
            # Get the latest files
            cmdline = [pbgit.get_git_executable()]
            # if we can fast forward merge, do that instead of a rebase (faster, safer)
            if it_has_any(out, "+0"):
                pblog.info(
                    "Fast forwarding workspace to the latest changes from the repository..."
                )
                cmdline.extend(["merge", "--ff-only"])
            else:
                pblog.info(
                    "Rebasing workspace with the latest changes from the repository..."
                )
                cmdline.extend(["rebase", "--autostash"])
            cmdline.append(f"origin/{branch_name}")
            result = run_stream(cmdline, logfunc=res_log)

            # update plugin submodules
            if run_with_combined_output(
                [pbgit.get_git_executable(), "ls-files", "--", "Plugins"]
            ).stdout:
                run_stream(
                    [
                        pbgit.get_git_executable(),
                        "submodule",
                        "update",
                        "--init",
                        "--",
                        "Plugins",
                    ]
                )
            else:
                shutil.rmtree("Plugins", ignore_errors=True)

        # see if the update was successful
        code = result.returncode
        out = res_out.lower()
        error = code != 0
    else:
        error = False

    if not error:
        handle_success()
    elif "up to date" in out:
        handle_success()
    elif "rewinding head" in out and not it_has_any(out, "error", "conflict"):
        handle_success()
    elif "successfully rebased and updated" in out:
        handle_success()
    elif it_has_any(
        out, "failed to merge in the changes", "could not apply", "overwritten by merge"
    ):
        handle_error(
            f"Aborting the pull. Changes on one of your commits will be overridden by incoming changes. Please request help in {pbconfig.get('support_channel')} to resolve conflicts, and please do not run UpdateProject until the issue is resolved."
        )
    elif it_has_any(
        out, "unmerged files", "merge_head exists", "middle of", "mark resolution"
    ):
        handle_error(
            f"You are in the middle of a merge. Please request help in {pbconfig.get('support_channel')} to resolve it, and please do not run UpdateProject until the issue is resolved."
        )
    elif "unborn" in out:
        if should_attempt_auto_resolve():
            pblog.error("Unborn branch detected. Retrying...")
            retry_count += 1
            resolve_conflicts_and_pull(retry_count)
            return
        else:
            handle_error(
                f"You are on an unborn branch. Please request help in {pbconfig.get('support_channel')} to resolve it, and please do not run UpdateProject until the issue is resolved."
            )
    elif it_has_any(out, "no remote", "no such remote", "refspecs without repo"):
        if should_attempt_auto_resolve():
            pblog.error("Remote repository not found. Retrying...")
            retry_count += 1
            resolve_conflicts_and_pull(retry_count, 2)
            return
        else:
            handle_error(
                f"The remote repository could not be found. Please request help in {pbconfig.get('support_channel')} to resolve it, and please do not run UpdateProject until the issue is resolved."
            )
    elif "cannot open" in out:
        if should_attempt_auto_resolve():
            pblog.error("Git file info could not be read. Retrying...")
            retry_count += 1
            resolve_conflicts_and_pull(retry_count, 3)
            return
        else:
            handle_error(
                f"Git file info could not be read. Please request help in {pbconfig.get('support_channel')} to resolve it, and please do not run UpdateProject until the issue is resolved."
            )
    elif (
        "The following untracked working tree files would be overwritten by reset"
        in out
    ):
        if should_attempt_auto_resolve():
            pblog.error("Untracked files would be overwritten. Retrying...")
            files = [l.strip() for l in out.splitlines()[1:]]
            run_with_combined_output(
                [pbgit.get_git_executable(), "add", "-A", "--", *files]
            )
            retry_count += 1
            resolve_conflicts_and_pull(retry_count, 1)
            return
        else:
            handle_error(
                f"Untracked files would be overwritten. Please request help in {pbconfig.get('support_channel')} to resolve it, and please do not run UpdateProject until the issue is resolved."
            )
    else:
        # We have no idea what the state of the repo is. Do nothing except bail.
        handle_error(
            f"Aborting the repo update because of an unknown error. Request help in {pbconfig.get('support_channel')} to resolve it, and please do not run UpdateProject until the issue is resolved."
        )

    maintain_repo()
