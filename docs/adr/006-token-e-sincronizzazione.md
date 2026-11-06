# 006. Token, thread di sincronizzazione, giornale e politica di ritentativi

- Stato: Accettato
- Data: 2026-11-06

## Contesto

La fase 2 collega Ponos Coach a Garmin Connect (ADR 002) e scarica le
attività. Quattro scelte diventano difficili da cambiare dopo il rilascio:
dove stanno i token, su quale thread e connessione gira la sincronizzazione,
che cosa si salva nel database per poter riprendere, e come ci si comporta
con i limiti di Garmin.

Questo ADR precisa la parte dell'ADR 002 che dice "i token stanno nel
portachiavi del sistema" e aggiunge una versione dello schema (v2) all'ADR
005. Il resto di entrambi resta valido.

Vincoli:

- l'accesso non è ufficiale: poche richieste, nessun ciclo senza fine;
- i token danno accesso all'account: non stanno nella cartella del profilo né
  in chiaro su disco;
- la finestra non si blocca durante login e download;
- un'interruzione in qualunque punto non perde né duplica attività;
- il database è un indice: i file originali restano la fonte di verità.

## Opzioni

### Dove stanno i token

1. **Windows: file per profilo cifrato con DPAPI (ambito utente); Ubuntu:
   Secret Service. Nessun ripiego in chiaro.**
   - Pro: nessun limite di dimensione; il testo dei token non tocca mai il
     disco in chiaro; il Secret Service è il posto standard su Linux.
   - Contro: su Windows il file lo gestisce l'app (formato, permessi,
     cancellazione); DPAPI protegge dagli altri utenti, non dagli altri
     processi dello stesso utente; senza Secret Service su Ubuntu l'account
     non si collega.
2. **Credential Manager di Windows tramite `keyring`.**
   - Pro: nessun formato da gestire; il posto previsto da Windows.
   - Contro: il Credential Manager generico accetta al massimo 2560 byte e i
     token di Garmin sono più lunghi (lo spike della fase 0 ne misura la
     dimensione): il salvataggio fallirebbe o li troncherebbe.
3. **File cifrato con una chiave propria dell'app o con una password
   dell'utente.**
   - Pro: stesso comportamento su ogni sistema.
   - Contro: una chiave nel codice o accanto al file è offuscamento; una
     password in più per l'utente va contro "il login si fa una volta" e
     richiede una chiave di recupero.
4. **File in chiaro con permessi privati, o ripiego in chiaro quando il
   portachiavi manca.**
   - Pro: funziona ovunque.
   - Contro: il segreto resta leggibile da backup, antivirus e strumenti di
     sincronizzazione; è il ripiego silenzioso che le regole vietano.

### Thread e connessione della sincronizzazione

1. **Un thread per operazione dell'account, con una sua connessione SQLite
   aperta sul profilo.**
   - Pro: la finestra e l'import restano disponibili; nessuna connessione
     condivisa tra thread; WAL permette di leggere mentre si scrive.
   - Contro: due connessioni possono contendersi la scrittura (attesa di 5 s,
     poi `StorageBusyError`); i risultati tornano alla UI con segnali
     accodati.
2. **La connessione del thread dei dati, con un lock.**
   - Pro: una connessione sola.
   - Contro: import, grafici e sincronizzazione si aspettano; un download
     lento tiene fermo il resto.
3. **Un processo figlio per la sincronizzazione.**
   - Pro: isolamento totale dalla libreria non ufficiale.
   - Contro: gestione del processo (job, chiusura, canale di comunicazione)
     sproporzionata per una sola libreria.

### Giornale e cursori

