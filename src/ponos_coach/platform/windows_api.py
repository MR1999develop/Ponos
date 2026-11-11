# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Win32 calls (via ctypes) with a safe interface: private folders and drive type.

Why ctypes: an explicit security descriptor is needed when creating the folder (the
default DACL contains the SID of the logon session, ephemeral, and inherited ones may
open the folder to other users) and the standard library does not allow it. All the memory
that Windows allocates (SID string, descriptor) is freed here with `LocalFree`; outside
this module there are no pointers or handles.

On systems other than Windows the functions raise `NotImplementedError`.
"""

from __future__ import annotations

import sys
from pathlib import Path

DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOTE = 4

# Only the current user and SYSTEM, full access, inheritable by files and folders; the `P`
# makes the DACL protected (it does not inherit from the parent).
_SDDL_TEMPLATE = "D:P(A;OICI;FA;;;{user_sid})(A;OICI;FA;;;SY)"

if sys.platform == "win32":
    import contextlib
    import ctypes
    from collections.abc import Iterator
    from ctypes import wintypes

    _SDDL_REVISION_1 = 1
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1
    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 0x4
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _ERROR_INSUFFICIENT_BUFFER = 122

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    _kernel32.CreateDirectoryW.restype = wintypes.BOOL
    _kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetDriveTypeW.restype = wintypes.UINT

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    class _DataBlob(ctypes.Structure):
        _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p))

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL

    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    _advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    _advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = (
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        )

    def _raise_last_error() -> None:
        raise ctypes.WinError(ctypes.get_last_error())

    def _token_user_sid(token: wintypes.HANDLE) -> str:
        """SID of the token's user as a string (`S-1-5-21-...`)."""
        needed = wintypes.DWORD(0)
        _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
            _raise_last_error()
        buffer = ctypes.create_string_buffer(needed.value)
        if not _advapi32.GetTokenInformation(
            token, _TOKEN_USER, buffer, needed.value, ctypes.byref(needed)
        ):
            _raise_last_error()
        # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is the pointer to the SID,
        # inside `buffer`, which stays alive until the end of the function.
        sid_pointer = ctypes.c_void_p.from_buffer(buffer).value
        text_pointer = ctypes.c_void_p()
        if not _advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text_pointer)):
            _raise_last_error()
        try:
            if text_pointer.value is None:
                raise OSError("SID in text form not available")
            return ctypes.wstring_at(text_pointer.value)
        finally:
            _kernel32.LocalFree(text_pointer)

    def current_user_sid() -> str:
        """SID of the user running the process, as a string."""
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            _raise_last_error()
        try:
            return _token_user_sid(token)
        finally:
            _kernel32.CloseHandle(token)

    @contextlib.contextmanager
    def _private_descriptor() -> Iterator[ctypes.c_void_p]:
        """Descriptor (current user + SYSTEM, protected DACL); freed on exit."""
        sddl = _SDDL_TEMPLATE.format(user_sid=current_user_sid())
        descriptor = ctypes.c_void_p()
        if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            _raise_last_error()
        try:
            yield descriptor
        finally:
            _kernel32.LocalFree(descriptor)

    def create_private_directory(path: Path) -> None:
        """Creates a folder (a single level) accessible only to the user and SYSTEM.

        The descriptor is applied by the creation itself: there is no instant at which the
        folder has wider permissions.

        Raises:
            FileExistsError: the folder already exists.
            OSError: creation failed.
        """
        with _private_descriptor() as descriptor:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            if not _kernel32.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                _raise_last_error()

    def restrict_to_current_user(path: Path) -> None:
        """Replaces the DACL of an existing folder with the private, protected one.

        Raises:
            OSError: permissions not applied.
        """
        with _private_descriptor() as descriptor:
            present = wintypes.BOOL()
            defaulted = wintypes.BOOL()
            dacl = ctypes.c_void_p()
            if not _advapi32.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
            ):
                _raise_last_error()
            # `dacl` points inside `descriptor`, alive for the whole `with` block.
            code = _advapi32.SetNamedSecurityInfoW(
                str(path),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                dacl,
                None,
            )
            if code != 0:
                raise ctypes.WinError(code)

    def drive_type(root: str) -> int:
        """Drive type (`GetDriveTypeW`): 4 = network, 0 or 1 = unknown."""
        return int(_kernel32.GetDriveTypeW(root))

    def _blob_of(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        """Blob over a copy of `data`; the buffer must be kept alive as long as the blob is
        needed."""
        buffer = ctypes.create_string_buffer(data, max(len(data), 1))
        return _DataBlob(len(data), ctypes.cast(buffer, ctypes.c_void_p)), buffer

    def _crypt(data: bytes, entropy: bytes, *, protect: bool) -> bytes:
        source, source_buffer = _blob_of(data)
        extra, extra_buffer = _blob_of(entropy)
        result = _DataBlob()
        function = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        # Same argument shape for the two functions (the description is None and not needed).
        if not function(
            ctypes.byref(source),
            None,
            ctypes.byref(extra),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(result),
        ):
            _raise_last_error()
        del source_buffer, extra_buffer  # alive until the end of the call
        try:
            return ctypes.string_at(result.pbData, result.cbData) if result.pbData else b""
        finally:
            if not protect and result.pbData:  # the system buffer holds the plaintext
                ctypes.memset(result.pbData, 0, result.cbData)
            _kernel32.LocalFree(result.pbData)

    def protect_data(data: bytes, entropy: bytes) -> bytes:
        """Encrypts `data` with DPAPI (user scope) and an additional entropy.

        Raises:
            OSError: encryption failed.
        """
        return _crypt(data, entropy, protect=True)

    def unprotect_data(blob: bytes, entropy: bytes) -> bytes:
        """Decrypts a blob from `protect_data` (same user, same entropy).

        Raises:
            OSError: blob altered, from another user or with another entropy.
        """
        return _crypt(blob, entropy, protect=False)

else:

    def protect_data(data: bytes, entropy: bytes) -> bytes:
        """Windows only."""
        raise NotImplementedError

    def unprotect_data(blob: bytes, entropy: bytes) -> bytes:
        """Windows only."""
        raise NotImplementedError

    def current_user_sid() -> str:
        """Windows only."""
        raise NotImplementedError

    def create_private_directory(path: Path) -> None:
        """Windows only."""
        raise NotImplementedError

    def restrict_to_current_user(path: Path) -> None:
        """Windows only."""
        raise NotImplementedError

    def drive_type(root: str) -> int:
        """Windows only."""
        raise NotImplementedError
