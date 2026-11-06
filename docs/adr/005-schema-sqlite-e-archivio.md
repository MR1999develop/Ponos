# 005. Schema SQLite e archivio dei file originali

- Stato: Accettato
- Data: 2026-11-05

## Contesto

La fase 1 fissa il formato che l'utente si ritrova sul disco: lo schema del
database e il modo in cui si conservano i file FIT e GPX. Dopo il rilascio
sono entrambi difficili da cambiare.

Questo ADR precisa l'ADR 003 e sostituisce due sue parti: il percorso dei
file (`fit/<anno>/<id_attività>.fit`) e l'identità, che ora vale anche per i
GPX. Il resto dell'ADR 003 resta valido.

Vincoli:

- i file originali sono la fonte di verità; il database è un indice e i dati
  dell'utente, e i valori derivati si rigenerano;
- la stessa attività non deve entrare due volte, da qualunque origine e in
  qualunque formato;
- nessun dato esterno entra nel nome di un file o in una query;
- un crash non deve lasciare un record senza file né un file a metà.

## Opzioni

### Schema del database

1. **Tabelle con colonne tipizzate, `STRICT`, vincoli `CHECK` uguali agli
   invarianti del dominio.**
   - Pro: il database rifiuta i dati impossibili anche se il codice sbaglia;
     le query per data, sport e distanza sono semplici (fase 9).
   - Contro: ogni cambio di forma richiede una migrazione; i `CHECK` vanno
     tenuti allineati agli enum del dominio.
2. **Una riga per attività con un documento JSON.**
   - Pro: nessuna migrazione per aggiungere un campo.
   - Contro: nessun vincolo né indice sui campi; i valori impossibili
     passano; le query sul contenuto sono lente e fragili.
3. **Schema generato da un ORM.**
   - Pro: meno codice a mano.
   - Contro: dipendenza in più sul formato salvato; vincoli e migrazioni non
     controllati; la forma su disco dipende dalla versione della libreria.

### Nome e posizione dei file originali

1. **`archivio/<anno>/<sha256>.<fit|gpx>`, tutto generato dal programma.**
   - Pro: nessun dato esterno nel nome; stesso contenuto, stesso nome (la
     scrittura è idempotente); l'integrità si controlla con il nome stesso;
     l'anno limita il numero di file per cartella.
   - Contro: il nome non dice nulla all'utente; l'anno è quello dell'inizio e
     un file con inizio sbagliato finisce nell'anno sbagliato.
