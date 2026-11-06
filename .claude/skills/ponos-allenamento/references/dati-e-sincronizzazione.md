# Dati e sincronizzazione

**Quando leggerlo:** scrivendo o rivedendo `storage`, `profiles`, `garmin`,
`activities` (import e sincronizzazione), `platform`, i test `T-DATI`, `T-SYNC`,
`T-IMP`, `T-GPX`. Sezione 5 di `SKILL.md`. ADR: `docs/adr/003-formato-dati.md`
e `docs/adr/005-schema-sqlite-e-archivio.md` (schema, archivio e identità:
qui non si ripetono).

## 1. Cartella del profilo

Scelta dall'utente alla creazione; autosufficiente. Nomi in
`profiles/domain/layout.py`.

```
<cartella del profilo>/
  ponos.sqlite3     database (WAL: anche -wal e -shm)
  archivio/<anno>/<sha256>.fit|.gpx
  backup/           ponos-v{da}-v{a}.sqlite3: copia prima di ogni migrazione
  ponos.lock        lock del sistema operativo sul file aperto
```

- I token Garmin non stanno qui: stanno nel portachiavi del sistema, legati
  all'id del profilo.
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
- **Creazione:** cartella, lock, database, parametri iniziali e voce
  nell'elenco; se un passo fallisce, il rollback annulla solo ciò che ha
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
  carichi e (dalla v2) il giornale della sincronizzazione; obiettivi, piani,
  infortuni e impostazioni arrivano con le loro fasi, con nuove migrazioni.
  Carichi e PMC sono derivati e rigenerabili.
- **Schema v2** (`sync_journal`, `sync_cursor`, `sync_state`, tutte `STRICT`,
  tempi in millisecondi UTC): vedi la sezione 5. La migrazione v1 → v2 passa
  dalla copia `backup/ponos-v1-v2.sqlite3` come ogni migrazione (T-DATI-01
  prova un database v1 congelato scritto a mano in
  `tests/collaudo/test_database_del_profilo.py`, insieme alle due migrazioni
  congelate). Gli insiemi chiusi della v2 (stati, cause, elenchi) hanno un test
  che li confronta con gli enum del dominio.
- Campi sconosciuti di `meta` conservati al risalvataggio.
- Connessione: `foreign_keys = ON`, `trusted_schema = OFF`, WAL,
  `synchronous = FULL`; `quick_check` e `foreign_key_check` all'apertura.

## 3. Archivio dei file originali

- I file originali (FIT, GPX) sono la fonte di verità delle attività; la porta
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
  `activities/domain/limits.py`, `FitLimits` e `GpxLimits`): dimensione
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
  (`total_ascent`, m; per un GPX la soglia di 3 m), punto di partenza e
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

## 4. Identità dell'attività

- Unione di due casi (`ActivityIdentity`): `FitIdentity(FileId)` con il
  `file_id` del FIT (produttore, prodotto, numero di serie, ora di creazione
  UTC), uguale per lo stesso file scaricato da Garmin o copiato
  dall'orologio; `GpxIdentity(sha256_hex)` con lo SHA-256 dei byte del GPX,
  che non ha un `file_id`.
- Chiave testuale stabile per i dati salvati (`identity_key` /
  `identity_from_key`): `fit:<produttore>:<prodotto>:<serie>:<ora ISO UTC>`
  oppure `gpx:<sha256>`.
- L'id Garmin (facoltativo) si aggiunge quando è noto; con origine `GARMIN`
  c'è sempre. Un'identità e un id Garmin sono unici nell'indice.
- La stessa attività arrivata come FIT e come GPX non ha la stessa identità:
  si riconosce come possibile duplicato con `is_possible_duplicate` (inizi a
  non più di 60 s, tempi trascorsi che differiscono al più del 5% del
  maggiore, confronto in millisecondi interi, simmetrico); si tiene la prima
  arrivata. `ActivityIndex.find_possible_duplicates` applica quella funzione.

## 5. Sincronizzazione riprendibile

1. Token dal portachiavi; se mancano o sono scaduti, dialogo di login con MFA.
   La password va solo a `garminconnect` e non si salva.
2. Prima sincronizzazione: elenco di tutto lo storico a pagine, con il punto
   raggiunto nel giornale. Successive: elenco dalle più recenti finché si
   trova una pagina di attività già note, più le voci in sospeso o fallite.
3. Ogni id nuovo diventa una voce "da scaricare" nel giornale (idempotente
   per id Garmin).
4. "Verifica completa": rielenca tutto lo storico senza riscaricare, per
   trovare attività con date vecchie caricate in ritardo.
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
8. Il lavoro gira in un worker con annullamento (si ferma entro 1 s); la UI
   mostra avanzamento e Annulla. A fine: notifica di sistema, barra di stato,
   PMC e piano aggiornati.
