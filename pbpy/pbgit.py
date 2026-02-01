import fnmatch
import itertools
import json
import multiprocessing
import os
import re
import shutil
import stat
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pbpy import pbconfig, pblog, pbtools

missing_version = "not installed"


@lru_cache()
def get_current_branch_name():
    return pbtools.get_one_line_output(
        [get_git_executable(), "branch", "--show-current"]
    )


def compare_with_current_branch_name(compared_branch):
    return get_current_branch_name() == compared_branch


@lru_cache
def get_binaries_mode():
    return pbconfig.get_user("project", "binaries", "on")


@lru_cache
def is_on_expected_branch():
    binaries_mode = get_binaries_mode()
    if binaries_mode == "force":
        return True
    elif binaries_mode == "local" or binaries_mode == "build" or binaries_mode == "off":
        return False
    for expected_branch in pbconfig.get("expected_branch_names"):
        if compare_with_current_branch_name(expected_branch):
            return True
    return False


@lru_cache()
def get_git_executable():
    return pbconfig.get_user("paths", "git", "git")


@lru_cache()
def get_lfs_executable():
    return pbconfig.get_user("paths", "git-lfs", "git-lfs")


def get_gcm_executable(recursed=False):
    gcm_exec = pbtools.get_one_line_output(
        [get_git_executable(), "config", "--get", "credential.helper"]
    ).replace("\\", "")
    # no helper installed
    if not gcm_exec:
        # try setting GCM
        if not recursed:
            pbtools.run(
                [get_git_executable(), "config", "credential.helper", "manager"]
            )
            return get_gcm_executable(recursed=True)
        return None
    # old style
    if "manager-core" == gcm_exec:
        return [get_git_executable(), "credential-manager-core"]
    # new style
    if "manager" == gcm_exec:
        return [get_git_executable(), "credential-manager"]
    # helper installed, but not GCM
    if "git-credential-manager" not in gcm_exec:
        if not recursed:
            pbtools.run(
                [get_git_executable(), "config", "credential.helper", "manager"]
            )
            return get_gcm_executable(recursed=True)
        return [f"diff.{gcm_exec}"]
    return [gcm_exec]


def get_git_version():
    installed_version_split = pbtools.get_one_line_output(
        [get_git_executable(), "--version"]
    ).split(" ")

    list_len = len(installed_version_split)
    if list_len == 0:
        return missing_version

    # Get latest index as full version of git
    installed_version = str(installed_version_split[list_len - 1])

    if installed_version == "":
        return missing_version

    return installed_version


def get_lfs_version(lfs_exec=None):
    if lfs_exec is None:
        lfs_exec = get_lfs_executable()
    installed_version_split = pbtools.get_one_line_output(
        [lfs_exec, "--version"]
    ).split(" ")

    if len(installed_version_split) == 0:
        return missing_version

    # Get first index as full version of git-lfs
    installed_version = str(installed_version_split[0])

    if installed_version == "":
        return missing_version

    return installed_version.split("/")[1]


def get_gcm_version():
    gcm_exec = get_gcm_executable()
    if gcm_exec is None:
        return missing_version
    if gcm_exec[0].startswith("diff"):
        return gcm_exec[0]
    installed_version = pbtools.get_one_line_output([*gcm_exec, "--version"])

    if installed_version == "":
        return missing_version

    # strip git commit
    installed_version = installed_version.split("+")[0]

    return installed_version


def get_lockables():
    lockables = set()
    content_dir = Path("Content")
    lockables.update(content_dir.glob("**/*.uasset"))
    lockables.update(content_dir.glob("**/*.umap"))
    plugins_dir = Path("Plugins")
    if plugins_dir.is_dir():
        lockables.update(plugins_dir.glob("*/Content/**/*.uasset"))
        lockables.update(plugins_dir.glob("*/Content/**/*.umap"))
    return lockables


