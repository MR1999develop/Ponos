# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Chiamate Win32 (via ctypes) con interfaccia sicura: cartelle private e tipo di unità.

Perché ctypes: serve un descrittore di sicurezza esplicito alla creazione della cartella (la
DACL di default contiene il SID della sessione di accesso, effimero, e le ereditate possono
aprire la cartella ad altri utenti) e la libreria standard non lo permette. Tutta la memoria
che Windows alloca (SID in stringa, descrittore) è liberata qui con `LocalFree`; fuori da
questo modulo non esistono puntatori né handle.

Su sistemi diversi da Windows le funzioni sollevano `NotImplementedError`.
"""

from __future__ import annotations

import sys
from pathlib import Path

DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOTE = 4

# Solo l'utente corrente e SYSTEM, accesso completo, ereditabile da file e cartelle; la `P`
# rende la DACL protetta (non eredita dal padre).
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
        """SID dell'utente del token in forma di stringa (`S-1-5-21-...`)."""
        needed = wintypes.DWORD(0)
        _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
            _raise_last_error()
        buffer = ctypes.create_string_buffer(needed.value)
        if not _advapi32.GetTokenInformation(
            token, _TOKEN_USER, buffer, needed.value, ctypes.byref(needed)
        ):
            _raise_last_error()
        # TOKEN_USER inizia con SID_AND_ATTRIBUTES, il cui primo campo è il puntatore al SID,
        # dentro `buffer`, che resta vivo fino alla fine della funzione.
        sid_pointer = ctypes.c_void_p.from_buffer(buffer).value
        text_pointer = ctypes.c_void_p()
        if not _advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text_pointer)):
            _raise_last_error()
        try:
            if text_pointer.value is None:
                raise OSError("SID in forma di testo non disponibile")
            return ctypes.wstring_at(text_pointer.value)
        finally:
            _kernel32.LocalFree(text_pointer)

    def current_user_sid() -> str:
        """SID dell'utente che esegue il processo, in forma di stringa."""
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
        """Descrittore (utente corrente + SYSTEM, DACL protetta); liberato all'uscita."""
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
        """Crea una cartella (un solo livello) accessibile solo all'utente e a SYSTEM.

        Il descrittore è applicato dalla creazione stessa: non c'è nessun istante in cui la
        cartella ha permessi più larghi.

        Raises:
            FileExistsError: la cartella esiste già.
            OSError: creazione non riuscita.
        """
        with _private_descriptor() as descriptor:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            if not _kernel32.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                _raise_last_error()

    def restrict_to_current_user(path: Path) -> None:
        """Sostituisce la DACL di una cartella esistente con quella privata, protetta.

        Raises:
            OSError: permessi non applicati.
        """
        with _private_descriptor() as descriptor:
            present = wintypes.BOOL()
            defaulted = wintypes.BOOL()
            dacl = ctypes.c_void_p()
            if not _advapi32.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
            ):
                _raise_last_error()
            # `dacl` punta dentro `descriptor`, vivo per tutto il blocco `with`.
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
        """Tipo dell'unità (`GetDriveTypeW`): 4 = di rete, 0 o 1 = sconosciuta."""
        return int(_kernel32.GetDriveTypeW(root))

    def _blob_of(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        """Blob su una copia di `data`; il buffer va tenuto vivo finché serve il blob."""
        buffer = ctypes.create_string_buffer(data, max(len(data), 1))
        return _DataBlob(len(data), ctypes.cast(buffer, ctypes.c_void_p)), buffer

    def _crypt(data: bytes, entropy: bytes, *, protect: bool) -> bytes:
        source, source_buffer = _blob_of(data)
        extra, extra_buffer = _blob_of(entropy)
        result = _DataBlob()
        function = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        # Stessa forma di argomenti per le due funzioni (la descrizione è None e non serve).
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
        del source_buffer, extra_buffer  # vivi fino alla fine della chiamata
        try:
            return ctypes.string_at(result.pbData, result.cbData) if result.pbData else b""
        finally:
            _kernel32.LocalFree(result.pbData)

    def protect_data(data: bytes, entropy: bytes) -> bytes:
        """Cifra `data` con DPAPI (ambito utente) e un'entropia aggiuntiva.

        Raises:
            OSError: cifratura non riuscita.
        """
        return _crypt(data, entropy, protect=True)

    def unprotect_data(blob: bytes, entropy: bytes) -> bytes:
        """Decifra un blob di `protect_data` (stesso utente, stessa entropia).

        Raises:
            OSError: blob alterato, di un altro utente o con un'altra entropia.
        """
        return _crypt(blob, entropy, protect=False)

else:

    def protect_data(data: bytes, entropy: bytes) -> bytes:
        """Solo Windows."""
        raise NotImplementedError

    def unprotect_data(blob: bytes, entropy: bytes) -> bytes:
        """Solo Windows."""
        raise NotImplementedError

    def current_user_sid() -> str:
        """Solo Windows."""
        raise NotImplementedError

    def create_private_directory(path: Path) -> None:
        """Solo Windows."""
        raise NotImplementedError

    def restrict_to_current_user(path: Path) -> None:
        """Solo Windows."""
        raise NotImplementedError

    def drive_type(root: str) -> int:
        """Solo Windows."""
        raise NotImplementedError
