from urllib.parse import urlparse
from functools import lru_cache

from pbpy import pbconfig


@lru_cache()
def get_repo_folder():
    repo_folder = pbconfig.get("repo_folder")
    if repo_folder and repo_folder != "default":
        return repo_folder

    git_url = pbconfig.get("git_url")
    if not git_url:
        return "Tools"

    hostname = urlparse(git_url).hostname

    if hostname == "github.com":
        return ".github"
    elif hostname == "gitlab.com":
        return ".gitlab"
    else:
        # Fall back to gitlab path as that's most likely
        # what our provider will be if we can't determine
        return ".gitlab"


@lru_cache()
def format_repo_folder(base):
    return f"{get_repo_folder()}{base}"