9. Abbinamento: prima di scaricare, un'attività dell'elenco Garmin con la
   stessa ora d'inizio e lo stesso sport di una già importata da file si
   abbina a quella senza riscaricarla. Regola (`find_matching_import`): il
   record non ha ancora un id Garmin, lo sport coincide (`sport_from_garmin_key`
   mappa le chiavi di Garmin sugli sport dell'app, il resto è `OTHER`) e vale
   `is_possible_duplicate` (inizi entro 60 s, durate entro il 5%); con più
   candidati il più vicino nell'inizio, poi la chiave d'identità più piccola.
   L'abbinamento collega l'id con `ActivityIndex.link_garmin_id` e segna la
   voce `MATCHED`; l'origine del record resta `IMPORT`.

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
  `RETRYABLE` si va in tutti gli altri (anche `RETRYABLE` → `RETRYABLE`); gli
  stati finali non si muovono (`can_transition`, `InvalidTransitionError`),
  e il database rifiuta le combinazioni incoerenti con i CHECK.
- Cause (`FailureReason`): ritentabili `TRANSIENT_ERROR` e `RATE_LIMITED`;
  definitive `UNSUPPORTED_FORMAT` (per esempio un TCX), `CORRUPT_FILE`,
  `FILE_TOO_LARGE`, `NOT_AN_ACTIVITY`, `ARCHIVE_INVALID`, `REMOTE_REJECTED`
  (originale assente o rifiutato) e `ATTEMPTS_EXHAUSTED`. `mark_failed`:
  causa definitiva → `FAILED`; causa ritentabile → `RETRYABLE` fino al terzo
  fallimento (`max_syncs_per_activity`), poi `FAILED` con
  `ATTEMPTS_EXHAUSTED`. Un errore di scrittura locale (disco pieno) non è
  della voce: ferma la sincronizzazione, non si registra sulla voce.
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

## 6. Import di file, cartelle e ZIP

Porta `ImportSource` (`activities/ports.py`); adattatori `FilesImportSource` e
`FolderImportSource` in `storage/import_scan.py`; caso d'uso
`import_activities` in `activities/use_cases.py`; tetti in
`activities/import_limits.py`.

1. **Esame** (`scan`): solo nomi, dimensioni e directory degli ZIP, mai i
   contenuti. File `.fit`, `.gpx` e `.zip`, sottocartelle comprese per una
   cartella. Solo file regolari dentro la cartella scelta; collegamenti,
   giunzioni, reparse point, FIFO e dispositivi si saltano e si segnalano;
   nomi riservati di Windows e percorsi lunghi si saltano; il tipo si
   ricontrolla sull'handle aperto.
2. Tetti (default del progetto, costanti in un solo modulo):

   | Tetto | Valore |
   |---|---|
   | File di attività (FIT e GPX, voci degli ZIP comprese) | 50.000 |
   | Voci esaminate (file e cartelle di ogni tipo, voci degli ZIP comprese) | 500.000 |
   | Livelli di sottocartelle | 32 |
   | Dimensione di un file o di una voce | 50 MB |
   | Voci di un solo ZIP | 50.000 |
   | Dimensioni dichiarate di uno ZIP | 5 GB |
   | Compressione di una voce | 100:1 |
   | Directory centrale di uno ZIP | 32 MiB |
   | Percorso mostrato | 2048 caratteri |

3. Se si supera uno dei primi tre, l'esame si ferma, non si importa nulla e
   l'app chiede una sottocartella: niente import parziali. I file oltre 50 MB
   sono segnalati e saltati.
4. **ZIP:** si leggono solo le voci `.fit` e `.gpx`. Prima di estrarre si
   controllano directory centrale, numero di voci (dichiarato e contato),
   somma e rapporto delle dimensioni; durante l'estrazione la dimensione reale
   e il CRC. Nomi delle voci mai usati come percorsi; rifiutati `..`,
   percorsi assoluti, unità, controlli, nomi riservati, nomi duplicati (anche
   per maiuscole), voci cifrate e ZIP annidati. Nell'archivio va il file
   estratto, non lo ZIP.
5. **Lettura di un file:** `decode_activity_file` riconosce il formato dal
   contenuto, controlla che coincida con l'estensione (altri XML, come il
   TCX, sono `UNSUPPORTED_FORMAT`) e lo decodifica con il lettore FIT o GPX.
   Un solo errore, `ActivityFileError`, con un motivo enumerato.
6. **Esiti per file** (`ImportStatus`): importata; già presente (stessa
   identità); duplicato di un altro formato; ignorata (il file non è
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

`T-DATI-01…06`, `T-SYNC-01…06`, `T-IMP-01…04`, `T-GPX-01…02`, `T-SEC-02`,
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
  `PhysiologyStore` e `LoadCache` (`load/ports.py`), `ProfileRegistry`,
  `ProfileFolders` e `ProfileStorage` (`profiles/ports.py`). `ProfileStorage`
  apre un `OpenProfile` i cui tre store condividono una connessione:
  `activities.transaction()` copre anche parametri e cache.
- Una transazione per volta (annidare è `TransactionStateError`); fuori da
  una transazione ogni operazione è atomica da sola.
- Il lock del profilo (`ProfileFolders.acquire_lock`) è separato dal database:
  prima si prende il lock, poi si apre il database.
- I fake in memoria stanno in `tests/fakes/`; i contratti in
  `tests/contracts/` (classi `*Contract` con la fixture `harness`): ogni
  adattatore reale li eredita con la propria impalcatura.
