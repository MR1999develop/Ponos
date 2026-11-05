# Registro delle dipendenze

Registro delle dipendenze dirette di `pyproject.toml`: esecuzione, gruppo
`dev` e backend di build. Validazione: **2026-11-05**.

## Regole del registro

- **Licenze ammesse** (compatibili con GPLv3): MIT, BSD, Apache-2.0, ISC, PSF,
  LGPL, GPL-3.0. Ogni altra licenza è un problema da riportare e non si
  approva senza una decisione esplicita, registrata in questo file.
- **Attesa sulle versioni nuove**: `exclude-newer = "7 days"` in
  `[tool.uv]` di `pyproject.toml`. Una versione pubblicata da meno di 7 giorni
  non entra nel lock. Eccezione: le correzioni di sicurezza, decise a mano e
  annotate qui, con il diff letto.
- **Vulnerabilità**: `uv run --locked nox -s audit` esporta le dipendenze di
  esecuzione dal lock e le controlla con `pip-audit` (`--require-hashes`).
  La sessione fa parte di `uv run --locked nox`, quindi della CI.
- **Ogni dipendenza diretta** ha una voce con: versione bloccata in
  `uv.lock`, licenza letta dai metadati del pacchetto installato, scopo nel
  progetto, data dell'ultima release, segni di manutenzione, alternative
  considerate e data della validazione.
- **Aggiornamento**: una versione nuova cambia la voce e riporta una nuova
  data di validazione.

## Esito della validazione (2026-11-05)

- **Audit** (`uv run --locked nox -s audit`): `No known vulnerabilities
  found`. Esito controllato anche su tutto l'ambiente, dev compreso, con
  `uv run --locked pip-audit`: nessuna vulnerabilità. Il pacchetto
  `ponos-coach` è saltato perché non è su PyPI: è atteso.
