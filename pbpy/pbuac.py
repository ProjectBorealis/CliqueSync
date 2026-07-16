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
import traceback
from subprocess import list2cmdline

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


def run_as_admin(cmd_line, wait=True, show_cmd=False):
    """
    Attempt to relaunch the current script as an admin using the same command line parameters.

    WARNING: this function only works on Windows. Future support for Posix might be possible.
    Calling this from other than Windows will raise a RuntimeError.

    :param cmd_line: set the command line of the program being launched as admin.
    It must be a list in [command, arg1, arg2...] format.

    :param wait: Set to False to avoid waiting for the sub-process to finish. You will not
    be able to fetch the exit code of the process if wait is False.

    :param show_cmd: Set to True to show the command window of the process being launched.

    :returns: the sub-process return code, unless wait is False, in which case it returns None.
    """

    if os.name != "nt":
        raise RuntimeError("This function is only implemented on Windows.")

    import pywintypes
    import win32con
    import win32event
    import win32process

    # noinspection PyUnresolvedReferences
    from win32com.shell import shellcon

    # noinspection PyUnresolvedReferences
    from win32com.shell.shell import ShellExecuteEx

    if not cmd_line or not isinstance(cmd_line, (tuple, list)):
        raise ValueError("cmd_line is not a sequence.")

    if show_cmd:
        showCmdArg = win32con.SW_SHOWNORMAL
    else:
        showCmdArg = win32con.SW_HIDE

    lpVerb = "runas"  # causes UAC elevation prompt.

    cmd = cmd_line[0]
    params = list2cmdline(cmd_line[1:])

    try:
        procInfo = ShellExecuteEx(
            nShow=showCmdArg,
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
            lpVerb=lpVerb,
            lpFile=cmd,
            lpParameters=params,
        )
    except pywintypes.error as e:
        raise OSError("Failed to execute command as admin.") from e

    if wait:
        procHandle = procInfo["hProcess"]
        _ = win32event.WaitForSingleObject(procHandle, win32event.INFINITE)
        rc = win32process.GetExitCodeProcess(procHandle)
    else:
        rc = None

    return rc
