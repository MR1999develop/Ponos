# Threat model

Versione della fase 0 (2026-11-05). Da riaggiornare nella stessa modifica di
ogni funzione che tocca un confine di fiducia. Gli identificativi dei test
(`T-SEC-04`…) sono quelli della sezione 7 di `PLAN.md`.

## 1. Cosa stiamo costruendo

App desktop locale (Windows e Ubuntu) per un utente alla volta per profilo.
Scarica attività da Garmin Connect con un accesso non ufficiale, legge file
FIT, calcola il carico, mostra grafici in una pagina web locale e salva tutto
in una cartella scelta dall'utente. Non ha un server né una porta in ascolto.

### Componenti e confini di fiducia

```
                         Internet
   +-----------------------+
   | Garmin Connect        |  (B1) risposte JSON e FIT scaricati: non fidati
   +-----------+-----------+
               | HTTPS
 ==============|=========================== confine del processo dell'app
   +-----------v-----------+      +------------------------------+
   | garmin (adattatore)   |      | Import manuale / da cartella |
   +-----------+-----------+      |  (B2) file FIT: non fidati   |
               |                  +--------------+---------------+
               v                                 v
   +-------------------------------------------------------+
   | Casi d'uso  ->  Dominio puro (load, planning, injury) |
   +--+-------------+--------------+--------------+--------+
      |             |              |              |
      v             v              v              v
 +----------+  +-----------+  +-----------+  +-----------------+
 | storage  |  | platform  |  | log       |  | ui              |
 | SQLite + |  | portachia-|  | strutturato|  | Qt + grafici:   |
 | archivio |  | vi, notif.|  | (B6)      |  | QWebEngineView  |
 | FIT      |  | (B5)      |  +-----------+  | (B4) pagina web |
 +----+-----+  +-----------+                 +-----------------+
      |
 =====|============================== confine del file system
      v
 (B3) Cartella del profilo scelta dall'utente
      (database, fit/, backup/, ponos.lock)
      Può stare su rete o su disco sincronizzato.

 (B7) Pacchetto distribuito (PyInstaller, SHA256SUMS), build e CI
```

Confini:

| Id | Confine | Dati che lo attraversano |
|---|---|---|
| B1 | Garmin Connect → app | risposte JSON, FIT scaricati (zip), codici di errore, MFA |
| B2 | File FIT importati a mano o da cartella → app | file, nomi, percorsi, collegamenti |
| B3 | App ↔ cartella del profilo | database, FIT, backup, lock, permessi |
| B4 | App → pagina web dei grafici | dati dei grafici come JSON, nomi di attività |
| B5 | App ↔ portachiavi del sistema | token Garmin |
| B6 | App → log | eventi, errori |
| B7 | Sviluppo → utente | eseguibile, dipendenze, workflow di rilascio |

### Asset

| Asset | Perché conta |
|---|---|
| Credenziali e token Garmin | accesso all'account dell'utente |
| Account Garmin dell'utente | blocco (429) o segnalazione per uso anomalo; calendario modificabile |
| Dati di salute: FC, infortuni, dolore, note del medico | riservatezza |
| Integrità dei dati del profilo (FIT, database, parametri) | calcoli e piano si basano su di essi; un piano sbagliato può danneggiare |
| Disponibilità dell'app e dei dati | non perdere anni di storico |
| Integrità del pacchetto | l'utente esegue il codice distribuito |

### Assunzioni

- La macchina e l'account dell'utente non sono compromessi.
- Credential Manager (Windows) e Secret Service (Ubuntu) sono leggibili da
  ogni processo dell'utente: il token non è protetto da altre sue applicazioni.
- Garmin, Qt e le dipendenze non sono malevoli, ma le risposte e i file che
  arrivano da fuori non sono fidati.
- L'utente sceglie cartelle e file da importare; non tutti i file in una
  cartella sono suoi.
- L'attaccante conosce il codice (è pubblico).

## 2. Cosa può andare storto (STRIDE per confine)

S spoofing, T manomissione, R ripudio, I fuga di informazioni, D negazione del
servizio, E elevazione dei privilegi.

