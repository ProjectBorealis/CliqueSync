"""User Access Control for Microsoft Windows Vista and higher.  This is
only for the Windows platform.

This will relaunch either the current script - with all the same command
line parameters - or else you can provide a different script/program to
run.  If the current user doesn't normally have admin rights, he'll be
prompted for an admin password. Otherwise he just gets the UAC prompt.

Note that the prompt may simply shows a generic python.exe with "Publisher:
Unknown" if the python.exe is not signed.

This is meant to be used something like this::

    if not pyuac.isUserAdmin():
        return pyuac.runAsAdmin()

    # otherwise carry on doing whatever...

See L{runAsAdmin} for the main interface.

https://gist.github.com/sylvainpelissier/ff072a6759082590a4fe8f7e070a4952

"""

import os
import traceback

from pbpy import pblog


def isUserAdmin():
    """@return: True if the current user is an 'Admin' whatever that
    means (root on Unix), otherwise False.

    Warning: The inner function fails unless you have Windows XP SP2 or
    higher. The failure causes a traceback to be printed and this
    function to return False.
    """

    if os.name == "nt":
        import ctypes

        # WARNING: requires Windows XP SP2 or higher!
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            traceback.print_exc()
            pblog.warning("Admin check failed, assuming not an admin.")
            return False
    else:
        # Check for root on Posix
        return os.getuid() == 0


def runAsAdmin(cmdLine=None, wait=True):
    """Attempt to relaunch the current script as an admin using the same
    command line parameters.  Pass cmdLine in to override and set a new
    command.  It must be a list of [command, arg1, arg2...] format.

    Set wait to False to avoid waiting for the sub-process to finish. You
    will not be able to fetch the exit code of the process if wait is
    False.

    Returns the sub-process return code, unless wait is False in which
    case it returns None.

    @WARNING: this function only works on Windows.
    """

    if os.name != "nt":
        raise RuntimeError("This function is only implemented on Windows.")

    import pywintypes
    import win32con
    import win32event
    import win32process
    from win32com.shell import shellcon
    from win32com.shell.shell import ShellExecuteEx

    if not isinstance(cmdLine, (tuple, list)):
        raise ValueError("cmdLine is not a sequence.")
    cmd = '"%s"' % (cmdLine[0],)
    # XXX TODO: isn't there a function or something we can call to massage command line params?
    params = " ".join(["%s" % (x,) for x in cmdLine[1:]])
    # cmdDir = ''
    showCmd = win32con.SW_SHOWNORMAL
    lpVerb = "runas"  # causes UAC elevation prompt.

    # print "Running", cmd, params

    # ShellExecute() doesn't seem to allow us to fetch the PID or handle
    # of the process, so we can't get anything useful from it. Therefore
    # the more complex ShellExecuteEx() must be used.

    # procHandle = win32api.ShellExecute(0, lpVerb, cmd, params, cmdDir, showCmd)

    try:
        procInfo = ShellExecuteEx(
            nShow=showCmd,
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
            lpVerb=lpVerb,
            lpFile=cmd,
            lpParameters=params,
        )
    except pywintypes.error:
        raise OSError

    if wait:
        procHandle = procInfo["hProcess"]
        win32event.WaitForSingleObject(procHandle, win32event.INFINITE)
        rc = win32process.GetExitCodeProcess(procHandle)
    else:
        rc = None

    return rc
