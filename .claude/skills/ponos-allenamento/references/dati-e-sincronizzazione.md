# Dati e sincronizzazione

**Quando leggerlo:** scrivendo o rivedendo `storage`, `profiles`, `garmin`,
`activities` (import e sincronizzazione), `platform`, i test `T-DATI`, `T-SYNC`,
`T-IMP` (i `T-GPX` sono tolti in fase 8). Sezione 5 di `SKILL.md`. ADR: `docs/adr/003-formato-dati.md`
e `docs/adr/005-schema-sqlite-e-archivio.md` (schema, archivio e identità:
qui non si ripetono) e `docs/adr/006-token-e-sincronizzazione.md` (perché i
token, il thread, il giornale e la politica sono fatti così: qui ci sono le
regole operative, non le motivazioni).

## 1. Cartella del profilo

Scelta dall'utente alla creazione; autosufficiente. Nomi in
`profiles/domain/layout.py`.

```
<cartella del profilo>/
  ponos.sqlite3     database (WAL: anche -wal e -shm)
  archivio/<anno>/<sha256>.fit
  backup/           ponos-v{da}-v{a}.sqlite3: copia prima di ogni migrazione
  ponos.lock        lock del sistema operativo sul file aperto
```

- I token Garmin non stanno qui né nel backup: vedi la sezione 5 ("Token"). Un
  profilo spostato su un altro PC chiede un nuovo login.
- **Elenco dei profili:** `profiles.json` nella cartella di configurazione
  (`platformdirs`), con id, cartella, nome in cache e ultimo uso. Formato
  versionato, tetto di 1 MiB, campi sconosciuti conservati, scrittura atomica
  sotto un lock su `profiles.json.lock` con attesa limitata. Un file
  illeggibile, corrotto o di una versione futura è un errore e non si
  sovrascrive. Lo stato della UI è in `QSettings`.
- **Cartella proposta** per un profilo nuovo: nella cartella dati locale
  dell'app (`.../profiles/<nome>`), non in Documenti, che su Windows è spesso
  sincronizzata con OneDrive.
- **Rete e cloud:** `SystemFolderKindDetector` riconosce UNC, unità remote,
  mount di rete, OneDrive, Dropbox, Google Drive, iCloud Drive; nel dubbio il
  tipo è ignoto. Dà un avviso (`FolderWarning`), mai un blocco.
- **Permessi:** cartella creata privata (Windows: DACL protetta con
  utente e SYSTEM alla creazione; Ubuntu: 0700 verificato). Database, archivio
  e backup ereditano i permessi privati della cartella; file creati in modo
  esclusivo.
- **Creazione:** cartella, lock, database, misure iniziali (parametri
  fisiologici, peso, altezza e data di nascita in una sola transazione) e
  voce nell'elenco; se un passo fallisce, il rollback annulla solo ciò che ha
  creato. Chi perde la gara con un'altra istanza sulla stessa cartella non
  tocca il profilo del vincitore (T-DATI-06).
- **Elenco:** `ProfileRegistry.upsert` porta l'elenco allo stato "questo
  profilo sta in questa cartella" in una sola scrittura: sostituisce la voce
  con lo stesso id, toglie ogni altra voce con la stessa cartella, aggiunge se
  l'id non c'era; tutto o niente. Lo usano creazione e apertura da cartella.
- **Apertura:** prima il lock, poi il database. Il formato più nuovo si
  rifiuta; uno più vecchio si migra dopo una copia coerente in
  `backup/ponos-v{da}-v{a}.sqlite3` (API di backup di SQLite dopo il
  checkpoint del WAL, su temporaneo esclusivo poi rinominato). Senza la copia
  non si migra; un database già aggiornato non ne fa. Un file che non ha le
  tabelle di un profilo è danneggiato, qualunque sia il suo `user_version`.
- **Anteprima (`peek`):** `ProfileStorage.peek` legge informazioni e numero di
  attività in sola lettura: non migra, non scrive, non prende il lock, e non
  crea `-wal` né `-shm` (database chiuso: `immutable=1`; con un `-wal`
  presente, `mode=ro`). Si può usare su un profilo aperto altrove. Serve alla
  conferma di eliminazione; se non legge, la conferma lo dice.