| Conf. | Minaccia | Cat. | Test |
|---|---|---|---|
| B1 | Risposta JSON enorme, annidata, con tipi sbagliati o id con `../` | T, D | T-SEC-03 |
| B1 | Nomi di attività con HTML o script arrivano alla pagina o ai log | T, E | T-SEC-03, T-SEC-06, T-SEC-08 |
| B1 | Server falso o intercettazione della rete | S, I | TLS con verifica completa (T-SEC-11 per la configurazione) |
| B1 | 429, 5xx, timeout, risposte troncate: ritentativi senza fine, account bloccato | D | T-SYNC-03 |
| B1 | Token scaduto o revocato; login fallito che salva uno stato parziale | S | T-SYNC-04 |
| B1 | FIT scaricato rovinato o ostile | T, D | T-SEC-01, T-SEC-02 |
| B2 | FIT con dimensioni dichiarate enormi, record senza fine, estensione falsa | D | T-SEC-01, T-SEC-02 |
| B2 | Symlink, junction, reparse point, nomi riservati di Windows, percorsi lunghi | T, I, E | T-SEC-04 |
| B2 | Cartella enorme (disco intero) | D | T-IMP-02, T-PERF-02 |
| B2 | Duplicati o file alterati che cambiano il passato | T | T-IMP-01, T-IMP-03, T-SYNC-05 |
| B3 | Interruzione a metà scrittura: record senza file, temporanei orfani | T, D | T-DATI-02 |
| B3 | FIT dell'archivio alterato dopo l'import | T | T-DATI-03 |
| B3 | Due istanze sullo stesso profilo; disco di rete o sincronizzato | T, D | T-DATI-04, T-SEC-09 |
| B3 | Altri utenti del PC leggono la cartella | I | T-SEC-09 |
| B3 | Iniezione SQL da nomi e note | T | T-SEC-05 |
| B3 | Database di versione vecchia o sconosciuta | T | T-DATI-01 |
| B3 | Perdita dei dati senza backup utilizzabile | D | T-DATI-05 |
| B4 | La pagina fa richieste di rete o carica immagini remote | I | T-SEC-06 |
| B4 | Script nei dati eseguito nella pagina; ponte verso il codice dell'app | E | T-SEC-06 |
| B4 | Esportazione SVG/PNG con contenuto non voluto | I | T-UI-04 |
| B5 | Token nella cartella, nella configurazione, nei log, nei temporanei | I | T-SEC-07 |
| B5 | Token non cancellato dopo il logout | I | T-SEC-07 |
| B5 | Altro processo dell'utente legge il token | I | rischio accettato (assunzioni) |
| B6 | Dati di salute, password o input non controllato nei log | I | T-SEC-08 |
| B6 | Iniezione di righe di log con caratteri di controllo | T, R | T-SEC-08 |
| B6 | Eccezione imprevista mostra dettagli all'utente | I | T-UI-08 |
| B7 | Dipendenza vulnerabile o con licenza vietata | T, E | T-SEC-11 |
| B7 | Pacchetto con percorsi locali, nome utente, file di sviluppo | I | T-SEC-12 |
| B7 | Pacchetto alterato dopo la pubblicazione | T | T-SEC-12 (hash in `SHA256SUMS`) |
| B7 | DLL caricata da una cartella non fidata (Windows) | E | T-SEC-12 |
| B7 | Workflow di CI con permessi eccessivi o input interpolato | E | T-SEC-11 |
| Calendario | Eliminazione di allenamenti non voluti su Garmin (fase 7) | T | T-SEC-10, T-UI-03 |
| Piano | Piano pericoloso per dati sbagliati o regole errate | T | T-PIANO-02, T-PIANO-03, T-PIANO-04, T-INF-01, T-INF-02, T-INF-03 |
| UI | Interfaccia bloccata da una operazione lunga | D | T-UI-02, T-SYNC-06 |

## 3. Cosa facciamo

- Input esterni (JSON, FIT, nomi di file) diventano tipi di dominio al
  confine, con limiti controllati prima di leggere o allocare; ordine:
  origine, dimensione, caratteri, sintassi, semantica. Si rifiuta, non si
  ripara.
- I nomi dei file dell'archivio li genera il programma; nessun dato esterno
  entra in un percorso.
- Import da cartella: solo file regolari dentro la cartella, tetti dichiarati
  in un solo modulo, cartella d'origine mai modificata.
