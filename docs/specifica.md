# Specifica: cosa vede e fa l'utente

Descrive il comportamento osservabile di Ponos Coach, versione prevista a fine
sviluppo. Formule, soglie e regole tecniche non stanno qui: sono nella skill
del progetto, in
[`.claude/skills/ponos-allenamento/`](../.claude/skills/ponos-allenamento/SKILL.md).
Le fasi di lavoro sono in `PLAN.md`.

Ponos Coach non è un dispositivo medico e non fa diagnosi.

## 1. Profili e cartella

- Un profilo è una persona che usa l'app. Si possono avere più profili.
- Al primo avvio, senza profili, si apre il dialogo "Nuovo profilo".
- Alla creazione l'utente sceglie la cartella del profilo. Lì stanno tutti i
  suoi dati: database, archivio dei file originali (FIT e GPX), backup.
- Nessuna cartella è proposta: il campo parte vuoto e "Crea profilo" resta
  disabilitato finché l'utente non sceglie una cartella con "Sfoglia…" o la
  scrive. Il selettore parte dall'ultima cartella usata per i profili, la
  prima volta da Documenti. Se la cartella scelta è vuota o non esiste, è la
  cartella del profilo; se contiene altri file, il profilo va in una
  sottocartella con il suo nome, e il dialogo mostra dove andranno i dati.
  L'app crea la cartella leggibile solo dall'utente. Le cartelle sincronizzate
  (OneDrive, spesso anche Documenti su Windows) sono sconsigliate.
- La cartella si può spostare o aprire da un altro PC con "Apri profilo da
  cartella".
- Se la cartella è su un disco di rete o in una cartella sincronizzata
  (OneDrive, Dropbox, Google Drive…), l'app avverte nel dialogo e, a profilo
  aperto, nella barra di stato: il database può essere lento o rovinarsi.
  L'avviso non blocca. Se non riesce a capire dove sta la cartella, non
  avverte.
- Un profilo si apre in una sola istanza alla volta. Una seconda istanza è
  rifiutata con un messaggio.
- Un profilo salvato da una versione più nuova dell'app non si apre: il
  messaggio invita ad aggiornare. Prima di portare un profilo vecchio alla
  versione attuale, l'app ne copia il database in `backup/`
  (`ponos-v{da}-v{a}.sqlite3`); se la copia non riesce, non migra.
- Menu File: Nuovo profilo (Ctrl+N), Apri profilo da cartella (Ctrl+O), Cambia
  profilo, Rimuovi profilo dall'elenco, Elimina cartella del profilo. Il nome
  del profilo attivo sta nel titolo della finestra. Mentre un'operazione è in
  corso i comandi sono disabilitati, con il motivo nel suggerimento.
- Menu Aiuto: "Apri la cartella dei log" (Ctrl+Maiusc+L) apre con il gestore
  di file del sistema la cartella dove l'app scrive i suoi log; se non
  riesce a aprirla, dice dov'è. Il log contiene eventi e tipi di errore, mai
  dati personali, di salute, nomi di file o percorsi. È un file con rotazione
  (circa 4 MB al massimo).
- Chiusura della finestra: se un lavoro è in corso (per esempio un import)
  l'app lo interrompe, aspetta che finisca e intanto scrive "Chiusura del
  profilo in corso..." nella barra di stato; la finestra non si blocca. Se il
  lavoro non finisce entro 10 secondi, la finestra resta aperta e un
  messaggio ("Ponos Coach sta ancora chiudendo il profilo") invita ad
  attendere e a chiuderla di nuovo.
- "Rimuovi" toglie il profilo solo dall'elenco. Cancellare la cartella è un
  comando a parte e non vale per il profilo aperto.
- La conferma dell'eliminazione mostra il nome del profilo, la cartella e il
  numero di attività (letto senza modificare il database; se non si legge, lo
  dice). Il bottone dice "Elimina cartella", il predefinito è Annulla.
- L'eliminazione cancella solo ciò che l'app ha creato: il database, i suoi
  file `-wal`, `-shm` e `-journal`, `archivio/`, `backup/` e il file di
  blocco. Se nella cartella c'è altro (per esempio file dell'utente), o un
  collegamento, è rifiutata: il messaggio "La cartella non è stata eliminata"
  dice che non è stato eliminato nulla e chiede di spostare o eliminare a mano
  quei file, poi riprovare, oppure di rimuovere il profilo dall'elenco.
- I dati di salute restano in locale. Le credenziali Garmin non stanno nella
  cartella.

