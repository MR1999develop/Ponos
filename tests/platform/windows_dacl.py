# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Lettura del descrittore di sicurezza di un file o di una cartella (solo Windows, per i test).

Indipendente dal codice sotto prova: legge ciò che il sistema ha davvero registrato.
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
        """Forma estesa `S-1-...` di un SID scritto come alias SDDL (`LA`, `SY`) o per esteso."""
        sid = ctypes.c_void_p()
        if not _advapi32.ConvertStringSidToSidW(text, ctypes.byref(sid)):
            raise ctypes.WinError(ctypes.get_last_error())
        out = ctypes.c_void_p()
        try:
            if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(out)):
                raise ctypes.WinError(ctypes.get_last_error())
            if out.value is None:
                raise OSError("SID non disponibile")
            return ctypes.wstring_at(out.value)
        finally:
            _kernel32.LocalFree(out)
            _kernel32.LocalFree(sid)

    def dacl_sddl(path: Path) -> str:
        """DACL di `path` in forma SDDL, per esempio `D:P(A;OICI;FA;;;SY)`."""
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
                raise OSError("SDDL non disponibile")
            return ctypes.wstring_at(text.value)
        finally:
            _kernel32.LocalFree(text)
            _kernel32.LocalFree(descriptor)

else:

    def dacl_sddl(path: Path) -> str:
        """Solo Windows."""
        raise NotImplementedError

    def normalize_sid(text: str) -> str:
        """Solo Windows."""
        raise NotImplementedError


_ACE = re.compile(r"\(([^)]*)\)")


def trustees(sddl: str) -> list[str]:
    """Chi ha una voce nella DACL, come SID in forma estesa (gli alias `LA`, `SY`... si
    espandono), nell'ordine in cui compaiono. Si confrontano i SID, non le stringhe SDDL."""
    return [normalize_sid(ace.split(";")[5]) for ace in _ACE.findall(sddl)]


def is_protected(sddl: str) -> bool:
    """Vero se la DACL è protetta, cioè non eredita dal padre (flag `P`)."""
    return sddl.startswith("D:") and "P" in sddl[2 : sddl.index("(")]
