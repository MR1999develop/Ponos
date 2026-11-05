# 003. Formato dei dati su disco

- Stato: Accettato
- Data: 2026-11-05

## Contesto

I dati dell'utente (attività, parametri, obiettivi, piani, infortuni) devono
sopravvivere a crash e spostamenti. Ogni profilo ha una cartella scelta
dall'utente. Il formato salvato è difficile da cambiare dopo il rilascio.
Le attività arrivano da Garmin, da file importati a mano e da cartelle: la
stessa attività non deve entrare due volte.

## Opzioni

1. **FIT originali come fonte di verità e SQLite come indice e dati utente,
   in una cartella per profilo.**
   - Pro: il dato originale non si perde e i valori derivati (carichi, PMC) si
     rigenerano; i file si possono aprire con altri strumenti; il database
     resta piccolo.
   - Contro: due archivi da tenere coerenti (ordine di scrittura rigoroso);
     più file nella cartella.
2. **Solo SQLite, con i FIT come BLOB.**
   - Pro: un solo file, una sola transazione.
   - Contro: database grande (anni di attività); una corruzione colpisce tutto;
     backup e verifica di integrità dei singoli FIT più scomodi.
3. **Un file JSON per attività.**
   - Pro: semplice da leggere.
   - Contro: perde il dato originale o lo duplica; nessuna query né
     transazione; consistenza fra file difficile.

## Decisione

Opzione 1. Nella cartella del profilo: `ponos.sqlite3` (WAL, chiavi esterne,
`user_version` e migrazioni), `fit/<anno>/<id_attività>.fit`, `backup/`,
`ponos.lock`. Ogni FIT ha un SHA-256 registrato. Un FIT si scrive su file
temporaneo, con flush e rinomina; il record in SQLite si scrive dopo il file.

L'identità di un'attività è il `file_id` del FIT (produttore, numero di serie,
ora di creazione): è uguale per lo stesso file scaricato da Garmin o copiato
dall'orologio. L'id Garmin si aggiunge quando è noto.

I token Garmin non stanno mai nella cartella: stanno nel portachiavi del
sistema, legati all'id del profilo. I carichi e il PMC sono derivati.

## Conseguenze

- La cartella è autosufficiente e si sposta o si apre da un altro PC.
- Contiene dati di salute in chiaro (vedi rischi accettati in
  `docs/threat-model.md`).
- Al riavvio si riconciliano archivio e database: nessun record senza file,
  nessun temporaneo orfano.
- Cambiare il formato richiede una migrazione versionata (expand-contract) e
  un nuovo ADR se la struttura della cartella cambia.
- Cartelle su disco di rete o sincronizzato sono a rischio: avviso e lock.

## Come se ne verifica il rispetto

- T-DATI-01 (migrazioni), T-DATI-02 (interruzione in ogni punto della
  scrittura), T-DATI-03 (FIT alterato), T-DATI-04 (lock), T-DATI-05 (backup e
  ripristino), T-IMP-03 (idempotenza).
- T-SEC-07: il token non compare nella cartella del profilo.
- T-SEC-04: nessun dato esterno nei nomi dei file dell'archivio.
