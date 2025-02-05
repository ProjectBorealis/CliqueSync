import os
import os.path
import shutil
from functools import lru_cache
from urllib.parse import urlparse
from zipfile import ZipFile

from pbpy import pbconfig, pbgit, pbinfo, pblog, pbtools, pbunreal

gh_executable_path = "\\git\\gh.exe"
chglog_executable_path = "\\git\\git-chglog.exe"
glab_executable_path = "\\git\\glab.exe"
chglog_config_path = "\\chglog.yml"
release_file = "RELEASE_MSG"
binary_package_name = "Binaries.zip"


@lru_cache()
def get_token_var(git_url=None):
    hostname = urlparse(git_url if git_url else pbconfig.get("git_url")).hostname

    if hostname == "github.com":
        return "GITHUB_TOKEN"
    elif hostname == "gitlab.com":
        return "GITLAB_TOKEN"
    else:
        # Fall back to gitlab path as that's most likely
        # what our provider will be if we can't determine
        return "GITLAB_TOKEN"


@lru_cache()
def get_token_env(repo=None):
    _, token = pbgit.get_credentials(repo)

    if token:
        ret = {}
        ret[get_token_var(repo)] = token
        return ret
    else:
        pbtools.error_state(
            f"Credential retrieval failed. Please get help from {pbconfig.get('support_channel')}"
        )


@lru_cache()
def get_cli_executable(git_url=None):
    hostname = urlparse(git_url if git_url else pbconfig.get("git_url")).hostname

    if hostname == "github.com":
        return pbinfo.format_repo_folder(gh_executable_path)
    elif hostname == "gitlab.com":
        return pbinfo.format_repo_folder(glab_executable_path)
    else:
        # Fall back to gitlab path as that's most likely
        # what our provider will be if we can't determine
        return pbinfo.format_repo_folder(glab_executable_path)


def download_release_file(version, pattern=None, directory=None, repo=None):
    full_repo = repo
    repo = urlparse(repo).path[1:]
    cli_exec_path = get_cli_executable(full_repo)

    if not os.path.isfile(cli_exec_path):
        pblog.error(f"CLI executable not found at {cli_exec_path}")
        return 1

    args = [cli_exec_path, "release", "download"]

    if version:
        args.append(version)

    if directory:
        args.extend(["-D", directory])
    else:
        directory = "."

    def try_remove(path):
        path = os.path.join(directory, path)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                pblog.exception(str(e))
                pblog.error(
                    f"Exception thrown while removing {path}. Please remove it manually."
                )
                return -1
        return 0

    def check_wildcard(path):
        if "*" in file:
            return False
        return True

    if pattern:
        if not isinstance(pattern, list):
            pattern = [pattern]
        for file in pattern:
            if check_wildcard(file):
                res = try_remove(file)
                if res != 0:
                    return res
            if "glab" in cli_exec_path:
                args.extend(["-n", file])
            else:
                args.extend(["-p", file])
    else:
        pattern = "*"

    if repo:
        args.extend(["-R", repo])

    creds = get_token_env(full_repo)

    try:
        proc = pbtools.run_with_combined_output(args, env=creds)
        output = proc.stdout
        if proc.returncode == 0:
            pass
        elif pbtools.it_has_any(output, "release not found", "no assets"):
            pblog.error(
                f"Release {version} not found. Please wait and try again later."
            )
            return -1
        elif "The file exists" in output:
            pblog.error(
                f"File {directory}/{pattern} was not able to be overwritten. Please remove it manually and run UpdateProject again."
            )
            return -1
        else:
            pblog.error(
                f"Unknown error occurred while pulling release file {pattern} for release {version}"
            )
            pblog.error(f"Command output was: {output}")
            return 1
    except Exception as e:
        pblog.exception(str(e))
        pblog.error(f"Exception thrown while pulling release file {file} for {version}")
        return 1

    return 0


def is_pull_binaries_required():
    if not os.path.isfile(get_cli_executable()):
        return False
    checksum_json_path = pbconfig.get("checksum_file")
    if not os.path.exists(checksum_json_path):
        return False
    return not pbtools.compare_hash_all(checksum_json_path)


