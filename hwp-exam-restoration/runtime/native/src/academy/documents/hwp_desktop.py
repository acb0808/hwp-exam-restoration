"""Read-only Windows token preflight for desktop COM activation.

An unrestricted token is not the same as an administrator/elevated token.
This check requests TOKEN_QUERY only and never changes the caller's token.
"""
from __future__ import annotations
import sys

def desktop_activation_allowed() -> bool | None:
    """True for unrestricted Windows token; False restricted; None unknown.

    Unknown (including non-Windows) must block Hancom COM activation. A True
    result is only this preflight result, not a guarantee of COM availability.
    """
    if sys.platform != 'win32':
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        current_process = kernel.GetCurrentProcess
        current_process.argtypes = []
        current_process.restype = wintypes.HANDLE
        open_token = advapi.OpenProcessToken
        open_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        open_token.restype = wintypes.BOOL
        is_restricted = advapi.IsTokenRestricted
        is_restricted.argtypes = [wintypes.HANDLE]
        is_restricted.restype = wintypes.BOOL
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        token = wintypes.HANDLE()
        if not open_token(current_process(), 0x0008, ctypes.byref(token)):
            return None
        try:
            ctypes.set_last_error(0)
            restricted = bool(is_restricted(token))
            if not restricted and ctypes.get_last_error():
                return None
            return not restricted
        finally:
            close(token)
    except Exception:
        return None
