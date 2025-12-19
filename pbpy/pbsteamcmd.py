import os
import re
import shutil
import time
import traceback
from pathlib import Path

import gevent
import steam.protobufs.steammessages_partnerapps_pb2  # don't remove
from steam.client import SteamClient

from pbpy import pbconfig, pbhttp, pbinfo, pblog, pbtools

drm_upload_regex = re.compile(
    r"https:\/\/partnerupload\.steampowered\.com\/upload\/(\d+)"
)


class SteamWorker:
    def __init__(self):
        self.logged_on_once = False

        self.steam = worker = SteamClient()
        worker.set_credential_location(
            str((Path(pbconfig.config_filepath).parent / "Saved").resolve())
        )

        @worker.on("error")
        def handle_error(result):
            print("Logon result:", traceback.format_exc())

        @worker.on("connected")
        def handle_connected():
            print("Connected to", worker.current_server_addr)

        @worker.on("channel_secured")
        def send_login():
            if self.logged_on_once and self.steam.relogin_available:
                self.steam.relogin()

        @worker.on("logged_on")
        def handle_after_logon():
            self.logged_on_once = True

            print("⎯" * 30)
            print("Logged on as:", worker.user.name)
            print("Community profile:", worker.steam_id.community_url)
            print("Last logon:", worker.user.last_logon)
            print("Last logoff:", worker.user.last_logoff)
            print("⎯" * 30)

        @worker.on("disconnected")
        def handle_disconnect():
            print("Disconnected.")

            if self.logged_on_once:
                print("Reconnecting...")
                worker.reconnect(maxdelay=30)

        @worker.on("reconnect")
        def handle_reconnect(delay):
            print(
                f"Reconnect in {delay}...",
            )

    def login(self, username, password, no_2fa):
        two_factor_code = None if no_2fa else input("Enter 2FA code: ")
        if two_factor_code or os.path.exists(self.steam._get_sentry_path(username)):
            self.steam.login(username, password, two_factor_code=two_factor_code)
        else:
            self.steam.cli_login(username, password)

    def close(self):
        if self.steam.logged_on:
            self.logged_on_once = False
            print("Logout")
            self.steam.logout()
        if self.steam.connected:
            self.steam.disconnect()


def publish_build(
    branch_type,
    steamcmd_exec_path,
    publish_stagedir,
    app_script,
    drm_app_id,
    drm_exe_path,
    drm_useonprem,
):
    # Test if our configuration values exist
    if not app_script:
        pblog.error("steamcmd was not configured.")
        return False

    # The basic needed command line to get into steamcmd
    base_steamcmd_command = [
        steamcmd_exec_path,
        "+login",
        pbconfig.get_user("steamcmd", "username"),
        pbconfig.get_user("steamcmd", "password"),
    ]

    drm_active = False
    drm_id = None
    drm_download_failed = False

    def steam_log(log):
        nonlocal drm_id
        nonlocal drm_download_failed
        log = log.rstrip()
        if not log:
            return
        if drm_active:
            if log.startswith("Uploading") and "partnerupload" in log:
                search = drm_upload_regex.search(log)
                drm_id = search.group(1)
            if log == "DRM wrap failed with EResult 3 (No Connection)":
                drm_download_failed = True
        pblog.info("[steamcmd] " + log)

    # if drm wrapping is configured
    nondrm_bytes = None

    result = 0

    def push_app():
        nonlocal result
        script_path = (Path() / app_script.format(branch_type)).resolve()
        build_cmd = base_steamcmd_command.copy()
        build_cmd.extend(["+run_app_build", script_path, "+quit"])
        proc = pbtools.run_stream(build_cmd, logfunc=steam_log)
        result = proc.returncode

        if drm_exe_path and drm_exe_path.is_file():
            # remove drm wrapped file and write the original file, so that a subsequent build will re-build a non-wrapped executable
            pbtools.remove_file(drm_exe_path)
            with open(drm_exe_path, "wb") as orig_file:
                orig_file.write(nondrm_bytes)

    if drm_app_id and drm_exe_path:
        drm_exe_path = Path(drm_exe_path)
        if not drm_exe_path.is_absolute():
            drm_exe_path = (
                Path(pbconfig.config_filepath).parent / drm_exe_path
            ).resolve()
        if not drm_exe_path.is_file():
            pblog.error("steamcmd/drm/targetbinary does not exist.")
            return False
        thirdparty_path = Path(pbinfo.format_repo_folder("/thirdpartylegalnotices.txt"))
        thirdparty_dst = Path(publish_stagedir)
        if thirdparty_path.exists():
            for dst in thirdparty_dst.glob("*/"):
                shutil.copy(thirdparty_path, dst)
        if False:
            with open(drm_exe_path.parent / "steam_appid.txt", "w") as appid_file:
                appid_file.write(drm_app_id)
        drm_command = base_steamcmd_command.copy()
        drm_output = (
            Path(pbconfig.config_filepath).parent
            / Path("wrappedBin" + drm_exe_path.suffix)
        ).resolve()  # save file to wrappedBin.exe temporarily
        drm_command.extend(
            [
                "+drm_wrap",
                drm_app_id,
                str(drm_exe_path),
                str(drm_output),
                "drmtoolp",
                "6",
                "local" if drm_useonprem else "cloud",
                "+quit",
            ]
        )  # the drm wrap command https://partner.steamgames.com/doc/features/drm
        pblog.info("Wrapping game with Steamworks DRM...")
        drm_active = True
        drm_proc = pbtools.run_stream(drm_command, logfunc=steam_log)
        drm_active = False

        def handle_drm_file():
            nonlocal nondrm_bytes
            with open(drm_exe_path, "rb") as orig_file:
                nondrm_bytes = orig_file.read()
            pbtools.remove_file(str(drm_exe_path))  # remove original file on success
            shutil.move(
                str(drm_output), str(drm_exe_path)
            )  # move drm-wrapped file to location of original
            push_app()

        if drm_proc.returncode != 0:

            if drm_download_failed and drm_id:
                steamclient = SteamWorker()
                steamclient.login(
                    pbconfig.get_user("steamcmd", "username"),
                    pbconfig.get_user("steamcmd", "password"),
                    True,
                )

                while not steamclient.logged_on_once:
                    # TODO: doesn't work, "this operation would block forever"
                    # steamclient.steam.wait_event("logged_on")
                    gevent.sleep(1)

                resp = steamclient.steam.send_um_and_wait(
                    "PartnerApps.Download#1",
                    {
                        "file_id": f"/{drm_app_id}/{drm_id}/{drm_exe_path.name}_{drm_id}",
                        "app_id": int(drm_app_id),
                    },
                )
                url = resp.body.download_url
                if url:
                    pbhttp.download(url, str(drm_output))
                steamclient.close()

            if not drm_output.exists() and drm_download_failed:
                input(
                    f"DRM download failed, download the file from https://partner.steamgames.com/apps/drm/{drm_app_id} and place it at {drm_output}, then press enter"
                )
            handled_drm = False
            if drm_output.exists():
                handle_drm_file()
                handled_drm = True
            if (
                drm_proc.returncode != 0
                and not drm_download_failed
                or drm_download_failed
                and not handled_drm
            ):
                if drm_output.exists():
                    pbtools.remove_file(str(drm_output))
                pbtools.error_state(
                    f"DRM wrapping failed: exit code {drm_proc.returncode}",
                    hush=True,
                    term=True,
                )
        else:
            handle_drm_file()
    else:
        drm_exe_path = None
        push_app()

    return result