- **Licenze da riportare** (non approvate):
  - `hypothesis` 6.170.13: **MPL-2.0**, non nell'elenco. Sta solo nel gruppo
    `dev` e non entra nel pacchetto distribuito.
  - `numpy` 2.5.4: l'espressione di licenza è `BSD-3-Clause AND 0BSD AND MIT
    AND Zlib AND CC0-1.0`. `0BSD`, `Zlib` e `CC0-1.0` non sono nell'elenco.
    Numpy è in esecuzione e finisce nel pacchetto: va verificato quali
    componenti sono davvero distribuiti nel wheel.
- **Note di licenza** (ammesse, ma con obblighi da rispettare): `pyside6`
  (vedi voce), `plotly` con `plotly.js` incluso (MIT).
- **Release recenti** (meno di 7 giorni alla data di validazione): nessuna
  versione bloccata nel lock. Non bloccate e più recenti del lock:
  `platformdirs` 4.14.3, 4.14.4, 4.14.5 (1-4 novembre 2026) e `uv_build`
  0.13.7 (3 novembre 2026). `hypothesis` 6.170.14 (30 ottobre 2026) è a 6
  giorni. Nessuna è una correzione di sicurezza dichiarata.
- **Manutenzione da tenere d'occhio**: `fitdecode` non ha release né push
  dal 6 agosto 2025 (quindici mesi). `pytest-qt` ha l'ultima release del 1
  luglio 2025, ma il repository è attivo. `desktop-notifier` ha l'ultima
  release dell'8 agosto 2025, con push del 16 settembre 2026.

## Voci

### pyside6 (esecuzione)

- Versione bloccata: **6.12.0** (release 2026-10-08).
- Licenza: `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` (metadati
  installati). Il progetto usa l'opzione **LGPL-3.0**, compatibile con
  GPLv3.
- Obbligo LGPL: l'app si usa senza modifiche alle librerie Qt. Nel pacchetto
  distribuito le librerie Qt devono restare sostituibili. Da verificare in
  fase 8 con PyInstaller. QtWebEngine (pyside6-webengine, pyside6-addons,
  pyside6-essentials, pyside6-pdf, shiboken6) porta Chromium, con licenze di
  terzi: anche queste vanno verificate in fase 8.
- Scopo: interfaccia Qt (finestra, tabelle modello/vista, dialoghi) e
  `QWebEngineView` per i grafici Plotly (ADR 001).
- Ultima release: 6.12.0 (2026-10-08); repository `pyside/pyside-setup`
  con push del 2026-11-05, non archiviato.
- Alternative: pyqtgraph con PySide6 (scartata, ADR 001: grafici meno
  interattivi); Rust + egui e Tauri + ECharts (scartate, ADR 001). PyQt6 non è
  stata valutata nell'ADR.
- Validata: 2026-11-05.

### plotly (esecuzione)

- Versione bloccata: **7.1.0** (release 2026-09-15).
- Licenza: **MIT** (metadati installati). Il `plotly.js` incluso nel
  pacchetto è **v4.1.1, MIT** (intestazione di `plotly.min.js`), e verrà
  distribuito nell'app.
- Scopo: grafici interattivi del carico (PMC, zone, FC) nella
  `QWebEngineView` (ADR 001). Il JSON dei grafici arriva dal dominio; la
  pagina non ha accesso alla rete.
- Ultima release: 7.1.0 (2026-09-15); repository `plotly/plotly.py` con push
  del 2026-11-03, non archiviato, molte issue aperte (752).
- Alternative: pyqtgraph (scartata, ADR 001); ECharts in Tauri (scartata, ADR
  001).
- Validata: 2026-11-05.

### garminconnect (esecuzione)

- Versione bloccata: **0.3.20** (release 2026-10-23).
- Licenza: **MIT** (metadati installati).
- Attenzione: usa un **accesso non ufficiale** a Garmin Connect. Garmin non
  offre un'API ai privati. La libreria può smettere di funzionare a ogni
  cambio di login, e l'uso può essere contrario ai termini d'uso di Garmin.
  Vedi `docs/adr/002-accesso-garmin.md`: l'adattatore è isolato dietro la
  porta `ActivitySource`, con import FIT manuale sempre disponibile.
- Scopo: scaricare attività, FIT e calendario dal login diretto a Garmin
  Connect, con MFA.
- Ultima release: 0.3.20 (2026-10-23); repository
  `cyberjunky/python-garminconnect` con push del 2026-10-31, non archiviato.
- Alternative: `garth` (rotta da Garmin a marzo 2026, come riporta ADR 002);
  intervals.icu come intermediario (opzione 2 di ADR 002, da aggiungere con
  un nuovo ADR se serve).
- Validata: 2026-11-05.

### fitdecode (esecuzione)

- Versione bloccata: **0.11.0** (release 2025-08-06).
- Licenza: **MIT** (metadati installati).
- Scopo: lettura dei file FIT (attività importate a mano o da cartella).
  Il lettore FIT è un confine di fiducia: si fuzza con `hypothesis` (T-SEC-01).
- Ultima release: 0.11.0 (2025-08-06); repository `polyvertex/fitdecode` con
  ultimo push il 2025-08-06. **Segno di scarsa manutenzione**: nessuna
  release né push da quindici mesi. Da rivalutare a ogni fase che tocca il
  lettore FIT.
- Alternative: SDK FIT ufficiale di Garmin (`garmin-fit-sdk`) da valutare; nel
  progetto non è stata discussa un'alternativa.
- Validata: 2026-11-05.

### numpy (esecuzione)

- Versione bloccata: **2.5.4** (release 2026-10-10).
- Licenza: `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` (metadati
  installati). **Da riportare**: `0BSD`, `Zlib` e `CC0-1.0` non sono
  nell'elenco delle licenze ammesse. Vedi le note sopra.
- Scopo previsto: calcoli numerici su collezioni contigue di valori, per
  esempio le medie esponenziali di ATL, CTL e TSB (PLAN.md). Da confermare
  nelle fasi di dominio.
- Ultima release: 2.5.4 (2026-10-10); repository `numpy/numpy` con push del
  2026-11-04, non archiviato.
- Alternative: la libreria standard (`statistics`, `math`) per i calcoli
  semplici; nel progetto non è stata discussa un'alternativa.
- Validata: 2026-11-05, con la licenza da riportare sopra.

### keyring (esecuzione)

- Versione bloccata: **25.7.0** (release 2025-11-16).
- Licenza: **MIT** (metadati installati).
- Scopo: token Garmin nel portachiavi del sistema (Credential Manager su
  Windows, Secret Service su Ubuntu), legati all'id del profilo, mai nella
  cartella dei dati (CLAUDE.md, ADR 003).
- Ultima release: 25.7.0 (2025-11-16); repository `jaraco/keyring` con push
  del 2026-10-29, non archiviato.
- Alternative: chiamate dirette alle API del sistema (`ctypes` su Windows,
  `secretstorage` su Linux); nel progetto non sono state discusse.
- Validata: 2026-11-05.

### desktop-notifier (esecuzione)

- Versione bloccata: **6.2.0** (release 2025-08-08).
- Licenza: **MIT** (metadati installati).
- Scopo: notifica di fine sincronizzazione (toast su Windows, notifica del
  desktop su Ubuntu; PLAN.md).
- Ultima release: 6.2.0 (2025-08-08); repository `samschott/desktop-notifier`
  con push del 2026-09-16, non archiviato. Release vecchia di quindici mesi,
  ma il repository è attivo.
- Alternative: `plyer`; notifiche native per
  piattaforma. Nel progetto non sono state discusse.
- Validata: 2026-11-05.

### platformdirs (esecuzione)

- Versione bloccata: **4.14.0** (release 2026-10-28).
- Licenza: **MIT** (metadati installati).
- Scopo: cartelle standard del sistema per la configurazione dell'app e
  l'elenco dei profili (PLAN.md, sezione 4.2).
- Ultima release: 4.14.5 (2026-11-04), più recente del lock; nel lock resta
  4.14.0, e le release 4.14.3-4.14.5 sono a meno di 7 giorni. Repository
  `tox-dev/platformdirs` con push del 2026-11-04, non archiviato.
- Alternative: `appdirs` (non più sviluppata, sostituita da platformdirs);
  percorsi scritti a mano per ciascun sistema.
- Validata: 2026-11-05.

### pytest (gruppo dev)

- Versione bloccata: **9.2.0** (release 2026-10-27).
- Licenza: **MIT** (metadati installati).
- Scopo: test unitari, di caso d'uso e di integrazione.
- Ultima release: 9.2.0 (2026-10-27); repository `pytest-dev/pytest` con push
  del 2026-11-05, non archiviato.
- Alternative: `unittest` (libreria standard), più verboso.
- Validata: 2026-11-05.

### pytest-qt (gruppo dev)

- Versione bloccata: **4.5.0** (release 2025-07-01).
- Licenza: **MIT** (metadati installati).
- Scopo: test della GUI Qt in modalità `offscreen` (sezione 4 e 5 del piano).
- Ultima release: 4.5.0 (2025-07-01); repository `pytest-dev/pytest-qt` con
  push del 2026-11-02, non archiviato. Release vecchia di sedici mesi.
- Alternative: nessuna in ambito Qt; test manuali o scrittura di un
  fixture proprio sulla `QApplication`.
- Validata: 2026-11-05.

### hypothesis (gruppo dev)

- Versione bloccata: **6.170.13** (release 2026-10-28).
- Licenza: **MPL-2.0** (metadati installati). **Da riportare**: MPL-2.0 non è
  nell'elenco delle licenze ammesse. Sta solo nel gruppo `dev` e non entra nel
  pacchetto distribuito. Non approvata: serve una decisione esplicita.
- Scopo: property test e fuzzing del lettore FIT e dei parser (T-SEC-01).
- Ultima release: 6.170.13 (2026-10-28); 6.170.14 (2026-10-30) è più
  recente e non è nel lock. Repository `HypothesisWorks/hypothesis` con push
  del 2026-11-03, non archiviato.
- Alternative: `pytest` con input scritti a mano; `atheris` per il fuzzing
  puro. Nel progetto non sono state discusse.
- Validata: 2026-11-05, con la licenza da riportare sopra.

### ruff (gruppo dev)

- Versione bloccata: **0.17.3** (release 2026-10-27).
- Licenza: **MIT** (metadati installati).
- Scopo: formattazione e lint (`ruff format --check`, `ruff check`) in
  `uv run --locked nox`.
- Ultima release: 0.17.3 (2026-10-27); repository `astral-sh/ruff` con push
  del 2026-11-05, non archiviato.
- Alternative: `black` e `flake8` con `isort`, più strumenti separati.
- Validata: 2026-11-05.

### mypy (gruppo dev)

- Versione bloccata: **2.4.1** (release 2026-10-28).
- Licenza: **MIT** (metadati installati).
- Scopo: controllo dei tipi in modalità `--strict` su `src`, `tests`,
  `experiments` e `noxfile.py`.
- Ultima release: 2.4.1 (2026-10-28); repository `python/mypy` con push del
  2026-11-05, non archiviato.
- Alternative: `pyright` (controllo dei tipi alternativo).
- Validata: 2026-11-05.

### import-linter (gruppo dev)

- Versione bloccata: **2.15** (release 2026-09-04).
- Licenza: **BSD 2-Clause** (metadati installati, classificatore BSD).
- Scopo: contratti di architettura (`lint-imports`): il dominio è puro, la
  UI non parla con gli adattatori, gli adattatori sono indipendenti.
- Ultima release: 2.15 (2026-09-04); repository `seddonym/import-linter` con
  push del 2026-11-05, non archiviato.
- Alternative: test scritti a mano che analizzano gli `import` (per esempio con
  `grimp`); nel progetto non sono state discusse.
- Validata: 2026-11-05.

### pip-audit (gruppo dev)

- Versione bloccata: **2.10.1** (release 2026-06-10).
- Licenza: **Apache-2.0** (testo `LICENSE` nei metadati installati).
- Scopo: controllo delle vulnerabilità delle dipendenze bloccate, nella
  sessione `audit`.
- Ultima release: 2.10.1 (2026-06-10); repository `pypa/pip-audit` con push
  del 2026-10-30, non archiviato.
- Alternative: `safety` (database a pagamento per alcuni usi); `osv-scanner`
  di Google.
- Validata: 2026-11-05.

### nox (gruppo dev)

- Versione bloccata: **2026.10.20** (release 2026-10-20).
- Licenza: **Apache-2.0** (metadati installati).
- Scopo: sessioni di controllo (`uv run --locked nox`) che la CI esegue
  identiche al locale, e le sessioni `dist` e `collaudo`.
- Ultima release: 2026.10.20 (2026-10-20); repository `wntrblm/nox` con push
  del 2026-11-05, non archiviato.
- Alternative: `tox`; un `Makefile` con script. Nel progetto non sono state
  discusse.
- Validata: 2026-11-05.

### uv_build (backend di build)

- Versione: **non bloccata in `uv.lock`**. Il backend è in `[build-system]`
  (`uv_build>=0.9`) e lo risolve il build isolato; non è nel lock. Da
  valutare se fissarne una versione.
- Ultima release: 0.13.7 (2026-11-03), **a meno di 7 giorni** alla data di
  validazione: non è nel lock e non è una correzione di sicurezza.
- Licenza: **MIT OR Apache-2.0** (espressione dei metadati su PyPI; il
  pacchetto non è installato nell'ambiente, quindi la licenza non è letta
  dai metadati installati).
- Scopo: costruzione del pacchetto del progetto (`uv build`).
- Repository `astral-sh/uv` con push del 2026-11-05, non archiviato.
- Alternative: `hatchling`, `setuptools`.
- Validata: 2026-11-05.