def pull_binaries(version_number: str, pass_checksum=False):
    cli_exec_path = get_cli_executable()

    if pass_checksum:
        checksum_json_path = None
    else:
        checksum_json_path = pbconfig.get("checksum_file")
        if not os.path.exists(checksum_json_path):
            pblog.error(f"Checksum json file not found at {checksum_json_path}")
            return 1

    if not pbtools.compare_hash_single(binary_package_name, checksum_json_path):
        if not os.path.isfile(cli_exec_path):
            pblog.error(f"CLI executable not found at {cli_exec_path}")
            return 1

        # Remove binary package if it exists, gh is not able to overwrite existing files
        if os.path.exists(binary_package_name):
            try:
                os.remove(binary_package_name)
            except Exception as e:
                pblog.exception(str(e))
                pblog.error(
                    f"Exception thrown while removing {binary_package_name}. Please remove it manually."
                )
                return -1

        creds = get_token_env()

        try:
            proc = pbtools.run_with_combined_output(
                [
                    cli_exec_path,
                    "release",
                    "download",
                    version_number,
                    "-n" if "glab" in cli_exec_path else "-p",
                    binary_package_name,
                ],
                env=creds,
            )
            output = proc.stdout
            if proc.returncode == 0:
                pass
            elif pbtools.it_has_any(output, "release not found", "no assets"):
                pblog.error(
                    f"Release {version_number} not found. Please wait and try again later."
                )
                return -1
            elif "The file exists" in output:
                pblog.error(
                    f"File {binary_package_name} was not able to be overwritten. Please remove it manually and run UpdateProject again."
                )
                return -1
            else:
                pblog.error(
                    f"Unknown error occurred while pulling binaries for release {version_number}"
                )
                pblog.error(f"Command output was: {output}")
                return 1
        except Exception as e:
            pblog.exception(str(e))
            pblog.error(f"Exception thrown while pulling binaries for {version_number}")
            return 1

        if not pbtools.compare_hash_single(binary_package_name, checksum_json_path):
            return 1

    pbunreal.ensure_ue_closed()

    # Temp fix for Binaries folder with unnecessary content
    if os.path.isdir("Binaries"):
        try:
            shutil.rmtree("Binaries")
        except Exception as e:
            pblog.exception(str(e))
            pblog.error("Exception thrown while cleaning Binaries folder")
            return 1
    try:
        with ZipFile(binary_package_name) as zip_file:
            zip_file.extractall()
            if pass_checksum:
                return 0
            elif not pbtools.compare_hash_all(checksum_json_path, True):
                return 1

    except Exception as e:
        pblog.exception(str(e))
        pblog.error(
            f"Exception thrown while extracting binary package for {version_number}"
        )
        return 1

    return 0


def generate_release():
    version = pbunreal.get_latest_project_version()
    cli_exec_path = get_cli_executable()

    if version is None:
        pbtools.error_state("Failed to get project version!")
    target_branch = pbconfig.get("expected_branch_names")[0]
    proc = pbtools.run_with_combined_output(
        [pbgit.get_git_executable(), "rev-parse", version, "--"]
    )
    if proc.returncode == 0:
        pblog.error("Tag already exists. Not creating a release.")
        pblog.info(
            "Please use --autoversion {major,minor,patch} if you'd like to make a new version."
        )
        return
    proc = pbtools.run_with_combined_output(
        [pbgit.get_git_executable(), "tag", version]
    )
    pblog.info(proc.stdout)
    proc = pbtools.run_with_combined_output(
        [pbgit.get_git_executable(), "push", "origin", version]
    )
    pblog.info(proc.stdout)
    if not os.path.exists(pbinfo.format_repo_folder(chglog_executable_path)):
        pbtools.error(
            f"git-chglog executable not found at {pbinfo.format_repo_folder(chglog_executable_path)}"
        )
    proc = pbtools.run_with_combined_output(
        [
            pbinfo.format_repo_folder(chglog_executable_path),
            "-c",
            pbinfo.format_repo_folder(chglog_config_path),
            "-o",
            release_file,
            version,
        ]
    )
    if proc.returncode != 0:
        os.remove(release_file)
        pbtools.error_state(proc.stdout)
    else:
        pblog.info(proc.stdout)

    if pbconfig.get("is_ci"):
        creds = None
    else:
        creds = get_token_env()
    if not os.path.exists(cli_exec_path):
        pbtools.error(f"CLI executable not found at {cli_exec_path}")

    cmds = [
        cli_exec_path,
        "release",
        "create",
        version,
        binary_package_name,
        "-F",
        release_file,
    ]

    if cli_exec_path == pbinfo.format_repo_folder(gh_executable_path):
        gh_cmds = ["--target", target_branch, "-t", version]
        cmds.extend(gh_cmds)

    proc = pbtools.run_with_combined_output(cmds, env=creds)
    if proc.returncode != 0:
        os.remove(release_file)
        pbtools.error_state(proc.stdout)
    else:
        pblog.info(proc.stdout)
    os.remove(release_file)