- SQL sempre parametrizzato; controllo statico contro SQL composto.
- Scrittura dei FIT su temporaneo, flush, rinomina; record dopo il file;
  checksum SHA-256 verificato all'uso.
- Cartella del profilo: file creati in modo esclusivo, permessi solo per
  l'utente, lock contro due istanze, avviso per cartelle di rete o
  sincronizzate.
- Pagina dei grafici: solo contenuto locale, rete bloccata da un intercettore,
  dati come JSON codificato, nessun `QWebChannel`.
- Token nel portachiavi del sistema, legati all'id del profilo; la password va
  solo a `garminconnect` e non si salva.
- Poche richieste a Garmin, pause con jitter, backoff con tetto e numero
  massimo di tentativi.
- Log strutturati nel solo guscio, senza dati di salute, token, password né
  input non controllato.
- Gestore unico degli errori con messaggio generico.
- Dipendenze: lockfile, `pip-audit` bloccante, licenze controllate; CI con
  permessi minimi e action fissate a SHA; `SHA256SUMS` per i pacchetti.
- Piano deterministico con limiti rigidi di rampa e segnali d'allarme degli
  infortuni; nessuna diagnosi.

## 4. L'abbiamo fatto bene?

- Ogni minaccia della tabella ha almeno un test, per id. Il collaudatore, che
  non ha scritto il codice, scrive i test approfonditi.
- Per ogni test positivo c'è un test con input ostile (T-SEC-01…13).
- T-SEC-13 (fase 8): rilettura di questo documento con giudizio scritto,
  percorso dei dati non fidati seguito a mano nel codice, rischi accettati
  riconfermati.
- Ogni vulnerabilità trovata: causa radice, casi simili, un controllo automatico
  che ne impedisca il ritorno.

### Matrice minaccia → test

| Minaccia | Test |
|---|---|
| FIT ostili e fuzzing | T-SEC-01, T-SEC-02 |
| JSON ostili di Garmin | T-SEC-03 |
| Percorsi, link, nomi riservati | T-SEC-04 |
| SQL | T-SEC-05 |
| Pagina dei grafici | T-SEC-06, T-UI-04 |
| Segreti | T-SEC-07 |
| Log | T-SEC-08 |
| Cartella del profilo | T-SEC-09, T-DATI-04 |
| Calendario Garmin | T-SEC-10, T-UI-03 |
| Analisi statica e dipendenze | T-SEC-11 |
| Pacchetto | T-SEC-12 |
| Revisione finale | T-SEC-13 |
| Sincronizzazione ostile o interrotta | T-SYNC-02, T-SYNC-03, T-SYNC-04, T-SYNC-06 |
| Import da cartella | T-IMP-01, T-IMP-02, T-IMP-03 |
| Integrità e crash | T-DATI-01, T-DATI-02, T-DATI-03, T-DATI-05 |
| Risorse | T-PERF-02, T-PERF-03 |
| Errori imprevisti | T-UI-08 |

## 5. Rischi accettati

| Rischio | Motivo |
|---|---|
| API Garmin non ufficiale: può rompersi, può violare i termini d'uso, può portare al blocco dell'account | Non esiste un accesso ufficiale per i privati (ADR 002). Compensazioni: adattatore isolato, poche richieste, import FIT sempre disponibile. |
| Eseguibili non firmati | Progetto personale senza certificato di firma. Compensazione: `SHA256SUMS`, README con verifica dell'hash e avviso di SmartScreen. |
| Token leggibili da ogni processo dell'utente | Limite del Credential Manager e del Secret Service; la macchina è assunta non compromessa. |
| Dati di salute in chiaro nella cartella del profilo, protetti solo dai permessi del sistema | Semplicità e apertura con altri strumenti; cifrare richiede gestione di una password e di una chiave di recupero. Accettato dall'utente il 2026-11-05. Compensazione: permessi della cartella solo per l'utente (T-SEC-09); il README consiglia la cifratura del disco (BitLocker su Windows, LUKS su Ubuntu). |
| Nessuna cancellazione sicura dei dati | Fuori dalla portata di un'app su file system comuni. |

## Punti aperti

- Il backup contiene dati di salute: formato e protezione da decidere in
  fase 8.