- **Rimuovere** un profilo lo toglie solo dall'elenco. **Cancellare la
  cartella** è un comando a parte con conferma: solo per un profilo non
  aperto (il lock lo prende l'operazione) e solo i nomi che il profilo
  possiede, `PROFILE_OWNED_NAMES` in `profiles/domain/layout.py` (database con
  `-wal`, `-shm` e `-journal`, `archivio/`, `backup/`, lock). Se c'è altro
  (`FolderHasOtherContentError`) o un collegamento, non si cancella nulla e il
  messaggio lo dice; `platform/tree.py` non segue mai link né giunzioni.

## 2. SQLite

- Parametri sempre con `?`, mai SQL composto.
- Fonte unica: `storage/sqlite_schema.py` (tabelle, colonne, vincoli, insiemi
  chiusi). Versione in `PRAGMA user_version`; migrazioni come elenchi di
  istruzioni in una transazione, mai `executescript`; una migrazione
  rilasciata non si modifica.
- Tempi in millisecondi UTC interi; durate misurate in secondi.
- Contiene l'indice delle attività, i parametri fisiologici, la cache dei
  carichi, (dalla v2) il giornale della sincronizzazione e (dalla v3) i dati
  personali, il peso (e fino al 2026-11-10 i pesi degli sport, ora non usati); obiettivi, piani, infortuni e
  impostazioni arrivano con le loro fasi, con nuove migrazioni.
  Carichi e PMC sono derivati e rigenerabili.
- **Schema v2** (`sync_journal`, `sync_cursor`, `sync_state`, tutte `STRICT`,
  tempi in millisecondi UTC): vedi la sezione 5. La migrazione v1 → v2 passa
  dalla copia `backup/ponos-v1-v2.sqlite3` come ogni migrazione (T-DATI-01
  prova un database v1 congelato scritto a mano in
  `tests/collaudo/test_database_del_profilo.py`, insieme alle due migrazioni
  congelate). Gli insiemi chiusi della v2 (stati, cause, elenchi) hanno un test
  che li confronta con gli enum del dominio.
- **Schema v3** (dati personali, pesi degli sport, zone; tutte `STRICT`):
  - `profile_body`: una sola riga (`id = 1`) con altezza in cm (CHECK
    100-250) e data di nascita (CHECK `date(col) IS col` e `>= 1900-01-01`). Altezza e data di
    nascita sono **fisse**: un trigger `BEFORE UPDATE` e uno `BEFORE DELETE` rifiutano ogni
    modifica e cancellazione, la porta `FixedDataStore.set_once` scrive una volta sola
    (`FixedDataAlreadySetError` poi). La riga manca nei profili creati
    prima della v3: restano validi, i dati si completano una volta in
    "Modifica profilo" (`UpdateMeasurements`, nella stessa transazione di peso e FC).
  - `weight`: serie del peso, `valid_from` data ISO esistente (`date(col) IS col`, non
    prima del 1900-01-01) e `tenths_kg` intero
    (200-3000: nessun float binario). Si aggiunge con `UpdateMeasurements`;
    la stessa data sostituisce.
  - `sport_weights`: solo i pesi diversi dal default (`REAL` tra 0 e 5).
    **Dal 2026-11-10 la tabella resta ma non è né letta né scritta** (il
    carico è il TRIMPeq, senza pesi): niente più `SetSportWeights` né
    `SportWeightStore`; la toglierà una migrazione futura (expand-contract).
  - `load_cache` guadagna `zone1_s ... zone5_s` (secondi in zona, `>= 0`).
    La cache è derivata: la migrazione la svuota, così nessun carico resta
    senza zone (`LOAD_MODEL_VERSION` lo garantisce anche per le impronte; 3 dal
    TRIMPeq). Dal TRIMPeq la colonna `hr_tss` riceve 0, mentre `trimp` e `load`
    ricevono il TRIMPeq; l'impronta `load_fingerprint(physiology)` non ha pesi.
  - La migrazione v2 → v3 passa da `backup/ponos-v2-v3.sqlite3`. T-DATI-01:
    `tests/collaudo/test_fase3_migrazione.py` ha la v3 congelata e un
    database v2 scritto a mano, dati intatti, copia coerente, migrazione
    interrotta a metà, limiti dei CHECK e trigger.
  - `OpenProfile` espone anche `weights` (`WeightStore`) e `fixed_data`
    (`FixedDataStore`): stessa
    connessione e stessa transazione di `activities.transaction()`.
  - Altezza, peso e data di nascita sono dati di salute: mai nei log né nei
    messaggi d'errore (i tipi di dominio e gli store non ripetono i valori).
    Motivazioni e alternative: `docs/adr/007-dati-del-profilo-e-zone.md`.
- **Schema v4** (alimentazione, tutte `STRICT`; formule e soglie in
  `references/alimentazione.md`):
  - `activities.calories_kcal`: totale dichiarato dal dispositivo (FIT
    `session.total_calories`), NULL se manca (CHECK > 0 e <= 50000); le righe
    precedenti restano NULL.
  - `activity_level`: livello fuori dallo sport con data di validità (stessa
    regola del peso), insieme chiuso `sedentary`, `light`, `active`,
    `very_active`.
  - `meal_times`: una riga (`id = 1`), minuti dalla mezzanotte in ordine e ad
    almeno 120 minuti l'uno dall'altro; assente = orari di default.
  - `nutrition_goal`: periodi (`start_date` chiave, `end_date` NULL = senza
    fine), `kind` `weight_loss`/`maintenance`/`performance`, `weekly_loss_g`
    (100-1000) solo per il dimagrimento; un trigger `BEFORE INSERT` rifiuta i
    periodi che si sovrappongono.
  - Migrazione v3 → v4 con copia in `backup/ponos-v3-v4.sqlite3`. T-DATI-01:
    `tests/collaudo/test_fase4b_migrazione.py` (v3 congelata, uccisione a ogni
    istruzione, limiti dei CHECK e del trigger).
  - `OpenProfile` espone anche `activity_levels`, `meal_times` e
    `goal_periods`, sulla stessa connessione e transazione.
- **Schema v5** (test di soglia, `STRICT`; regole in `references/test-soglia.md`):
  - `threshold_test`: storico dei test applicati, una riga per attività
    (`activity_key` chiave, l'`identity_key` dell'attività, prefisso `fit:` o
    `gpx:`), `test_date` (giorno locale, data esistente non prima del
    1900-01-01), `segment_start_ms` (inizio del segmento, millisecondi UTC),
    `threshold_bpm` (40-250), `pace_s_per_km` (90-3600, NULL senza distanza),
    `method_version` (>= 1). Nessuna chiave esterna verso `activities`: l'indice
    è derivato e rigenerabile, lo storico dei test deve sopravvivere alla sua
    ricostruzione.
  - Migrazione v4 → v5 con copia in `backup/ponos-v4-v5.sqlite3`. T-DATI-01:
    `tests/collaudo/test_fase4_migrazione.py` (v4 congelata, uccisione a ogni
    istruzione, limiti dei CHECK). La copia porta il nome `ponos-v<da>-v<a>` con
    la versione di destinazione dell'app: una v3 aperta oggi lascia
    `ponos-v3-v5.sqlite3`.
  - `OpenProfile` espone anche `threshold_tests` (porta `ThresholdTestStore` in
    `assessment/ports.py`), sulla stessa connessione e transazione; la porta
    `PhysiologyStore` ha `remove(valid_from)` per annullare un test.
- **Schema v6** (obiettivi, disponibilità, tipologie e piano, `STRICT`; regole in
  `references/piano.md` sezione 13):
  - `goal`: gare e obiettivi di forma in una tabella (`kind` `race` o `fitness`).
    `goal_id` (1-64 caratteri `A-Za-z0-9_-`, scelto da chi chiama), `goal_date`
    (data esistente non prima del 1900-01-01), per la gara `priority` (A, B, C),
    `sport`, `distance_m` (400-500000) e `target_time_s` facoltativo (60-604800),
    dallo schema v16 `ascent_m` (0-30000, 0 = piana),
    per l'obiettivo di forma `ctl` (0-200); i CHECK tengono separate le due
    forme. Un indice univoco parziale (`WHERE kind = 'race'`) ammette una sola
    gara per giorno, anche con una scrittura diretta; gli obiettivi di forma
    possono dividere il giorno.
  - `weekly_availability`: una sola riga (`id = 1`). Dallo schema v12 (ADR 016) ha
    `days_mask` (0-127, bit i = giorno i disponibile, lunedì = bit 0), `sessions_per_week`
    (0-7, al massimo i giorni), `long_day` (0-6, un giorno disponibile), `max_week_min`
    (0-6000); tutto a zero = non impostata. In v6-v11 erano `day0_min` (lunedì) ...
    `day6_min` (0-720). Assente = non impostata.
  - `session_choice`: una riga per sport e tipologia scelti (insiemi chiusi).
  - `plan_state`: una sola riga (`id = 1`) con l'ultimo esito di `build_plan`:
    piano (`kind = 'plan'`: `method_version`, `plan_json` canonico fino a
    16 MiB, `plan_sha256`; versione, parametri e ingressi stanno nel JSON) o
    rifiuto (`kind = 'refusal'`: `refusal_reason` su insieme chiuso,
    `refusal_goal_ids` separati da virgola). Derivato e sostituibile.
  - `plan_past_day`: il passato com'era pianificato, una riga per giorno
    (`day`, `planned_on` sempre prima del giorno, `day_json` fino a 64 KiB,
    `day_sha256`). Due trigger rifiutano ogni `UPDATE` e `DELETE`: un giorno
    congelato non cambia mai.
  - Migrazione v5 → v6 con copia in `backup/ponos-v5-v6.sqlite3`. T-DATI-01:
    `tests/collaudo/test_fase5_migrazione.py` (v5 congelata, uccisione a ogni
    istruzione, limiti dei CHECK, trigger, JSON del piano manomesso).
  - `OpenProfile` espone anche `goals`, `availability`, `session_choices` (porte
    in `goals/ports.py`) e `plans` (`planning/ports.py`), sulla stessa
    connessione e transazione. Il JSON letto dal database è input
    (`planning/plan_codec.py`): dimensione, checksum, sintassi e forma dei tipi;
    ciò che non regge è `UnreadablePlan` (il piano è derivato, il ricalcolo lo
    sostituisce) o, per un giorno del passato, un giorno omesso.
- **Schema v7** (protocollo del test di soglia e massa magra misurata, `STRICT`; regole in
  `references/test-soglia.md` sezione 1 e `references/alimentazione.md` sezione 7):
  - `threshold_test.protocol`: colonna aggiunta in fondo (`ALTER TABLE ... ADD COLUMN`),
    insieme chiuso `thirty_minutes` / `twenty_minutes`, `NOT NULL DEFAULT 'thirty_minutes'`:
    i test salvati prima erano tutti di 30'. `method_version` non si riscrive. Le
    `INSERT` dirette nella tabella nominano le colonne.
  - `lean_mass`: `valid_from` (data esistente non prima del 1900-01-01, chiave primaria) e
    `tenths_kg` (150-2000). La plausibilità col peso del giorno è una regola del caso d'uso
    (`profiles/lean_mass.py`) e di `PlanDayNutrition`, non dello schema.
  - Migrazione v6 → v7 con copia in `backup/ponos-v6-v7.sqlite3` (da v5: una sola copia
    `ponos-v5-v7.sqlite3`, le due migrazioni nella stessa transazione). T-DATI-01:
    `tests/collaudo/test_fase5_migrazione_v7.py` (v6 congelata, v7 invariata dopo il rilascio,
    uccisione a ogni istruzione, limiti dei CHECK).
  - `OpenProfile` espone anche `lean_masses` (porta `LeanMassStore` in `nutrition/ports.py`),
    sulla stessa connessione e transazione; fake e contract test accanto ai
    livelli di attività.
- **Schema v8** (riquadro della traccia per la mappa; ADR 010):
  - `activities.track_south`, `track_west`, `track_north`, `track_east`: quattro colonne `REAL`
    aggiunte in fondo (`ALTER TABLE ... ADD COLUMN`), tutte NULL o tutte presenti (CHECK
    sull'ultima colonna: intervalli, `south <= north`, tutto o niente). `west > east` = il
    riquadro attraversa l'antimeridiano. NULL = attività senza posizione, oppure riga non ancora
    riempita. Derivato e rigenerabile.
  - Il riquadro è quello di **partenza + polyline semplificata** della stessa riga
    (`track_box_of`, dominio): non rilegge mai il file archiviato. Può essere più piccolo di quello
    della traccia piena al massimo della tolleranza dell'indice (`TRACK_TOLERANCE_M`, 10 m). Lo
    scrive `SqliteActivityIndex.add` (quindi import e sincronizzazione senza altre modifiche); una
    polyline illeggibile dà "nessun riquadro" e la riga resta da riempire.
  - Le attività indicizzate prima della v8 restano senza riquadro finché non gira
    `backfill_track_boxes(index, batch_size, report, is_cancelled)` (`activities/map_queries.py`):
    lotti da 500 righe, ciascuno una transazione propria (riprendibile: ciò che è fatto resta),
    `is_cancelled()` prima di ogni lotto, `report(fatte, totale)`; usa solo l'indice. Una polyline
    rovinata ripiega sul solo punto di partenza; senza nemmeno quello la riga conta come
    `unresolved` e la passata successiva la ritrova. Sul thread dei dati come l'import
    (`DataThread`, un job annullabile).
  - Porta `TrackBoxIndex` (`activities/ports.py`; `OpenProfile.track_boxes`, stessa connessione
    e transazione): `revision()`, `list_by_start()` (record + riquadro), `count_without_box()`,
    `candidates_without_box(after_row_id, limit)`, `store_boxes({row_id: GeoBox})` (non
    sovrascrive un riquadro già presente). Fake e contract test in
    `tests/contracts/contract_track_boxes.py`.
  - Migrazione v7 → v8 con copia in `backup/ponos-v7-v8.sqlite3`. T-DATI-01:
    `tests/collaudo/test_fase5b_migrazione_v8.py` (v7 congelata scritta a mano, v8 invariata dopo
    il rilascio, uccisione a ogni istruzione, limiti dei CHECK, riempimento riprendibile). Gli
    altri test congelati leggono `activities` con l'helper `rows()` che nasconde le colonne
    aggiunte dopo (`LATER_COLUMNS`).
- **Schema v12** (giorni e sedute a settimana al posto dei minuti per giorno; ADR 016):
  - `weekly_availability` ricostruita (SQLite non toglie colonne con CHECK) nella stessa
    transazione: i giorni sono quelli che avevano minuti, le sedute tante quanti i giorni, il
    giorno del lungo si aggiunge ai giorni se non lo era, il massimo settimanale resta; una riga
    senza minuti diventa la disponibilità vuota (tutto a zero). I minuti di ogni giorno si
    perdono; restano nella copia in `backup/` fatta prima della migrazione.
  - `METHOD_VERSION` del piano era 6 (oggi 8, vedi schema v16). Il piano salvato è derivato: un piano con `method_version`
    più bassa resta finché il ricalcolo (dopo ogni panoramica) non lo sostituisce; il codice non
    confronta la versione.
- **Schema v13** (voci che Garmin non fornisce): `sync_journal` ricostruita (SQLite non
  cambia i CHECK sul posto) con la colonna `unanswered_rounds` e la causa
  `remote_not_provided`; indice `sync_journal_to_do` ricreato; copia in
  `backup/ponos-v12-v13.sqlite3` (da un v12 il nome dice la versione d'arrivo, oggi
  `ponos-v12-v14`). T-DATI-01: `tests/collaudo/test_fase8_migrazione_v13.py`.
- **Schema v14** (passo soglia impostato a mano): tabella `threshold_pace(valid_from PK,
  s_per_km 90-3600)`, `STRICT`, serie datata a sé come `weight`; non tocca `load_cache`.
  Migrazione `THRESHOLD_PACE_MIGRATION` in `storage/schema_threshold_pace.py`; test in
  `tests/storage/test_threshold_pace_store.py` (trovata per identità, non per numero).
  Il passo in vigore per il piano è `threshold_pace_in_force` (`profiles/domain`): il più
  recente per data fra l'ultima misura manuale e l'ultimo test con passo, a pari giorno
  la manuale.
- **Schema v15** (soglie FC per sport): colonne nullable `bike_threshold_bpm` e
  `other_threshold_bpm` in `physiology`, `CHECK (>= 40 AND > min_bpm AND <= max_bpm)`,
  NULL = default; `ALTER TABLE ... ADD COLUMN`, `load_cache` non svuotata; migrazione
  `SPORT_THRESHOLDS_MIGRATION` in `storage/schema_sport_thresholds.py`, copia
  `backup/ponos-v14-v15.sqlite3`; test in `tests/storage/test_sport_thresholds*.py`.
- **Schema v16** (dislivello della gara; ADR 017): colonna `goal.ascent_m INTEGER NOT NULL
  DEFAULT 0 CHECK (ascent_m BETWEEN 0 AND 30000)`, aggiunta con `ALTER TABLE ... ADD COLUMN`;
  le gare già salvate hanno 0 (piane), l'obiettivo di forma ha 0 (lo scrive il dominio). Limiti
  congelati nella migrazione (`RACE_ASCENT_MIGRATION` in `storage/schema_race_ascent.py`, come
  `MAX_ASCENT_M` di `goals/domain/goal.py`); copia in `backup/ponos-v15-v16.sqlite3`. Gli
  `INSERT` e gli `UPDATE` di `sqlite_goals.py` nominano la colonna. Test in
  `tests/storage/test_race_ascent_store.py` (migrazione trovata per identità, non per numero;
  migrazione congelata, copia, limiti dei CHECK, lettura e sostituzione); i test congelati delle
  migrazioni precedenti ignorano la colonna (`tidy:` 80c0dde).
  - Piano serializzato (`METHOD_VERSION` 8): campi nuovi `PlannedSession.ascent_m` (D+ indicativo
    in m, 0 = seduta piana o nessuna salita) e `PlannedRace.ascent_m` (D+ della gara), tutti e due
    con default 0 alla lettura: un piano o un giorno congelato salvato prima si rilegge, con
    `ascent_m` 0. `Shape.slope_m_per_km` non si salva (serve solo al motore). Il piano salvato è
    derivato: lo sostituisce il ricalcolo.
- **Schema v17** (FC dell'attività): `load_cache` svuotata (derivata) con le colonne
  `avg_bpm` e `max_bpm` (INTEGER 20-250, NULL senza FC), dai campioni usati per il carico
  (media pesata sul tempo, arrotondata); `LOAD_MODEL_VERSION` 4, quindi tutti i carichi si
  ricalcolano una volta. Migrazione `LOAD_HEART_RATE_MIGRATION`
  (`storage/schema_load_heart_rate.py`); test `tests/storage/test_load_heart_rate_store.py`.
- **Schema v18** (LT1 per gruppo di sport): colonne nullable `run_lt1_bpm` e `other_lt1_bpm`
  in `physiology`, `CHECK (NULL OR (> min_bpm AND < LT2))`, con LT2 = `threshold_bpm` (per
  `other_lt1_bpm`: `COALESCE(other_threshold_bpm, threshold_bpm)`); `ALTER TABLE ... ADD
  COLUMN`, `load_cache` non svuotata (l'impronta cambia da sola: `LOAD_MODEL_VERSION` 5,
  perché la bici non è più corsa − 4). Dal 2026-11-10 `bike_threshold_bpm` (v15) **non si
  legge né si scrive più**: resta nella tabella (expand-contract), la toglierà una migrazione
  futura; `other_threshold_bpm` è ora la LT2 di tutti gli sport che non sono corsa o trail.
  Migrazione `LT1_MIGRATION` (`storage/schema_lt1.py`); test `tests/storage/test_lt1_store.py`.
- Campi sconosciuti di `meta` conservati al risalvataggio.
- Connessione: `foreign_keys = ON`, `trusted_schema = OFF`, WAL,
  `synchronous = FULL`; `quick_check` e `foreign_key_check` all'apertura.

## 3. Archivio dei file originali

- I file originali (FIT) sono la fonte di verità delle attività; la porta
  è `ActivityArchive` (`activities/ports.py`), l'adattatore `FileArchive`.
- Ogni file ha il suo SHA-256 e la sua dimensione registrati
  (`ArchivedFile`: percorso relativo, SHA-256, byte). Il percorso è generato
  dal programma, fatto solo di caratteri `A-Za-z0-9._-` (nessun dato esterno
  entra in un nome); l'anno è quello dell'inizio in UTC. Salvare gli stessi
  byte due volte dà lo stesso riferimento e non riscrive nulla.
- Se il file c'è ma i byte non corrispondono al nome, `store` lo sostituisce in
  modo atomico con quelli giusti: è il caso "riparata" dell'import
  (`ImportStatus.REPAIRED`). Si ripara solo se il file importato ha lo stesso
  SHA-256 e la stessa dimensione registrati; con altri byte e la stessa
  identità l'esito resta "già presente" e nulla si riscrive.
- Leggere dall'archivio verifica sempre dimensione e SHA-256 (nessuna lettura
  non verificata) e rispetta un massimo di byte; `verify` controlla senza
  tenere il file in memoria. Nessun collegamento si segue.
- Scrittura: temporaneo `.tmp-<32 cifre esadecimali>` creato in modo
  esclusivo, `fsync`, rinomina durevole (Windows `MoveFileExW` con
  write-through; POSIX `os.replace` e `fsync` della cartella); il record in
  SQLite si scrive dopo il file. La sostituzione durevole sta in un solo
  modulo condiviso, `ponos_coach.fsio` (`write_file_atomically`,
  `replace_file`, `make_subdirectory`, `sync_directory`), senza dipendenze dal
  progetto: lo usano archivio, copie di sicurezza ed elenco dei profili, che
  restano indipendenti tra loro. Le scritture dell'archivio passano da `WriteOps`, che i
  test sostituiscono per far fallire l'N-esima operazione (T-DATI-02).
- All'avvio: nessun record senza file, nessun temporaneo orfano
  (`remove_orphan_temporaries`).
- Un file alterato (hash diverso) o mancante è segnalato, mai usato in
  silenzio: l'attività resta fuori dalla panoramica con il suo motivo, finché
  non si reimporta l'originale.
- Limiti prima della lettura (default del progetto, costanti in
  `activities/domain/limits.py`, `FitLimits`): dimensione
  massima 50 MB, un milione di record o punti, durata massima 7 giorni.
  Finestra degli orari plausibili: [1990-01-01, 2100-01-01) UTC
  (`MIN_ACTIVITY_INSTANT` e `MAX_ACTIVITY_INSTANT` in `limits.py`,
  `is_plausible_instant` e `is_plausible_span` in `domain/timing.py`). Se
  l'inizio o la fine (inizio + tempo trascorso) ne esce, il file è corrotto;
  una fine non rappresentabile conta come fuori.

- Campioni di FC con timestamp ripetuto o non crescente: non rendono il file
  corrotto; si scartano in modo deterministico (vale il primo di una
  ripetizione, un ritorno indietro non entra) e si contano come scartati
  (default del progetto). Un file è corrotto per CRC, struttura o record senza
  timestamp, mai per questo.
- L'attività porta il tempo trascorso (`total_elapsed_time`) e il tempo in
  movimento (`total_timer_time`, ripiego sul trascorso se manca); i limiti di
  durata si applicano al trascorso.
- Regola timer/trascorso (default del progetto): tempo in movimento maggiore
  del trascorso di oltre 1 s (confronto in millisecondi) → file corrotto;
  entro 1 s → portato al trascorso. Invariante di `Activity` e
  `ActivityRecord`: `timer_s <= elapsed_s`.

### Riepilogo ricavato dal file (`ActivitySummary`)

- Distanza (`total_distance` della sessione, m), dislivello positivo
  (`total_ascent`, m), punto di partenza e
  traccia semplificata; ogni voce può mancare (bici indoor: solo distanza).
  Somma sulle sessioni che le dichiarano; valori non validi (0xFFFFFFFF,
  0xFFFF) ignorati, mai riparati.
- Partenza: `start_position_*` della prima sessione, altrimenti il primo
  record con posizione valida. Semicerchi → gradi (`s * 180 / 2^31`);
  il valore non valido 0x7FFFFFFF e le coordinate fuori da ±90°/±180° si
  ignorano; un record con una sola coordinata valida non conta.
- Traccia: i record con posizione valida, semplificati con
  Ramer-Douglas-Peucker iterativo (`simplify_indices`, tolleranza 10 m,
  tetto 2000 punti: si divide sempre il tratto con lo scostamento maggiore e
  ci si ferma sotto la tolleranza o al tetto) e salvati come polyline.
- Polyline: algoritmo standard dei polyline codificati, precisione 1e-5
  gradi (circa 1 m), latitudine e longitudine alternate, differenze dal punto
  precedente; solo caratteri ASCII 63-126, al massimo 24.000 caratteri
  (2000 punti). Decodificare rifiuta caratteri fuori intervallo, valori o
  coppie incomplete e coordinate fuori dal globo.
- Le posizioni sono dati personali sensibili: solo nell'indice locale, mai nei
  log. Costanti in `activities/domain/limits.py`.

### Traccia completa e quota (`ActivityTrack`, fase 5b)

- La traccia completa **non** sta nell'indice né nella cache dei carichi: si
  rilegge dal file archiviato con `decode_stored_track(data, formato)`
  (`None` se il file non ha posizioni). Non tocca `Activity`, la serie di FC,
  il carico né la sua impronta: sono due letture indipendenti dello stesso
  file, con gli stessi limiti di byte, record e punti.
- `ActivityTrack` = tuple di `TrackSegment` (almeno uno, al più 1.000.000
  punti in tutto). Un tratto ha colonne `seconds`, `lat_deg`, `lon_deg` e
  `altitude_m` (facoltativa, `NaN` dove manca); secondi **dal primo record
  (FIT) o dal primo punto (GPX), come la serie di FC**, strettamente
  crescenti; coordinate finite e dentro i limiti.
- Buchi: un record FIT senza posizione valida, un `trkseg` diverso in un GPX
  o una pausa di posizione oltre 30 s (`TRACK_GAP_S`, anche uguale a 30 s non
  spezza) chiudono il tratto. Un punto con secondi non maggiori di tutti i
  precedenti si scarta (vale il primo), come per la FC.
- Quota FIT: `enhanced_altitude`, altrimenti `altitude` del record; GPX:
  `ele`.
- Semplificazione per zoom: `simplify_for_zoom(traccia, zoom)` usa la
  tolleranza di un pixel (`zoom_tolerance_m`: `2πR·cos(lat)/(256·2^zoom)`,
  zoom 0-22, latitudine limitata a 85°), tiene primo e ultimo punto di ogni
  tratto, omette i tratti di un solo punto e resta entro `MAX_TRACK_POINTS`
  (raddoppia la tolleranza fino a 24 volte, poi tiene i tratti più lunghi).
  Codifica compatta: `encode_segments`/`decode_segments`, un polyline per
  tratto separati da uno spazio (fuori dall'alfabeto ASCII 63-126).
- Riquadro: `GeoBox(south, west, north, east)`; `west > east` attraversa
  l'antimeridiano; 180 e -180 sono lo stesso meridiano; `GeoBox.of_points`
  sceglie il lato corto; `GeoBox.from_view` accetta l'area di una mappa che
  esce dal globo (360° o più = tutto il globo).

### Filtri e ricerca per area (`activities/domain/filters.py`, `area_query.py`)

- `ActivityFilter` (data da-a in giorni inclusi nel fuso passato, durata =
  tempo in movimento, distanza, dislivello positivo, insieme di sport; vuoto =
  tutto). `NumericRange.of(a, b)` scambia gli estremi invertiti. Un valore
  ignoto (indoor senza distanza) non passa un filtro che lo limita.
- Parser tolleranti (`parse_duration`, `parse_distance_km`, `parse_amount`):
  virgola o punto, "90" = minuti, "1:30" = ore e minuti, "1:30:15"; vuoto =
  nessun limite; negativi, non numerici e oltre 10^9 → `FilterInputError` con
  un `FilterInputReason` (l'input non si ripete nel messaggio). Nessun
  separatore delle migliaia (il punto è sempre decimale).
- `StartIndex(attività)` tiene le partenze in colonne; `.in_area(GeoBox)`
  dà `AreaResult` (attività nell'area in ordine d'ingresso, `AreaTotals`
  con numero, km, dislivello, ore, e le attività senza posizione contate a
  parte, indipendenti dall'area). `track_touches_area` decide su una
  polyline per le attività vicine al bordo. `StartIndex` espone anche `located`,
  `lat_deg`, `lon_deg` (colonne delle sole attività con partenza).
- `GeoBox.intersects(altro)` e `intersects_many(south, west, north, east)` (vettoriale,
  antimeridiano compreso; il contatto esatto sulla linea ±180 non si vede).

### Casi d'uso della mappa (`activities/map_queries.py`, `load/activity_profile.py`)

Tutti sul thread dei dati (la connessione è di un solo thread), nessun log (posizioni).

- `ActivitySnapshots(track_boxes, fuso)` legge l'indice **una volta per revisione**
  (`TrackBoxIndex.revision()`: conteggio, ultimo id, riquadri riempiti: una query leggera) e
  tiene l'ultima selezione per filtro; nuove attività o riquadri riempiti si notano da soli.
- `SearchActivities(snapshots)(filtro) -> SearchResult`: `activities` (per partenza crescente),
  `totals` (`AreaTotals`), `without_position`, e le colonne `start_indices`, `start_lat_deg`,
  `start_lon_deg` (sola lettura) per i cerchi numerati: nessun oggetto per partenza.
- `ActivitiesInArea(snapshots, archivio, decode_track)(filtro, GeoBox, zoom) -> AreaView`:
  `in_area` (`AreaResult`: attività filtrate con partenza nell'area, totali, senza posizione),
  `tracks` e `tracks_omitted`. Lo zoom si limita a 0-22. Sotto lo zoom 11
  (`TRACK_MIN_ZOOM`) nessuna traccia. Da 11: le attività filtrate la cui partenza **o** il cui
  riquadro tocca l'area, le più recenti, **al massimo 300** (`MAX_MAP_TRACKS`), ciascuna di al
  massimo 2000 punti. Sorgente della traccia: se la tolleranza di un pixel a quello zoom e
  latitudine è almeno quella dell'indice (10 m) si usa la polyline dell'indice (un solo tratto);
  se è più fine si rilegge il file (`archive.read` con hash e tetto di byte,
  `decode_stored_track`, `simplify_for_zoom`: i buchi del GPS spezzano la linea) con una cache
  LRU di 600 tracce per (identità, zoom); un file inutilizzabile ripiega sulla polyline. In
  ogni caso una linea si taglia dove salta l'antimeridiano.
- `GetActivityProfile(index, archivio, physiology, decode, decode_track, timezone)(identità)`:
  `ActivityProfileData` | `NoTrack` | `ExcludedActivity`. Colonne (al massimo 2000 punti, primo
  e ultimo inclusi): `segment` (cambia dove c'è un buco), `distance_m` (serie del dispositivo
  interpolata, altrimenti lungo la traccia), `lat_deg`, `lon_deg`, `altitude_m` (`None` se il
  file non ha quota: profilo assente), `zone` (0-4 o `NO_ZONE` = -1; il tratto che parte dal punto
  i prende `zone[i]`) e `zone_absence` (`NO_HEART_RATE`, `NO_PARAMETERS`) quando `zone` è `None`,
  `threshold_bpm` e `zone_starts_bpm` del giorno locale dell'attività. La zona di un istante è
  quella del campione di FC in vigore (fino al successivo) **se il carico lo conta** (dentro i
  limiti di FC, intervallo al massimo `MAX_GAP_S`): `zones_at`; i test confrontano i secondi per
  zona con `compute_activity_load`.

### Sfondo della mappa (`maps/`, `platform/tile_*.py`)

Fonti (dal 2026-11-10, ADR 010 emendato due volte): tre sfondi a scelta, elenco chiuso
`BACKGROUNDS` in `maps/domain.py` (`MapBackground`: chiave stabile, nome, host con `{s}` e
sottodomini, prefisso del percorso, zoom massimo, attribuzione): `openstreetmap` (default,
`tile.openstreetmap.org`, zoom 19), `opentopomap` (`{a,b,c}.tile.opentopomap.org`, zoom 17) e
`cyclosm` (`{a,b,c}.tile-cyclosm.openstreetmap.fr/cyclosm/`, zoom 20). Senza chiave (dal
2026-11-09; prima Tracestrack con chiave nel portachiavi: tutto ciò che la riguardava è stato
tolto). Nessun URL arriva da fuori: la sorgente si cerca per chiave nell'elenco.

- `maps/domain.py`: `TileAddress(z, x, y, background=OPENSTREETMAP)` (z da 0 allo zoom massimo
  della sorgente, x e y in `[0, 2^z)`), `parse_tile_path` (`/sorgente/z/x/y` o
  `sorgente/z/x/y.png`, lunghezza prima, solo cifre, sorgente cercata con `background_by_key`), `CacheLimit` (64 MiB - 64 GiB, default
  4 GiB), `parse_cache_limit_gib` ("4", "0,5"). Niente stile, niente chiave.
- `maps/ports.py`: `TileSource.fetch(z, x, y, validatori) -> TileImage | TileNotModified |
  TileUnavailable` (un solo tentativo, mai eccezioni per rete o fornitore; `TileAddressError` per
  coordinate non valide), `TileProblem` (`REFUSED` = 403/429, `NETWORK_UNAVAILABLE`,
  `INVALID_RESPONSE`), `TileValidators(etag, last_modified)` (valori opachi, ASCII stampabile,
  già controllati dall'adattatore), `TileCache` (`get`, `put`, `renew`).
- `maps/use_cases.py`: `TileProxy(sources: Mapping[MapBackground, TileSource], cache, now_utc,
  monotonic)` (una sorgente e una pausa per sfondo): `serve(percorso)` /
  `tile(indirizzo) -> TileAnswer(png, problem)`; prima la cache (fresca), poi il server; una tile
  scaduta si richiede con i validatori salvati (richiesta condizionale) e un 304 la rinnova
  (`renew`). Dopo un problema non richiede per un po' (`COOLDOWN_S`: `REFUSED` 300 s, rete e
  risposta non valida 15 s; `INVALID_RESPONSE` solo per quella tile); una tile scaduta si serve
  comunque con il problema se il server non risponde. Mai prefetch né scaricamenti in blocco.
- `platform/tile_http.py`: `HttpTileSource` su `http.client` (nessun reindirizzamento seguito,
  nessun proxy di sistema, nessun ritentativo). `HttpTileSource(background, user_agent=...)`:
  host e percorso dallo sfondo, sempre HTTPS, sottodominio fisso per tile (`(x + y) % 3`); nell'app
  `create_tile_sources(user_agent)` ne crea una per sfondo; un endpoint `http` solo su loopback
  (`endpoint=`, nei test). `User-Agent` `PonosCoach/<versione> (+<URL del repository>)`, composto
  in `__main__.py`, ASCII stampabile, al massimo 128 caratteri. Timeout per operazione (8 s) e
  scadenza totale (20 s); risposta: stato, `Content-Length` ed `image/png`, al massimo 1 MiB, PNG
  completo con IHDR di lato 1-1024. Scadenza da `Cache-Control` (`max-age`; `no-store`/`no-cache`
  = non tenere), poi `Expires`, altrimenti 7 giorni (`DEFAULT_TTL_S`), mai oltre 30 giorni.
  `ETag` e `Last-Modified` si tengono solo se ASCII stampabile di 1-256 caratteri, e tornano come
  `If-None-Match` / `If-Modified-Since`. 403 e 429 sono `REFUSED`. Mai l'indirizzo della tile nei
  log.
- `platform/tile_cache.py`: `DiskTileCache(cartella, limite)` su un file SQLite
  (`tiles.sqlite3`, WAL) nella cartella cache dell'app (non nel profilo; cartella privata).
  Schema 3 (`user_version`): una riga per `(source, z, x, y)` con `expires_ms`, `etag`,
  `modified`; i file v1 (colonna dello stile) e v2 (senza sorgente) si ricostruiscono vuoti, come
  ogni file illeggibile o di altra versione; un solo tetto per tutti gli sfondi. Tetto sul **conteggio per pagine** (`accounted_bytes`: byte +
  160 di riga, arrotondati a 4096): il file non supera il tetto più poche decine di KiB;
  rimozione LRU per contatore d'uso (non per orologio); `clear()` cancella e ricrea il file.
  Thread-safe (un lock).
- Attribuzione dello sfondo in uso (campo `attribution` dello sfondo), sempre visibile sulla
  mappa; scelta in `ui/map_background.py` (`BackgroundChoice`, `QSettings` `map/background`),
  menu Mappa → Sfondo e controllo nella pagina (`backgroundChosen(chiave)`, convalidata), la
  pagina cambia layer con `setBackground`.
- Pagina della mappa: chiede `ponos://tiles/{sorgente}/{z}/{x}/{y}`; il gestore dello schema chiama
  `TileProxy.serve(percorso, lingua)` (fuori dal thread GUI), risponde 200 con il PNG, 400 per
  `TileAddressError`, 204/404 senza PNG; `problem` va all'avviso della vista (non nell'URL).

## 4. Identità dell'attività

- `ActivityIdentity` è `FitIdentity(FileId)` con il `file_id` del FIT (produttore,
  prodotto, numero di serie, ora di creazione UTC), uguale per lo stesso file
  scaricato da Garmin o copiato dall'orologio. Dalla fase 8 (ADR 014) non
  esiste più `GpxIdentity`: `FileFormat` ha il solo valore `fit`.
- Chiave testuale stabile per i dati salvati (`identity_key` /
  `identity_from_key`): `fit:<produttore>:<prodotto>:<serie>:<ora ISO UTC>`.
- L'id Garmin (facoltativo) si aggiunge quando è noto; con origine `GARMIN`
  c'è sempre. Un'identità e un id Garmin sono unici nell'indice.
- `is_possible_duplicate` (inizi a non più di 60 s, tempi trascorsi che
  differiscono al più del 5% del maggiore, confronto in millisecondi interi,
  simmetrico) serve solo ad abbinare una voce di Garmin a un'attività già
  importata da file (`find_matching_import`); `ActivityIndex.find_possible_duplicates`
  applica quella funzione.

## 5. Sincronizzazione riprendibile

1. Token dal loro archivio (sotto); se mancano o sono scaduti, dialogo di
   login con MFA. La password va solo a `garminconnect` e non si salva.
2. Prima sincronizzazione: elenco di tutto lo storico a pagine, con il punto
   raggiunto nel giornale. Successive: elenco dalle più recenti finché si
   trova una pagina di attività già note, più le voci in sospeso o fallite.
3. Ogni id nuovo diventa una voce "da scaricare" nel giornale (idempotente
   per id Garmin).
   L'elenco finisce con una pagina corta o vuota, mai al totale dichiarato
   (Garmin può ricevere attività nuove durante l'elenco): il totale serve
   solo all'avanzamento.
4. "Verifica completa": rielenca tutto lo storico senza riscaricare, per
   trovare attività con date vecchie caricate in ritardo; riarma anche le voci
   fallite per `ATTEMPTS_EXHAUSTED` o `REMOTE_NOT_PROVIDED` (`REARMABLE_REASONS`,
   `rearm_exhausted`: tornano `PENDING` con i contatori a zero).
5. Per ogni voce: download del FIT originale (zip), controlli dei limiti,
   scrittura durevole, lettura, calcolo del carico; una transazione segna la
   voce completata.
6. Pause tra le richieste con jitter; su 429 o errori transitori, backoff
   esponenziale con tetto e numero massimo di tentativi; poi errore chiaro.
   Valori (default del progetto, in `SyncPolicy`, `activities/domain/
   sync_policy.py`, da rivedere con l'esito dello spike): pausa minima 1 s più
   jitter fino a 0,75 s; backoff da 5 s che raddoppia, più il jitter, con
   tetto 120 s; al massimo 5 tentativi per la stessa richiesta (il quinto
   fallimento ferma i tentativi); 3 sincronizzazioni fallite per la stessa
   attività prima di "fallita definitiva". Un'attesa suggerita da Garmin
   (`Retry-After`) alza il minimo dell'attesa; se supera il tetto non si
   aspetta, si ferma con un errore chiaro. Il jitter viene da una sorgente
   casuale iniettata (`RandomSource`, `random.Random` la soddisfa). Pagine
   dell'elenco da 100 voci (massimo 1000); tetto dello ZIP scaricato 50 MB.
7. Chiusura a metà: all'avvio successivo si riparte dalle voci in sospeso e si
   cancellano i temporanei orfani.
8. Il lavoro gira in un thread proprio (`SyncController`, `SyncWorker` in
   `ui/`) con una connessione propria al profilo, chiusa alla fine, e con
   annullamento cooperativo (si ferma entro 1 s, salvo una richiesta già
   partita: il timeout della libreria, 15 s, non si interrompe); la UI mostra
   avanzamento e Interrompi. Il controller fa una operazione dell'account per
   volta (controllo dei token, login, logout, sincronizzazione). A fine:
   notifica di sistema (solo se ci sono attività nuove) e barra di stato.
   `new_activity_count` conta solo le scaricate: né le abbinate né quelle già
   presenti sono nuove.
9. Abbinamento: prima di scaricare, un'attività dell'elenco Garmin con la
   stessa ora d'inizio e lo stesso sport di una già importata da file si
   abbina a quella senza riscaricarla. Regola (`find_matching_import`): il
   record non ha ancora un id Garmin, lo sport coincide (`sport_from_garmin_key`
   mappa le chiavi di Garmin sugli sport dell'app, il resto è `OTHER`) e vale
   `is_possible_duplicate` (inizi entro 60 s, durate entro il 5%); con più
   candidati il più vicino nell'inizio, poi la chiave d'identità più piccola.
   L'abbinamento collega l'id con `ActivityIndex.link_garmin_id` e segna la
   voce `MATCHED`; l'origine del record resta `IMPORT`.

### Token e login

- Porta `TokenStore` (`profiles/ports.py`: `save`, `load`, `delete`, per id del
  profilo); adattatori e scelta in `platform/token_store.py`
  (`create_token_store`).
- Windows: `DpapiTokenStore`, un file `secrets/<id>.bin` nella cartella dati
  locale dell'app (fuori dal profilo), formato `PNSEC` + versione (1 byte) +
  blob DPAPI con entropia legata all'app e all'id; lettura con tetto di
  dimensione. Il Credential Manager generico non basta (2560 byte).
- Ubuntu: `KeyringTokenStore` sul solo Secret Service (anche dentro un
  `ChainerBackend`), valore con prefisso `v1:`. Backend assente, bloccato o in
  chiaro: `TokenStoreUnavailableError`, mai un file.
- File alterato o non riconosciuto: `TokenStoreCorruptError`, quindi stato
  "da ricollegare" e nuovo login che lo sostituisce. Portachiavi non
  utilizzabile: i comandi dell'account sono spenti con il motivo.
- Ripresa (`GarminConnectAccount.resume`): solo token che sono un oggetto JSON;
  se la libreria li ha rinnovati, il worker li risalva (un guasto qui non
  ferma il giro).
- Login: email e password vuote o oltre i tetti rifiutate prima di contattare
  Garmin; codice MFA chiesto al più 3 volte, vuoto o oltre 32 caratteri non
  inviato; l'attesa del codice nella UI ha un tetto di 300 s (`MFA_WAIT_S`) e
  poi vale come annullata. Annullato o fallito: nulla si salva.
- Errori della libreria tradotti in `garmin/errors.py` secondo come reagisce
  chi chiama: 429 e 427 limite di frequenza (con `Retry-After` solo se fatto di
  cifre ASCII, al più 3600 s), 401 e autenticazione rifiutata, rete e 5xx e 408
  transitori, il resto definitivo. I logger di `garminconnect` stanno a livello
  critico, quelli di rete ad avviso (a livello errore la libreria scrive id e
  testi di Garmin).

### Giornale, stati e cursori

- Ogni attività remota è una voce di `sync_journal` (chiave primaria l'id
  Garmin), con il riepilogo dell'elenco (`RemoteActivity`: inizio, durata,
  chiave dello sport, FC nota; nessun testo libero di Garmin), lo stato, i
  tentativi falliti e la causa. Id, inizio e durata sono validati al confine
  (`GarminActivityId.parse`, `RemoteActivity`, `ActivityPage`); una voce
  malformata si scarta e conta in `raw_count`.
- Stati (`SyncStatus`): `PENDING` (da scaricare), `DOWNLOADED`, `MATCHED`,
  `RETRYABLE` (fallita per una causa transitoria), `FAILED` (definitiva). Non
  c'è uno stato "in corso": download, archivio e conferma stanno in una sola
  transazione, quindi un'interruzione lascia la voce `PENDING`. Da `PENDING` e
  `RETRYABLE` si va in tutti gli altri (anche `RETRYABLE` → `RETRYABLE`);
  `DOWNLOADED` e `MATCHED` non si muovono, `FAILED` solo verso `PENDING` e solo
  per `rearm_exhausted` (`can_transition`, `InvalidTransitionError`),
  e il database rifiuta le combinazioni incoerenti con i CHECK.
- Cause (`FailureReason`): ritentabili `TRANSIENT_ERROR` e `RATE_LIMITED`;
  definitive `UNSUPPORTED_FORMAT` (per esempio un TCX), `CORRUPT_FILE`,
  `FILE_TOO_LARGE`, `NOT_AN_ACTIVITY`, `ARCHIVE_INVALID`, `REMOTE_REJECTED`
  (originale assente o rifiutato), `ATTEMPTS_EXHAUSTED` e `REMOTE_NOT_PROVIDED`. `mark_failed`:
  causa definitiva → `FAILED`; causa ritentabile → `RETRYABLE` fino al terzo
  fallimento (`max_syncs_per_activity`), poi `FAILED` con
  `ATTEMPTS_EXHAUSTED`. Un errore di scrittura locale (disco pieno) non è
  della voce: ferma la sincronizzazione, non si registra sulla voce. Neppure
  un giro fermato da limite di frequenza o servizio irraggiungibile consuma
  tentativi.
- Due contatori per voce: `failed_attempts` (fallimenti della voce) e
  `unanswered_rounds` (schema v13: giri in cui il download della voce non ha avuto
  risposta utile, 5xx o silenzio, mentre Garmin rispondeva ad altro). La voce senza
  risposta è trattenuta e non scritta; il giro conta per lei (`mark_unanswered`:
  `RETRYABLE`/`TRANSIENT_ERROR`, senza toccare `failed_attempts`) solo se Garmin
  risponde a un download successivo o a una sonda (la prima pagina dell'elenco, chiesta
  quando il giro si ferma dopo 3 voci senza risposta di fila o finisce con voci
  trattenute; anche un rifiuto vale come risposta). Senza risposta alla sonda è la
  rete: nulla si scrive e il giro si ferma. Al terzo giro
  (`SyncPolicy.max_unanswered_rounds`) la voce diventa `FAILED` con
  `REMOTE_NOT_PROVIDED` e non ferma più i giri; un download riuscito azzera il
  contatore. Invariante: `PENDING` ha i due contatori a zero, `RETRYABLE` e `FAILED`
  ne hanno almeno uno positivo. Il file scaricato deve coincidere con la voce (inizio e
  durata, regola dei duplicati); se no `REMOTE_REJECTED`, mai registrato.
  `SyncOutcome.downloaded` conta solo le attività nuove, `matched` i gemelli
  in un altro formato, `already_present` i file già nel profilo.
- Lavoro: `next_to_process(limit)` dà `PENDING` e `RETRYABLE` dalla più
  recente (a parità, id decrescente); `add_pending` è idempotente per id e non
  tocca le voci esistenti.
- Cursori (`ListingKind`): `FULL` (prima sincronizzazione), `VERIFY` ("Verifica
  completa") e `INCREMENTAL`. `ListingCursor` (prossima posizione, totale
  dichiarato, completato) avanza con `advance_cursor` e si salva a ogni
  pagina, dopo aver aggiunto le voci della pagina nella stessa transazione.
  L'elenco finisce con una pagina corta, al raggiungimento del totale o, solo
  per `INCREMENTAL`, con una pagina intera di id già noti (`is_known_page`). Il
  cursore incrementale si salva anche lui, così un'interruzione a metà non
  fa perdere le pagine non ancora lette. Un elenco completato riparte da
  `ListingCursor.start()`.
- `sync_state` ricorda la fine dell'ultima sincronizzazione
  (`record_completed`, per la barra di stato).
- Stima del tempo restante: media mobile delle ultime 20 durate per voce
  (pause comprese) per le voci rimaste (`estimate_remaining_s`).

## 6. Import di file FIT e di cartelle

Porta `ImportSource` (`activities/ports.py`); adattatori `FilesImportSource` e
`FolderImportSource` in `storage/import_scan.py`; caso d'uso
`import_activities` in `activities/use_cases.py`; tetti in
`activities/import_limits.py`.

1. **Esame** (`scan`): solo nomi e dimensioni, mai i contenuti. Solo file
   `.fit` (senza badare alle maiuscole), sottocartelle comprese per una
   cartella; gli altri file (GPX, ZIP, TCX...) in una cartella si ignorano
   senza segnalarli, scelti a mano sono `UNSUPPORTED_FORMAT`. Solo file regolari dentro la cartella scelta; collegamenti,
   giunzioni, reparse point, FIFO e dispositivi si saltano e si segnalano;
   nomi riservati di Windows e percorsi lunghi si saltano; il tipo si
   ricontrolla sull'handle aperto.
2. Tetti (default del progetto, costanti in un solo modulo):

   | Tetto | Valore |
   |---|---|
   | File di attività (`.fit`) | 50.000 |
   | Voci esaminate (file e cartelle di ogni tipo) | 500.000 |
   | Livelli di sottocartelle | 32 |
   | Dimensione di un file | 50 MB |
   | Percorso mostrato | 2048 caratteri |

3. Se si supera uno dei primi tre, l'esame si ferma, non si importa nulla e
   l'app chiede una sottocartella: niente import parziali. I file oltre 50 MB
   sono segnalati e saltati.
4. **ZIP:** non si importano (fase 8). Lo ZIP si legge ancora per l'originale
   che Garmin manda nella sincronizzazione (`zipread.read_activity_entry`:
   l'unica voce `.fit`; un solo GPX o TCX è `NO_ACTIVITY`, cioè
   `UNSUPPORTED_FORMAT`, e più voci `.fit` sono `MULTIPLE_ACTIVITIES`) e per il
   backup del profilo, con le stesse difese (directory centrale, numero di
   voci, somma e rapporto delle dimensioni, CRC, nomi mai usati come
   percorsi, voci cifrate e ZIP annidati rifiutati).
5. **Lettura di un file:** `decode_activity_file` controlla l'estensione
   (`.fit`, altrimenti `UNSUPPORTED_FORMAT`) e decodifica con il lettore FIT,
   che verifica firma, CRC e struttura. Un solo errore, `ActivityFileError`,
   con un motivo enumerato.
6. **Esiti per file** (`ImportStatus`): importata; già presente (stessa
   identità); ignorata (il file non è
   un'attività); fallita, con il motivo (`ImportIssue`); riparata (`REPAIRED`):
   l'attività c'è già ma il suo file in archivio manca o è alterato, e la
   reimportazione dello stesso originale lo rimette a posto (il riepilogo la
   conta a parte, non come problema). Un errore imprevisto su un file è
   un esito fallito di quel file e l'import continua.
7. Ogni file passa dagli stessi controlli dei FIT scaricati; il file si
   archivia e il record si scrive nella stessa transazione del controllo dei
   duplicati. La cartella o il file d'origine non si modifica mai.
8. L'import è idempotente e si annulla tra un file e l'altro; un riepilogo
   parziale (`cancelled`) si riprende rilanciando. Alla fine un riepilogo:
   conteggi per esito e, per ogni file non importato, nome e motivo.
9. Nessun log: nomi e posizioni sono dati personali.

## 7. Lock

- `ponos.lock` è un lock del sistema operativo sul file aperto (`msvcrt` su
  Windows, `flock` su Ubuntu), tenuto per tutta la vita del profilo: un solo
  processo per profilo. Vale anche per due aperture nello stesso processo.
- Una seconda istanza è rifiutata con `ProfileInUseError` e un messaggio.
- Un lock rimasto da un crash non blocca nessuno: il sistema lo rilascia
  quando il processo muore (T-DATI-04). Il file di lock non può essere un
  collegamento.
- Lo stesso meccanismo protegge le scritture dell'elenco dei profili
  (`profiles.json.lock`), con attesa limitata.
- Su rete o cartelle sincronizzate il lock non è garantito: per questo c'è
  l'avviso.

## 8. Test attesi

`T-DATI-01…06`, `T-SYNC-01…06`, `T-IMP-01…04`, `T-SEC-02`,
`T-SEC-04`, `T-SEC-09` in `PLAN.md` sezione 7.

## 9. Porte della fase 1 e test di contratto

- Porte della fase 2: `ActivitySource` (pagina dell'elenco, download dello ZIP
  con tetto di byte), `GarminAccount` (`login` con funzione per il codice MFA,
  `resume` da token; entrambi dànno una `GarminSession` con sorgente e token da
  salvare), `SyncJournal` (stessa connessione e transazione dell'indice),
  `ActivityIndex.find_by_garmin_id` e `link_garmin_id`, e in
  `profiles/ports.py` `TokenStore` (token per profilo: `save`, `load`,
  `delete`). I token sono `SessionTokens`, una stringa opaca il cui `repr`
  non mostra mai il contenuto. Errori di Garmin, distinti dove cambia la
  reazione: `AuthenticationRequiredError`, `RateLimitedError`
  (`retry_after_s`), `TransientSourceError` (`ServiceUnavailableError`),
  `DefinitiveSourceError` (`DownloadTooLargeError`), `InvalidCredentialsError`
  e `MfaCancelledError`; `TokenStoreUnavailableError` e
  `TokenStoreCorruptError` per il portachiavi. Fake in `tests/fakes/garmin.py`
  (`FakeActivitySource` con guasti per chiamata o per attività e risposta
  lenta, `FakeGarminAccount`) e `tests/fakes/tokens.py`; contratti in
  `tests/contracts/contract_sync.py` e `contract_tokens.py`.
- Porte: `ActivityArchive` e `ActivityIndex` (`activities/ports.py`),
  `PhysiologyStore` e `LoadCache` (`load/ports.py`),
  `WeightStore` e `FixedDataStore` (`profiles/ports.py`), `ProfileRegistry`,
  `ProfileFolders` e `ProfileStorage` (`profiles/ports.py`). `ProfileStorage`
  apre un `OpenProfile` i cui store condividono una connessione:
  `activities.transaction()` copre anche parametri, peso, dati fissi
  e cache.
- Una transazione per volta (annidare è `TransactionStateError`); fuori da
  una transazione ogni operazione è atomica da sola.
- Il lock del profilo (`ProfileFolders.acquire_lock`) è separato dal database:
  prima si prende il lock, poi si apre il database.
- I fake in memoria stanno in `tests/fakes/`; i contratti in
  `tests/contracts/` (classi `*Contract` con la fixture `harness`): ogni
  adattatore reale li eredita con la propria impalcatura.

## 10. Calendario Garmin (fase 7)

Decisioni in `docs/adr/013-calendario-garmin.md`; che cosa offre `garminconnect` 0.3.20 e le forme
JSON (non verificate dal vivo, le controlla `T-MAN-01`) in `experiments/garmin_workouts.md`.

- **Dominio** (`planning/domain/workouts.py`): `workout_from_session(PlannedSession) ->
  WorkoutSpec`; passi piatti (`WorkoutStep`: tipo, secondi, FC bassa e alta in bpm, ritmo lento e
  veloce in s/km se noto). Obiettivi di un passo nell'allenamento Garmin (ADR 013 emendato il
  2026-11-09): se il passo di corsa ha un intervallo di ritmo, il **ritmo è l'obiettivo
  principale** e l'intervallo di FC il secondario (in intervalli la FC è in ritardo e guida male
  lo sforzo); senza intervallo di ritmo la FC resta l'obiettivo principale. L'app elenca il
  ritmo prima della FC. Le ripetizioni di una seduta a intervalli sono scritte una per una
  (lavoro, recupero, lavoro, ..., lavoro); i blocchi a ritmo gara puntano al ritmo gara ±5 s/km. Il
  nome è `"Ponos · " + tipo + minuti` (`WORKOUT_PREFIX`): da qui l'app riconosce i suoi
  allenamenti (`ScheduledWorkout.is_app_created`). Massimo 40 passi.
- **Porta** (`planning/workout_calendar.py`): `WorkoutCalendar` con `create_and_schedule`,
  `list_scheduled(first, last)`, `export_definition(workout_id)`, `create_from_definition`,
  `remove(item, delete_workout=)`; errori della sorgente Garmin più `CalendarUnsupportedError`
  (adattatore incompleto). `WorkoutBackupStore` (`add`, `pending`, `mark_restored`): copia locale
  di ciò che si elimina, tabella `workout_backup` (v9 provvisoria in
  `storage/schema_workout_backup.py`), al massimo 1000 copie in attesa, le ripristinate si
  dimenticano dopo 30 giorni.
- **Casi d'uso** (`planning/calendar_use_cases.py`, tutti con `CalendarPorts`):
  `SendPlanToGarmin(plan, scope, today, is_cancelled)` con `WholePlan | WeekOfPlan |
  SessionOfDay`; `ListScheduledWorkouts(first, last)`; `DeleteScheduledWorkouts(items, today)`;
  `RecreateDeletedWorkouts(today, only=)` e `.pending()`. Non sollevano per gli errori di Garmin:
  l'esito ha `stop` (`StopReason`) e i fallimenti per voce (`Failure`, `FailureReason`).
- **Regole comuni**: pausa con jitter prima di ogni richiesta e backoff come la sincronizzazione
  (`SyncPolicy`); 429 e transitori ritentati fino ai tentativi della politica, poi la corsa si
  ferma; sessione scaduta, annullamento, operazione non supportata e copia non salvabile fermano
  la corsa; un rifiuto di Garmin fa fallire solo quella voce. Tetti: 60 allenamenti per invio (i
  primi per data, il resto in `deferred`), 100 per eliminazione, 60 per ricreazione.
- **Idempotenza**: prima si legge il calendario; stesso nome e stesso giorno = già presente, non si
  ricrea; dopo un errore transitorio si cerca l'allenamento prima di crearlo di nuovo.
- **Eliminazione** (T-SEC-10): solo le voci date (la lista confermata dall'utente), solo da oggi in
  poi; per ognuna: legge la definizione, salva la copia, `remove` (rilegge la voce e rifiuta se non
  è più quella scelta), toglie dal calendario; dalla libreria Garmin solo se ha il prefisso
  dell'app. Senza copia non si elimina.
- **Adattatore** (`garmin/`): `workout_client.py` (le sette chiamate alla libreria),
  `workout_json.py` (costruzione e lettura dei JSON, voci illeggibili scartate mai riparate),
  `workouts.py` (`GarminWorkoutCalendar`), `connect.py` (`ConnectClient` fa anche da
  `GarminWorkoutClient`), `GarminConnectSession.calendar` (None se il client non sa fare
  allenamenti).
- **Test**: fake in `tests/fakes/workout_calendar.py` e `tests/garmin/workout_fakes.py`; contratti
  in `tests/contracts/test_workout_contracts.py`; fixture JSON sintetiche in
  `tests/fixtures/garmin/`.

## 7. Profili con attività GPX (migrazione v11, ADR 014)

Un profilo v1-v10 con attività importate da GPX si migra all'apertura
(`storage/sqlite_database.py`, `sqlite_schema.py`, `archive_cleanup.py`):

1. copia coerente del database in `backup/ponos-v<da>-v11.sqlite3` (la fa il
   meccanismo di ogni migrazione; senza copia non si migra);
2. si leggono prima i percorsi d'archivio delle attività non FIT;
3. una sola transazione (`MIGRATIONS[10]`): le voci del giornale che puntano a
   un'attività GPX tornano `pending`; la cache dei carichi si svuota;
   `activities` e `threshold_test` si ricostruiscono (copia delle sole righe
   `fit`; i CHECK ammettono solo `fit`) e si rinominano; l'indice si ricrea;
4. dopo la conferma, `remove_gpx_files` toglie i file GPX dall'archivio, solo
   se il percorso ha la forma esatta `<anno>/<sha256>.gpx` e il file è
   regolare (mai un collegamento); un file rimasto è un orfano innocuo;
5. `OpenProfile.removed_unsupported_activities` dà il numero di attività
   tolte (0 senza migrazione): la finestra mostra l'avviso una volta
   (`ProfileView.removed_unsupported`). Il numero non si salva.

Il formato del backup (`.ponosbackup`) accetta ancora voci `.gpx`, per poter
ripristinare un backup fatto prima: il profilo ripristinato si migra aprendolo.
Test: `tests/collaudo/test_fase8_solo_fit.py`.