2. **Nome dall'id dell'attività (come nell'ADR 003).**
   - Pro: nome leggibile.
   - Contro: serve l'identità prima di scrivere; l'id Garmin manca per i file
     importati; un id esterno nel nome è un dato non fidato in un percorso.
3. **Nome originale del file.**
   - Pro: riconoscibile per l'utente.
   - Contro: nomi riservati di Windows, percorsi lunghi, collisioni, `..`:
     tutti i problemi di sicurezza dei percorsi.
4. **Un'unica cartella piatta con l'hash.**
   - Pro: più semplice.
   - Contro: fino a 50.000 file in una cartella; elenco e backup più lenti.

### Identità dell'attività

1. **FIT: `file_id` del file. GPX: SHA-256 dei byte. Duplicati tra formati
   riconosciuti per inizio e durata.**
   - Pro: lo stesso FIT scaricato o copiato dall'orologio ha la stessa
     identità; il GPX, che non ha `file_id`, ha comunque un'identità stabile.
   - Contro: la stessa attività come FIT e come GPX ha due identità; il
     confronto per inizio e durata può sbagliare (due attività quasi uguali,
     una sola vera).
2. **SHA-256 per tutti.**
   - Pro: una regola sola.
   - Contro: lo stesso FIT riesportato ha un hash diverso e entra due volte.
3. **Solo inizio e durata per tutti.**
   - Pro: non dipende dal formato.
   - Contro: falsi duplicati (due sedute ravvicinate) che cancellano dati
     veri.

## Decisione

### Schema v1

- File `ponos.sqlite3`. A ogni connessione: `foreign_keys = ON`,
  `trusted_schema = OFF`, WAL, `synchronous = FULL`, attesa dei lock di 5 s.
- La versione sta solo in `PRAGMA user_version`; la v1 vale 1.
- Tabelle, tutte `STRICT`:
  - `meta(key, value)`: `profile_id`, `name`, `timezone`; le chiavi
    sconosciute si conservano.
  - `activities`: una riga per attività, con `identity_key` e
    `garmin_activity_id` unici, sport e formato in insiemi chiusi, durata
    trascorsa e in movimento in secondi (`timer_s <= elapsed_s`), distanza,
    dislivello, partenza e traccia semplificata facoltativi, percorso
    relativo, SHA-256 e dimensione del file archiviato, origine e istante
    d'import. Indice su `(start_ms, identity_key)`.
  - `physiology`: parametri validi da una data ISO (chiave primaria).
  - `load_cache`: un carico per attività, con l'impronta dei parametri;
    derivata, si cancella con l'attività e si rigenera.
- I tempi sono interi in millisecondi UTC (`start_ms`, `imported_ms`). Le
  durate misurate restano in secondi (`REAL`).
- Le colonne e i vincoli sono in `storage/sqlite_schema.py`, che è la fonte
  unica: questo ADR non li ripete.

### Migrazioni

- Una migrazione è un elenco di istruzioni, eseguito in una sola
  transazione; mai `executescript`. Una migrazione già rilasciata non si
  modifica: se ne aggiunge un'altra.
- Prima di ogni migrazione l'app copia il database in `backup/`.
- Un database di una versione più nuova di quella nota si rifiuta senza
  toccarlo. Un file senza schema o che non supera `quick_check` e
  `foreign_key_check` è segnalato come danneggiato.
- Un test confronta gli insiemi chiusi della v1 con gli enum del dominio:
  un valore nuovo obbliga a scrivere una migrazione.

### Archivio

- Percorso `archivio/<anno>/<sha256>.<fit|gpx>`; l'anno è quello dell'inizio
  in UTC. Il percorso è fatto solo di caratteri `A-Za-z0-9._-`.
- Scrittura: file temporaneo creato in modo esclusivo nella cartella
  dell'anno, `fsync`, rinomina sul nome finale, poi sincronizzazione della
  cartella dove il sistema lo permette. Il record in SQLite si scrive dopo il
  file, nella stessa transazione del controllo dei duplicati. Un crash lascia
  al più un temporaneo orfano, che si toglie all'avvio.
- Le sostituzioni durevoli di file passano da un solo modulo condiviso.
- L'archivio eredita i permessi privati della cartella del profilo.
- Salvare gli stessi byte due volte non scrive nulla. Se il file c'è ma i byte
  non corrispondono al nome, si sostituisce in modo atomico con quelli giusti
  (è il caso "riparato" dell'import).
- Ogni lettura verifica dimensione e SHA-256; un file alterato è segnalato,
  mai usato in silenzio. Nessun collegamento si segue.

### Identità e duplicati

- FIT: identità dal `file_id` (produttore, prodotto, numero di serie, ora di
  creazione UTC). GPX: SHA-256 dei byte. Chiave testuale salvata:
  `fit:<...>` oppure `gpx:<sha256>`.
- Stessa attività in due formati: possibile duplicato se gli inizi distano al
  più 60 s e le durate differiscono al più del 5% (default del progetto). Si
  tiene la prima arrivata.
- Formato riconosciuto dal contenuto e coerente con l'estensione.

## Conseguenze

- La cartella resta autosufficiente. L'ADR 003 conserva la sua validità tranne
  il percorso dei file e l'identità dei GPX.
- Il nome del file non dice nulla all'utente; per aprire un'attività serve
  l'app.
- Un database può avere vincoli più stretti del codice: un bug che scrive
  dati impossibili fallisce in modo visibile.
- Ogni cambio di schema richiede una migrazione, la copia di sicurezza e un
  test sul database della versione precedente.
- Un cambio del percorso dell'archivio o dell'identità richiede un nuovo ADR.
- Il confronto per inizio e durata può non riconoscere un duplicato fuori
  soglia: l'attività entra due volte. Accettato; l'utente non perde dati.

## Come se ne verifica il rispetto

- T-DATI-01: un database di prova per ogni versione, portato all'ultima;
  versione futura rifiutata; copia di sicurezza creata.
- T-DATI-02: interruzione in ogni punto della scrittura, con un adattatore
  del file system che fallisce all'N-esima operazione.
- T-DATI-03: file archiviato alterato o mancante, segnalato e riparato dal
  nuovo import.
- T-IMP-03 e T-GPX-02: idempotenza e duplicati tra FIT e GPX in qualunque
  ordine.
- T-SEC-04: nessun dato esterno nei nomi dell'archivio. T-SEC-05: nessun SQL
  composto. T-SEC-09: permessi privati.
- Test di contratto in `tests/contracts/` per le porte `ActivityArchive` e
  `ActivityIndex`, eseguiti sul fake e sull'adattatore reale.
- Il test sugli insiemi chiusi della v1 (sopra) e `import-linter` nella CI.
