# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Reading the security descriptor of a file or folder (Windows only, for tests).

Independent of the code under test: it reads what the system really recorded.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 4
    _SDDL_REVISION_1 = 1

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    _advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    def normalize_sid(text: str) -> str:
        """Extended `S-1-...` form of a SID written as an SDDL alias (`LA`, `SY`) or in full."""
        sid = ctypes.c_void_p()
        if not _advapi32.ConvertStringSidToSidW(text, ctypes.byref(sid)):
            raise ctypes.WinError(ctypes.get_last_error())
        out = ctypes.c_void_p()
        try:
            if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(out)):
                raise ctypes.WinError(ctypes.get_last_error())
            if out.value is None:
                raise OSError("SID not available")
            return ctypes.wstring_at(out.value)
        finally:
            _kernel32.LocalFree(out)
            _kernel32.LocalFree(sid)

    def dacl_sddl(path: Path) -> str:
        """DACL of `path` in SDDL form, for example `D:P(A;OICI;FA;;;SY)`."""
        descriptor = ctypes.c_void_p()
        code = _advapi32.GetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            None,
            None,
            None,
            None,
            ctypes.byref(descriptor),
        )
        if code != 0:
            raise ctypes.WinError(code)
        text = ctypes.c_void_p()
        try:
            if not _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(text), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if text.value is None:
                raise OSError("SDDL not available")
            return ctypes.wstring_at(text.value)
        finally:
            _kernel32.LocalFree(text)
            _kernel32.LocalFree(descriptor)

else:

    def dacl_sddl(path: Path) -> str:
        """Windows only."""
        raise NotImplementedError

    def normalize_sid(text: str) -> str:
        """Windows only."""
        raise NotImplementedError


_ACE = re.compile(r"\(([^)]*)\)")


def trustees(sddl: str) -> list[str]:
    """Who has an entry in the DACL, as SIDs in extended form (the aliases `LA`, `SY`... are
    expanded), in the order they appear. SIDs are compared, not SDDL strings."""
    return [normalize_sid(ace.split(";")[5]) for ace in _ACE.findall(sddl)]


def is_protected(sddl: str) -> bool:
    """True if the DACL is protected, i.e. does not inherit from the parent (flag `P`)."""
    return sddl.startswith("D:") and "P" in sddl[2 : sddl.index("(")]
