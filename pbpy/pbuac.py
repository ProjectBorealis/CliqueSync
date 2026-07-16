"""User Access Control for Microsoft Windows Vista and higher.  This is only for the Windows platform.

This will relaunch either the current script - with all the same command line parameters - or
else you can provide a different script/program to run.  If the current user doesn't normally
have admin rights, they'll be prompted for an admin password. Otherwise they'll just get the UAC prompt.

Note that the prompt may simply shows a generic python.exe with "Publisher: Unknown" if the
python.exe is not signed. However, the standard python.org binaries are signed.

This is meant to be used something like this:

>>> import pbuac

>>> if __name__ == "__main__":
...    if not pbuac.is_user_admin():
...        return pbuac.run_as_admin()
...    # otherwise carry on doing whatever...
...    main()

See also this utility function which runs a function as admin and captures the stdout/stderr:

run_function_as_admin(my_main_function)

https://github.com/Preston-Landers/pyuac
https://gist.github.com/JesterEE/c946375652761b020dc2d9c82694b25b

MIT License

Copyright (c) 2020 Preston Landers

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

"""

import os
import tempfile
import traceback
from pathlib import Path
from subprocess import CompletedProcess, list2cmdline

from pbpy import pblog


def is_user_admin():
    """Check if the current OS user is an Administrator or root.

    :return: True if the current user is an 'Administrator', otherwise False.
    """
    if os.name == "nt":
        import win32security

        try:
            adminSid = win32security.CreateWellKnownSid(
                win32security.WinBuiltinAdministratorsSid, None
            )
            rv = win32security.CheckTokenMembership(None, adminSid)
            return rv
        except:
            traceback.print_exc()
            pblog.warning("Admin check failed, assuming not an admin.")
            return False
    else:
        # TODO: for now, return true since the accompanying run_as_admin in not implemented
        # Check for root on Posix
        # return os.getuid() == 0
        return True


def _build_env_cmds(env):
    if not env:
        return []
    return [f'set "{key}={value}"' for key, value in env.items()]


def _run_via_shell_execute(
    cmd,
    params,
    wait=True,
    show_cmd=False,
    cwd=None,
) -> CompletedProcess[str]:
    import pywintypes
    import win32con
    import win32event
    import win32process

    # noinspection PyUnresolvedReferences
    from win32com.shell import shellcon

    # noinspection PyUnresolvedReferences
    from win32com.shell.shell import ShellExecuteEx

    showCmdArg = win32con.SW_SHOWNORMAL if show_cmd else win32con.SW_HIDE

    try:
        procInfo = ShellExecuteEx(
            nShow=showCmdArg,
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
            lpVerb="runas",
            lpFile=cmd,
            lpParameters=params,
            lpDirectory=str(cwd) if cwd else None,
        )
    except pywintypes.error as e:
        raise OSError("Failed to execute command as admin.") from e

    cmd_line = f"{cmd} {params}" if params else cmd

    if wait:
        procHandle = procInfo["hProcess"]
        _ = win32event.WaitForSingleObject(procHandle, win32event.INFINITE)
        ret = win32process.GetExitCodeProcess(procHandle)
        return CompletedProcess(cmd_line, ret)
    return CompletedProcess(cmd_line, 0)


