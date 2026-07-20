import os
import os.path
import shutil
from functools import lru_cache
from urllib.parse import urlparse
from zipfile import ZipFile

from pbpy import pbconfig, pbgit, pbinfo, pblog, pbtools, pbunreal

gh_executable_path = "git/gh"
chglog_executable_path = "git/git-chglog"
glab_executable_path = "git/glab"
chglog_config_path = "chglog.yml"
release_file = "RELEASE_MSG"
binary_package_name = "Binaries.zip"


@lru_cache()
def get_git_provider(git_url=None):
    provider = pbconfig.get("git_provider")
    if provider:
        return provider.lower()

    if not git_url:
        git_url = pbconfig.get("git_url")

    if not git_url:
        # try to parse from git remote -v
        output = pbtools.get_combined_output(
            [pbgit.get_git_executable(), "remote", "-v"]
        )
        for line in output.splitlines():
            if "origin" in line and "(fetch)" in line:
                git_url = line.split()[1]
                break

    if git_url:
        hostname = urlparse(git_url).hostname
        if hostname == "github.com":
            return "github"
        elif hostname == "gitlab.com" or "gitlab" in hostname:
            return "gitlab"

    return "none"


@lru_cache()
def get_token_var(git_url=None):
    provider = get_git_provider(git_url)
    if provider == "github":
        return "GITHUB_TOKEN"
    elif provider == "gitlab":
        return "GITLAB_TOKEN"
    else:
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
    provider = get_git_provider(git_url)
    if provider == "github":
        return pbinfo.format_repo_folder(pbtools.get_executable_filepath(gh_executable_path))
    elif provider == "gitlab":
        return pbinfo.format_repo_folder(pbtools.get_executable_filepath(glab_executable_path))
    else:
        return None


def download_release_file(
    version: str | None, pattern=None, directory=None, repo: str | None = None
):
    cli_exec_path = get_cli_executable(repo)

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
        if "*" in path:
            return True
        return False

    if pattern:
        if not isinstance(pattern, list):
            pattern = [pattern]
        for file in pattern:
            if not check_wildcard(file):
                res = try_remove(file)
                if res != 0:
                    return res
            elif "gh" in cli_exec_path:
                args.extend(["--clobber"])
            if "glab" in cli_exec_path:
                args.extend(["-n", file])
            else:
                args.extend(["-p", file])
    else:
        pattern = "*"
        if "gh" in cli_exec_path:
            args.extend(["--clobber"])

    creds = get_token_env(repo)

    if repo:
        repo = urlparse(repo).path[1:]
        args.extend(["-R", repo])

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
        pblog.error(
            f"Exception thrown while pulling release file {pattern} for {version}"
        )
        return 1

    return 0