def get_locked(key="ours", include_new=True):
    proc = pbtools.run_with_combined_output(
        [get_lfs_executable(), "locks", "--verify", "--json"]
    )
    if proc.returncode:
        return None
    locked_objects = json.loads(proc.stdout)[key]
    locked = set([l.get("path") for l in locked_objects])
    # also check untracked and added files
    if key == "ours" and include_new:
        proc = pbtools.run_with_combined_output(
            [
                get_git_executable(),
                "--no-optional-locks",
                "status",
                "--porcelain",
                "-uall",
            ]
        )
        if not proc.returncode:
            for line in proc.stdout.splitlines():
                if line[0] == "?" or line[1] == "?" or line[0] == "A" or line[1] == "A":
                    locked.add(line[3:])
    return locked


def read_only(file):
    try:
        os.chmod(file, stat.S_IREAD)
        return None
    except OSError as e:
        return str(e)


def read_write(file):
    try:
        os.chmod(file, stat.S_IWRITE)
        return None
    except OSError as e:
        err_str = str(e)
        if pbtools.it_has_any(
            err_str,
            "The system cannot find the file specified",
            "The system cannot find the path specified",
        ):
            return f"You have a locked file which does not exist: {str(e.filename)}"
        else:
            return err_str


def fix_lfs_ro_attr(should_unlock_unmodified):
    if should_unlock_unmodified:
        unlock_unmodified()
    lockables = get_lockables()
    locked = get_locked()
    if locked is None:
        pblog.error("Failed to get locked files from git-lfs, skipping lock cleanup.")
        return
    not_locked = lockables - locked
    cpus = os.cpu_count() or 1
    with multiprocessing.Pool(min(8, cpus)) as pool:
        for message in itertools.chain(
            pool.imap_unordered(read_only, not_locked, 100),
            pool.imap_unordered(read_write, locked),
        ):
            if message:
                pblog.warning(message)


def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def unlock_unmodified():
    modified = get_modified_files(paths=False)
    pending = pbtools.get_combined_output(
        [get_lfs_executable(), "push", "--dry-run", "origin", "HEAD"]
    )
    pending = pending.splitlines()
    pending = {line.rsplit(" => ", 1)[1] for line in pending if line}
    keep = modified | pending
    locked = get_locked()
    if locked is None:
        pblog.error("Failed to get locked files from git-lfs, skipping lock cleanup.")
        return
    unlock = {file for file in locked if file not in keep}
    prefix_filter = []
    for path in modified:
        # if a folder
        if Path(path).is_dir():
            prefix_filter.append(path)
    unlock_it = list(unlock)
    for file in unlock_it:
        found = False
        for prefix in prefix_filter:
            if file.startswith(prefix):
                unlock.remove(file)
                found = True
        if found:
            continue
    unlock = list(unlock)
    if not unlock:
        return True
    for chunk in chunks(unlock, 50):
        args = [get_lfs_executable(), "unlock"]
        args.extend(chunk)
        proc = pbtools.run(args)
        if proc.returncode:
            return False
    return True


@lru_cache()
def get_lfs_file_regex():
    files = []
    with open(".gitattributes") as f:
        for line in f:
            pair = line.strip().split(" ")
            if pair[1] == "lfs" or pair[1] == "lock":
                files.append(fnmatch.translate(pair[0]))
    file_group = "|".join(files)
    return re.compile(f"^({file_group})$")


def is_lfs_file(file):
    return get_lfs_file_regex().match(file) is not None


def set_tracking_information(upstream_branch_name: str):
    output = pbtools.get_combined_output(
        [
            get_git_executable(),
            "branch",
            f"--set-upstream-to=origin/{upstream_branch_name}",
            upstream_branch_name,
        ]
    )
    pblog.info(output)


def stash_pop():
    pblog.info("Popping stash...")

    output = pbtools.get_combined_output([get_git_executable(), "stash", "pop"])
    pblog.info(output)
    lower_case_output = output.lower()

    if pbtools.it_has_all(
        lower_case_output, "auto-merging", "conflict", "should have been pointers"
    ):
        pbtools.error_state(
            f"git stash pop failed. Some of your stashed local changes would be overwritten by incoming changes. Request help from {pbconfig.get('support_channel')} to resolve conflicts, and please do not run UpdateProject until the issue is resolved.",
            True,
        )
    elif "dropped refs" in lower_case_output:
        return
    elif "no stash entries found" in lower_case_output:
        return
    else:
        pbtools.error_state(
            f"git stash pop failed due to an unknown error. Request help from {pbconfig.get('support_channel')} to resolve possible conflicts, and please do not run UpdateProject until the issue is resolved.",
            True,
        )


