# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# ruff: noqa: ANN002, ANN003, ANN202
"""Phase 8 test process: a backup or a restore that dies in the middle, like a power-off.

Usage: `python -I collaudo_fase8_worker.py <backup|restore> <source> <target> <kill_at>`.
`backup`: backs up the profile folder `source` to the file `target`. `restore`: restores the backup
file `source` into the folder `target`. At the `kill_at`-th file written (counted from 1) the
process ends with `os._exit(77)`, without any cleanup. With `kill_at` 0 nothing is killed and the
process prints `files <n>` (how many files the operation wrote); with `kill_at` beyond the last it
simply finishes (exit 0).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.profiles.backup import BackupProfile, RestoreProfile
from ponos_coach.profiles.domain import FolderKind
from ponos_coach.storage import profile_backup
from ponos_coach.storage.profile_backup import FileProfileBackup

DIED = 77


def main(mode: str, source: Path, target: Path, kill_at: int) -> int:
    counter = 0

    def tick() -> None:
        nonlocal counter
        counter += 1
        if counter == kill_at:
            os._exit(DIED)

    if mode == "backup":
        original_add = vars(profile_backup)["_add_file"]

        def counting_add(*args, **kwargs):  # type: ignore[no-untyped-def]
            tick()
            return original_add(*args, **kwargs)

        vars(profile_backup)["_add_file"] = counting_add
        BackupProfile(FileProfileBackup(), lambda: datetime(2026, 11, 8, tzinfo=UTC), "0.1.0")(
            source, target
        )
    else:
        original_replace = vars(profile_backup)["replace_file"]

        def counting_replace(*args, **kwargs):  # type: ignore[no-untyped-def]
            tick()
            return original_replace(*args, **kwargs)

        vars(profile_backup)["replace_file"] = counting_replace
        RestoreProfile(FileProfileBackup(), LocalProfileFolders(lambda _folder: FolderKind.LOCAL))(
            source, target
        )
    if kill_at == 0:
        sys.stdout.write(f"files {counter}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])))