1. **Tabelle nello schema v2: `sync_journal` (una riga per attività remota
   con stato e causa), `sync_cursor` (punto raggiunto in ciascun elenco),
   `sync_state` (fine dell'ultima sincronizzazione).**
   - Pro: la ripresa è un fatto del database, nella stessa transazione
     dell'indice; le voci fallite hanno una causa e un conteggio; l'elenco
     riparte dall'ultima pagina.
   - Contro: una migrazione (v1 → v2) con copia di sicurezza; i `CHECK` e gli
     insiemi chiusi vanno tenuti allineati agli enum del dominio.
2. **Nessun giornale: si confronta l'elenco di Garmin con la tabella
   `activities` a ogni giro.**
   - Pro: nessuna tabella nuova.
   - Contro: ogni giro rielenca tutto lo storico; un'attività che fallisce
     per sempre viene riscaricata a ogni giro; nessun punto di ripresa
     dell'elenco.
3. **Un file di stato (JSON) accanto al database.**
   - Pro: nessuna migrazione.
   - Contro: due fonti di verità da tenere coerenti; niente transazione in
     comune con l'indice.

### Pause, backoff e ritentativi

1. **Politica propria (`SyncPolicy`), con la libreria a zero ritentativi.**
   - Pro: un solo livello di ritentativi, con valori visibili, orologio e
     jitter iniettati, quindi provabili senza dormire.
   - Contro: i valori sono ipotesi finché non c'è un'esperienza reale con
     Garmin; vanno rivisti con l'esito dello spike.
2. **Ritentativi della libreria (`retry_attempts` predefinito).**
   - Pro: nessun codice.
   - Contro: due livelli di ritentativi si moltiplicano; attese che l'app non
     vede né può annullare.
3. **Nessun ritentativo: il primo errore ferma il giro.**
   - Pro: massima prudenza verso Garmin.
   - Contro: un timeout isolato interrompe una sincronizzazione lunga.

### Attività già importate da file

1. **Abbinamento prima del download: stessa ora d'inizio (entro 60 s), stesso
   sport e durata entro il 5%, su una voce senza id Garmin.**
   - Pro: nessuna richiesta inutile; nessun duplicato.
   - Contro: due attività molto simili dello stesso sport possono essere
     abbinate male; la regola è la stessa dei duplicati tra FIT e GPX (ADR
     005).
2. **Scaricare sempre e lasciare che l'identità elimini il duplicato.**
   - Pro: nessuna euristica.
   - Contro: un FIT riesportato non ha la stessa identità dell'originale
     importato dall'orologio; si scarica tutto lo storico anche se già
     presente.
3. **Chiedere all'utente.**
   - Pro: nessun errore.
   - Contro: migliaia di domande alla prima sincronizzazione.

## Decisione

### Token

- Opzione 1. Il punto unico di scelta è `create_token_store`
  (`platform/token_store.py`); la porta è `TokenStore` (`save`, `load`,
  `delete`), per id del profilo. I token sono `SessionTokens`, una stringa
  opaca il cui `repr` non mostra il contenuto.
- Windows: `<cartella dati locale dell'app>/secrets/<id>.bin`, cartella privata,
  scrittura atomica. Contenuto: magic, versione del formato e blob DPAPI. Una
  entropia legata all'app e all'id del profilo evita che il file di un profilo
  si decifri in un altro. L'entropia non è un segreto: il codice è pubblico.
- Ubuntu: Secret Service tramite `keyring`, servizio dell'app, utente = id del
  profilo, valore con prefisso di versione. Un backend assente, bloccato o in
  chiaro è `TokenStoreUnavailableError`: mai un ripiego su file.
- Senza archivio utilizzabile i comandi dell'account sono disabilitati con il
  motivo; l'import da file resta disponibile.
- I token non stanno mai nella cartella del profilo né nel backup. Un profilo
  portato su un altro PC richiede un nuovo login.
- Un file illeggibile o alterato è `TokenStoreCorruptError`: l'app chiede un
  nuovo login e lo sostituisce.
- Il portachiavi è leggibile dai processi dell'utente: rischio accettato nel
  threat model.

### Thread e connessione

- Opzione 1. Controllo dei token, login, logout e sincronizzazione sono
  operazioni di un solo controller (`SyncController`), una alla volta, ognuna
  in un thread proprio.
- La sincronizzazione apre una sua connessione al profilo e la chiude alla
  fine; l'archivio è scritto prima della transazione, che dura solo il tempo
  del record.
- L'annullamento è cooperativo (`threading.Event`), controllato prima di ogni
  richiesta, voce e pagina; le pause sono interrompibili.
- La password e il codice di verifica passano solo a `GarminAccount` e non si
  conservano. L'attesa del codice ha un tetto di 5 minuti, poi vale come
  annullata e non si salva nulla.
- Il thread non solleva verso la UI: restituisce un esito (`SyncFinished` o
  `SyncNotStarted`); un guasto imprevisto si registra col solo tipo.

### Giornale e cursori

- Opzione 1, schema v2. Colonne e vincoli sono in `storage/sqlite_schema.py`
  (fonte unica); questo ADR non li ripete. La migrazione v1 → v2 segue la
  procedura dell'ADR 005 (copia in `backup/ponos-v1-v2.sqlite3`).
- Stati: da scaricare, scaricata, abbinata, ritentabile, fallita. Non esiste
  uno stato "in corso": un'interruzione lascia la voce da scaricare.
- Il cursore si salva a ogni pagina, nella stessa transazione delle voci. Un
  elenco finisce con una pagina corta o vuota, mai al totale dichiarato da
  Garmin (che serve solo all'avanzamento).
- Elenchi: completo (prima volta), incrementale (dalle più recenti, fermo a
  una pagina intera di id già noti), verifica completa (rielenca tutto e
  riarma le voci fallite per tentativi esauriti).
- Una voce fallita per causa ritentabile diventa definitiva dopo un numero
  fisso di sincronizzazioni fallite. Un giro fermato da limite di frequenza o da
  servizio irraggiungibile non consuma tentativi.

### Politica di pause e ritentativi

- Opzione 1. I valori (pausa con jitter, backoff esponenziale con tetto,
  numero massimo di tentativi per richiesta e per attività, dimensione della
  pagina) sono default del progetto in `SyncPolicy`
  (`activities/domain/sync_policy.py`) e nella skill del progetto; qui non si
  ripetono.
- Un'attesa suggerita da Garmin (`Retry-After`) alza il minimo dell'attesa; se
  supera il tetto non si aspetta e la sincronizzazione si ferma con un
  messaggio. Un valore illeggibile è ignorato; uno negativo o non finito vale
  "attesa implausibile", cioè una sincronizzazione che si ferma, mai un
  errore né una pausa lunga.
- Dopo alcune voci di fila fallite per servizio irraggiungibile ci si ferma.
- La libreria è configurata a zero ritentativi.
- Il jitter e l'orologio sono iniettati.

### Abbinamento

- Opzione 1. L'abbinamento collega l'id Garmin al record esistente e segna la
  voce come abbinata; l'origine del record resta quella dell'import.
- Il file scaricato deve coincidere con la voce dell'elenco (stessa regola dei
  duplicati); se è un'altra attività la voce fallisce in modo definitivo e non
  si registra nulla.
- Le attività già presenti nel profilo non contano come nuove nel riepilogo e
  nella notifica.

## Conseguenze

- Su Windows l'app possiede il formato del file dei token: una modifica ha
  bisogno di una nuova versione del formato e di un test di lettura della
  precedente.
- Senza Secret Service su Ubuntu non si collega l'account. Il messaggio lo
  dice e indica l'import da file.
- Due connessioni al database: un'attesa del lock oltre 5 s ferma la
  sincronizzazione con un errore chiaro e senza toccare nulla.
- Una richiesta già partita non si interrompe prima del timeout della libreria
  (15 s per richiesta, 30 s per passo di login); l'annullamento vale dalla
  richiesta successiva.
- I valori della politica sono ipotesi; cambiarli non cambia il formato su
  disco e non richiede un ADR. Cambiare gli stati o le cause richiede una
  migrazione.
- Un abbinamento sbagliato (due attività quasi uguali) collega un id al record
  sbagliato e l'altra non si scarica; l'utente non perde il file originale
  già importato. Accettato.
- Un cambio del posto dei token o del significato degli stati richiede un
  nuovo ADR.

## Come se ne verifica il rispetto

- Token: contratto `TokenStore` sul fake e sugli adattatori reali
  (`tests/contracts/contract_tokens.py`, `tests/platform/test_token_store.py`);
  T-SEC-07 (con un token segnaposto, nulla nella cartella del profilo, nei log,
  nei temporanei, dopo login, sincronizzazione, kill e logout; il logout
  cancella; il file di un altro profilo non si decifra) in
  `tests/collaudo/test_fase2_segreti.py`.
- Log: T-SEC-08 sulla sincronizzazione intera, nello stesso file.
- Thread e annullamento: T-SYNC-06 (fermo entro 1 s) in
  `tests/activities/test_sync_errors.py` e
  `tests/collaudo/test_fase2_sincronizzazione.py`; T-UI-02 in
  `tests/ui/test_garmin_window.py` e `tests/collaudo/test_fase2_prestazioni.py`.
- Giornale e ripresa: T-SYNC-01 (completa poi incrementale; 5.000 attività in
  `tests/activities/test_sync_volume.py`, 20.000 in
  `tests/collaudo/test_fase2_prestazioni.py`); T-SYNC-02 con un vero kill del
  processo (`tests/collaudo/test_sync_kill.py`); T-DATI-01 con il database v1
  congelato e un test che confronta gli insiemi chiusi della v2 con gli enum.
- Politica: T-SYNC-03 con orologio finto (`tests/activities/test_sync_errors.py`)
  e con `Retry-After` ostili (`tests/collaudo/test_fase2_garmin_ostile.py`);
  T-SYNC-04 per token scaduti e login (`tests/collaudo/test_fase2_sincronizzazione.py`).
- Abbinamento: T-SYNC-05 (`tests/activities/test_sync.py`); il caso del file
  di un'altra attività è D-F2-03.
- Risposte ostili: T-SEC-03.
- `lint-imports`: solo `garmin` importa `garminconnect`.

## Nota di attuazione 2026-11-06

Esito dello spike con un account reale (`experiments/garmin_spike.md`):

- Il token serializzato è di 4510 byte in UTF-16, oltre il limite del Credential
  Manager: conferma che i token non stanno lì ma protetti con DPAPI in un file.
- Il login non ha richiesto l'MFA (il percorso con codice resta supportato).
- L'inizio del file FIT coincide con l'inizio dell'elenco (differenza 0 s).