def check_remote_connection():
    current_url = pbtools.get_one_line_output(
        [get_git_executable(), "remote", "get-url", "origin"]
    )
    recent_url = pbconfig.get("git_url")
    if not recent_url:
        raise ValueError("Git URL not set in config.")
    git_user = pbconfig.get_user("project", "git_user")
    if git_user:
        recent_url = recent_url.replace("https://", f"https://{git_user}@")

    if current_url != recent_url:
        output = pbtools.get_combined_output(
            [get_git_executable(), "remote", "set-url", "origin", recent_url]
        )
        current_url = recent_url
        pblog.info(output)

    out = pbtools.run_with_output(
        [get_git_executable(), "ls-remote", "-hq", "--refs"]
    ).returncode
    return out == 0, current_url


def check_credentials():
    output = pbtools.get_one_line_output([get_git_executable(), "config", "user.name"])
    if output == "" or output is None:
        user_name = input("Please enter your GitHub username: ")
        pbtools.run_with_output(
            [get_git_executable(), "config", "user.name", user_name]
        )

    output = pbtools.get_one_line_output([get_git_executable(), "config", "user.email"])
    if output == "" or output is None:
        user_mail = input("Please enter your GitHub email: ")
        pbtools.run_with_output(
            [get_git_executable(), "config", "user.email", user_mail]
        )


def sync_file(file_path, sync_target=None):
    if sync_target is None:
        sync_target = f"origin/{get_current_branch_name()}"
    proc = pbtools.run(
        [get_git_executable(), "restore", "-qWSs", sync_target, "--", file_path]
    )
    return proc.returncode


def abort_all():
    # Abort everything
    pbtools.run_with_output([get_git_executable(), "merge", "--abort"])
    pbtools.run_with_output([get_git_executable(), "rebase", "--abort"])
    pbtools.run_with_output([get_git_executable(), "am", "--abort"])
    # Just in case
    shutil.rmtree(os.path.join(os.getcwd(), ".git", "rebase-apply"), ignore_errors=True)
    shutil.rmtree(os.path.join(os.getcwd(), ".git", "rebase-merge"), ignore_errors=True)


def abort_rebase():
    # Abort rebase
    pbtools.run_with_output([get_git_executable(), "rebase", "--abort"])


def setup_config():
    pbtools.run_with_output(
        [get_git_executable(), "config", "include.path", "../.gitconfig"]
    )


@lru_cache()
def get_credentials(repo_str=None):
    gcm_bin = get_gcm_executable()
    if gcm_bin is None:
        return (None, None)
    if not repo_str:
        repo_str = pbtools.get_one_line_output(
            [get_git_executable(), "remote", "get-url", "origin"]
        )
    repo_url = urlparse(repo_str)

    creds = f"protocol={repo_url.scheme}\n"
    creds += f"host={repo_url.hostname}\n"
    if repo_url.username:
        creds += f"username={repo_url.username}\n"
    creds += "\n"

    proc = pbtools.run_with_stdin([*gcm_bin, "get"], input=creds)

    if proc.returncode != 0:
        return (None, None)

    out = proc.stdout

    pairs = out.splitlines()
    kv = []
    for pair in pairs:
        if pair:
            kv.append(pair.split("=", 1))
    cred_dict = dict(kv)

    # force reauthentication
    if cred_dict.get("username") == "PersonalAccessToken":
        proc = pbtools.run_with_stdin([*gcm_bin, "erase"], input=creds)
        check_remote_connection()

    return cred_dict.get("username"), cred_dict.get("password")


def get_modified_files(paths=True):
    proc = pbtools.run_with_output(
        [get_git_executable(), "--no-optional-locks", "status", "--porcelain"]
    )
    if paths:
        return {Path(line[3:]) for line in proc.stdout.splitlines()}
    return {line[3:] for line in proc.stdout.splitlines()}
