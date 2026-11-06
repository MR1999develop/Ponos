# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Lettura sicura di ZIP non fidati: modulo di basso livello che non importa il progetto."""

from ponos_coach.zipread.archive import (
    ACTIVITY_SUFFIXES,
    ZIP_SUFFIX,
    ZipActivity,
    ZipLimits,
    ZipReadError,
    ZipReason,
    entry_issue,
    extract_entry,
    open_checked_zip,
    read_activity_entry,
)
from ponos_coach.zipread.names import (
    file_suffix,
    has_invalid_windows_chars,
    is_reserved_name,
    is_safe_entry_name,
)

__all__ = [
    "ACTIVITY_SUFFIXES",
    "ZIP_SUFFIX",
    "ZipActivity",
    "ZipLimits",
    "ZipReadError",
    "ZipReason",
    "entry_issue",
    "extract_entry",
    "file_suffix",
    "has_invalid_windows_chars",
    "is_reserved_name",
    "is_safe_entry_name",
    "open_checked_zip",
    "read_activity_entry",
]