def is_pull_binaries_required():
    if not pbconfig.get("binaries_cloud_storage"):
        cli = get_cli_executable()
        if not cli:
            pblog.error(
                "No method to pull binaries found. Either configure a git provider to use releases or use binaries_cloud_storage."
            )
            return False
        if not os.path.isfile(cli):
            pblog.error(
                f"CLI executable not found at {cli}, cannot pull binaries with git provider."
            )
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
        cs = pbconfig.get("binaries_cloud_storage")
        if not cs and not os.path.isfile(cli_exec_path):
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

        if cs:
            pblog.info(f"Pulling binaries from cloud storage ({cs})...")
            bucket_uri = pbunreal.get_binaries_gsuri()
            if not bucket_uri:
                pblog.error("Binaries cloud storage URI is not configured.")
                return 1

            if bucket_uri.endswith("/"):
                bucket_uri = bucket_uri[:-1]

            uses_longtail_cfg = pbunreal.uses_longtail()

            if uses_longtail_cfg:
                # Longtail fast incremental download
                longtail_path = pbunreal.get_longtail_path()

                project_name = pbconfig.get("project_name")
                args = [
                    longtail_path,
                    "get",
                    "--source-path",
                    f"{bucket_uri}/lt/{project_name}/{version_number}.json",
                    # "--target-path",
                    # str(pbinfo.get_root_path()), This function never existed, so assuming target path wasn't set anyway
                    "--enable-file-mapping",
                ]

                env, success = pbunreal.generate_cloud_storage_args_env(
                    cs, bucket_uri, args
                )
                if not success:
                    return 1

                proc = pbtools.run_stream(
                    args,
                    env=env,
                    logfunc=pbtools.progress_stream_log,
                )
                print("")
                if proc.returncode != 0:
                    pblog.error(
                        f"Failed to pull project binaries from cloud storage. Return code {proc.returncode}."
                    )
                    return 1

                # Extracting is not needed for longtail, it places files directly.
                # However, it doesn't place Binaries.zip, so we can't run `compare_hash_single` on Binaries.zip.
                # We need to skip `extract_binaries` step since files are already placed.
                return 0
            else:
                # Zip fallback download
                env, success = pbunreal.generate_cloud_storage_args_env(
                    cs, bucket_uri, []
                )
                if not success:
                    return 1

                args = []
                if cs == "s3":
                    endpoint_arg = []
                    if pbunreal.is_custom_s3_uri(bucket_uri):
                        endpoint = pbunreal.get_s3_endpoint_url()
                        if endpoint:
                            endpoint_arg = ["--endpoint-url", endpoint]
                    args = (
                        [pbunreal.get_aws_cli_path(), "s3", "cp"]
                        + endpoint_arg
                        + [
                            f"{bucket_uri}/{version_number}/{binary_package_name}",
                            binary_package_name,
                        ]
                    )
                elif cs == "gcs":
                    args = [
                        pbunreal.get_gsutil_path(),
                        "cp",
                        f"{bucket_uri}/{version_number}/{binary_package_name}",
                        binary_package_name,
                    ]

                try:
                    proc = pbtools.run_with_combined_output(args, env=env)
                    if proc.returncode != 0:
                        pblog.error(
                            f"Failed to download zip from cloud storage: {proc.stdout}"
                        )
                        return 1
                except Exception as e:
                    pblog.exception(str(e))
                    pblog.error(
                        f"Exception thrown while pulling binaries from cloud storage."
                    )
                    return 1
        else:
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
                pblog.error(
                    f"Exception thrown while pulling binaries for {version_number}"
                )
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

    has_git = os.path.isdir(".git")

    if has_git:
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

        changelog_executable = pbinfo.format_repo_folder(pbtools.get_executable_filepath(chglog_executable_path))
        if not os.path.exists(changelog_executable):
            pblog.error(
                f"git-chglog executable not found at {changelog_executable}"
            )
            # Create a fallback release file
            with open(release_file, "w") as f:
                f.write(f"Release {version}\n")
        else:
            proc = pbtools.run_with_combined_output(
                [
                    changelog_executable,
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
    else:
        # Create an empty release file if no git
        with open(release_file, "w") as f:
            f.write(f"Release {version}\n")

    cs = pbconfig.get("binaries_cloud_storage")

    if cs:
        bucket_uri = pbunreal.get_binaries_gsuri()
        if not bucket_uri:
            pbtools.error_state("Binaries cloud storage URI is not configured.")

        # Upload binaries to cloud storage
        if pbunreal.uses_longtail():
            # longtail put
            staged_dir = "Saved/StagedBinaries"
            if not os.path.exists(staged_dir):
                pbtools.error_state(
                    f"Staged binaries not found at {staged_dir}. Did you run package_binaries?"
                )

            args = [
                pbunreal.get_longtail_path(),
                "put",
                "--source-path",
                staged_dir,
                "--target-path",
                f"{bucket_uri}/{version}",
                "--enable-file-mapping",
            ]

            env, success = pbunreal.generate_cloud_storage_args_env(
                cs, bucket_uri, args
            )
            if not success:
                pbtools.error_state(
                    "Failed to generate cloud storage credentials/args."
                )

            pblog.info(f"Uploading binaries via Longtail to {bucket_uri}/{version}...")

            # Using run() so stdout streams directly to user
            proc = pbtools.run(args, env=env, priority="below_normal")
            if proc.returncode != 0:
                pbtools.error_state("Failed to upload binaries using Longtail.")
            else:
                pblog.info("Longtail upload successful.")

            # Clean up staged dir
            shutil.rmtree(staged_dir, ignore_errors=True)

        else:
            # zip fallback
            # TODO: deprecate this or fix calls to non-existing functions get_aws_cli_path, get_gsutil_path
            env, success = pbunreal.generate_cloud_storage_args_env(cs, bucket_uri, [])
            if not success:
                pbtools.error_state(
                    "Failed to generate cloud storage credentials/args."
                )

            if not os.path.exists(binary_package_name):
                pbtools.error_state(
                    f"{binary_package_name} not found. Did you run package_binaries?"
                )

            args = []
            if cs == "s3":
                endpoint_arg = []
                if pbunreal.is_custom_s3_uri(bucket_uri):
                    endpoint = pbunreal.get_s3_endpoint_url()
                    if endpoint:
                        endpoint_arg = ["--endpoint-url", endpoint]
                args = (
                    [pbunreal.get_aws_cli_path(), "s3", "cp"]
                    + endpoint_arg
                    + [
                        binary_package_name,
                        f"{bucket_uri}/{version}/{binary_package_name}",
                    ]
                )
            elif cs == "gcs":
                args = [
                    pbunreal.get_gsutil_path(),
                    "cp",
                    binary_package_name,
                    f"{bucket_uri}/{version}/{binary_package_name}",
                ]

            pblog.info(f"Uploading {binary_package_name} to cloud storage...")
            proc = pbtools.run_with_combined_output(args, env=env)
            if proc.returncode != 0:
                pbtools.error_state(
                    f"Failed to upload zip to cloud storage: {proc.stdout}"
                )
            else:
                pblog.info("Cloud storage upload successful.")

    if cli_exec_path and os.path.exists(cli_exec_path):
        if pbconfig.get("is_ci"):
            creds = None
        else:
            creds = get_token_env()

        cmds = [
            cli_exec_path,
            "release",
            "create",
            version,
            "-F",
            release_file,
        ]

        if not cs:
            # If not using cloud storage, attach the zip to the release
            cmds.insert(4, binary_package_name)

        if get_git_provider(None) == "github":
            if has_git:
                target_branch = pbconfig.get("expected_branch_names")[0]
                gh_cmds = ["--target", target_branch, "-t", version]
                cmds.extend(gh_cmds)

        pblog.info("Creating Git release...")
        proc = pbtools.run_with_combined_output(cmds, env=creds)
        if proc.returncode != 0:
            if os.path.exists(release_file):
                os.remove(release_file)
            pbtools.error_state(proc.stdout)
        else:
            pblog.info(proc.stdout)
    elif not cs:
        pbtools.error_state(
            f"CLI executable not found at {cli_exec_path} and cloud storage is not configured."
        )

    if os.path.exists(release_file):
        os.remove(release_file)
