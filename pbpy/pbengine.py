import json
from pathlib import Path

from pbpy import pbgit, pbtools


def generate_module_changes(old_commitish, new_commitish):
    proc = pbtools.run_with_combined_output(
        [
            pbgit.get_git_executable(),
            "diff",
            "--cumulative",
            f"{old_commitish}...{new_commitish}",
        ]
    )
    if proc.returncode != 0:
        pbtools.error_state(proc.stdout)
    diffs = reversed(proc.stdout.splitlines())
    folders = set()
    for diff in diffs:
        if not diff.startswith(" "):
            continue
        folder = diff.split("% ")[1]
        folders.add(Path(folder))
    module_delta = dict()
    with open("modules.delta.json", "w") as f:
        json.dump(module_delta, f)