Regole tecniche: [dati e sincronizzazione](../.claude/skills/ponos-allenamento/references/dati-e-sincronizzazione.md).
Formato su disco: [ADR 005](adr/005-schema-sqlite-e-archivio.md).

## 2. Primo avvio guidato

Si ripete per ogni nuovo profilo. I passi:

1. Nome e cartella del profilo.
2. Login Garmin (si può rimandare: l'import di file FIT funziona sempre).
3. FC minima e massima.
4. Sesso (serve al calcolo del carico).
5. Disponibilità settimanale.
6. Tipologie di seduta.
7. Proposta del test di soglia.

Poi parte la prima sincronizzazione di tutto lo storico.

Stato della fase 1: il primo avvio apre il dialogo "Nuovo profilo" con nome,
cartella, FC a riposo, massima e di soglia (stimata al 90% della massima
finché l'utente non la fissa), sesso e fuso orario. I passi successivi
arrivano con le loro fasi. Nella fase 2 il login Garmin non è un passo del
dialogo: si fa da Garmin, Collega account Garmin… (sezione 3).

## 3. Sincronizzazione con Garmin

Regole tecniche: [dati e sincronizzazione](../.claude/skills/ponos-allenamento/references/dati-e-sincronizzazione.md).
Decisioni: [ADR 006](adr/006-token-e-sincronizzazione.md). L'accesso non è
ufficiale (ADR 002): può smettere di funzionare, e l'import di file resta
sempre disponibile.

### Menu Garmin

| Voce | Scorciatoia | Quando è disponibile |
|---|---|---|
| Collega account Garmin… | Ctrl+G | profilo aperto, nessun account collegato o accesso da rifare |
| Scollega account Garmin | Ctrl+Maiusc+G | account collegato o da ricollegare |
| Sincronizza | F5 | account collegato, nessun'altra operazione |
| Verifica completa | Ctrl+F5 | come Sincronizza |
| Interrompi sincronizzazione | Maiusc+F5 | solo durante una sincronizzazione |

Una voce spenta dice il motivo nel suggerimento (nessun profilo, account non
collegato, accesso da rifare, operazione in corso, portachiavi non
disponibile).

### Collegare e scollegare l'account

- "Collega account Garmin…" apre un dialogo con email e password. Vanno solo a
  Garmin e non si salvano. Se Garmin chiede la verifica in due passaggi,
  compare un secondo dialogo per il codice.
- Se l'attesa del codice scade (5 minuti) o l'utente annulla, il collegamento
  finisce e non si salva nulla. Se l'attesa è scaduta, un messaggio lo dice.
- Credenziali rifiutate, troppi tentativi, Garmin irraggiungibile e
  portachiavi non disponibile hanno ciascuno il proprio messaggio nel dialogo,
  in parole comuni.
- A collegamento riuscito i token si salvano protetti dal sistema (Windows:
  cifrati per l'utente; Ubuntu: Secret Service) e parte la prima
  sincronizzazione. Se il sistema non ha un posto sicuro per i token
  (su Ubuntu senza Secret Service) l'account non si collega e il messaggio lo
  dice; l'import da file resta disponibile.
- I token non stanno nella cartella del profilo: un profilo spostato su un
  altro PC chiede un nuovo accesso.
- "Scollega account Garmin" chiede conferma (bottone "Scollega", predefinito
  Annulla), poi cancella i token. Le attività già scaricate restano. Se i
  token non si cancellano, un messaggio lo dice.
- Un solo account per profilo: per usarne un altro si scollega il primo.

### Sincronizzare

- Con un account collegato parte quando si apre il profilo, se l'interruttore
  "Sincronizza all'apertura del profilo" (menu Garmin, Ctrl+Maiusc+A) è
  attivo: è acceso di default e la scelta si ricorda per profilo. A mano si
  sincronizza sempre con Sincronizza (F5).
- La prima sincronizzazione elenca e scarica tutto lo storico. Può durare a
  lungo: la barra di stato mostra la fase ("Elenco delle attività", poi
  "Scaricamento dell'attività n di N"), una barra di avanzamento, il tempo stimato
  (meno di un minuto, minuti, ore) e "Interrompi".
- Le successive elencano dalle più recenti finché trovano una pagina di
  attività già note, e scaricano solo quelle mancanti.
- "Verifica completa" rielenca tutto lo storico, senza riscaricare ciò che c'è
  già, per trovare attività caricate in ritardo con date vecchie. Ripropone
  anche le attività rinunciate per troppi fallimenti.
- Se l'app si chiude o si interrompe a metà, alla sincronizzazione successiva
  riprende da dove era rimasta, senza perdere né duplicare attività. La
  chiusura dell'app non resta bloccata da un accesso o da un download in
  corso.
- Mentre scarica, la finestra resta usabile; l'import di file continua a
  funzionare.
- Un'attività già importata da file (stessa ora d'inizio, stesso sport,
  durata simile) non si riscarica né si duplica: si collega a quella che c'è
  e il riepilogo la conta come "abbinata a file importati". Il file scaricato
  deve poi essere davvero quello dell'attività elencata; se è un'altra
  attività, quella voce fallisce e non si registra nulla.
- Le attività già presenti non contano come nuove.

### Cosa succede quando qualcosa non va

- **Limite di frequenza di Garmin:** l'app aspetta, con attese crescenti e un
  massimo di tentativi; se Garmin chiede un'attesa troppo lunga o i tentativi
  finiscono, la sincronizzazione si ferma con il messaggio "Garmin sta limitando
  le richieste. Riprova più tardi.". Nulla si perde e il giro successivo
  riprende.
- **Rete assente o Garmin che non risponde:** pochi tentativi con attese, poi
  si ferma ("Garmin non risponde. Controlla la connessione e riprova"). Un giro
  fermato così non conta come fallimento delle singole attività.
- **Token scaduti o revocati:** la sincronizzazione si ferma e il riepilogo
  offre "Collega account…". Finché non si rifà l'accesso, Sincronizza e
  Verifica completa sono spente.
- **File non utilizzabile** (formato non supportato come il TCX, danneggiato,
  troppo grande, non è un'attività, originale non fornito da Garmin, pacchetto
  non valido): quell'attività è segnata come non scaricata, col motivo, e la
  sincronizzazione continua con le altre. Dopo 3 sincronizzazioni fallite per
  cause passeggere l'attività è messa da parte; "Verifica completa" la
  riprova.
- **Disco pieno o cartella non scrivibile:** la sincronizzazione si ferma e lo
  dice; l'attività in corso resta da scaricare.
- **Risposta che l'app non sa leggere:** si ferma con un messaggio generico;
  la libreria di accesso non è ufficiale e può smettere di funzionare quando
  Garmin cambia.
- Un errore imprevisto dà un messaggio generico e rimanda ad Aiuto, Apri la
  cartella dei log. Nel log non ci sono password, token, nomi né dati di
  salute.

### Riepilogo e notifica

- A fine sincronizzazione la barra di stato dice com'è andata ("Garmin è
  aggiornato", "Sincronizzata: n nuove attività", "Sincronizzazione
  interrotta").
- Se qualcosa non è andato come previsto (voci non scaricate, arresto per un
  motivo diverso dall'interruzione dell'utente, giro non partito) si apre un
  riepilogo: quante attività nuove, quante abbinate, quante non scaricate per
  ciascun motivo, perché si è fermata e che cosa fare. Un'interruzione
  voluta non apre la finestra.
- Se ci sono attività nuove, il sistema mostra una notifica ("Nuove attività da
  Garmin") col numero. Se il sistema non supporta le notifiche non succede
  nulla. Non c'è area di notifica.

## 4. Import di file, cartelle e ZIP

- Tre modi, tutti su richiesta e solo con un profilo aperto:
  - "Importa file…" (Ctrl+I): si scelgono uno o più file `.fit`, `.gpx` o
    `.zip`;
  - "Importa da cartella…" (Ctrl+Maiusc+I): si sceglie una cartella;
  - trascinando file o una cartella sulla finestra.
- Formati: FIT e GPX, anche dentro file ZIP (come quelli scaricati dal sito
  Garmin). Del GPX si leggono traccia, orari, quota e FC; un GPX senza orari
  non è un'attività. I TCX non sono supportati e vengono rifiutati con il
  motivo. Il formato si riconosce dal contenuto e deve coincidere con
  l'estensione.
- Un'attività con l'inizio o la fine fuori dal periodo 1990–2099 (UTC) è
  rifiutata come file danneggiato.
- Con una cartella, l'app la esamina prima di importare (nomi e dimensioni,
  sottocartelle comprese). Salta, segnalandoli, i collegamenti simbolici, le
  giunzioni e i file speciali, i nomi non ammessi su Windows e i percorsi
  troppo lunghi.
- Degli ZIP si leggono solo le voci `.fit` e `.gpx`. Si rifiutano, col
  motivo, gli archivi danneggiati, troppo grandi o troppo pieni, le voci con
  nomi non sicuri, i file protetti da password e gli ZIP dentro gli ZIP.
- Tetti: 50.000 file di attività (le voci degli ZIP contano), 500.000 voci
  esaminate, 32 livelli di sottocartelle, 50 MB per file. Se si supera uno dei
  primi tre, non si importa nulla e l'app chiede di scegliere una
  sottocartella. I file oltre 50 MB sono segnalati e saltati. Limiti degli
  ZIP: 50.000 voci, 5 GB in tutto, compressione al massimo 100:1.
- I file si copiano nell'archivio del profilo. La cartella o il file d'origine
  non vengono mai modificati.
- I duplicati si saltano. Rilanciare l'import non cambia nulla. La stessa
  attività arrivata come FIT e come GPX conta una volta sola: si tiene la
  prima.
- L'import non blocca la finestra. La barra di stato mostra l'avanzamento e
  "Interrompi" (Esc). Un import interrotto si riprende rilanciandolo.
- Esito per file:
  - importata;
  - già presente (anche se arrivata in un altro formato);
  - riparata: l'attività c'era già, ma il suo file nell'archivio mancava o
    era alterato; reimportando lo stesso file originale, l'app lo rimette a
    posto. Il riepilogo la conta a parte ("n file archiviati riparati
    dall'originale importato"), non come problema;
  - ignorata: il file non è un'attività;
  - con errore, col motivo (danneggiato, troppo grande, non leggibile, non
    salvato…).
- Un errore imprevisto su un file lo segna come fallito. L'import continua con
  gli altri.
- Alla fine un riepilogo: i conteggi per esito e, per ogni file non
  importato, il nome e il motivo in parole comuni.
- L'app ricorda l'ultima cartella usata per i file e per le cartelle.

## 5. Parametri fisiologici

- L'utente inserisce FC minima, FC massima, sesso e FC di soglia.
- Ogni valore ha una data di validità. Ogni attività usa i valori del suo
  periodo. Se un valore cambia, i carichi si ricalcolano.
- In bici la soglia è quella della corsa meno 4 bpm, finché non c'è un test in
  bici.
- Un valore di FC fuori da minima e massima è un errore: il campione si
  scarta. Il dettaglio dell'attività dice quanti campioni sono stati scartati.
  Se molti superano la massima, l'app propone di rivederla.
- Attività senza FC, o con troppi campioni scartati, hanno un carico stimato,
  marcato "stimato" in tabella e nei grafici.
- I pesi degli sport si possono modificare nelle impostazioni del profilo.

Formule e valori: [modello di carico](../.claude/skills/ponos-allenamento/references/modello-carico.md).

## 6. Carico e grafici

- La Panoramica mostra la forma di oggi, la seduta di oggi e il grafico PMC
  (CTL, ATL, TSB, carico giornaliero, proiezione del piano, linee degli
  obiettivi).
- Altri grafici: carico settimanale per sport, tempo nelle zone, FC
  dell'attività.
- Assi con nome e unità, tema chiaro e scuro, esportazione in SVG e PNG.
- I grafici non accedono alla rete.

### Panoramica

- Senza profilo, la Panoramica invita a creare il primo profilo. Con un
  profilo senza attività, invita a importare file.
- Mentre il profilo si apre e si calcola il carico, mostra l'avanzamento con
  Annulla. Se il calcolo è annullato o fallisce, un bottone lo ripete.
- La barra di stato mostra il numero di attività, il numero di attività
  escluse e i giorni del grafico.
- Un'attività è esclusa dal calcolo quando il suo file nell'archivio manca, è
  stato alterato dopo il salvataggio, è troppo grande, non si legge o non è più
  valido. Il suggerimento sul conteggio delle escluse dice i motivi, col numero
  per ciascuno. Un file alterato non si usa mai in silenzio; per rimetterlo a
  posto si reimporta il file originale (esito "riparata").

### Tabella delle attività

- Una riga per attività, ordinata per data, dalla più recente. Si riordina
  cliccando l'intestazione di una colonna.
- Colonne: data e ora (nel fuso del profilo), sport, tempo in movimento
  (h:mm:ss), distanza (km), dislivello positivo (m), carico (hrTSS), origine
  del carico (frequenza cardiaca o stima) e formato del file (FIT o GPX).
- Numeri allineati a destra, unità nell'intestazione, niente linee. La cella è
  vuota dove il dato manca (per esempio la distanza di una seduta indoor).
- Senza attività, la pagina invita a importare file.
- I filtri (data, durata, distanza, dislivello, sport) arrivano con la fase 9.
- Il dettaglio dell'attività, con FC e zone, arriva con i grafici (fase 3).

## 7. Test di soglia

- L'app propone un test di soglia di 30 minuti in corsa all'inizio e lo
  ripropone ogni 6–8 settimane, in una settimana di scarico.
- Le istruzioni sono nell'app. Dopo il test l'utente indica quale attività è
  il test.
- L'app calcola FC e ritmo di soglia, aggiorna le zone e conserva lo storico
  dei test. Un test interrotto o troppo corto è rifiutato con il motivo.

Protocollo: [test di soglia](../.claude/skills/ponos-allenamento/references/test-soglia.md).

## 8. Obiettivi

- Tipi: gara con data e priorità A/B/C e tempo obiettivo; livello di forma
  (CTL) a una data. Più obiettivi insieme.
- Il calendario mostra gli obiettivi accanto a fatto e pianificato.
- Aggiungere, modificare ed eliminare un obiettivo si può annullare e
  ripetere.
- L'app dichiara se il tempo obiettivo è realistico, ambizioso o non
  raggiungibile entro la data.

## 9. Piano adattivo

- L'utente indica giorni disponibili, ore massime a settimana, giorno del
  lungo e le tipologie di seduta ammesse per sport. Il piano non supera mai la
  disponibilità e programma solo quelle tipologie.
- Se un obiettivo richiede una tipologia esclusa, il piano lo segnala e spiega
  l'effetto, ma non la aggiunge.
- Il piano è deterministico: stessi dati e parametri, stesso piano.
- Dopo ogni sincronizzazione l'app confronta fatto e pianificato e ricalcola
  da domani. Il passato resta storia.
- In caso di forte affaticamento l'app inserisce recupero. Il carico perso
  non si recupera oltre la rampa massima.
- Ogni seduta ha durata, zone e carico stimato.

Regole e soglie: [piano](../.claude/skills/ponos-allenamento/references/piano.md).

## 10. Infortuni

- L'utente descrive l'infortunio con un questionario: zona e lato,
  insorgenza, dolore, attività che non fanno male, indicazioni del medico
  (testo libero, solo in locale).
- Con un segnale d'allarme il piano della zona colpita si ferma e l'app invita
  a rivolgersi a un medico. Nessuna diagnosi.
- Altrimenti l'app propone un percorso di ripresa a gradini e fa un check-in
  ogni giorno. Sale di gradino solo con dolore basso e mattino dopo normale;
  scende se peggiora.
- Le attività alternative senza dolore mantengono la forma. Dopo il rientro la
  crescita del carico è più prudente.

Regole: [infortuni](../.claude/skills/ponos-allenamento/references/infortuni.md).

## 11. Calendario Garmin (fase 7)

- L'utente sceglie cosa inviare: piano intero, una settimana o una seduta.
- Può vedere gli allenamenti futuri programmati su Garmin ed eliminarne
  alcuni. La conferma mostra l'elenco, usa un bottone con il verbo e l'app
  salva prima una copia locale per poterli ricreare.
- Gli allenamenti creati dall'app hanno un prefisso nel nome.

## 12. Backup

- Un profilo si può salvare in un file di backup versionato e ripristinare in
  una cartella nuova, con lo stesso contenuto (attività, parametri, obiettivi,
  piano, infortuni). Previsto in fase 8.
- Il backup contiene dati di salute: va conservato come tali. Non contiene i
  token Garmin.

## 13. Comportamenti generali

- Interfaccia in italiano e inglese.
- Menu completi con scorciatoie; tutto usabile da tastiera.
- Annulla e ripeti per obiettivi e calendario.
- L'app ricorda finestra, pannelli, ultimo profilo e ultime cartelle. Nella
  fase 1: dimensioni e posizione della finestra, la sezione aperta (si salva
  appena cambia; un valore non valido apre la Panoramica; una geometria
  illeggibile dà la finestra predefinita), le cartelle degli import, della
  scelta del profilo e dell'esportazione dei grafici, l'ultimo profilo.
- Le operazioni lunghe non bloccano la finestra e si possono annullare.
- Un errore imprevisto mostra un messaggio generico; il dettaglio va nel log,
  senza dati di salute.

## Punti aperti

- Formato e contenuto del file di backup (fase 8).