def _run_as_admin_with_capture(
    cmd_line,
    show_cmd=False,
    cwd=None,
    env=None,
    combine_output=False,
    encoding="utf-8",
    errors="replace",
) -> CompletedProcess[str]:
    if cwd:
        cwd = str(Path(cwd).resolve())

    temp_files = []
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as out_tmp:
            out_path = Path(out_tmp.name)
        temp_files.append(out_path)

        err_path = None
        if not combine_output:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as err_tmp:
                err_path = Path(err_tmp.name)
            temp_files.append(err_path)

        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".cmd", mode="w"
        ) as script:
            script_path = Path(script.name)
            script.write("@echo off\n")
            if cwd:
                script.write(f'cd /d "{cwd}"\n')
            for env_cmd in _build_env_cmds(env):
                script.write(f"{env_cmd}\n")
            cmd = list2cmdline(cmd_line)
            if combine_output:
                script.write(f'{cmd} 1>"{out_path}" 2>&1\n')
            else:
                script.write(f'{cmd} 1>"{out_path}" 2>"{err_path}"\n')
            script.write("exit /b %ERRORLEVEL%\n")
        temp_files.append(script_path)

        proc = _run_via_shell_execute(
            "cmd.exe",
            "/d /c " + list2cmdline([str(script_path)]),
            wait=True,
            show_cmd=show_cmd,
            cwd=cwd,
        )

        stdout = out_path.read_text(encoding=encoding, errors=errors)
        if err_path:
            stderr = err_path.read_text(encoding=encoding, errors=errors)
        else:
            stderr = ""
        return CompletedProcess(cmd_line, proc.returncode, stdout=stdout, stderr=stderr)
    finally:
        for temp_file in temp_files:
            try:
                temp_file.unlink(missing_ok=True)
            except Exception:
                pass


def run_as_admin(
    cmd_line,
    wait=True,
    show_cmd=False,
    cwd=None,
    env=None,
    capture_output=False,
    combine_output=False,
    encoding="utf-8",
    errors="replace",
) -> CompletedProcess[str]:
    """
    Attempt to relaunch the current script as an admin using the same command line parameters.

    WARNING: this function only works on Windows. Future support for Posix might be possible.
    Calling this from other than Windows will raise a RuntimeError.

    :param cmd_line: set the command line of the program being launched as admin.
    It must be a list in [command, arg1, arg2...] format.

    :param wait: Set to False to avoid waiting for the sub-process to finish. You will not
    be able to fetch the exit code of the process if wait is False.

    :param show_cmd: Set to True to show the command window of the process being launched.

    :param cwd: Optional working directory for the elevated process.

    :param env: Optional environment variables to set for the elevated process.

    :param capture_output: If True, capture stdout/stderr

    :param combine_output: If True and capture_output is enabled, combine stderr into stdout.

    :returns: CompletedProcess
    """

    if os.name != "nt":
        raise RuntimeError("This function is only implemented on Windows.")

    if not cmd_line or not isinstance(cmd_line, (tuple, list)):
        raise ValueError("cmd_line is not a sequence.")

    if capture_output and not wait:
        raise ValueError("capture_output requires wait=True")

    if capture_output:
        return _run_as_admin_with_capture(
            cmd_line,
            show_cmd=show_cmd,
            cwd=cwd,
            env=env,
            combine_output=combine_output,
            encoding=encoding,
            errors=errors,
        )

    # ShellExecuteEx does not directly support passing env. Use a wrapper cmd script.
    if env:
        if not wait:
            raise ValueError("env support requires wait=True")
        return _run_as_admin_with_capture(
            cmd_line,
            show_cmd=show_cmd,
            cwd=cwd,
            env=env,
            combine_output=True,
            encoding=encoding,
            errors=errors,
        )

    cmd = cmd_line[0]
    params = list2cmdline(cmd_line[1:])
    return _run_via_shell_execute(cmd, params, wait=wait, show_cmd=show_cmd, cwd=cwd)


def run_as_admin_with_output(
    cmd_line, show_cmd=False, cwd=None, env=None
) -> CompletedProcess[str]:
    """Run a command elevated and capture stdout/stderr separately."""
    return run_as_admin(
        cmd_line,
        wait=True,
        show_cmd=show_cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
    )


def run_as_admin_with_combined_output(
    cmd_line, show_cmd=False, cwd=None, env=None
) -> CompletedProcess[str]:
    """Run a command elevated and capture combined stdout/stderr."""
    return run_as_admin(
        cmd_line,
        wait=True,
        show_cmd=show_cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        combine_output=True,
    )
