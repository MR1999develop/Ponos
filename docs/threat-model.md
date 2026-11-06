# Threat model

Versione della fase 3 (2026-11-06): aggiunge altezza, peso e data di nascita
come dati personali di salute, le misure datate, i pesi degli sport, le zone di
FC e i dettagli di attività. Sulla base di quella della fase 2 (2026-11-06:
login nell'app, archivio dei token, risposte e download di Garmin, log della
libreria e notifiche) e di quella della fase 1 (2026-11-05, aggiornata il 2026-11-06 con i
difetti del collaudo e il confine dei log) e della fase 0. Da
riaggiornare nella stessa modifica di ogni funzione che tocca un confine di
fiducia. Gli identificativi dei test (`T-SEC-04`…) sono quelli della sezione 7
di `PLAN.md`.

## 1. Cosa stiamo costruendo

App desktop locale (Windows e Ubuntu) per un utente alla volta per profilo.
Scarica attività da Garmin Connect con un accesso non ufficiale, legge file
FIT e GPX (anche dentro ZIP), calcola il carico, mostra grafici in una pagina
web locale e salva tutto in una cartella scelta dall'utente. Non ha un server
né una porta in ascolto.

### Componenti e confini di fiducia

```
                         Internet
   +-----------------------+
   | Garmin Connect        |  (B1) risposte JSON e FIT scaricati: non fidati
   +-----------+-----------+
               | HTTPS
 ==============|=========================== confine del processo dell'app
   +-----------v-----------+      +--------------------------------------+
   | garmin (adattatore)   |      | Import di file, cartelle e ZIP       |
   +-----------+-----------+      |  (B2) FIT, GPX, ZIP: non fidati      |
               |                  +------------------+-------------------+
               v                                     v
   +-------------------------------------------------------+
   | Casi d'uso  ->  Dominio puro (load, planning, injury) |
   +--+-------------+--------------+--------------+--------+
      |             |              |              |
      v             v              v              v
 +----------+  +-----------+  +-----------+  +-----------------+
 | storage  |  | platform  |  | log       |  | ui              |
 | SQLite + |  | portachia-|  | strutturato|  | Qt + grafici:   |
 | archivio |  | vi, notif.|  | (B6)      |  | QWebEngineView  |
 | FIT/GPX  |  | elenco dei|  +-----------+  | (B4) pagina web |
 +----+-----+  | profili   |                 +-----------------+
      |        | (B8)      |
      |        +-----+-----+
 =====|==============|=============== confine del file system
      v              v
 (B3) Cartella del profilo      (B8) Cartella di configurazione
      scelta dall'utente:            dell'app: profiles.json
      ponos.sqlite3 (+ -wal/-shm),   e profiles.json.lock
      archivio/, backup/,
      ponos.lock
      Può stare su rete o su disco sincronizzato.

 (B7) Pacchetto distribuito (PyInstaller, SHA256SUMS), build e CI
```

Confini:

