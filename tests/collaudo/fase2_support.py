# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Impalcatura del collaudo della fase 2: la pila vera della sincronizzazione senza rete.

Profilo vero (SQLite, archivio su disco, lock), `SyncWorker` e `GarminConnectAccount` veri; di finto
c'e solo il client di `garminconnect` (`ScriptedClient`/`ScriptedFactory`) e, dove serve, il
portachiavi in memoria. Nessun login reale e nessuna chiamata di rete.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ponos_coach.__main__ import extract_original_activity
from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import RemoteActivity, SyncPolicy, SyncStatus
from ponos_coach.activities.sync import SyncMode, SyncProgress
from ponos_coach.garmin.account import GarminConnectAccount
from ponos_coach.garmin.source import GarminActivitySource
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.ports import TokenStore
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.ui.sync_model import SyncResult
from ponos_coach.ui.sync_worker import Credentials, SyncServices, SyncWorker
from tests.collaudo.support import (
    RealProfile,
    create_profile,
    open_profile_from_folder,
    real_ports,
)
from tests.fakes.garmin import make_remote_history, original_zip
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.tokens import InMemoryTokenStore
from tests.fixtures.generators.sync_harness import (
    FakeSyncClock,
    MidpointRandom,
    SyncRig,
    in_memory_rig,
)
from tests.garmin.scripted import ScriptedClient, ScriptedFactory, entry_for, newest_first

FAST = SyncPolicy(min_pause_s=0.001, max_jitter_s=0.0, backoff_base_s=0.01, backoff_cap_s=0.05)
EMAIL = "atleta@example.invalid"
PASSWORD = "password-di-prova"  # noqa: S105 - segnaposto
MFA_CODE = "MFA-7r4Qx9"


def scripted_client(
    count: int = 0,
    *,
    first: int = 0,
    history: Sequence[RemoteActivity] | None = None,
    **options: object,
) -> ScriptedClient:
    """Un client finto con le corse `first..first+count-1` (elenco dalla più recente, ZIP veri)."""
    runs = list(history) if history is not None else make_remote_history(first + count)[first:]
    ordered = newest_first(runs)
    downloads = {a.garmin_id.value: original_zip(a.garmin_id.value - 1_000_000) for a in runs}
    return ScriptedClient(
        [entry_for(a) for a in ordered],
        downloads=downloads,
        **options,  # type: ignore[arg-type]
    )


@dataclass(slots=True)
class Stack:
    """Profilo vero + worker vero + client finto, con tutto ciò che serve per osservarli."""

    profile: RealProfile
    worker: SyncWorker
    factory: ScriptedFactory
    tokens: TokenStore
    profile_id: ProfileId
    clock: FakeSyncClock = field(default_factory=FakeSyncClock)

    @property
    def folder(self) -> Path:
        return self.profile.folder

    def link(self, mfa_code: str | None = None) -> object:
        """Collega l'account come farebbe l'utente."""
        return self.worker.log_in(
            self.profile_id,
            Credentials(EMAIL, PASSWORD),
            lambda: mfa_code,
            threading.Event(),
        )

    def sync(
        self,
        mode: SyncMode = SyncMode.SYNC,
        *,
        cancelled: threading.Event | None = None,
        report: Callable[[SyncProgress], None] | None = None,
    ) -> SyncResult:
        return self.worker.synchronize(
            self.profile_id,
            self.folder,
            mode,
            automatic=False,
            cancelled=cancelled or threading.Event(),
            report=report or (lambda _progress: None),
        )

    def journal_counts(self) -> Mapping[SyncStatus, int]:
        return self.profile.session.sync_journal.counts()

    def close(self) -> None:
        self.profile.close()


def attach_stack(
    profile: RealProfile,
    client: ScriptedClient | None = None,
    *,
    tokens: TokenStore | None = None,
    policy: SyncPolicy = FAST,
    linked: bool = True,
) -> Stack:
    """Mette worker e client finto sopra un profilo già aperto."""
    factory = ScriptedFactory(client or scripted_client())
    store: TokenStore = tokens if tokens is not None else InMemoryTokenStore()
    clock = FakeSyncClock()
    services = SyncServices(
        account=GarminConnectAccount(factory),
        tokens=store,
        extract_original=extract_original_activity,
        decode=decode_stored_activity,
        clock=clock,
        make_rng=MidpointRandom,
        notifier=RecordingNotifier(),
        policy=policy,
    )
    worker = SyncWorker(services, SqliteProfileStorage(), FileArchive.in_profile_folder)
    stack = Stack(profile, worker, factory, store, profile.session.info.profile_id, clock)
    if linked:
        stack.link()
    return stack


def build_stack(
    base: Path,
    client: ScriptedClient | None = None,
    *,
    tokens: TokenStore | None = None,
    policy: SyncPolicy = FAST,
    folder_name: str = "profilo",
    linked: bool = True,
) -> Stack:
    """Costruisce la pila su un profilo nuovo in `base / folder_name`."""
    profile = create_profile(base, folder_name=folder_name)
    return attach_stack(profile, client, tokens=tokens, policy=policy, linked=linked)


def reopen_stack(
    base: Path,
    folder: Path,
    client: ScriptedClient | None = None,
    *,
    tokens: TokenStore | None = None,
    policy: SyncPolicy = FAST,
    linked: bool = False,
) -> Stack:
    """Riapre come l'app al riavvio un profilo che c'è già (stessa configurazione di `base`)."""
    profile = open_profile_from_folder(real_ports(base / "config"), folder)
    return attach_stack(profile, client, tokens=tokens, policy=policy, linked=linked)


def adapter_rig(client: ScriptedClient, count_in_memory: int = 0) -> SyncRig:
    """Un rig in memoria (attesa e orologio finti, niente `sleep`) la cui sorgente è
    l'adattatore Garmin vero sopra `client`: si prova la traduzione dei guasti e dei tetti."""
    rig = in_memory_rig(count_in_memory)
    rig.source = GarminActivitySource(client)
    return rig
