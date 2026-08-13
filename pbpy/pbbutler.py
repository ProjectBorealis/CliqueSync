import platform
import shutil
from pathlib import Path

from pbpy import pblog, pbtools, pbunreal


def publish_build(
    branch_type, butler_exec_path, publish_stagedir, butler_project, butler_manifest
):
    # Test if our configuration values exist
    if not butler_project or not butler_manifest:
        pblog.error("butler was not configured.")
        return False

    if branch_type == "default":
        branch_type = "internal"

    plat = platform.system()
    if plat == "Windows":
        plat = "win"
    elif plat == "Darwin":
        plat = "mac"
    elif plat == "Linux":
        plat = "linux"
    else:
        plat = plat.lower()

    publish_stagedir = pbunreal.get_uproject_folder() / Path(publish_stagedir)
    manifest_path = publish_stagedir / ".itch.toml"

    shutil.copyfile(butler_manifest.format(plat), manifest_path)

    channel = f"{branch_type}-{plat}"

    # Push and Publish the build
    proc = pbtools.run(
        [
            butler_exec_path,
            "push",
            str(publish_stagedir),
            f"{butler_project}:{channel}",
            "--userversion",
            pbunreal.get_project_version(),
        ]
    )
    result = proc.returncode
    manifest_path.unlink()
    return result
