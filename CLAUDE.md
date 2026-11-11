# Ponos Coach: istruzioni del progetto

Solo ciò che è specifico di questo progetto. Le decisioni e le fasi stanno in
`PLAN.md`.

## Lingue

- Interfaccia in inglese (lingua predefinita) e italiano (scelto dal menu
  Lingua e ricordato), con il meccanismo di traduzione di Qt
  (`self.tr()`, file `.ts` in `src/ponos_coach/i18n/`); testi sorgente in
  inglese nel codice.
- In inglese (regola dell'utente del 2026-11-07): identificatori, commenti,
  docstring, test, messaggi di commit, descrizioni delle PR, `docs/`
  (ADR, specifica, threat model, collaudo, dipendenze), README, `TIDY.md`,
  `SECURITY.md`.
- In italiano: `PLAN.md`, questo file, la skill del progetto
  (`.claude/skills/ponos-allenamento/`) e le istruzioni degli agenti
  (`.claude/agents/`).

## Comandi

- `uv run --locked nox`: tutti i controlli obbligatori (ruff format e lint,
  `mypy --strict`, `lint-imports`, pytest con pytest-qt dei test unitari e
  d'integrazione, test delle traduzioni, `pip-audit`). La CI esegue
  esattamente questo. I test di proprietà usano 30 esempi; `tests/collaudo`
  non è compreso (richiesta dell'utente del 2026-11-10: meno test a ogni
  controllo).
- `uv run --locked nox -s i18n`: controlla che `ponos_it.ts` sia completo e
  coerente con i sorgenti e genera i `.qm` (già nei controlli di default).
- `uv run --locked nox -s i18n_update`: aggiorna `ponos_it.ts` dai sorgenti
  (lo usa il traduttore).
- `uv run --locked nox -s collaudo`: tutti i test di `tests/collaudo` e poi la
  campagna lunga della sezione 7 di `PLAN.md` (fuzzing esteso, mutazione,
  prestazioni, test di durata); una volta per fase e prima del rilascio.
- `uv run --locked nox -s dist`: eseguibile PyInstaller, smoke test, `SHA256SUMS`.
- `uv run ponos-coach`: avvia l'app.

## Documenti di stato e decisioni

- `PLAN.md`: piano e checklist delle fasi; si spunta una voce solo se
  completata e verificata, con i suoi test. La sezione 7 è il piano di test,
  con gli identificativi (`T-CALC-01`, `T-SEC-04`…) citati da test, threat
  model e collaudo.
- `docs/collaudo.md`: esiti del collaudo manuale e della campagna lunga.
- `docs/`: `specifica.md`, `threat-model.md`, `dipendenze.md`, `adr/`
  (decisioni difficili da cambiare).
- `TIDY.md`: pulizie rimandate.

## Struttura

Pacchetti per funzionalità in `src/ponos_coach/` (`activities`, `load`,
`assessment`, `goals`, `planning`, `injury`, `profiles`), adattatori
(`garmin`, `storage`, `platform`), `ui`, Composition Root in `__main__.py`.

- Ogni profilo ha una cartella scelta dall'utente con tutti i suoi dati
  (database, archivio FIT, backup). I token Garmin, legati all'id del
  profilo, sono protetti dal sistema (DPAPI su Windows, Secret Service su
  Linux) e non stanno mai nella cartella.

- Dipendenze in una sola direzione: `ui → casi d'uso → dominio`; gli
  adattatori implementano porte definite nelle funzionalità.
- Il dominio è puro: niente I/O, Qt, rete né log. Le regole sono in
  `importlinter` e la CI le verifica.

## Skill

- Sempre: `software-design-core`, `python-design`.
- Per argomento:
  - `ui-design-core` e `dataviz`: interfaccia, tabelle e grafici Plotly.
  - `security-design`: login Garmin e token, file FIT e risposte JSON
    esterne, pagina web dei grafici, rilascio.
  - `durable-data-design`: SQLite, archivio FIT, sincronizzazione
    riprendibile, backup.
  - `windows-system-design`: Credential Manager, notifiche di sistema,
    PyInstaller su Windows.
- Skill del progetto: `ponos-allenamento` (modello di carico, PMC,
  periodizzazione, test, infortuni, sincronizzazione, formato dei dati).

## Squadra di agenti (`.claude/agents/`)

- `sviluppatore-nucleo` (sonnet): dominio, casi d'uso e adattatori.
- `sviluppatore-interfaccia` (sonnet): finestra Qt, tabelle, grafici, dialoghi.
- `generatore-fixture` (sonnet): FIT sintetici, risposte Garmin anonimizzate,
  scenari per il piano.
- `collaudatore` (sonnet): scrive ed esegue i test approfonditi della
  sezione 7 (end-to-end, interruzioni, fuzzing, sicurezza, prestazioni);
  non tocca `src/`.
- `revisore-codice` (sonnet): rivede un diff rispetto al piano e alle skill;
  solo lettura.
- `redattore-documenti` (sonnet): README, `docs/`, ADR, questo file, skill
  del progetto.
- `verificatore` (haiku): esegue i controlli e riassume gli errori; solo
  lettura.
- `riparatore-lint` (haiku): correzioni meccaniche di ruff e mypy.
- `traduttore-qt` (haiku): allinea le traduzioni italiano e inglese.
- `controllore-conformita` (haiku): licenze, intestazioni, autore, dati
  personali; solo lettura.
- `registro-dipendenze` (haiku): `docs/dipendenze.md`, licenze, `pip-audit`.

La sessione principale fa domande, piano, orchestrazione e revisione finale
di ogni fase. In parallelo solo su file che non si sovrappongono, ogni lavoro
parallelo sul codice nel suo worktree.

Ritmo di lavoro (regola dell'utente del 2026-11-07, per accelerare):

- Gli agenti eseguono solo i controlli del loro perimetro (ruff e mypy sui
  file toccati, `pytest` delle cartelle toccate); `uv run --locked nox` e
  `nox -s collaudo` li esegue la sessione principale una volta per fase,
  prima del commit finale.
- Ambito congelato: una richiesta nuova durante una fase va in coda alla
  fase successiva (voce in `PLAN.md`), salvo correzioni di difetti.
- Più lavoro in parallelo: il nucleo della fase successiva parte in un
  worktree mentre gira l'interfaccia della fase corrente; i conflitti sui
  file comuni (finestra principale, schema) li risolve la sessione
  principale all'unione.
- Collaudo (`collaudatore`) e revisione (`revisore-codice`) ogni due fasi,
  sul diff di entrambe.

## Git

- Autore e committer già nella configurazione del repository; nessuno script
  ne imposta un altro.
- **Mai `git push`** (regola dell'utente del 2026-11-07, che sostituisce il
  push automatico del 2026-11-06): il push lo fa l'utente a mano. La
  sessione principale fa i commit sul ramo di lavoro della fase e dice
  quali sono da caricare; su `main` si arriva solo con una PR. Mai
  `--force` né riscrittura di commit già pushati.

## Dominio

- Ponos Coach non è un dispositivo medico: nessuna diagnosi; con i segnali
  d'allarme il piano della zona colpita si ferma e l'app invita a un medico.
- Il piano è deterministico: stessi dati e parametri, stesso piano.
- Dati di salute (infortuni, dolore, FC) solo in locale, mai nei log.

## Eccezioni alle regole generali

1. **Login a Garmin nell'app, non nel browser di sistema con PKCE**
   (permanente finché Garmin non offre un'API ai privati).
   - Motivo: Garmin non ha un'API ufficiale per i privati; l'unico accesso è
     il login non ufficiale usato da `garminconnect`.
   - Compensazione: la password si inserisce in un dialogo dell'app, va solo
     a `garminconnect` e non si salva; token protetti dal sistema (DPAPI su Windows, Secret Service su Linux);
     MFA supportato; adattatore isolato dietro una porta; import FIT manuale
     sempre disponibile.
2. **Firma degli eseguibili** (permanente).
   - Motivo: progetto personale senza certificato di firma del codice.
   - Compensazione: `SHA256SUMS` accanto ai pacchetti; il README spiega
     l'avviso di SmartScreen e la verifica dell'hash.
3. **Comandi del calendario solo nel menu del giorno** (permanente, richiesta
   dell'utente del 2026-11-09: "in alto togliere menu calendario"). La regola
   generale vuole ogni comando nei menu della barra in alto.
   - Motivo: la barra dei menu resta corta; il calendario è sempre visibile
     nel suo riquadro e i comandi agiscono sul giorno sotto il mouse.
   - Compensazione: tutti i comandi stanno nel menu del tasto destro del
     giorno, con la scorciatoia accanto, e le scorciatoie valgono senza menu
     (test in `tests/ui/test_main_panes.py` e `tests/ui/test_goals_window.py`).
4. **Niente menu View** (permanente, richiesta dell'utente del 2026-11-10:
   "elimina il menu Vista"). La regola generale vuole ogni comando nei menu
   della barra in alto.
   - Motivo: i quattro riquadri sono sempre visibili; i comandi "Go to …"
     spostano solo il fuoco della tastiera e non meritano un menu.
   - Compensazione: Ctrl+1..4 valgono da tutta la finestra (Panoramica,
     Calendario, Attività, Mappa), descritti in specifica e README; test in
     `tests/ui/test_main_panes.py`.
5. **CI e rilascio solo su Windows** (temporanea, richiesta dell'utente del
   2026-11-11: "per ora abbandona ubuntu"). La regola generale vuole la CI su
   Windows e Ubuntu.
   - Motivo: sul runner Ubuntu di GitHub l'installazione dei pacchetti di
     sistema per Qt falliva per errori di rete (DNS dei mirror Ubuntu), e
     bloccava CI e rilascio.
   - Compensazione: il codice per Linux resta (Secret Service, step `apt`
     saltati dove `runner.os` non è Linux); per riprendere Ubuntu basta
     rimettere `ubuntu-latest` nelle matrici di `ci.yml` e `release.yml` e
     togliere questa eccezione. Finché vale, il README non promette il
     pacchetto Ubuntu.