| Id | Confine | Dati che lo attraversano |
|---|---|---|
| B1 | Garmin Connect → app | risposte JSON, FIT scaricati (zip), codici di errore, intestazione `Retry-After`, richiesta del codice MFA |
| B2 | File FIT, GPX e ZIP importati a mano, trascinati o da una cartella → app | file, nomi, percorsi, collegamenti, voci di archivio |
| B3 | App ↔ cartella del profilo | database, archivio, backup, lock, permessi |
| B4 | App → pagina web dei grafici | dati dei grafici come JSON, nomi di attività |
| B5 | App ↔ archivio dei token (Windows: file cifrato con DPAPI in `secrets/` nella cartella dati locale dell'app; Ubuntu: Secret Service) | token Garmin, per id del profilo |
| B6 | App → file di log nella cartella dei log dell'utente (`ponos-coach.log`) | eventi, tipi delle eccezioni |
| B7 | Sviluppo → utente | eseguibile, dipendenze, workflow di rilascio |
| B8 | App ↔ cartella di configurazione | elenco dei profili (id, cartella, nome, ultimo uso), lock dell'elenco |
| B9 | Utente → dialogo di login → libreria `garminconnect` | email, password, codice MFA (solo in memoria, nel thread del login) |
| B10 | App → notifiche del sistema | titolo fisso e numero di attività nuove |

### Asset

| Asset | Perché conta |
|---|---|
| Credenziali e token Garmin | accesso all'account dell'utente; la password e il codice MFA vivono solo nel thread del login, i token nel loro archivio |
| Account Garmin dell'utente | blocco (429) o segnalazione per uso anomalo; calendario modificabile |
| Dati di salute: FC, infortuni, dolore, note del medico | riservatezza |
| Altezza, peso e data di nascita (con lo storico del peso) | dati personali di salute: stanno solo nel database del profilo; mai in log, messaggi o catene d'errore; in chiaro come gli altri dati del profilo (rischio accettato, sezione 5) |
| Posizioni GPS delle attività | rivelano casa e abitudini |
| Integrità dei dati del profilo (file originali, database, parametri) | calcoli e piano si basano su di essi; un piano sbagliato può danneggiare |
| Disponibilità dell'app e dei dati | non perdere anni di storico |
| Elenco dei profili | contiene percorsi e nomi; se si perde, i profili non si trovano |
| Dati fuori dal profilo (altre cartelle dell'utente) | la cancellazione di un profilo non deve toccarli |
| Integrità del pacchetto | l'utente esegue il codice distribuito |

### Assunzioni

- La macchina e l'account dell'utente non sono compromessi.
- DPAPI (Windows, ambito utente) e Secret Service (Ubuntu) rendono il token
  leggibile da ogni processo dell'utente: non è protetto da altre sue
  applicazioni. Il Credential Manager non si usa (limite di 2560 byte, ADR 006).
- Garmin, Qt e le dipendenze non sono malevoli, ma le risposte e i file che
  arrivano da fuori non sono fidati.
- L'utente sceglie cartelle e file da importare; non tutti i file in una
  cartella sono suoi.
- Un lock del sistema operativo su un disco locale funziona; su un disco di
  rete o sincronizzato non è garantito (avviso all'utente).
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
| B1 | `Retry-After` ostile (negativo, NaN, enorme, cifre non ASCII) che fa sollevare il caso d'uso o dormire a lungo | D | T-SYNC-03, D-F2-01, D-F2-02 |
| B1 | Token scaduto o revocato; login fallito che salva uno stato parziale | S | T-SYNC-04 |
| B1 | FIT scaricato rovinato o ostile | T, D | T-SEC-01, T-SEC-02 |
| B1 | ZIP scaricato ostile (bomba, nessuna voce, due file, nomi con `../`) | T, D | T-SEC-02, T-SEC-03, T-SYNC-03 |
| B1 | Download che è un'altra attività rispetto all'id elencato, registrato sotto l'id sbagliato | T | T-SYNC-05, D-F2-03 |
| B1 | Elenco che scivola durante la prima sincronizzazione e fa perdere attività vecchie | T | T-SYNC-01, D-F2-04 |
| B1 | Risposta o download enormi letti per intero in memoria dalla libreria prima del tetto | D | rischio accettato (sezione 5) |
| B1 | Elenco senza fine (oltre 1.000.000 di voci) | D | nessun test: punto aperto |
| B1 | Il testo di un errore della libreria (con id o frammenti di risposta) arriva ai log o all'utente | I | T-SEC-08 |
| B1 | Una richiesta bloccata non si può annullare: sincronizzazione che non si ferma | D | T-SYNC-06; timeout non interrompibili: rischio accettato |
| B9 | Password o codice MFA salvati, registrati o mostrati (`repr`, log, eccezioni) | I | T-SEC-07, T-SEC-08 |
| B9 | Login annullato o fallito che lascia token parziali; attesa del codice senza fine | T, D | T-SEC-07, T-SYNC-04 |
| B9 | Codice MFA vuoto o enorme inviato a Garmin; tentativi di codice illimitati | D | T-SYNC-04 (`tests/garmin/test_account.py`) |
| B9 | Email o password vuote o enormi inviate alla libreria | D | T-SYNC-04 (`tests/garmin/test_account.py`) |
| B2 | FIT con dimensioni dichiarate enormi, record senza fine, estensione falsa | D | T-SEC-01, T-SEC-02 |
| B2 | FIT o GPX con inizio o fine assurdi (fuori da 1990–2099 UTC) che sporcano archivio, tabella e calcoli | T | T-SEC-02, T-GPX-01 |
| B2 | GPX con DTD o entità (anche esterne), espansione esponenziale (XXE, billion laughs) | I, D | T-SEC-02, T-GPX-01 |
| B2 | GPX con annidamento profondo, milioni di punti o testi enormi | D | T-SEC-02, T-GPX-01 |
| B2 | GPX con estensione `.gpx` e contenuto di altro tipo (FIT, TCX) o il contrario | T | T-SEC-02, T-GPX-01 |
| B2 | ZIP: bomba di decompressione, rapporto oltre 100:1, troppe voci, dimensioni dichiarate false | D | T-SEC-02, T-IMP-04 |
| B2 | ZIP: nomi con `../`, percorsi assoluti o nomi riservati scritti fuori dall'archivio | T, E | T-SEC-02, T-SEC-04, T-IMP-04 |
| B2 | ZIP annidati, voci cifrate, CRC sbagliato | D, T | T-SEC-02, T-IMP-04 |
| B2 | Symlink, junction, reparse point, nomi riservati di Windows, percorsi lunghi nella scansione di una cartella | T, I, E | T-SEC-04 |
| B2 | File sostituito da un collegamento tra l'esame e la lettura | T, I | T-SEC-04 |
| B2 | Cartella enorme (disco intero) | D | T-IMP-02, T-PERF-02 |
| B2 | Duplicati o file alterati che cambiano il passato; stessa attività in FIT e GPX | T | T-IMP-01, T-IMP-03, T-GPX-02, T-SYNC-05 |
| B2 | Errore imprevisto su un file che ferma l'import o lascia metà dei file | D | T-IMP-01, T-UI-08 |
| B3 | Interruzione a metà scrittura: record senza file, temporanei orfani | T, D | T-DATI-02 |
| B3 | File dell'archivio alterato o mancante dopo l'import | T | T-DATI-03 |
| B3 | Due istanze sullo stesso profilo; disco di rete o sincronizzato | T, D | T-DATI-04, T-SEC-09 |
| B3 | File di lock sostituito da un collegamento | T, E | T-DATI-04, T-SEC-04 |
| B3 | Altri utenti del PC leggono la cartella, l'archivio o le copie in `backup/` | I | T-SEC-09 |
| B3 | Cartella del profilo creata con permessi larghi (DACL ereditata, file system che ignora i modi) | I | T-SEC-09 |
| B3 | Iniezione SQL da nomi e note | T | T-SEC-05 |
| B3 | Database di versione vecchia o sconosciuta; migrazione che lo rovina | T | T-DATI-01 |
| B3 | Migrazione v2 → v3 interrotta a metà o fatta da due istanze insieme | T, D | T-DATI-01 |
| B3 | Altezza o data di nascita modificate o cancellate dopo la creazione (bug o SQL diretto) | T | T-DATI-01 (trigger), T-QUAL-02 |
| B3 | Peso, altezza o data di nascita fuori intervallo, con cifre non ASCII, NaN, esponenti o testo enorme (input dell'utente o file del profilo alterato) | T, D | T-QUAL-02, T-DATI-01 |
| B3 | Misura con giorno di validità nel futuro, prima del 1900-01-01 o che porta FC incoerenti | T | T-QUAL-02 |
| B3 | Peso di uno sport NaN, infinito o fuori da 0-5 che azzera o gonfia il carico | T | T-QUAL-02, D-F3-02 |
| B3 | Carico di una versione precedente mescolato ai nuovi dopo un cambio di parametri, pesi, zone o modello | T | T-CALC-05, T-CALC-01 |
| B3 | Altezza, peso, data di nascita o FC in log, messaggi, catene d'errore | I | T-SEC-08 |
| B3 | Database di versione futura riscritto da una versione vecchia | T | T-DATI-01 |
| B3 | Cancellazione della cartella di un profilo che porta fuori (link, giunzione) o cancella file dell'utente non creati dall'app | T, E | T-SEC-04, T-UI-03 |
| B3 | Cancellazione della cartella di un profilo aperto altrove | T, D | T-DATI-04 |
| B3 | Creazione di un profilo interrotta: cartelle e file a metà, o cancellazione di cartelle che c'erano già | T, D | T-DATI-06 |
| B3 | Perdita dei dati senza backup utilizzabile | D | T-DATI-05 |
| B4 | La pagina fa richieste di rete o carica immagini remote | I | T-SEC-06 |
| B4 | Script nei dati eseguito nella pagina; ponte verso il codice dell'app | E | T-SEC-06 |
| B4 | Esportazione SVG/PNG con contenuto non voluto | I | T-UI-04 |
| B4 | Grafici della fase 3 (carico settimanale, zone, FC dell'attività) con nomi o testi non controllati nella pagina | T, E | T-SEC-06, T-UI-04 |
| B3 | File dell'archivio sparito o alterato mentre il dettaglio è aperto o mentre si calcola | T | T-DATI-03, scenari di `tests/collaudo/test_fase3_scenari.py` |
| B3 | Pesi cambiati durante una sincronizzazione: carichi che non corrispondono al calcolo da zero | T | T-CALC-05, `tests/collaudo/test_fase3_scenari.py` |
| B5 | Token nella cartella, nella configurazione, nei log, nei temporanei, nei backup | I | T-SEC-07 |
| B5 | Token non cancellato dopo il logout | I | T-SEC-07 |
| B5 | Altro processo dell'utente legge il token | I | rischio accettato (assunzioni) |
| B5 | Il file di un profilo copiato in un altro si decifra o i token di un profilo si usano per un altro | S, I | T-SYNC-04 |
| B5 | File dei token alterato, troncato, enorme o di formato sconosciuto | T, D | T-SYNC-04 |
| B5 | Ripiego silenzioso su un file in chiaro quando manca il Secret Service | I | T-SEC-07 (contratto `TokenStore`) |
| B5 | Token che non sono un oggetto JSON letti dalla libreria come percorso di un file | T, E | T-SYNC-04 |
| B10 | Dati personali nel testo della notifica; notifica che blocca la UI o fa cadere la sincronizzazione | I, D | T-SEC-08, `tests/platform/test_notifier.py` |
| B6 | Dati di salute, password o input non controllato nei log | I | T-SEC-08 |
| B6 | Percorsi, nomi di file o posizioni GPS nei log | I | T-SEC-08 |
| B6 | Iniezione di righe di log con caratteri di controllo | T, R | T-SEC-08 |
| B6 | Testo di un'eccezione (con percorsi o nomi) scritto nel log | I | T-SEC-08 |
| B6 | Log che cresce senza fine nella cartella dell'utente | D | T-SEC-08 (rotazione) |
| B6 | Eccezione non gestita in un thread: nessuna traccia o dettagli all'utente | I, R | T-SEC-08, T-UI-08 |
| B6 | Eccezione imprevista mostra dettagli o percorsi all'utente | I | T-UI-08 |
| B7 | Dipendenza vulnerabile o con licenza vietata | T, E | T-SEC-11 |
| B7 | Pacchetto con percorsi locali, nome utente, file di sviluppo | I | T-SEC-12 |
| B7 | Pacchetto alterato dopo la pubblicazione | T | T-SEC-12 (hash in `SHA256SUMS`) |
| B7 | DLL caricata da una cartella non fidata (Windows) | E | T-SEC-12 |
| B7 | Workflow di CI con permessi eccessivi o input interpolato | E | T-SEC-11 |
| B8 | Elenco dei profili illeggibile, troppo grande, corrotto o di una versione futura, sovrascritto | T, D | T-DATI-01 |
| B8 | Due istanze che aggiornano l'elenco insieme e si perdono le modifiche | T | T-DATI-04 |
| B8 | Voce dell'elenco che punta a una cartella con un altro profilo o senza profilo | S, T | T-DATI-04, T-UI-01 |
| B8 | Percorsi e nomi dei profili (dati personali) nei log | I | T-SEC-08 |
| Calendario | Eliminazione di allenamenti non voluti su Garmin (fase 7) | T | T-SEC-10, T-UI-03 |
| Piano | Piano pericoloso per dati sbagliati o regole errate | T | T-PIANO-02, T-PIANO-03, T-PIANO-04, T-INF-01, T-INF-02, T-INF-03 |
| UI | Interfaccia bloccata da una operazione lunga | D | T-UI-02, T-SYNC-06 |

## 3. Cosa facciamo

- Input esterni (JSON, FIT, GPX, ZIP, nomi di file) diventano tipi di dominio
  al confine, con limiti controllati prima di leggere o allocare; ordine:
  origine, dimensione, caratteri, sintassi, semantica. Si rifiuta, non si
  ripara. Un solo punto decodifica FIT e GPX, con lo stesso errore.
- Il formato si riconosce dal contenuto e deve coincidere con l'estensione.
  Un inizio o una fine (inizio più tempo trascorso) fuori da
  [1990-01-01, 2100-01-01) UTC rendono il file danneggiato.
- GPX: XML letto a eventi con `defusedxml`, senza DTD, entità né risorse
  esterne; limiti di 50 MB, profondità 32, 1.000.000 di punti e 4096
  caratteri per testo, controllati prima e durante la lettura.
- ZIP: directory centrale letta con un tetto, numero di voci, somma delle
  dimensioni e rapporto di compressione controllati prima di estrarre;
  estrazione in memoria a blocchi con la dimensione reale ricontrollata e il
  CRC verificato; i nomi delle voci non diventano mai percorsi; ZIP annidati e
  voci cifrate rifiutati; nomi duplicati (anche solo per maiuscole) rifiutati.
- Scansione di una cartella: solo file regolari dentro la cartella scelta;
  collegamenti, giunzioni, reparse point e file speciali saltati e segnalati;
  la catena di cartelle si ricontrolla alla lettura e il tipo del file sul
  handle aperto; nomi riservati di Windows e percorsi lunghi segnalati. Tetti
  dichiarati in un solo modulo; cartella d'origine mai modificata.
- Un errore imprevisto su un file è un esito fallito di quel file; l'import
  continua e il messaggio non contiene l'eccezione.
- I nomi dei file dell'archivio (`archivio/<anno>/<sha256>.<fit|gpx>`) li
  genera il programma; nessun dato esterno entra in un percorso. Formato in
  ADR 005.
- SQL sempre parametrizzato; controllo statico contro SQL composto. Vincoli
  `CHECK`, chiavi esterne e `STRICT` nel database.
- Scrittura dei file su temporaneo creato in modo esclusivo, `fsync`,
  rinomina durevole, tutto in un solo modulo condiviso; record dopo il file;
  SHA-256 e dimensione verificati a ogni lettura. Un file alterato o mancante
  è segnalato e si ripara reimportando l'originale.
- Database: `quick_check` e `foreign_key_check` all'apertura; versione futura
  rifiutata senza toccarlo; un file senza le tabelle di un profilo è
  danneggiato anche se il suo `user_version` coincide; copia di sicurezza in
  `backup/ponos-v{da}-v{a}.sqlite3` prima di ogni migrazione (senza copia non
  si migra); l'anteprima di un profilo (conferma di eliminazione) lo apre in
  sola lettura, senza migrare né creare file accanto.
- Cartella del profilo: creata privata (Windows: DACL protetta con solo
  l'utente e SYSTEM; Ubuntu: 0700, verificato dopo la creazione, errore se il
  file system lo ignora), file creati in modo esclusivo, `archivio/` e
  `backup/` con gli stessi permessi, nessun collegamento seguito.
- Lock di un profilo: lock del sistema operativo su `ponos.lock`, tenuto per
  tutta la vita del profilo; si libera da solo se il processo muore; il file
  non può essere un collegamento. Il lock si prende prima di aprire il
  database.
- Elenco dei profili (`profiles.json` nella cartella di configurazione):
  formato versionato, tetto di 1 MiB, campi sconosciuti conservati, scrittura
  atomica sotto un lock del sistema con attesa limitata; se è illeggibile,
  corrotto o di una versione futura è un errore e non si sovrascrive.
- Cancellazione di un profilo: rifiutata per il profilo aperto (il lock lo
  prende l'operazione stessa); limitata ai nomi che il profilo possiede
  (database e file `-wal`, `-shm` e `-journal`, `archivio/`, `backup/`, lock);
  rifiutata se c'è altro o se
  nell'albero c'è un collegamento o una giunzione; conferma con attrito che
  mostra cartella e numero di attività.
- Creazione di un profilo: se un passo fallisce, il rollback annulla solo ciò
  che la creazione ha fatto; cartelle e file preesistenti non si toccano, né
  il profilo di un'altra istanza che ha vinto la gara sulla stessa cartella
  (T-DATI-06).
- Pagina dei grafici: solo contenuto locale, rete bloccata da un intercettore,
  dati come JSON codificato, nessun `QWebChannel`.
- Login Garmin nell'app (eccezione scritta in `CLAUDE.md`, ADR 002). La
  password e il codice MFA passano solo a `garminconnect`, non si salvano e non
  compaiono in `repr`, messaggi o log; email e password vuote o enormi sono
  rifiutate prima di contattare Garmin; il codice MFA vuoto o enorme non si
  invia, i tentativi di codice sono al più 3 e l'attesa del codice ha un tetto
  di 5 minuti. Un login annullato o fallito non salva nulla.
- Token (ADR 006): su Windows un file per profilo cifrato con DPAPI (ambito
  utente) in una cartella privata fuori dal profilo, con entropia legata
  all'app e all'id del profilo, formato versionato e lettura con un tetto di
  dimensione; su Ubuntu il Secret Service, e nessun ripiego in chiaro se manca.
  Mai nella cartella del profilo né nel backup. Un file illeggibile o alterato
  chiede un nuovo login. Il logout cancella i token. Alla ripresa, token che non
  sono un oggetto JSON non arrivano alla libreria (potrebbe leggerli come un
  percorso). I token rinnovati dalla libreria si risalvano.
- Risposte di Garmin (B1): l'elenco diventa tipi di dominio al confine (id
  numerici semplici, orari e durate plausibili, chiave dello sport semplice);
  una voce malformata si scarta, una pagina oltre il tetto o non interpretabile
  è un errore definitivo; nessun testo libero di Garmin (nomi, descrizioni)
  entra nel dominio. Il totale dichiarato serve solo all'avanzamento.
- Download di Garmin: tetto di 50 MB sullo ZIP; l'originale si estrae con i
  limiti dell'adattatore ZIP e passa dagli stessi controlli dei file importati;
  deve coincidere con la voce elencata (inizio, durata) o la voce fallisce e
  non si registra. L'archivio si scrive prima della transazione del record.
- Poche richieste a Garmin, pause con jitter, backoff con tetto e numero
  massimo di tentativi; la libreria ritenta zero volte. `Retry-After` si legge
  solo se è un numero con cifre ASCII; un valore illeggibile è ignorato, uno
  implausibile ferma la sincronizzazione. Dopo 3 voci di fila con Garmin
  irraggiungibile ci si ferma senza consumare i tentativi delle voci.
- Log della libreria: `garminconnect` a livello critico (a livello errore
  registra il percorso della richiesta, con l'id dell'attività, e il testo
  degli errori); `requests`, `urllib3`, `curl_cffi` ad avviso. Nei nostri
  log degli errori della sincronizzazione solo il tipo.
- Notifiche: titolo fisso e numero di attività nuove, mai nomi né dati; partono
  in un thread proprio, una per volta, e se falliscono resta un record col solo
  tipo.
- Sincronizzazione su un thread con una connessione propria; annullamento
  cooperativo controllato prima di ogni richiesta, voce e pagina, con pause
  interrompibili.
- Log nel solo guscio (`ui/diagnostics.py`, configurato dal Composition
  Root). Mai dati di salute, token, password, percorsi, nomi di file o
  posizioni GPS. I messaggi per l'utente nascono in un solo punto e non
  riportano il testo delle eccezioni. Il dettaglio di un file rifiutato è un
  motivo enumerato, non il suo contenuto.
  - Confine dei log: un file con rotazione (1 MB, tre copie) nella cartella dei
    log dell'utente (`platformdirs`); se la cartella non si può usare l'app
    parte e i log vanno su stderr.
  - Un solo formatter produce il testo: di un'eccezione scrive la catena dei
    tipi (`A <- B`), mai il testo né il traceback, e sostituisce i caratteri di
    controllo con la forma scritta, così un nome ostile non fabbrica righe
    false. Orari in UTC.
  - Gestore di ultima istanza per il thread principale e per gli altri thread:
    registra solo i tipi, avvisa l'utente con un messaggio generico e non
    ferma mai l'app, nemmeno se l'avviso fallisce. Ctrl+C passa.
  - Test: `tests/ui/test_diagnostics.py`, `tests/collaudo/test_interfaccia.py`
    (T-SEC-08, D-F1-05), `tests/ui/test_window_lifecycle.py`.
  - Dal menu Aiuto l'utente apre la cartella dei log (Ctrl+Maiusc+L). Il
    file è leggibile da ogni processo dell'utente: per questo non contiene
    dati personali.
- Gestore unico degli errori con messaggio generico.
- Dipendenze: lockfile, `pip-audit` bloccante, licenze controllate; CI con
  permessi minimi e action fissate a SHA; `SHA256SUMS` per i pacchetti.
- Dati personali del corpo (fase 3, ADR 007): altezza, peso e data di nascita
  diventano tipi di dominio validi per costruzione (`Height`, `Weight`,
  `BirthDate`) e le loro eccezioni non ripetono il valore rifiutato. Il peso è
  un intero in decimi di chilo; il testo si legge con una grammatica stretta
  (lunghezza prima, solo cifre ASCII, un separatore decimale; esponenti,
  `NaN`, `_` e cifre a larghezza piena rifiutati, mai reinterpretati). Il
  database ripete i limiti con `CHECK`; altezza e data di nascita hanno un
  trigger che rifiuta `UPDATE` e `DELETE`. Il giorno di validità di una misura
  non è nel futuro né prima del 1900-01-01; il vincolo tra le FC si verifica
  sul risultato. Nessuno di questi valori entra in un log, in un messaggio o
  nella catena di un errore (T-SEC-08); l'interfaccia mostra i problemi con
  categorie enumerate, non con il valore.
- Misure, pesi degli sport e dati fissi si scrivono in una sola transazione
  (o tutto o niente); la migrazione v3 passa dalla copia di sicurezza e si
  prova interrotta a ogni istruzione (T-DATI-01). La cache dei carichi è
  derivata: la migrazione la svuota e l'impronta comprende parametri, peso
  dello sport, percentuali delle zone e versione del modello, così nessun
  carico vecchio resta mescolato ai nuovi (T-CALC-05).
- Zone e dettaglio: i secondi in zona vengono dagli stessi campioni del carico
  (scarti e buchi compresi); il dettaglio rilegge l'originale dall'archivio con
  gli stessi controlli di dimensione e SHA-256, e un file mancante o alterato è
  un esito con il motivo, mai usato in silenzio. Le figure sono costruite da
  funzioni pure e passano dalla stessa pagina locale e senza rete dei grafici
  precedenti.
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
| GPX ostili (XXE, espansione, profondità, punti) | T-SEC-02, T-GPX-01 |
| ZIP ostili (bomba, voci, nomi, cifrati, annidati) | T-SEC-02, T-IMP-04 |
| Orari fuori da 1990–2099 | T-SEC-02, T-GPX-01 |
| JSON, ZIP e `Retry-After` ostili di Garmin | T-SEC-03, T-SYNC-03 |
| Percorsi, link, reparse point, nomi riservati, scansione di cartelle | T-SEC-04 |
| Cancellazione della cartella di un profilo | T-SEC-04, T-DATI-04, T-UI-03 |
| SQL | T-SEC-05 |
| Pagina dei grafici | T-SEC-06, T-UI-04 |
| Segreti (password, codice MFA, token; login, sincronizzazione, kill, logout) | T-SEC-07 |
| Log (salute, percorsi, nomi, posizioni, controlli, rotazione, gestore di ultima istanza, sincronizzazione intera) | T-SEC-08 |
| Creazione di un profilo interrotta o in gara | T-DATI-06 |
| Cartella del profilo, permessi e archivio | T-SEC-09 |
| Lock del profilo e dell'elenco | T-DATI-04 |
| Elenco dei profili e formati versionati | T-DATI-01 |
| Calendario Garmin | T-SEC-10, T-UI-03 |
| Analisi statica e dipendenze | T-SEC-11 |
| Pacchetto | T-SEC-12 |
| Revisione finale | T-SEC-13 |
| Sincronizzazione ostile o interrotta | T-SYNC-02, T-SYNC-03, T-SYNC-04, T-SYNC-06 |
| Elenco completo e incrementale, elenco che scivola | T-SYNC-01, D-F2-04 |
| Abbinamento con gli import, download di un'altra attività | T-SYNC-05, D-F2-03 |
| Token, login e MFA | T-SYNC-04, T-SEC-07 |
| Notifiche | T-SEC-08 (`tests/platform/test_notifier.py`) |
| Import di file, cartelle e ZIP | T-IMP-01, T-IMP-02, T-IMP-03, T-IMP-04 |
| Duplicati tra FIT e GPX | T-GPX-02 |
| Integrità e crash | T-DATI-01, T-DATI-02, T-DATI-03, T-DATI-05 |
| Risorse | T-PERF-02, T-PERF-03 |
| Errori imprevisti | T-UI-08, T-IMP-01 |
| Dati personali del corpo: limiti, grammatica del peso, dati fissi immutabili | T-QUAL-02, T-DATI-01 |
| Migrazione v3 interrotta o concorrente | T-DATI-01 |
| Altezza, peso, data di nascita e FC nei log | T-SEC-08 (`tests/collaudo/test_fase3_log_salute.py`) |
| Carico coerente dopo cambi di parametri, pesi e zone | T-CALC-01…07 |
| Grafici e finestre della fase 3 (contrasto, tastiera, nomi accessibili, lingue) | T-UI-04, T-UI-05, T-UI-06, T-UI-07 |
| Budget con 5.000 attività | T-PERF-01 |

### Casi chiusi dal collaudo della fase 1 (D-F1)

Difetti trovati dal collaudo della fase 1 (D-F1-01…10, nei test di
`tests/collaudo/`) e comportamento che li chiude. Ogni riga ha un test; un
difetto non si considera chiuso senza. I test dei difetti hanno l'id nel nome
(`test_d_f1_NN_…`).

| Id | Difetto | Comportamento | Test (piano) | Test (file) |
|---|---|---|---|---|
| D-F1-01 | Un database di un altro programma con `user_version` 1 faceva uscire un errore grezzo di SQLite invece di "profilo danneggiato" | Si controllano le tabelle dello schema: è un profilo danneggiato e il file non si tocca | T-DATI-01 | `tests/collaudo/test_database_del_profilo.py` |
| D-F1-02 | Temporanei orfani di una scrittura interrotta restavano nell'archivio | All'apertura del profilo si tolgono; nel log solo il numero | T-DATI-02 | `tests/collaudo/test_interfaccia.py`, `tests/ui/test_window_lifecycle.py` |
| D-F1-03 | File d'archivio mancante o alterato di un'attività già presente: nessun modo di rimetterlo a posto | Esito "riparata": reimportando l'originale il file si sostituisce in modo atomico | T-DATI-03, T-IMP-03 | `tests/collaudo/test_integrita_e_cartella.py`, `tests/activities/test_import_use_case.py` |
| D-F1-04 | Un GPX con data dell'anno 1 faceva coprire secoli alla serie del carico | Orario d'inizio fuori da [1990-01-01, 2100-01-01) UTC: file rifiutato come danneggiato | T-SEC-02, T-GPX-01 | `tests/collaudo/test_date_ostili.py`, `tests/activities/test_instants.py` |
| D-F1-05 | Nel log finivano nomi di cartelle dell'utente e un nome con a-capo poteva fingere una riga | Il formatter scrive solo i tipi delle eccezioni e neutralizza i caratteri di controllo | T-SEC-08 | `tests/collaudo/test_interfaccia.py`, `tests/ui/test_diagnostics.py` |
| D-F1-06 | Permessi dell'archivio e dei file originali non garantiti come privati | Ereditano i permessi privati della cartella del profilo | T-SEC-09 | `tests/collaudo/test_sicurezza_input.py` |
| D-F1-07 | Chi perdeva la gara nella creazione di un profilo cancellava il database del vincitore | Il rollback annulla solo ciò che la creazione ha fatto | T-DATI-06 | `tests/collaudo/test_due_istanze.py`, `tests/profiles/test_use_cases.py` |
| D-F1-08 | La sezione aperta non si ricordava al riavvio | Si scrive a ogni cambio; un valore non valido apre la Panoramica | T-UI-07 | `tests/collaudo/test_interfaccia.py`, `tests/ui/test_window_lifecycle.py` |
| D-F1-09 | Una geometria salvata di tipo sbagliato bloccava l'apertura della finestra | Finestra di dimensioni predefinite | T-UI-07 | `tests/collaudo/test_interfaccia.py`, `tests/ui/test_window_lifecycle.py` |
| D-F1-10 | Il primo disegno del grafico teneva fermo il ciclo degli eventi oltre 100 ms | Figura e JSON si costruiscono nel thread dei dati; il motore web parte dopo il primo `show()` | T-UI-02 | `tests/collaudo/test_prestazioni.py`, `tests/ui/test_window_lifecycle.py` |

### Casi chiusi dal collaudo della fase 2 (D-F2)

Difetti trovati dalla revisione e dal collaudo della sincronizzazione
(D-F2-01…04, citati nei test) e comportamento che li chiude. I test dei
difetti hanno l'id nel nome (`test_d_f2_NN_…`) o in un commento.

| Id | Difetto | Comportamento | Test (piano) | Test (file) |
|---|---|---|---|---|
| D-F2-01 | `Retry-After` con una cifra che `float()` non legge (per esempio un apice) faceva sollevare l'adattatore | Si accettano solo cifre ASCII, intere o decimali; altrimenti nessuna attesa suggerita e vale il solo backoff | T-SYNC-03 | `tests/collaudo/test_fase2_garmin_ostile.py`, `tests/garmin/test_errors.py` |
| D-F2-02 | Un'attesa suggerita negativa, NaN o infinita faceva sollevare il caso d'uso invece di dare un esito | `RateLimitedError` porta un valore implausibile; la sincronizzazione si ferma con "limite di frequenza", senza dormire | T-SYNC-03 | `tests/collaudo/test_fase2_garmin_ostile.py` |
| D-F2-03 | Un download che era un'altra attività rispetto all'id elencato si registrava sotto quell'id | Il file deve coincidere con la voce (inizio e durata, regola dei duplicati); se no la voce fallisce in modo definitivo (`REMOTE_REJECTED`) e non si registra nulla | T-SYNC-05 | `tests/collaudo/test_fase2_sincronizzazione.py`, `tests/activities/test_sync_errors.py` |
| D-F2-04 | Se Garmin riceveva un'attività nuova durante la prima sincronizzazione, la più vecchia scivolava oltre il totale dichiarato e non si elencava | L'elenco finisce con una pagina corta o vuota, mai al totale dichiarato; il totale serve solo all'avanzamento; la verifica completa recupera ciò che fosse sfuggito | T-SYNC-01 | `tests/collaudo/test_fase2_sincronizzazione.py`, `tests/activities/test_sync_volume.py`, `tests/activities/test_sync_domain.py` |

### Casi chiusi dal collaudo della fase 3 (D-F3)

Difetti trovati dal collaudo della fase 3 (D-F3-01…11) e comportamento che
li chiude. Tutti chiusi il 2026-11-06: i loro test, nati come previsti-fallito
(`xfail` stretto), passano senza marcatura.

| Id | Difetto | Comportamento | Test (piano) | Test (file) |
|---|---|---|---|---|
| D-F3-01 | `Weight.parse` accettava "7_0.5", cifre non ASCII o a larghezza piena ed esponenti | Solo cifre ASCII con un separatore decimale | T-QUAL-02 | `tests/collaudo/test_fase3_dati_personali.py`, `tests/profiles/test_body_types.py` |
| D-F3-02 | `SportWeightsForm.set_value(nan)` faceva sollevare `build()` | Un NaN o un infinito non cambia il valore in vigore; `build()` dà sempre pesi tra 0 e 5 | T-QUAL-02 | `tests/collaudo/test_fase3_dati_personali.py` |
| D-F3-03 | Le tre caselle di FC di "Modifica profilo" non avevano nome accessibile (l'etichetta era legata al contenitore, non alla casella) | Ogni casella ha il suo nome accessibile | T-UI-05 | `tests/collaudo/test_fase3_interfaccia.py` |
| D-F3-04 | In "Nuovo profilo" nome, cartella e caselle di FC senza nome accessibile (stessa causa di D-F3-03) | Ogni controllo ha il suo nome accessibile | T-UI-05 | `tests/collaudo/test_fase3_interfaccia.py` |
| D-F3-05 | In "Modifica profilo", per un profilo senza dati fissi, il Tab partiva dalle misure e visitava altezza e data di nascita, in cima al dialogo, per ultime | L'ordine del Tab segue quello visivo, dall'alto | T-UI-05 | `tests/collaudo/test_fase3_interfaccia.py` |
| D-F3-06 | Nomi delle zone nel grafico della FC sotto 4,5:1 sulle bande di zona | Contrasto del testo almeno 4,5:1 su ogni banda, in entrambi i temi | T-UI-05 | `tests/collaudo/test_fase3_grafici.py` |
| D-F3-07 | Le barre del carico giornaliero (opacità 0,6) rendevano sotto 3:1 sul fondo | Barre almeno 3:1 sul fondo, come disegnate | T-UI-05 | `tests/collaudo/test_fase3_grafici.py` |
| D-F3-08 | Le croci dei campioni scartati sotto 3:1 sulle bande di zona del tema scuro | Croci almeno 3:1 su ogni banda | T-UI-05 | `tests/collaudo/test_fase3_grafici.py` |
| D-F3-09 | CTL e ATL differivano solo per il colore (stessa linea, stesso spessore) | Le linee si distinguono anche senza colore | T-UI-05 | `tests/collaudo/test_fase3_grafici.py` |
| D-F3-10 | In inglese i plurali restavano "activity(ies)" e "sample(s)": la forma plurale non esiste per la lingua sorgente | I plurali inglesi hanno la forma vera per il numero, senza desinenza opzionale | T-UI-06 | `tests/collaudo/test_fase3_interfaccia.py` |
| D-F3-11 | Nella finestra "Dettaglio attività" le etichette della pagina di messaggio (caricamento, file mancante, file alterato) restavano più basse del testo: righe tagliate | Le etichette mostrano ogni riga del loro testo | T-UI-06 | `tests/collaudo/test_fase3_interfaccia.py` |

Casi chiusi dalla revisione e dalla correzione del nucleo (gli id `R` sono
quelli citati nei test):

| Id | Caso | Comportamento | Test (piano) |
|---|---|---|---|
| R1 | Errore imprevisto ferma l'import | Esito fallito per quel file; l'import continua | T-IMP-01 |
| R4 | Chiusura che attende un lavoro in corso | La finestra resta aperta finché il thread dei dati finisce, senza bloccare la UI; oltre il tetto avvisa | T-UI-02, T-UI-08 |
| R5 | Nessun modo di vedere i log | Menu Aiuto, cartella dei log; log su file con rotazione | T-SEC-08 |
| R11 | Orari d'inizio senza finestra di plausibilità | Vedi D-F1-04 | T-SEC-02, T-GPX-01 |
| R14 | Elenco dei profili inutilizzabile | Messaggio con la posizione del file; la finestra resta usabile | T-DATI-01 |
| — | Cancellazione troppo larga | Solo i nomi che l'app mette nella cartella; rifiutata se c'è altro, un collegamento o un profilo aperto | T-SEC-04, T-DATI-04 |
| — | Migrazione senza rete di sicurezza | Copia del database in `backup/ponos-v{da}-v{a}.sqlite3` prima di ogni migrazione; senza copia non si migra | T-DATI-01 |
| — | Anteprima che modifica il profilo | `peek` apre il database in sola lettura, senza migrare né prendere il lock | T-UI-03 |
| — | Sostituzioni di file con tecniche diverse | Un solo modulo, `ponos_coach.fsio` | T-DATI-02 |

## 5. Rischi accettati

| Rischio | Motivo |
|---|---|
| API Garmin non ufficiale: può rompersi, può violare i termini d'uso, può portare al blocco dell'account | Non esiste un accesso ufficiale per i privati (ADR 002). Compensazioni: adattatore isolato, poche richieste, import FIT sempre disponibile. |
| Eseguibili non firmati | Progetto personale senza certificato di firma. Compensazione: `SHA256SUMS`, README con verifica dell'hash e avviso di SmartScreen. |
| Token leggibili da ogni processo dell'utente | DPAPI in ambito utente e il Secret Service proteggono dagli altri utenti, non dagli altri processi dello stesso utente. Il Credential Manager, scartato perché non contiene i token (2560 byte), avrebbe avuto lo stesso limite. Una password che l'utente deve digitare a ogni avvio andrebbe contro l'uso quotidiano. La macchina è assunta non compromessa. Compensazione: il logout cancella i token. |
| Timeout della libreria non interrompibili | Il timeout è della libreria (15 s per richiesta, 30 s per passo di login) e non si configura senza toccare parti interne. L'annullamento vale dalla richiesta successiva: una richiesta già partita finisce o scade da sola. Compensazione: il giornale rende la ripresa sicura in ogni punto. |
| Risposta o download letti per intero in memoria dalla libreria prima del tetto | Il tetto di 50 MB si applica dopo la lettura. Compensazione: il timeout di 15 s e i limiti dello ZIP a valle (T-SEC-02). |
| Entropia di DPAPI non segreta | Il codice è pubblico; l'entropia lega il file all'app e al profilo, non protegge dall'utente stesso. |
| Abbinamento sbagliato tra due attività quasi uguali (60 s, 5%, stesso sport) | Il file originale importato resta; l'altra attività non si scarica. Come per i duplicati tra FIT e GPX, meglio non perdere dati. |
| Dati di salute in chiaro nella cartella del profilo, protetti solo dai permessi del sistema | Semplicità e apertura con altri strumenti; cifrare richiede gestione di una password e di una chiave di recupero. Accettato dall'utente il 2026-11-05. Dalla fase 3 vi stanno anche altezza, peso (con lo storico) e data di nascita. Compensazione: permessi della cartella solo per l'utente (T-SEC-09); il README consiglia la cifratura del disco (BitLocker su Windows, LUKS su Ubuntu). |
| Copie del database in `backup/` e file dell'archivio con gli stessi dati di salute | Stesso rischio e stessa compensazione della cartella del profilo: stessi permessi privati. |
| Cartella del profilo su rete o in una cartella sincronizzata | L'app avverte ma non vieta: l'utente può avere un NAS affidabile. Su questi posti il lock e il WAL di SQLite non sono garantiti; un database rovinato si segnala all'apertura. |
| Rilevamento di rete e cloud non completo | Si riconoscono UNC, unità remote, mount di rete, OneDrive, Dropbox, Google Drive e iCloud Drive per nome e variabili; un servizio sconosciuto non è riconosciuto. Nel dubbio il tipo è ignoto e non c'è avviso. |
| Elenco dei profili leggibile da ogni processo dell'utente | Contiene percorsi e nomi dei profili, non dati di salute; sta nella cartella di configurazione dell'utente. |
| Un attacco con permessi dell'utente può sostituire l'archivio e il suo database insieme | L'integrità protegge da errori e da modifiche parziali, non da chi ha già il controllo dell'account (assunzioni). |
| Duplicato non riconosciuto tra FIT e GPX fuori soglia (60 s, 5%) | L'attività entra due volte: nessun dato perso. Meglio di cancellare un'attività vera. |
| Nessuna cancellazione sicura dei dati | Fuori dalla portata di un'app su file system comuni. |

## Punti aperti

- Il backup contiene dati di salute: formato e protezione da decidere in
  fase 8.
- Nessun test copre il tetto di 1.000.000 di voci elencate
  (`MAX_LISTED_ACTIVITIES`).
- La forma reale delle risposte di Garmin (chiavi dello sport, campi
  dell'elenco) è verificata solo sulle fixture: manca lo spike dal vivo della
  fase 0, che lancia l'utente (`experiments/README.md`). I valori di
  `SyncPolicy` vanno rivisti con il suo esito.
- Il comportamento della chiusura durante un login o un download è in
  evoluzione nella fase 2; da rileggere con la versione finale.
- L'id D-F3-01 non compare in nessun test della fase 3: da assegnare a un
  difetto o da lasciare vuoto.
- I difetti D-F1 e D-F2 non sono ancora registrati in `docs/collaudo.md`: lo
  fa la sessione principale.
