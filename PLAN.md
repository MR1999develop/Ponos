# Ponos Coach: piano

App desktop (Windows e Ubuntu), con più profili utente, che scarica le
attività da Garmin Connect,
calcola il carico dalla frequenza cardiaca, mostra ATL, CTL e TSB e genera un
piano di allenamento adattivo verso uno o più obiettivi nel calendario, con un
percorso di recupero dagli infortuni.

Stato: **piano da approvare** (2026-11-05). Si spunta una voce solo se
completata e verificata.

---

## 1. Decisioni prese con l'utente (2026-11-05)

| Tema | Decisione |
|---|---|
| Linguaggio e GUI | Python + PySide6 (Qt 6); tabelle Qt modello/vista |
| Grafici | Plotly in un `QWebEngineView`, `plotly.js` incluso nell'app, nessun accesso alla rete dalla pagina |
| Accesso a Garmin | Login diretto non ufficiale con `garminconnect` (MFA); password mai salvata, token nel portachiavi del sistema; import manuale di file FIT come riserva |
| Sport | Corsa (strada, trail), ciclismo, forza/palestra, escursionismo, camminata, altro |
| Obiettivi | Gare con data e priorità A/B/C, tempo obiettivo in gara, livello di forma (CTL) a una data; più obiettivi insieme |
| Infortuni | Descrizione dell'infortunio, domande dell'app, piano di recupero; nessuna diagnosi, segnali d'allarme → medico |
| Motore del piano | Regole deterministiche (stessi dati → stesso piano), niente LLM |
| Adattamento | Solo carico da FC (ATL, CTL, TSB) e carico passato |
| Test di valutazione | Test di soglia di 30' in corsa all'inizio, ripetuto ogni 6–8 settimane |
| Formula del carico | hrTSS da TRIMP di Banister sul tracciato FC, moltiplicato per il peso dello sport. **Nota 2026-11-10: sostituita dal TRIMPeq, senza hrTSS né pesi (fase 8, ADR 004 emendato)** |
| Profili | Più profili utente; per ogni profilo l'utente sceglie la cartella in cui stanno tutti i suoi dati |
| FC minima e massima | Inserite dall'utente; un valore di FC fuori da questi limiti è un errore |
| Soglia in bici | FC di soglia della corsa − 4 bpm, finché non c'è un test in bici |
| Tipologie di seduta | Scelte dall'utente; il piano programma solo quelle |
| Import da cartella | Oltre alla sincronizzazione, su richiesta: si sceglie una cartella e si aggiungono i file FIT che contiene |
| Formati dei file | Solo FIT (decisione del 2026-11-09, fase 8, ADR 014): GPX e import di ZIP tolti; lo ZIP scaricato da Garmin con dentro il FIT resta |
| Dati di salute sul disco | In chiaro nella cartella del profilo, protetti dai permessi del sistema (solo l'utente); il README consiglia la cifratura del disco; nessuna password dell'app |
| Ricerca e mappa (fase 5b) | Filtri delle attività per data, durata, distanza e dislivello; mappa in stile Komoot con le tracce delle attività svolte (linee per sport; a zoom basso partenze raggruppate in cerchi numerati), sfondo OpenStreetMap senza chiave (dal 2026-11-09; prima Tracestrack Topo con chiave personale nel portachiavi), sfondo neutro con avviso senza rete o se il server rifiuta, tile servite da un proxy in Python (la pagina non ha rete), cache su disco LRU con tetto esatto scelto nelle impostazioni (default 4 GB); accanto alla mappa profilo altimetrico collegato alla traccia, traccia colorabile per zone FC, elenco e totali delle attività nell'area. Subito dopo la fase 5 (decisioni del 2026-11-07; prima era la fase 9, dopo il rilascio) |
| Sincronizzazione | All'avvio e con il pulsante Sincronizza; la prima scarica tutto lo storico, le successive solo le attività mancanti; riprende da dove era rimasta dopo una chiusura; notifica a fine sincronizzazione; niente area di notifica |
| Calendario Garmin | In una fase successiva: invio degli allenamenti a scelta dell'utente ed eliminazione degli allenamenti futuri programmati |
| Lingue | Inglese come lingua predefinita, italiano scelto dal menu Lingua e ricordato (decisione del 2026-11-06; prima seguiva la lingua del sistema) |
| Dati del profilo | Alla creazione: nome, cartella, altezza, peso, data di nascita, sesso, FC minima e massima, soglia. Poi si aggiornano con una data di validità peso, FC minima, FC massima e soglia; nome, altezza, data di nascita e sesso restano fissi (decisione del 2026-11-06) |
| Alimentazione (fase 4b) | Cliccando un giorno passato del calendario: kcal e grammi di carboidrati, proteine e grassi per colazione, pranzo, cena, pre, durante (sedute lunghe) e post allenamento, (dal 2026-11-10 senza esempi di alimenti); nessun diario dei pasti; obiettivo alimentare per periodi (dimagrimento, mantenimento, performance); calorie fuori dallo sport da un livello di attività nel profilo; nessuna restrizione alimentare per ora; i giorni futuri arriveranno con il piano (fase 5) (decisioni del 2026-11-07) |
| Piattaforme e repository | Windows e Ubuntu, GitHub con CI (repository pubblico); il push lo fa l'utente a mano (regola del 2026-11-07; dal 2026-11-06 al 2026-11-07 era automatico); su `main` solo con PR |

## 2. Decisioni di default (le ho fissate io: da confermare o correggere)

1. **Cartella del profilo:** chiesta all'utente alla creazione del profilo,
   senza proposte automatiche (decisione dell'utente del 2026-11-06), e
   autosufficiente: database, archivio FIT e
   backup stanno lì, così si può spostare o aprire da un altro PC con "Apri
   profilo da cartella". Fanno eccezione i token Garmin, che restano nel
   portachiavi del sistema legati all'id del profilo. L'elenco dei profili e
   l'ultimo usato stanno nella configurazione dell'app.
2. **Rimuovere un profilo** lo toglie solo dall'elenco; cancellarne la
   cartella è un comando a parte, con conferma che mostra cartella e
   contenuto.
3. **FC fuori limiti:** si scarta il singolo campione, non l'attività. Il
   dettaglio dell'attività dice quanti campioni sono stati scartati; se molti
   superano la massima, l'app propone di rivederla. FC minima, FC massima e
   soglia hanno una data di validità: ogni attività usa i valori del suo
   periodo, e il cambio di un valore ricalcola i carichi derivati.
4. **Attività senza FC** (o con più del 30% del tempo in movimento senza
   FC valida): carico stimato da durata × intensità tipica dello sport,
   marcato "stimato" in tabella e nei grafici.
5. **Tipologie di seduta:** elenco per sport (es. corsa facile, lungo,
   soglia, ripetute, bici facile, forza, escursionismo) con caselle da
   spuntare. Se un obiettivo richiede una tipologia esclusa (es. mezza
   maratona senza lunghi), il piano lo segnala ma non la aggiunge.
6. **Notifica di fine sincronizzazione:** notifica di sistema (toast su
   Windows, notifica del desktop su Ubuntu) con il numero di attività nuove,
   più il messaggio nella barra di stato.
7. **Pesi di default degli sport** (provvisori, da tarare in fase 3): corsa
   1,0; trail 1,1; ciclismo 0,8; escursionismo 0,8; camminata 0,6; forza 1,2
   (la FC sottostima il carico della forza); altro 0,8.
   *Nota 2026-11-10: pesi tolti, il carico è il TRIMPeq (fase 8).*
8. **Disponibilità settimanale** inserita dall'utente: giorni disponibili,
   sedute a settimana, ore massime a settimana, giorno del lungo (dal
   2026-11-09, ADR 016; prima i minuti di ogni giorno). Il piano non la supera
   mai.
9. **Dati sul disco:** file FIT originali come fonte di verità delle attività;
   SQLite come indice e per i dati dell'utente (obiettivi, piani, infortuni,
   impostazioni); carichi e PMC derivati e rigenerabili.
10. **Messaggio di responsabilità** nell'app e nel README: Ponos Coach non è un
    dispositivo medico.
11. **Import da cartella:** file `.fit`, sottocartelle comprese; gli altri
    file (GPX, ZIP, TCX…) si ignorano in una cartella e si segnalano come
    formato non supportato se scelti a mano; i file
    si copiano nell'archivio del profilo e la cartella d'origine non si
    tocca; i duplicati (già importati o già scaricati da Garmin) si saltano;
    alla fine un riepilogo. L'app ricorda l'ultima cartella usata. Tetti:
    50.000 file di attività, 500.000 voci esaminate (file e cartelle di ogni tipo),
    32 livelli di sottocartelle, 50 MB per file. Il primo tetto è circa sette
    volte un decennio con due attività al giorno; gli altri impediscono di
    scandire per errore un disco intero.
12. **File GPX: tolti** (2026-11-09, fase 8, ADR 014). Il lettore GPX, la sua
    identità (SHA-256), i duplicati tra formati e la dipendenza `defusedxml`
    non esistono più; le attività già importate da GPX si tolgono dal
    profilo alla prima apertura (schema v11, dopo una copia del database in
    `backup/`), con un avviso che dice quante.

## 2b. Decisioni rimandate

- **Integrazione AI** (valutata il 2026-11-06, da decidere in seguito):
  server MCP in sola lettura su stdio, usabile da Claude o Gemini senza
  chiavi nell'app (consigliato, dopo la fase 5); in alternativa un
  assistente dentro l'app via API, opzionale e disattivato di default, con
  consenso esplicito prima di inviare dati di salute. In nessun caso il
  modello decide il piano: le regole restano l'unica autorità sui carichi.

## 3. Rischi principali

| Rischio | Conseguenza | Risposta |
|---|---|---|
| Garmin cambia il login o le API (è già successo a marzo 2026) | Sincronizzazione ferma | Adattatore isolato dietro una porta, test di contratto su risposte registrate e anonimizzate, import FIT manuale sempre disponibile, versione di `garminconnect` bloccata e aggiornata dopo prova |
| Accesso non ufficiale contrario ai termini d'uso, blocco temporaneo (429) | Account rallentato o segnalato | Poche richieste: solo all'avvio e a mano, pausa tra le richieste, backoff con limite, nessun ritentativo infinito |
| Consigli di allenamento sbagliati o pericolosi | Sovraccarico, infortuni | Limiti rigidi di rampa del CTL e di TSB, regole testate con scenari, segnali d'allarme, nessuna diagnosi |
| Indicazioni alimentari sbagliate o dannose (deficit eccessivo, disturbi alimentari, condizioni mediche) | Danno alla salute | Limiti rigidi sul deficit e sul minimo giornaliero, nessun deficit sotto i 18 anni o con IMC sotto 18,5, avviso di consultare un professionista, linee guida pubblicate (ISSN, ACSM, IOC) come fonte, nessuna diagnosi |
| Pacchetto pesante (QtWebEngine) | ~250 MB | Accettato con la scelta di Plotly; misurato in fase 8 |
| Prima sincronizzazione di tutto lo storico | Centinaia o migliaia di richieste: lunga e a rischio di 429 | Pause e backoff, giornale riprendibile, avanzamento con tempo stimato; si può chiudere l'app e riprendere |
| Cartella del profilo su disco di rete o sincronizzato (OneDrive, NAS) | SQLite può corrompersi con lock e sincronizzazioni a metà scrittura | Avviso alla scelta della cartella; un solo processo per profilo (file di lock) |

## 4. Architettura

### 4.1 Stack e dipendenze candidate

Ogni dipendenza diretta si valida e si registra in `docs/dipendenze.md`
(licenza compatibile con GPLv3, manutenzione, alternative).

- Python 3.13 o successivo (fissato con `uv python pin` nella fase 0, se
  PySide6 e `garminconnect` lo supportano), `uv` con `uv.lock`.
- Esecuzione: `PySide6` con `PySide6-Addons` per QtWebEngine (LGPL-3.0),
  `plotly` (MIT, anche per il `plotly.js` incluso), `garminconnect` (MIT),
  `fitdecode` (MIT), `numpy` (BSD), `keyring` (MIT), `desktop-notifier`
  (MIT), `platformdirs` (MIT).
- Sviluppo: `pytest`, `pytest-qt`, `hypothesis`, `ruff`, `mypy --strict`,
  `import-linter`, `pip-audit`, `nox`, `pyinstaller` (`.spec` versionato).

### 4.2 Pacchetti (cartelle per funzionalità)

```
src/ponos_coach/
  activities/       attività, lettura FIT, import, sincronizzazione (casi d'uso)
  load/             TRIMPeq (dal 2026-11-10; prima hrTSS e pesi degli sport), PMC (ATL, CTL, TSB), parametri fisiologici
  assessment/       test di soglia 30', zone FC e di ritmo, retest
  goals/            obiettivi (gara A/B/C, tempo obiettivo, CTL a una data)
  planning/         periodizzazione, modelli di seduta, adattamento, proiezione del PMC
  injury/           questionario, segnali d'allarme, percorso di ripresa
  profiles/         profili utente, cartella dei dati, blocco contro due istanze
  garmin/           adattatore garminconnect (porte ActivitySource e WorkoutCalendar)
  storage/          SQLite (schema, migrazioni con user_version), archivio FIT
  platform/         portachiavi, notifiche, cartelle dell'app
  ui/               finestra Qt, modelli di presentazione, grafici Plotly, tabelle, dialoghi
  i18n/             file .ts e .qm
  __main__.py       Composition Root
```

- In ogni funzionalità il dominio è puro (niente I/O, Qt, rete né log); i casi
  d'uso caricano, chiamano il dominio e salvano in un'unica transazione.
- Dipendenze permesse: `ui → casi d'uso → dominio`; gli adattatori
  (`garmin`, `storage`, `platform`) implementano porte definite nelle
  funzionalità. Nessun ciclo. Le regole stanno in `importlinter` e la CI le
  verifica.
- Il piano è un valore: il nucleo restituisce il piano, il guscio lo salva o
  lo invia a Garmin; prova a vuoto e test senza I/O.

### 4.3 Dati

- Cartella del profilo, scelta dall'utente: `ponos.sqlite3` (WAL,
  `foreign_keys = ON`, parametri `?`, schema con `user_version` e migrazioni
  versionate; contiene id del profilo e versione del formato),
  `archivio/<anno>/<sha256>.fit` (file originali, con il nome
  dato dal loro SHA-256), `backup/`, `ponos.lock` (creato in modo
  esclusivo: un solo processo per profilo).
- Configurazione dell'app (`platformdirs`): elenco dei profili con le loro
  cartelle e ultimo profilo usato.
- Ogni FIT ha SHA-256 registrato; scrittura su file temporaneo, flush,
  rinomina; il record in SQLite si scrive dopo il file.
- Identità dell'attività: il `file_id` del FIT (produttore, numero di serie,
  ora di creazione), uguale per lo stesso file scaricato da Garmin o copiato
  dall'orologio; l'id Garmin si aggiunge quando è noto. Così la stessa
  attività non entra due volte, qualunque sia la sua origine.
- Solo FIT (fase 8): l'estensione `.fit` decide se un file si prova a
  leggere, il lettore ne verifica firma, CRC e struttura. Un profilo creato
  prima, con attività GPX, le perde alla prima apertura (schema v11, ADR 014).
- Dal FIT si ricavano anche distanza, dislivello positivo, tempo in
  movimento, punto di partenza e una traccia semplificata (per la mappa),
  salvati nell'indice SQLite e rigenerabili dal FIT. Le posizioni GPS sono
  dati personali sensibili (rivelano casa e abitudini): solo in locale, mai
  nei log.
- Limiti al confine: dimensione massima del FIT (es. 50 MB) prima della
  lettura, numero massimo di record, durata massima dell'attività.
- Stato della UI (finestra, pannelli, ultime cartelle) in `QSettings`.

### 4.4 Sincronizzazione riprendibile e import da cartella

1. Token dall'archivio dei segreti del sistema (sezione 4.9); se mancano o
   sono scaduti, dialogo di login con MFA. La sincronizzazione gira in un
   thread proprio con una sua connessione al database del profilo (WAL),
   così import e panoramica restano disponibili mentre scarica.
   La password va solo a `garminconnect` e non si salva.
2. Prima sincronizzazione del profilo: elenco di tutto lo storico, a pagine,
   con il punto raggiunto salvato nel giornale. Sincronizzazioni successive:
   elenco dalle più recenti finché si trova una pagina di attività già note,
   più le voci rimaste in sospeso o fallite. Ogni id nuovo diventa una voce
   "da scaricare" nel giornale di SQLite (idempotente per id Garmin). Il
   comando "Verifica completa" rielenca tutto lo storico (solo l'elenco,
   senza riscaricare) per trovare attività caricate su Garmin in ritardo con
   date vecchie.
3. Per ogni voce: download del file originale (zip con un FIT; uno zip con solo un GPX è "formato non supportato"), controlli dei limiti,
   scrittura durevole nell'archivio, lettura, calcolo del carico; una sola
   transazione segna la voce come completata.
4. Pausa tra le richieste con jitter; su 429 o errori transitori backoff
   esponenziale con tetto e numero massimo di tentativi; poi errore chiaro.
5. Se l'app si chiude a metà, all'avvio successivo riparte dalle voci in
   sospeso e cancella i temporanei orfani.
6. Il lavoro gira in un worker con annullamento; la UI mostra avanzamento e
   Annulla. A fine sincronizzazione: notifica di sistema, barra di stato, PMC
   e piano aggiornati.
7. Prima di scaricare, un'attività dell'elenco Garmin con la stessa ora
   d'inizio e lo stesso sport di una già importata da file si abbina a
   quella senza riscaricarla.

**Import da cartella** (comando "Importa da cartella…", su richiesta):

1. Si sceglie una cartella e la si esamina prima di importare (solo nomi e
   dimensioni): file `.fit` anche nelle sottocartelle, solo file regolari
   che stanno davvero dentro la cartella (niente collegamenti o reparse
   point che portano fuori). Tetti: 50.000 file di attività, 500.000 voci
   esaminate, 32 livelli, 50 MB per file (i file più grandi sono segnalati
   e saltati). Se un tetto dei primi tre si supera, l'esame si ferma, non si
   importa nulla e l'app chiede di scegliere una sottocartella: niente
   import parziali a sorpresa. I tetti sono costanti in un solo modulo.
2. Ogni file passa dagli stessi controlli dei FIT scaricati e si copia
   nell'archivio del profilo; la cartella d'origine non si modifica mai.
3. I duplicati (stesso `file_id`) si saltano. L'import è idempotente: se si
   interrompe, basta rilanciarlo.
4. Worker con avanzamento e Annulla; alla fine un riepilogo (importate, già
   presenti, ignorate, con errore e motivo per ogni file), PMC e piano
   aggiornati.
5. Solo FIT: GPX e ZIP non si importano (una cartella li ignora, scelti a
   mano si segnalano come formato non supportato). Lo ZIP si legge solo per
   l'originale scaricato da Garmin (sincronizzazione) e per il backup del
   profilo; limiti in sezione 4.9.

### 4.5 Modello di carico

> **Nota 2026-11-10:** le formule di hrTSS e `peso_sport` qui sotto descrivono
> lo stato di fase 3. Dal 2026-11-10 il carico è il TRIMPeq, senza hrTSS né
> pesi: formule in `.claude/skills/ponos-allenamento/references/modello-carico.md`
> sezione 1 e in `docs/adr/004-modello-carico.md` (emendamento).

- `HRr(t) = (HR(t) − FCmin) / (FCmax − FCmin)`, con FC minima (a riposo) e
  massima inserite dall'utente; i campioni fuori da `[FCmin, FCmax]` sono
  errori: si scartano e si contano.
- `TRIMP = Σ Δt[min] · HRr · 0,64 · e^(k · HRr)`, con `k = 1,92` (uomo) o
  `1,67` (donna), dal profilo.
- `hrTSS = 100 · TRIMP / TRIMP_1h_soglia`, dove `TRIMP_1h_soglia` è il TRIMP
  di un'ora alla FC di soglia (in bici: soglia della corsa − 4 bpm finché
  non c'è un test in bici).
- `carico = hrTSS · peso_sport`. Buchi oltre 10 s non contati; campioni
  di FC con orario ripetuto o all'indietro scartati e contati.
- La regola del 30% e la stima senza FC usano il tempo in movimento (timer
  del FIT, con ripiego sul tempo trascorso), confrontato in millisecondi
  interi. In bici la soglia è `max(soglia_corsa − 4, FCmin + 1)`.
- `ATL` e `CTL` sono medie esponenziali giornaliere nella forma del PMC:
  `X(g) = X(g − 1) + (L(g) − X(g − 1)) / τ`, con τ = 7 giorni (ATL) e 42
  giorni (CTL), partendo da 0 il giorno prima della prima attività; `TSB(giorno) = CTL(giorno − 1) − ATL(giorno − 1)` (forma del
  mattino).
- Property test: implementazione vettoriale contro implementazione ingenua;
  invarianti (carico ≥ 0, monotonia rispetto a durata e FC).

### 4.6 Motore del piano (regole deterministiche)

- **Ingressi:** PMC di oggi, obiettivi, disponibilità, tipologie di seduta
  scelte, infortuni attivi, ultimo test (FC e ritmo di soglia), pesi degli
  sport.
- **Macrociclo** per ogni obiettivo A, all'indietro dalla data: scarico
  (7 giorni fino a 10 km, 14 per la mezza, 21 a gradini per la maratona e oltre: KNO24, HAU22), picco, costruzione, base. CTL obiettivo
  dal livello di forma richiesto o da una stima per distanza e tempo, limitato
  dalla rampa massima (default +5 CTL a settimana, +3 dopo un infortunio).
- **Mesocicli 3:1:** tre settimane di carico e una di scarico (60–70%).
- **Obiettivi B:** 3–4 giorni di mini-scarico; **C:** nessuno scarico.
- **Settimana:** carico obiettivo distribuito nei giorni di seduta (tanti
  quante le sedute a settimana scelte, tra i giorni disponibili, il più
  distanziati possibile; ADR 016), alternanza duro/facile, al massimo 2–3
  sedute intense, lungo nel giorno scelto e sempre la seduta più lunga,
  distribuzione 80/20 dell'intensità in base.
- **Sedute** da modelli per sport e fase (facile, lungo, medio, soglia a
  intervalli, VO2max, recupero, forza, alternativa), **solo tra le tipologie
  scelte dall'utente**: durata, zone FC o di ritmo, carico stimato (che
  alimenta la proiezione del PMC). Se all'obiettivo manca una tipologia
  necessaria, il piano lo dice e spiega l'effetto.
- **Tempo obiettivo:** dal ritmo di soglia si stima il tempo possibile e si
  dichiara l'obiettivo realistico, ambizioso o non raggiungibile entro la
  data.
- **Adattamento:** dopo ogni sincronizzazione si confronta fatto e
  pianificato e si ricalcola da domani; il passato resta storia. TSB sotto
  −30 o ATL/CTL oltre 1,5 inseriscono recupero; il carico perso non si
  recupera oltre la rampa massima.
- **Test:** test di soglia (30' o 20') come seduta dura nella prima settimana del
  piano se non c'è un test recente (ADR 015, versione 5 del metodo), poi ogni 6–8
  settimane in una settimana di scarico; dopo il test si aggiornano soglia e zone.
- Piano salvato con versione e con i parametri che l'hanno generato.

### 4.7 Infortuni

- **Questionario:** zona e lato, data e modo d'insorgenza (improvviso o
  graduale), dolore 0–10 a riposo, camminando, correndo e il mattino dopo,
  attività che non fanno male, diagnosi e indicazioni del medico (testo
  libero, solo in locale).
- **Segnali d'allarme** (dolore notturno o a riposo forte, gonfiore o
  deformità, formicolii o perdita di forza, impossibilità di caricare il
  peso, febbre, dolore al petto o palpitazioni): stop del piano per quella
  zona e invito a un medico.
- **Percorso a gradini:** riposo o attività alternative senza dolore →
  camminata → corsa e cammino → corsa continua facile → volume → intensità.
  Si sale se il dolore resta ≤ 3/10 durante e torna al livello di base il
  mattino dopo; si scende se peggiora. Durante il percorso l'app fa domande
  ogni giorno (check-in).
- Le attività alternative senza dolore mantengono il CTL.

### 4.8 Interfaccia

- Barra laterale: Panoramica (PMC, seduta di oggi, forma), Attività (tabella
  e dettaglio con FC e zone), Calendario (mese e settimana: fatto,
  pianificato, obiettivi, test, infortuni), Obiettivi, Infortuni, Test,
  Impostazioni del profilo (pesi [tolti il 2026-11-10], FC minima, massima e soglia con date,
  sesso, disponibilità, tipologie di seduta, account Garmin).
- Menu File: Nuovo profilo, Apri profilo da cartella, Cambia profilo,
  Rimuovi profilo dall'elenco; il nome del profilo attivo sta nel titolo
  della finestra.
- Fase 5b: filtri delle attività (data, durata, distanza, dislivello, sport),
  con un solo stato condiviso da tabella e mappa; vista Mappa in stile
  Komoot: mappa topografica al centro con le tracce delle attività svolte
  (linee colorate per sport, con legenda), che a zoom basso diventano
  partenze raggruppate in cerchi numerati; a destra l'elenco delle attività
  nell'area con i totali; sotto, per l'attività selezionata, il profilo
  altimetrico collegato alla traccia (passando sul profilo si vede il punto
  sulla mappa) e la scelta del colore della traccia (sport o zone FC). Il
  modello d'interazione è quello delle app outdoor come Komoot; l'aspetto
  resta quello di Ponos Coach.
- Menu completi con scorciatoie (Sincronizza: F5), annulla e ripeti
  (`QUndoStack`) per obiettivi e calendario, barra di stato con l'ultima
  sincronizzazione.
- Grafici Plotly: PMC (CTL, ATL, TSB, barre del carico giornaliero,
  proiezione tratteggiata dal piano, linee verticali degli obiettivi), carico
  settimanale per sport, tempo nelle zone, FC dell'attività. Tavolozza
  Okabe-Ito, assi con nome e unità, tema chiaro e scuro dagli stessi token,
  esportazione SVG e PNG.
- Primo avvio guidato (anche per ogni nuovo profilo): nome e cartella del
  profilo, login Garmin, FC minima e massima, sesso (per il TRIMP),
  disponibilità, tipologie di seduta, proposta del test di soglia; poi la
  prima sincronizzazione di tutto lo storico.

### 4.9 Sicurezza (dettaglio in `docs/threat-model.md`)

- Confini di fiducia: risposte JSON di Garmin, file FIT (da Garmin,
  importati a mano o da una cartella), pagina web dei grafici, portachiavi.
- Input esterni trasformati in tipi di dominio al confine, con limiti prima
  della lettura; fuzzing del lettore FIT con `hypothesis`.
- Pagina dei grafici: solo contenuto locale, accesso alla rete bloccato da
  un intercettore, dati passati come JSON codificato, nessun `QWebChannel`
  salvo necessità dichiarata.
- Token protetti dal sistema, fuori dalla cartella del profilo: su Windows
  con DPAPI (ambito utente) in un file della cartella dati locale dell'app,
  perché il Credential Manager generico accetta al massimo 2560 byte e i
  token di Garmin li superano; su Ubuntu nel Secret Service (`keyring`).
  Dichiarati leggibili dai processi dell'utente (decisione del 2026-11-06). Log strutturati senza token, password né
  dati di salute.
- Cartella del profilo scelta dall'utente: verificata scrivibile, file creati
  in modo esclusivo, lock contro due istanze; i token non stanno nella
  cartella, che contiene invece dati di salute (va protetta come tale).
- Nessun parser XML (fase 8): si importano solo file `.fit`; GPX e ZIP
  scelti dall'utente non si aprono mai.
- ZIP scaricato da Garmin: voci e dimensioni dichiarate controllate prima di
  estrarre (al massimo 50.000 voci, 50 MB per voce, 5 GB in tutto, rapporto
  di compressione al massimo 100:1), estrazione in memoria a blocchi con la
  dimensione reale ricontrollata, nomi delle voci mai usati come percorsi,
  ZIP annidati e voci cifrate rifiutati, CRC verificato (default del
  progetto); si estrae solo l'unica voce `.fit`.
- Pagina della mappa (fase 5b): **nessuna rete**, come la pagina dei
  grafici (intercettore che blocca tutto, CSP senza host esterni). Le tile le
  chiede a un proxy di tile in Python: la pagina carica
  `ponos://tiles/{z}/{x}/{y}`, il guscio valida z/x/y (interi nei limiti),
  scarica da `https://tile.openstreetmap.org` (dal 2026-11-09, ADR 010
  emendato; prima Tracestrack con chiave) con timeout, User-Agent con nome,
  versione e contatto dell'app e l'attribuzione "© OpenStreetMap
  contributors". Nessuna chiave. Cache su disco propria, con tetto
  esatto e rimozione delle tile meno usate di recente (LRU), default 4 GB,
  nella cartella cache del sistema (non nel profilo); comando per svuotarla;
  scadenza secondo gli header (7 giorni se mancano), richieste condizionali
  per le tile scadute, pausa di 300 s se il server rifiuta (403/429); solo le
  tile guardate, mai scaricamenti in massa. `QWebChannel` con pochi
  messaggi dalla pagina (area visibile, attività cliccata, punto del
  profilo), validati. Il server vede le aree consultate e può bloccare l'app
  senza preavviso: rischi dichiarati nel threat model e accettati
  dall'utente.

## 5. Fasi

### Fase 0 — Spike Garmin e scheletro

- [ ] Spike `experiments/garmin_spike.py`, **lanciato dall'utente nel proprio
      terminale** (Claude non inserisce password): login con MFA, token nel
      portachiavi, ultime 5 attività, conteggio di tutto lo storico a pagine
      (per stimare la durata della prima sincronizzazione), download di un
      FIT originale, lettura
      del tracciato FC, elenco (solo lettura) degli allenamenti programmati e
      verifica delle funzioni per crearli ed eliminarli. Risultato in
      `experiments/garmin_spike.md`, senza dati personali.
      *Stato 2026-11-05: script pronto e rivisto, da lanciare
      (`uv run --locked python experiments/garmin_spike.py`).*
- [x] `git init`, autore e committer MR nella configurazione del repository,
      `LICENSE` (GPLv3), `README.md`, `SECURITY.md`, `TIDY.md`, `.gitignore`,
      `.gitattributes`.
- [x] `pyproject.toml` (`src/`, `authors = ["MR"]`), `uv.lock`, `noxfile.py`
      con tutti i controlli, regole di `import-linter`.
- [x] CI su Windows e Ubuntu (permessi minimi, action fissate a SHA), che
      esegue esattamente `uv run --locked nox`.
      *Verificata il 2026-11-06 sulla PR #1: verde su `windows-latest` e
      `ubuntu-latest`, test POSIX compresi.*
- [x] ADR: 001 stack, 002 accesso a Garmin, 003 formato dei dati, 004 modello
      di carico. `docs/specifica.md`, `docs/threat-model.md`,
      `docs/dipendenze.md`.
- [x] Skill del progetto `.claude/skills/ponos-allenamento/` (modello di
      carico, PMC, periodizzazione, test, infortuni, sincronizzazione,
      formato dei dati).
- [x] Scheletro che gira end-to-end: la finestra si apre e mostra il PMC
      calcolato da un FIT di prova sintetico (test end-to-end con il motore
      web reale; interfaccia in italiano e inglese).

Agenti: `generatore-fixture`, `sviluppatore-nucleo`, `sviluppatore-interfaccia`,
`redattore-documenti`, `registro-dipendenze`, `verificatore`.

### Fase 1 — Profili, archivio e import FIT

- [x] Profili: creazione con scelta della cartella, apertura da cartella,
      cambio, rimozione dall'elenco, cancellazione della cartella con
      conferma; lock contro due istanze; avviso per cartelle di rete o
      sincronizzate.
- [x] Schema SQLite v1 e migrazioni; archivio FIT con scrittura durevole e
      SHA-256.
- [x] Lettore FIT con limiti, tipi di dominio (attività, sport, tracciato FC)
      e test di fuzzing.
- [x] (tolto in fase 8) Lettore GPX (sezione 2 punto 12, sezione 4.9) con lo stesso tipo
      d'errore e gli stessi tipi di dominio del lettore FIT; riconoscimento
      del formato dal contenuto; duplicati tra FIT e GPX.
- [x] Casi d'uso "Importa file" FIT (all'epoca anche GPX e ZIP, tolti in fase 8) (menu e trascinamento) e "Importa da
      cartella" (sottocartelle, duplicati saltati per `file_id`, riepilogo
      finale), tabella delle attività con ordinamento (i filtri sono della
      fase 5b).
- [x] Dal FIT anche distanza, dislivello positivo, tempo in movimento,
      punto di partenza e traccia semplificata, salvati nell'indice: la fase
      9 non dovrà rileggere l'archivio né migrare lo schema per averli.

*Stato 2026-11-06: fase completata sul ramo `fase-1-profili-archivio`
(PR #1); controlli verdi in locale e nella CI su Windows e Ubuntu, test
POSIX compresi; campagna di collaudo verde su Windows (esiti in
`docs/collaudo.md`).*

### Fase 2 — Sincronizzazione Garmin

- [x] Adattatore `garmin` dietro la porta `ActivitySource`, con test di
      contratto su risposte registrate e anonimizzate e su un fake.
      *Stato 2026-11-06: fixture sulla struttura reale registrata dallo
      spike del 2026-11-06 (valori inventati).*
- [x] Dialogo di login con MFA, token protetti dal sistema per profilo
      (DPAPI su Windows, Secret Service su Linux), logout.
- [x] Sincronizzazione riprendibile (sezione 4.4): prima tutto lo storico,
      poi solo le attività mancanti, "Verifica completa"; giornale, pause,
      backoff, annullamento, ripresa dopo chiusura (test che interrompe a
      metà, sia durante l'elenco sia durante i download).
- [x] (tolto in fase 8: ora "formato non supportato") Originali GPX scaricati da Garmin (attività caricate come GPX) letti
      come quelli importati.
- [x] Abbinamento con le attività già importate da file, senza duplicati né
      download inutili.
- [ ] Sincronizzazione all'avvio e con F5; notifica di sistema e barra di
      stato.
      *Stato 2026-11-06: fatto e provato con un notificatore finto; la
      notifica reale su Windows e Ubuntu e il login reale restano per il
      collaudo manuale.*

### Fase 3 — Carico e grafici

- [x] Lingua: inglese predefinito, menu Lingua con inglese e italiano,
      scelta ricordata.
- [x] Dati del profilo: altezza, peso e data di nascita alla creazione;
      "Modifica profilo" con misure datate (peso, FC minima, FC massima,
      soglia) e il loro storico; campi fissi in sola lettura. Altezza,
      peso e data di nascita sono dati personali: solo nel database del
      profilo, mai nei log.
- [x] Parametri fisiologici con data di validità inseriti dall'utente (FC
      minima e massima, sesso, soglia; soglia in bici = corsa − 4 bpm);
      campioni fuori limiti scartati e contati.
- [x] TRIMP e hrTSS, pesi degli sport, stima per attività senza FC; ricalcolo
      quando cambiano parametri o pesi. (Superata il 2026-11-10: vedi fase 8,
      "Carico in TRIMPeq".)
- [x] PMC (ATL, CTL, TSB) con property test.
- [x] Grafici Plotly in `QWebEngineView` isolato: PMC, carico settimanale per
      sport, zone, FC dell'attività; esportazione SVG e PNG. Zone FC sulla
      soglia secondo Friel, cinque zone (Z1 < 85%, Z2 85–89%, Z3 90–94%,
      Z4 95–99%, Z5 ≥ 100%: default del progetto, rivisto in fase 4).

*Stato 2026-11-06: completata sul ramo `fase-3-carico-e-grafici`; controlli e
collaudo verdi su Windows. I plurali inglesi usano frasi invarianti
("Activities imported: %n"), perché l'inglese è il testo sorgente senza un
catalogo proprio.*

### Fase 4 — Test di valutazione

- [x] Protocollo del test di soglia 30' (istruzioni nell'app), riconoscimento
      dell'attività del test (scelta dall'utente), calcolo della FC e del
      ritmo di soglia, zone FC e di ritmo.
- [x] Storico dei test e retest proposto ogni 6–8 settimane.

### Fase 4b — Alimentazione dei giorni passati

Decisioni dell'utente del 2026-11-07 (sezione 1). Valori e soglie sono default
del progetto da linee guida pubblicate (ISSN, ACSM, consenso IOC), in un solo
posto e documentati nella skill del progetto; non sono consigli medici.

- [x] **Dati**: calorie dell'attività dal FIT (`session.total_calories`) nel
      riepilogo e nell'indice (schema v4); stima quando mancano (GPX o FIT
      senza calorie): formula di Keytel sulla FC con età, peso e sesso, e se
      manca la FC un MET per sport. Le calorie del metabolismo di base durante
      l'attività si tolgono, per non contarle due volte.
- [x] **Profilo**: livello di attività fuori dallo sport con data di validità
      (sedentario, leggero, attivo, molto attivo); orari tipici di colazione,
      pranzo e cena; obiettivi alimentari per periodi nel calendario
      (dimagrimento con un ritmo in kg a settimana, mantenimento,
      performance), con annulla.
- [x] **Modello** (dominio puro, deterministico):
      - metabolismo di base con Mifflin-St Jeor (età dalla data di nascita,
        altezza, peso valido quel giorno, sesso); spesa del giorno = base ×
        fattore del livello fuori sport + calorie nette delle attività;
      - dimagrimento: deficit dal ritmo scelto, al massimo l'1% del peso a
        settimana (ACSM 2016), il 20% della spesa e 750 kcal al giorno, mai
        sotto il metabolismo di base né sotto 1.200 kcal (donne) o 1.500 kcal
        (uomini), mai con una disponibilità energetica sotto 30 kcal per kg di
        massa magra (stimata con la formula di Boer); deficit ridotto nei
        giorni di carico alto; nessuna indicazione numerica sotto i 18 anni,
        nessun deficit con IMC sotto 18,5 (avviso); performance: carboidrati
        secondo il carico;
      - macro: carboidrati in g/kg secondo il carico del giorno (riposo e
        leggero 3–5, moderato 5–7, alto 6–10, molto alto 8–12), proteine
        1,6 g/kg (2,0 in deficit), grassi il resto con un minimo del 20% delle
        kcal;
      - momenti: pre 1–4 h prima (carboidrati circa 1 g/kg se la seduta supera
        un'ora), durante oltre 75 minuti (30–60 g/h, oltre 2,5 h al massimo
        60 g/h perché la tabella non distingue le miscele glucosio-fruttosio;
        liquidi e sodio indicativi), post entro un'ora (proteine circa
        0,3 g/kg e carboidrati 1–1,2 g/kg se la seduta è lunga o ce n'è
        un'altra entro 8 h); il resto distribuito su colazione, pranzo e cena
        secondo gli orari tipici, con pasti spostati o fusi quando cadono
        vicino all'allenamento; due sedute nello stesso giorno gestite.
- [x] ~~**Alimenti**~~ (SUPERATO il 2026-11-10: tabella ed esempi tolti, i pasti mostrano solo calorie e macro): tabella curata di alimenti comuni con kcal e macro per
      100 g da una fonte con licenza compatibile (USDA FoodData Central, di
      pubblico dominio; dal 2026-11-09 ANSES-CIQUAL 2025, Etalab 2.0, per
      richiesta dell'utente: riferimenti europei, vedi ADR 008 e
      `docs/dipendenze.md`), nomi tradotti; 2–3 esempi per pasto con porzioni
      arrotondate.
- [x] **Calendario** dei giorni passati (vista mese, carico e attività per
      giorno): clic su un giorno → dettaglio con attività, carico e
      indicazione alimentare; base del calendario della fase 5.
- [x] Avviso fisso: indicazioni generali, non consigli medici o
      nutrizionali personalizzati; con patologie, gravidanza, disturbi
      alimentari o farmaci rivolgersi a un professionista.
- [x] Test T-NUT (sezione 7.12), collaudo, revisione, documenti (ADR della
      fase), traduzioni.

### Fase 5 — Obiettivi e piano adattivo

- [x] Obiettivi nel calendario (A/B/C, tempo obiettivo, CTL a una data), con
      annulla.
- [x] Disponibilità settimanale.
- [x] Scelta delle tipologie di seduta per sport; avviso quando un obiettivo
      ne richiede una esclusa.
- [x] Motore del piano (sezione 4.6) con test a scenari (un obiettivo, due A
      vicini, A + B, obiettivo irraggiungibile, sedute saltate, carico
      eccessivo).
- [x] Vista calendario: pianificato contro fatto; proiezione del PMC nel
      grafico.
- [x] Ricalcolo del piano dopo ogni sincronizzazione.
- [x] Alimentazione dei giorni futuri dal piano (ADR 011, T-NUT-07 e 08), con lo stesso modello della
      fase 4b; nessun deficit nella settimana di una gara A o B e nello
      scarico che la precede (ACSM 2016: dimagrire lontano dalle gare), con
      un avviso quando un periodo di dimagrimento si sovrappone.

### Fase 5b — Ricerca e mappa delle attività (stile Komoot)

Decisioni dell'utente del 2026-11-07 (sezione 1, riga "Ricerca e mappa").

- [x] ADR della mappa, dopo un prototipo misurato con 50.000 attività e le
      loro tracce: libreria (MapLibre GL JS, che disegna molte linee sulla
      GPU e raggruppa le partenze, oppure Leaflet con canvas e
      raggruppamento dei marcatori; scelta: Leaflet con
      leaflet.markercluster, nessuna GPU richiesta), sfondo raster Tracestrack Topo nella
      lingua dell'app (`topo_it`/`topo_en`, nomi locali dove manca; sostituito da
      OpenStreetMap il 2026-11-09, vedi fase 8), libreria
      inclusa nel pacchetto e servita dallo schema `ponos://`, tile servite
      dal proxy in Python (`ponos://tiles/...`), pagina senza rete (bozza in
      `docs/adr/010-mappa.md`). Aggiornati
      threat model, specifica e registro delle dipendenze (licenze della
      libreria e condizioni Tracestrack).
- [x] Dati: traccia semplificata (Douglas-Peucker con tolleranza per zoom) e
      riquadro di ogni attività nell'indice, rigenerabili dal FIT o dal GPX;
      serie della quota per il profilo; nessuna posizione nei log.
- [x] Impostazioni › Mappa: chiave Tracestrack (nel portachiavi, con verifica
      e messaggio chiaro se rifiutata), dimensione della cache (default
      4 GB), "Svuota cache"; senza chiave, senza rete o con il credito
      esaurito la mappa resta usabile su sfondo neutro con un avviso che dice
      perché e cosa fare. (Dal 2026-11-09 la chiave non c'è più: resta la
      dimensione della cache e "Svuota cache"; vedi fase 8.)
- [x] Filtri dell'elenco attività: data (da–a), durata, distanza, dislivello
      positivo, sport. Minimo e massimo facoltativi (vuoto = nessun limite),
      input tolleranti (virgola o punto, "1:30" o "90" per la durata),
      minimo e massimo invertiti scambiati. Un solo stato dei filtri,
      condiviso da tabella e mappa; l'app ricorda gli ultimi filtri.
- [x] Vista Mappa: tracce delle attività filtrate come linee colorate per
      sport (legenda, mai il solo colore); a zoom basso partenze raggruppate
      in cerchi con il numero, che si dividono o si uniscono a ogni livello;
      clic su un gruppo → zoom sull'area; clic su una traccia → selezione,
      traccia evidenziata e riga dell'elenco selezionata.
- [x] Elenco a destra delle attività visibili nell'area inquadrata (dopo i
      filtri), con conteggio e totali (km, dislivello, ore), aggiornato a
      ogni spostamento o zoom; selezione sincronizzata con la mappa; attività
      senza posizione (indoor, forza) contate a parte.
- [x] Dettaglio dell'attività selezionata: profilo altimetrico (quota sulla
      distanza) collegato alla traccia nei due sensi; traccia colorabile per
      zone FC con la legenda delle zone del giorno dell'attività.
- [x] Pagina della mappa isolata come quella dei grafici e senza alcuna rete
      (sezione 4.9); proxy delle tile in Python con timeout, chiave dal
      portachiavi e cache su disco LRU con tetto esatto.
- [x] Test T-FILT e T-MAP (sezione 7.11), T-SEC-14 e T-SEC-15; collaudo.

### Fase 6 — Infortuni e recupero

- [x] Questionario, segnali d'allarme, percorso a gradini, check-in
      giornaliero (sezione 4.7), con test per ogni regola di salita, discesa e
      stop.
- [x] Integrazione con il piano: zona colpita sospesa, alternative, rampa
      ridotta dopo il rientro.

### Fase 7 — Calendario Garmin

- [x] Porta `WorkoutCalendar`: invio degli allenamenti strutturati (zone FC,
      durate), a scelta dell'utente (piano intero, settimana, singola seduta).
- [x] Elenco degli allenamenti futuri programmati su Garmin ed eliminazione
      selettiva con conferma che mostra l'elenco e un bottone con il verbo;
      copia locale di ciò che si elimina, per poterlo ricreare.
- [x] Gli allenamenti creati dall'app sono riconoscibili (prefisso nel nome).

### Fase 8 — Rilascio

- [x] Solo file FIT (richiesta dell'utente del 2026-11-09, ADR 014): import
      solo di `.fit` (file e cartelle); tolti il lettore GPX, `GpxIdentity`,
      il formato `gpx`, i duplicati tra formati, l'import di ZIP scelti
      dall'utente e `defusedxml`; lo ZIP resta per l'originale di Garmin e
      per il backup. Schema v11: toglie le attività GPX (dopo una copia del
      database in `backup/`), stringe `file_format` e le chiavi di
      `threshold_test` a `fit`, svuota la cache dei carichi e riarma le voci
      del giornale; poi elimina i file GPX dall'archivio. Avviso una volta con il numero di attività tolte.
      Test: `tests/collaudo/test_fase8_solo_fit.py`.
- [x] Test di soglia nel piano, motore (richiesta dell'utente del 2026-11-09,
      ADR 015): il motore programma la seduta "Threshold test" nella prima
      settimana (versione 5 del metodo) con regole scritte, la ripropone una
      sola volta se salta, workout Garmin con nome e passi propri. Test:
      `T-PIANO-10` in `tests/planning/test_threshold_test_session.py`, oracoli
      di `tests/planning/invariants.py` e del collaudo del piano aggiornati.
- [x] Test di soglia nel piano, guscio e interfaccia: `RebuildPlan` passa
      `test_protocol` (impostazione dell'utente) e `missed_tests`
      (`missed_threshold_tests` sui giorni congelati e i test applicati); la
      finestra mostra la seduta "Threshold test" e `RetestScheduled`
      (`ui/plan_texts.py`). Fatto: test in
      `tests/profiles/test_rebuild_plan.py`
      (`test_the_plan_schedules_the_test_with_the_protocol_the_user_chose`,
      `test_a_planned_test_that_went_by_undone_is_told_to_the_engine`) e in
      `tests/ui/test_plan_presentation.py` (`RetestScheduled`); `mypy` passa.
- [x] Dislivello della gara e sedute in salita (richiesta dell'utente del
      2026-11-10, "quando aggiungi gara, aggiungi anche dislivello gara", ADR 017;
      l'utente ha scelto che il D+ cambi la stima del carico e faccia inserire
      sedute in salita): `RaceGoal.ascent_m` (0-30 000 m, pendenza media al
      massimo 50%), schema v16 (`goal.ascent_m`), distanza equivalente piana
      (distanza + 10 × D+) per tempo atteso, ritmo gara e verdetto, ripetute in
      salita e lunghi collinari per la gara A ripida (≥ 20 m/km) in costruzione e
      picco (versione 8 del metodo, nessun nuovo `SessionType`), workout Garmin
      "Hill repeats" e "Hilly long", campo, colonna e testi nell'interfaccia
      (italiano compreso). Test: `T-PIANO-11` in
      `tests/planning/test_race_ascent.py`, `tests/storage/test_race_ascent_store.py`.
      Aperti: i valori 7 km/h e 10 m al minuto di lavoro sono stime non
      validate; il calendario distingue le sedute in salita solo dal nome.
- [ ] Collaudo completo (sezione 7) superato sull'eseguibile, su Windows e
      Ubuntu, con esiti registrati in `docs/collaudo.md`.
- [x] Ubuntu di nuovo nelle matrici di `ci.yml` e `release.yml` (tolto il
      2026-11-09 su richiesta dell'utente, rimesso lo stesso giorno: eccezione 3
      di `CLAUDE.md` chiusa) e CI verde su Ubuntu. Fatto: matrici e crash di
      `test_tile_service` su Ubuntu (oggetto Qt distrutto da un worker,
      `ui/worker_handover.py`); CI verde su Windows e Ubuntu sul commit
      `a2092ae` (2026-11-09).
- [x] ~~Valori della tabella degli alimenti verificati~~ (SUPERATO il 2026-11-10: tabella tolta; la revisione a mano delle voci CIQUAL prima del rilascio non serve più) Valori della tabella degli alimenti verificati contro ANSES-CIQUAL 2025
      (file del 2025-11-03, Etalab 2.0) con `experiments/verify_foods.py
      --ciqual-dir <cartella> --apply` (`source.verified_on` = 2026-11-09, 42
      alimenti su 42 trovati); confronto con CREA solo come rapporto (licenza
      che non permette di copiare i valori), codici in
      `experiments/crea_mapping.json`. Tre alimenti sono approssimazioni
      dichiarate nella tabella (yogurt greco 0%, mozzarella parzialmente
      scremata, fagioli neri); la scelta di ogni voce CIQUAL è da rivedere a
      mano prima del rilascio (vedi `ciqual_note`). Il codice USDA sbagliato
      di circa dieci alimenti è superato dal cambio di fonte. Test:
      `tests/experiments/test_verify_foods.py`, `tests/nutrition/test_foods.py`.
- [x] ~~`nutrition/foods.json` incluso nel pacchetto PyInstaller.~~ (SUPERATO il 2026-11-10: file tolto dal pacchetto e dal controllo di avvio)
- [x] Backup e ripristino di un profilo in un file versionato, con test di
      ripristino.
- [x] PyInstaller con `.spec` versionato per Windows e Ubuntu, smoke test
      sull'eseguibile, `SHA256SUMS`, misura della dimensione del pacchetto.
- [ ] Controllo di conformità: licenze, intestazioni, autore dei commit,
      nessun dato personale né percorso locale nel pacchetto.
- [x] README con installazione, verifica dell'hash, avviso di SmartScreen,
      messaggio di responsabilità medica.
- [x] Richieste dell'utente dal collaudo a mano del 2026-11-09: zoom con la
      rotella e spostamento trascinando nei grafici della panoramica, con le
      date sincronizzate fra PMC e grafici settimanali; carico (hrTSS) di
      ogni zona nel dettaglio attività; rifornimento rapido in 2 apporti ogni
      2 ore, che si uniscono al pasto vicino, al posto di 4 apporti orari;
      una finestra per ogni attività e per ogni giorno del calendario; dati
      dell'attività accanto all'altimetria della mappa; riga dei grassi non
      più tagliata; nome del profilo modificabile; obiettivi e infortuni dal
      menu del giorno nel calendario (sezioni tolte dalla barra laterale);
      impostazioni riunite in File → Settings.
- [x] Sfondo della mappa da OpenStreetMap, senza chiave (richiesta
      dell'utente del 2026-11-09, commit `6ca8b68`, ADR 010 emendato): host
      `tile.openstreetmap.org`; tolti la chiave Tracestrack, il suo
      portachiavi e i messaggi relativi. Regole d'uso rispettate:
      User-Agent con nome, versione e contatto, attribuzione "© OpenStreetMap
      contributors" su entrambe le mappe, cache secondo gli header (7 giorni
      se mancano), richieste condizionali (`ETag`, `Last-Modified`) per le
      tile scadute, niente prefetch né scaricamenti in blocco, pausa di 300 s
      se il server rifiuta (403/429). Cache delle tile schema 2 (senza stile,
      con validatori; il file v1 si ricostruisce vuoto). Rischi accettati
      (threat model): il server può bloccare l'app senza preavviso, nessuna
      garanzia di servizio, URL fisso nel codice. Resta da fare prima del
      rilascio: rileggere le regole d'uso di OpenStreetMap.
- [x] Impostazioni di allenamento: giorni disponibili (caselle), sedute a
      settimana, giorno del lungo, ore massime a settimana; niente più minuti
      per giorno (richiesta dell'utente del 2026-11-09, commit `58409cc`,
      ADR 016). Giorni di seduta: prima il lungo, poi ogni volta il giorno
      disponibile più lontano da quelli scelti (settimana circolare, a parità
      il più basso); tetto di un giorno = massimo del tipo di seduta
      (`DAY_MAX_MINUTES` = 240); il lungo è sempre la seduta più lunga della
      settimana (facile e recupero al massimo il 75%). `METHOD_VERSION` 6.
      Schema 12 con migrazione da 11 (copia in `backup/`).
- [x] Altre richieste dell'utente del 2026-11-09, ognuna nel suo commit:
      corsa negli allenamenti Garmin con il passo come obiettivo principale e
      la FC come secondario quando c'è un intervallo di passo, e l'app elenca
      il passo prima della FC (`9a14d4a`, ADR 013 emendato); bottone "Save
      changes" (Ctrl+S) sempre visibile in Impostazioni, che salva la pagina in
      vista (`aa34231`); clic su un'attività nella finestra del giorno ne apre
      la finestra o la porta in primo piano (`f26cabf`); grafico del carico
      settimanale con una serie per sport, senza "Other" che raggruppa
      (`b94d67a`); calendario senza il bottone Today, restano Ctrl+T e il menu
      del giorno (`2a52928`).
- [x] Finestra a quattro riquadri e interfaccia della panoramica e della
      mappa (richieste dell'utente del 2026-11-09): quattro riquadri
      ridimensionabili senza barra laterale, Panoramica in alto a sinistra,
      Calendario in basso a sinistra, elenco Attività in alto a destra, Mappa
      in basso a destra, Ctrl+1..4 per i riquadri (menu View tolto il
      2026-11-10, eccezione 4 di `CLAUDE.md`), niente menu Calendar,
      bottoni sotto il pannello del giorno tolti (`2836d43`, `ui/main_panes.py`);
      Panoramica con un grafico alla volta scelto da un elenco, mappa con
      altezza minima, tolto il bottone "Activity detail...", più spazio al
      calendario (`b1343b0`); sulla mappa solo la traccia dell'attività
      selezionata, doppio clic nell'elenco che apre la finestra e centra la
      mappa, partenze più grandi da vicino, partenze nello stesso punto a
      ventaglio e cliccabili (`79180af`, `de89260`); Forma (TSB) nello stesso
      grafico del carico con un solo asse y, trascinamento col tasto destro per
      allungare il grafico, legenda del calendario compatta (`58606f0`); cache
      su disco della mappa chiusa alla chiusura della finestra (`a2092ae`).
- [x] Passo soglia nelle impostazioni del profilo (richiesta dell'utente del
      2026-11-10, merge `1de5db0`): serie datata a sé (`threshold_pace`,
      schema 14), campo `m:ss` tollerante accanto alla soglia FC, colonna nello
      storico; per il piano vale il più recente per data fra misura manuale e
      ultimo test con passo, a pari giorno la manuale. Test:
      `tests/profiles/test_threshold_pace.py`, `test_measurements.py`,
      `test_rebuild_plan.py`, `tests/storage/test_threshold_pace_store.py`,
      `tests/ui/test_measurements_form.py`.
- [x] Carico in TRIMPeq (richiesta dell'utente del 2026-11-10, commit
      `9ef5622`, merge `297c478`, testi italiani `fe6527a`; ADR 004 emendato):
      un'unica misura, Banister sull'intensità equivalente di corsa
      (`HRr_eq = min(1, HRr · HRr_T(corsa) / HRr_T(sport))`), scala "100 = un'ora
      alla soglia del proprio sport"; tolti hrTSS e i pesi per sport (calcolo,
      impostazioni e Ctrl+Shift+W, casi d'uso, porte); la tabella `sport_weights`
      resta nel database, non più letta né scritta (la toglierà una migrazione
      futura); `load_cache.hr_tss` = 0; `LOAD_MODEL_VERSION` 3 (ricalcolo da zero,
      T-CALC-05); `METHOD_VERSION` del piano 7, soglie del piano invariate;
      etichette "Load (TRIMPeq)", "TRIMPeq per day", "TRIMPeq per week". Effetti:
      forza e sedute alternative senza moltiplicatore, bici sulla propria
      soglia (valore congelato di `test_fase4_distanza`: 40,1935, prima 32,18).
      Test: `tests/load/` (T-CALC-01…07 aggiornati),
      `tests/planning/test_trimp_eq_equivalence.py` (11 scenari di sola corsa
      contro il golden `tests/planning/golden/plans_hrtss.json`),
      `tests/collaudo/test_fase4_distanza.py`, `tests/collaudo/test_fase3_carico.py`.
- [x] Nuovo profilo con soglie FC di ciclismo e altre attività e passo soglia,
      facoltativi, con gli stessi componenti e controlli di Edit profile;
      creazione atomica (richiesta dell'utente del 2026-11-10, `6046260`).
      Test: `tests/ui/test_new_profile_thresholds.py`,
      `tests/ui/test_new_profile_form_thresholds.py`,
      `tests/profiles/test_create_profile_thresholds.py`.
- [x] Doppio clic su un'attività nella mappa (partenza o traccia selezionata)
      apre la sua finestra; altrove ingrandisce (richiesta dell'utente del
      2026-11-10). Test: `tests/ui/test_map_page_web.py`.
- [x] Pasti senza esempi di alimenti, solo calorie e macronutrienti (richiesta
      dell'utente del 2026-11-10): tolti dalla finestra del giorno gli esempi e
      la nota sui valori non verificati.
- [x] Tolto tutto ciò che serviva solo agli esempi (2026-11-10, conferma
      dell'utente): `suggest_examples`, `Food`/`FoodExample`/porzioni,
      `MealSuggestion` (il rapporto del giorno ha solo `nutrition.meals`),
      `foods.json`, `food_table.py`, `experiments/verify_foods.py`, nomi degli
      alimenti da tradurre, controllo di avvio della tabella, voce del pacchetto
      PyInstaller, attribuzione ANSES del README; ADR 008 emendato. Le quantità
      per pasto non cambiano.
- [x] Mappa 3/5 e profilo altimetrico 2/5 del riquadro (richiesta dell'utente
      del 2026-11-10, `ab94cb2`).
- [x] Sotto il calendario solo la legenda (richiesta dell'utente del
      2026-11-10): tolto il pannello del giorno; il dettaglio completo del
      giorno è nel tooltip della cella, modifica e rimozione degli obiettivi
      dal menu del giorno. Test: `tests/ui/test_calendar_plan.py`,
      `tests/ui/test_pane_contents.py`.
- [x] Finestra del giorno con la seduta programmata completa (fase, blocchi,
      carico stimato), come il pannello del calendario (richiesta dell'utente
      del 2026-11-10, `db698a1`; caso d'uso `PlannedDay`).
- [x] Soglie FC per corsa, ciclismo e altre attività nel profilo (richiesta
      dell'utente del 2026-11-10): misure datate; bici di default corsa − 4,
      altre attività di default la corsa; `threshold_for_sport` unico punto da
      cambiare; migrazione dello schema. Fatto (`fc09bb6`, `a335b5b`): gruppi
      `SportGroup` corsa/trail, ciclismo, altre (camminata, escursionismo,
      forza, altro); soglie facoltative nella stessa serie datata della
      fisiologia (`bike_threshold_bpm`, `other_threshold_bpm`, NULL = default,
      migrazione 15 `SPORT_THRESHOLDS_MIGRATION`); entrano nell'impronta del
      carico solo se diverse dal default, così chi non le imposta non ricalcola
      nulla; il test dei 30 minuti aggiorna solo la soglia di corsa. Test:
      `tests/{load,profiles,storage,planning}/test_sport_thresholds*.py`,
      `tests/ui/test_sport_thresholds_form.py`.
- [x] Sincronizzazione: una voce che Garmin non scarica mai (errore 5xx o
      nessuna risposta a ogni giro, mentre l'elenco risponde) resta in attesa
      per sempre e ferma ogni giro dopo 3 voci; serve un contatore di giri
      senza risposta per voce (nuova colonna, schema 13) che dopo N giri la
      segni come fallita, senza consumare tentativi nei guasti veri di Garmin.
      Fatto (`4ba1cc0`): la voce conta un giro senza risposta
      (`unanswered_rounds`, schema 13) solo se Garmin risponde ad altro nel giro
      o a una sonda (prima pagina dell'elenco); al terzo giro
      (`SyncPolicy.max_unanswered_rounds`) diventa `FAILED` con
      `REMOTE_NOT_PROVIDED` e non ferma più i giri; 429 e rete assente non
      consumano nulla; "Verifica completa" la riarma. Test:
      `tests/activities/test_sync_never_provided.py`,
      `tests/collaudo/test_fase8_migrazione_v13.py`.
      Caso del 2026-11-09: 30 attività del 2012-2013, sistemate a mano nel
      profilo dell'utente.

## 6. Squadra di agenti e modelli

La sessione principale (Opus) fa domande, piano, orchestrazione e la
revisione finale di ogni fase. Lo sviluppo e i controlli vanno agli agenti di
`.claude/agents/`, con modelli più economici:

| Agente | Modello | Compito |
|---|---|---|
| `sviluppatore-nucleo` | sonnet | dominio, casi d'uso, adattatori (carico, piano, infortuni, sincronizzazione, archivio) |
| `sviluppatore-interfaccia` | sonnet | finestra Qt, modelli di presentazione, tabelle, grafici Plotly, dialoghi |
| `generatore-fixture` | sonnet | FIT sintetici, risposte Garmin anonimizzate, scenari per il piano |
| `collaudatore` | sonnet | test approfonditi della sezione 7 (end-to-end, robustezza, sicurezza, fuzzing), scritti da chi non ha scritto il codice; non tocca `src/` |
| `revisore-codice` | sonnet | revisione di un diff rispetto al piano e alle skill; sola lettura |
| `redattore-documenti` | sonnet | README, `docs/`, ADR, `CLAUDE.md`, skill del progetto |
| `verificatore` | haiku | esegue `uv run --locked nox` e riassume gli errori; sola lettura |
| `riparatore-lint` | haiku | correzioni meccaniche di ruff e mypy |
| `traduttore-qt` | haiku | allinea le traduzioni italiano e inglese |
| `controllore-conformita` | haiku | licenze, intestazioni, autore, dati personali; sola lettura |
| `registro-dipendenze` | haiku | `docs/dipendenze.md`, licenze, `pip-audit` |

Orchestrazione: in parallelo solo su file che non si sovrappongono, ogni
lavoro parallelo sul codice nel suo worktree; dopo ogni modifica
`verificatore`; a fine fase `revisore-codice`, poi la revisione della
sessione principale. Nessun agente fa `git push`.

## 7. Piano di test

Ogni test ha un identificativo, così la matrice minaccia → test del threat
model e gli esiti del collaudo possono citarlo.

### 7.1 Principi

- Ogni funzione nasce con un test d'accettazione che fallisce, scritto nel
  linguaggio del dominio e avviato dalla stessa funzione d'avvio della
  produzione. I test di una funzione fanno parte della fase in cui nasce: una
  voce di fase non si spunta senza i suoi test.
- Test deterministici: orologio, casualità e rete iniettati; niente `sleep`;
  si attende una condizione con un timeout definito in un solo posto.
- SQLite e file reali (in cartelle temporanee) nei test d'integrazione;
  Garmin sostituito da un fake verificato da test di contratto, eseguiti sia
  sul fake sia sulle risposte registrate e anonimizzate.
- Per ogni test positivo un test negativo con input ostile; ogni controllo ha
  un test che ne verifica il rifiuto; ogni minaccia del threat model ha
  almeno un test.
- Ogni bug trovato diventa prima un test che fallisce.
- I test approfonditi li scrive `collaudatore`, non chi ha scritto il
  codice.

### 7.2 Livelli ed esecuzione

| Livello | Cosa | Strumenti | Quando |
|---|---|---|---|
| 1. Dominio | formule, PMC, regole del piano e degli infortuni | pytest, hypothesis | ogni modifica (`uv run --locked nox`) |
| 2. Casi d'uso | con fake in memoria e SQLite reale temporaneo | pytest | ogni modifica |
| 3. Adattatori | Garmin (fake e registrazioni), FIT, SQLite, portachiavi, notifiche | pytest | ogni modifica |
| 4. Interfaccia | modelli di presentazione e viste | pytest-qt `offscreen` | ogni modifica |
| 5. End-to-end | un percorso per funzionalità, app avviata dal Composition Root | pytest-qt | ogni modifica |
| 6. Eseguibile | smoke test del pacchetto PyInstaller | `uv run --locked nox -s dist` | prima del rilascio |
| 7. Campagna lunga | fuzzing esteso, mutazione, prestazioni, sicurezza | `uv run --locked nox -s collaudo` | CI pianificata ogni settimana e prima del rilascio |

### 7.3 Calcolo del carico (T-CALC)

- **T-CALC-01** Valori calcolati a mano: 60 minuti a FC costante pari alla
  soglia danno carico 100 (dal 2026-11-10 TRIMPeq, alla soglia del proprio
  sport; prima hrTSS); FC uguale alla minima dà 0.
- **T-CALC-02** Property test: implementazione vettoriale uguale a quella
  ingenua, con tolleranza dichiarata, su tracciati casuali.
- **T-CALC-03** Invarianti: carico ≥ 0; crescente con durata e FC; un
  campione fuori limiti non cambia il risultato; peso 0 → carico 0.
- **T-CALC-04** PMC: risposta a un singolo giorno di carico (decadimento con
  costanti 7 e 42), carico costante L → CTL tende a L, TSB del mattino
  calcolato con i valori del giorno prima.
- **T-CALC-05** Parametri con data di validità: attività a cavallo di un
  cambio; ricalcolo dopo una modifica uguale al calcolo da zero.
- **T-CALC-06** Soglia in bici = soglia corsa − 4 bpm; attività senza FC o
  con più del 30% di campioni scartati → stima marcata.
- **T-CALC-07** Casi limite: attività di 1 s e di 24 h, FC piatta, buchi
  oltre 10 s, attività a cavallo della mezzanotte, cambio dell'ora legale,
  viaggi in un altro fuso.

### 7.4 Piano, test di soglia e infortuni (T-PIANO, T-INF)

- **T-PIANO-01** Determinismo: stessi ingressi → piano identico byte per
  byte una volta serializzato; l'ordine di inserimento degli obiettivi non
  conta.
- **T-PIANO-02** Vincoli sempre rispettati, con property test su ingressi
  casuali (anche con infortuni e malattie aperti: rampa +3, solo tipologie
  scelte, nessuna seduta con un segnale d'allarme): rampa del CTL entro il massimo, ore entro la disponibilità, solo
  giorni disponibili, solo tipologie scelte, al massimo 3 sedute intense a
  settimana, mai due dure di seguito, scarico prima di ogni obiettivo A e B.
- **T-PIANO-03** Scenari: un obiettivo A; due A a 3 settimane; A + B + C;
  obiettivo irraggiungibile (avviso, nessuna rampa oltre il limite);
  obiettivo oggi o nel passato; nessuna disponibilità; tipologia necessaria
  esclusa (avviso); partenza da CTL 0 e da CTL 100.
- **T-PIANO-04** Adattamento: sedute saltate (nessun recupero oltre la
  rampa), carico eccessivo (TSB sotto −30 → recupero), attività non
  pianificate, attività vecchie arrivate tardi che cambiano il passato.
- **T-PIANO-05** Tempo obiettivo: classificazione realistico, ambizioso o
  irraggiungibile, verificata ai confini.
- **T-PIANO-10** Test di soglia nel piano (ADR 015): piano senza test recente
  (nessun test, finestra aperta o passata) → test del protocollo scelto nella
  prima settimana, in un giorno non accanto al lungo né a un giorno duro; con un
  test recente nessuno; gara vicina → mai nelle 72 ore prima, spostato dopo o assente;
  infortunio o malattia aperti, o rientro recente → assente; un test saltato torna
  una volta la settimana dopo, due volte saltato mai più; seduta dura (limiti
  settimanali, carico nella rampa e nella proiezione); determinismo; nome e
  passi del workout Garmin; piano salvato che si rilegge.
- **T-PIANO-11** Dislivello della gara (ADR 017, `tests/planning/test_race_ascent.py`):
  distanza equivalente (100 m di D+ = 1 km) e tempo atteso, carico, ritmo gara e ritmo
  predefinito calcolati su di essa; soglia di pendenza ai confini (199 e 200 m in 10 km, cioè
  19,9 e 20 m/km); salite solo in costruzione e picco, mai in una settimana di scarico, in base
  o nello scarico finale; una ripetuta in salita a settimana, senza ritmo, in zona alta; ogni
  lungo di una settimana di carico collinare; nessuna salita per una B accanto a una A piana,
  per una gara che non è di corsa, con un infortunio che limita corsa o intensità (tornano
  all'ultimo gradino); determinismo; property test: gli invarianti di `invariants.py` valgono
  con D+ casuale.
- **T-PIANO-06** Test di soglia: FC di soglia = media degli ultimi 20' su
  tracciati sintetici; test interrotto o troppo corto rifiutato; retest
  proposto tra 6 e 8 settimane dopo l'ultimo test (dalla fase 5 anche in una
  settimana di scarico del piano).
- **T-INF-01** Ogni segnale d'allarme, uno per test, ferma il piano della
  zona e mostra l'invito al medico.
- **T-INF-02** Percorso a gradini: si sale con dolore ≤ 3 e mattino normale,
  si scende se peggiora, nessun salto di gradino, check-in mancante = nessuna
  salita.
- **T-INF-03** Integrazione con il piano: zona colpita sospesa, attività
  alternative senza dolore che mantengono il carico, rampa ridotta dopo il
  rientro.

### 7.5 Dati, sincronizzazione e import (T-DATI, T-SYNC, T-IMP)

- **T-DATI-01** Migrazioni: un database di prova per ogni versione dello
  schema, portato all'ultima; campi sconosciuti conservati.
- **T-DATI-02** Interruzione in ogni punto della scrittura di un FIT (prima
  del flush, dopo il flush, prima della rinomina, prima del commit), con un
  adattatore del file system che fallisce all'N-esima operazione: all'avvio
  nessun record senza file e nessun temporaneo orfano.
- **T-DATI-03** Integrità: FIT dell'archivio alterato → segnalato, mai usato
  in silenzio.
- **T-DATI-04** Lock: seconda istanza sullo stesso profilo rifiutata con un
  messaggio; lock rimasto da un crash riconosciuto e superato.
- **T-DATI-05** Backup e ripristino in una cartella nuova: profilo identico
  (attività, parametri, obiettivi, piano, infortuni).
- **T-DATI-06** Creazione del profilo interrotta in ogni passo, anche in gara
  con un'altra istanza sulla stessa cartella: si annulla solo ciò che ha creato
  quell'istanza, mai il profilo dell'altra.
- **T-SYNC-01** Prima sincronizzazione completa e poi incrementale, con il
  fake di Garmin e uno storico di 5.000 attività.
- **T-SYNC-02** Processo terminato davvero (kill di un sottoprocesso) durante
  l'elenco e durante i download, in più punti: al riavvio nessun duplicato e
  nessuna attività persa.
- **T-SYNC-03** 429, errori 5xx, timeout, risposte troncate o malformate:
  backoff con tetto, numero massimo di tentativi, errore comprensibile,
  nessun ciclo infinito (orologio finto).
- **T-SYNC-04** Token scaduto o revocato → dialogo di login; MFA richiesto;
  un login fallito non salva nulla.
- **T-SYNC-05** Attività già importate da file: nessun download e nessun
  duplicato; "Verifica completa" trova le attività caricate in ritardo.
- **T-SYNC-06** Annulla: la sincronizzazione si ferma entro 1 s e lascia lo
  stato coerente.
- **T-IMP-01** Albero con sottocartelle, duplicati, file non FIT e FIT
  rovinati: riepilogo esatto, file per file.
- **T-IMP-02** Tetti: 50.000 file di attività, 500.000 voci e 32 livelli, ciascuno
  provato al limite e un passo oltre (nessun import, messaggio); file oltre
  50 MB saltato e segnalato.
- **T-IMP-03** Idempotenza: import ripetuto → nessun cambiamento.
- **T-IMP-04** Solo FIT: una cartella ignora GPX, ZIP e altri file senza
  segnalarli; scelti a mano GPX e ZIP si segnalano come formato non
  supportato; l'estensione si confronta senza badare alle maiuscole.
- **T-GPX-01, T-GPX-02** (GPX validi, duplicati tra FIT e GPX): tolti in
  fase 8 insieme al formato.
- **T-DATI-01 (fase 8)** Migrazione v11: un profilo v10 con attività FIT e GPX e
  i loro file diventa un profilo di sole attività FIT (copia del database,
  file GPX tolti dopo la conferma, giornale riarmato, cache svuotata,
  conteggio giusto, test di soglia delle attività tolte tolti); la migrazione uccisa a ogni istruzione lascia v10 e i
  file intatti e riprende; un percorso fuori dalla forma dell'archivio non si
  elimina mai.

### 7.6 Interfaccia (T-UI)

- **T-UI-01** Un end-to-end per funzionalità dall'avvio reale: primo avvio
  guidato, nuovo profilo, import da cartella, sincronizzazione (fake),
  obiettivo → piano → calendario, test di soglia, infortunio → percorso.
- **T-UI-02** Il thread della UI non si blocca: durante sincronizzazione e
  import un evento di input si elabora entro 100 ms.
- **T-UI-03** Annulla e ripeti per obiettivi e calendario; conferma con
  attrito solo per l'irreversibile (cancellazione della cartella,
  eliminazione di allenamenti su Garmin).
- **T-UI-04** Grafici: le figure contengono le serie attese, con assi e
  unità; esportazione SVG e PNG produce file validi; temi chiaro e scuro.
- **T-UI-05** Accessibilità: contrasto dei token ≥ 4,5:1 per il testo e
  ≥ 3:1 per bordi e serie dei grafici in entrambi i temi; tutto raggiungibile
  da tastiera; ogni controllo con un nome accessibile.
- **T-UI-06** Traduzioni: italiano e inglese con gli stessi testi, nessuna
  voce non tradotta, plurali corretti, layout che regge testi lunghi.
- **T-UI-07** L'app ricorda finestra, pannelli, ultimo profilo, ultima
  cartella di import.
- **T-UI-08** Gestore unico degli errori: un'eccezione imprevista mostra un
  messaggio generico e registra il dettaglio.

### 7.7 Sicurezza (T-SEC)

- **T-SEC-01** Fuzzing del lettore FIT: `hypothesis` a ogni modifica e una
  campagna lunga con un fuzzer guidato dalla copertura, con limiti di tempo e
  memoria. Oracoli oltre il crash: nessuna eccezione non gestita, tempo e
  memoria sotto il tetto. Ogni input che fa fallire si riduce e diventa un
  test normale.
- **T-SEC-02** FIT ostili: dimensioni dichiarate enormi, record senza fine,
  file oltre 50 MB, estensione `.fit` con altro contenuto → rifiutati prima
  di allocare. XML (GPX con DTD, entità, annidamento) e ZIP ostili scelti
  dall'utente: mai aperti (una cartella li ignora, scelti a mano → formato
  non supportato), nessuna connessione, nessuna scrittura fuori
  dall'archivio. ZIP ostili scaricati da Garmin (bomba, voci, nomi, cifrati,
  annidati): rifiutati dall'adattatore ZIP (`tests/zipread`).
- **T-SEC-03** Risposte Garmin ostili: JSON enormi o annidati in profondità,
  tipi sbagliati, id non numerici o con percorsi (`../`), nomi di attività
  con HTML o script → rifiutati o trattati come testo.
- **T-SEC-04** Percorsi: nessun dato esterno entra nei nomi dei file
  dell'archivio (li genera il programma); nell'import da cartella symlink,
  junction e reparse point verso l'esterno, nomi riservati di Windows e
  percorsi lunghi → ignorati e segnalati.
- **T-SEC-05** SQL: nomi e note con apici e frammenti SQL salvati e riletti
  identici; controllo statico che vieta SQL composto con f-string o
  concatenazione.
- **T-SEC-06** Pagina dei grafici: una richiesta di rete e un'immagine remota
  tentate dalla pagina vengono bloccate; un nome di attività con `<script>`
  appare come testo; nessun `QWebChannel` esposto.
- **T-SEC-07** Segreti: con un token segnaposto noto, dopo login,
  sincronizzazione, crash e chiusura il token non compare nella cartella del
  profilo, nella configurazione, nei log, nei temporanei né nei rapporti
  d'errore; il logout lo cancella dal portachiavi.
- **T-SEC-08** Log: nessun dato di salute (dolore, infortuni, FC), nessuna
  password né input non controllato; caratteri di controllo neutralizzati.
- **T-SEC-09** Cartella del profilo: file creati in modo esclusivo, permessi
  solo per l'utente (descrittore esplicito su Windows, 0700/0600 su Ubuntu),
  cartella di rete o sincronizzata segnalata.
- **T-SEC-10** Calendario Garmin (fase 7): l'eliminazione tocca solo gli
  allenamenti futuri scelti, dopo la conferma e dopo averne salvato una
  copia locale.
- **T-SEC-11** Analisi statica in CI: regole di sicurezza di ruff, `mypy
  --strict`, ricerca di segreti nel repository, `pip-audit` bloccante per
  vulnerabilità sfruttate o critiche, licenze ammesse.
- **T-SEC-12** Pacchetto: nessun percorso locale né nome utente negli
  eseguibili, nessun file di sviluppo, hash uguali a `SHA256SUMS`; su
  Windows DLL caricate solo dalla cartella dell'app e di sistema.
- **T-SEC-13** Revisione manuale finale: threat model riletto con un
  giudizio scritto, percorso dei dati non fidati (FIT, JSON, cartelle)
  seguito a mano nel codice, rischi accettati riconfermati.
- **T-SEC-14** Canale della mappa (fase 5b): messaggi malformati (coordinate
  non numeriche o fuori intervallo, payload enormi, id inesistenti)
  rifiutati senza effetti; la pagina non può chiamare altro.
- **T-SEC-15** Rete della mappa (fase 5b): la pagina non fa nessuna richiesta
  di rete (tutto tranne `ponos://` bloccato); il proxy chiama solo
  `https://tile.openstreetmap.org` (altri host, HTTP e z/x/y fuori limite
  rifiutati, timeout rispettati, User-Agent presente, validatori anomali
  scartati); nessuna posizione GPS nei log né in file fuori dalle cartelle
  previste. (Fino al 2026-11-09 il test controllava anche che la chiave
  Tracestrack non finisse in pagina, log, profilo o cache: la chiave non c'è
  più.)

### 7.8 Prestazioni e durata (T-PERF)

Budget iniziali, da confermare con le prime misure della fase 3; si
riportano p50, p95 e massimo, mai la sola media, con l'ambiente di misura.

- **T-PERF-01** Profilo sintetico di 10 anni (5.000 attività): finestra
  usabile entro 2 s dall'avvio, ricalcolo completo del PMC dai carichi in
  cache < 1 s (il ricalcolo da zero dopo un cambio di parametri rilegge
  tutti i file: lavoro lungo con avanzamento e Interrompi), ricalcolo
  del piano < 0,5 s, grafico PMC interattivo < 0,4 s, import di 1.000 FIT
  < 2 minuti.
- **T-PERF-02** Memoria sotto un tetto dichiarato durante l'import al limite
  dei 50.000 file.
- **T-PERF-03** Durata: 100 sincronizzazioni di seguito con il fake, senza
  crescita di memoria, file temporanei o handle aperti.
- **T-PERF-04** Regressioni: la CI pianificata segnala un peggioramento oltre
  il rumore misurato.

### 7.9 Qualità dei test (T-QUAL)

- **T-QUAL-01** Test di mutazione su `load`, `planning`, `injury` e sul
  lettore FIT: almeno l'85% dei mutanti uccisi, ogni sopravvissuto
  analizzato.
- **T-QUAL-02** Passata sistematica su ogni tipo di dominio (FC, pesi, date,
  durate, tetti): partizioni dalla specifica e confini con il valore al
  limite e appena oltre.
- **T-QUAL-03** Copertura dei rami usata come allarme: ogni ramo scoperto del
  dominio analizzato uno per uno.

### 7.10 Collaudo manuale prima del rilascio

- **T-MAN-01** Su Windows e Ubuntu puliti, dall'eseguibile: primo avvio,
  login reale con MFA (lo fa l'utente), prima sincronizzazione reale, import
  da una cartella reale, piano verso un obiettivo reale, invio e
  cancellazione di un allenamento sul calendario Garmin. Esiti con data in
  `docs/collaudo.md`.
- **T-MAN-02** Test d'usabilità leggero con 2–3 persone e compiti precisi
  (creare un profilo, aggiungere una gara, leggere la forma di oggi,
  descrivere un infortunio); problemi registrati e corretti prima del
  rilascio.

### 7.11 Ricerca e mappa (T-FILT, T-MAP, fase 5b)

- **T-FILT-01** Ogni filtro da solo: confini inclusi e valore appena fuori;
  filtro vuoto = tutte le attività.
- **T-FILT-02** Filtri combinati: property test contro un filtro ingenuo
  scritto nel test.
- **T-FILT-03** Input tolleranti: virgola e punto, durate "1:30" e "90";
  minimo maggiore del massimo scambiati; valori negativi rifiutati con un
  messaggio.
- **T-FILT-04** Stato condiviso: lo stesso filtro dà gli stessi risultati in
  tabella e mappa, ed è ricordato alla riapertura.
- **T-MAP-01** Raggruppamento: a ogni livello di zoom la somma dei numeri dei
  gruppi visibili è uguale al numero di attività con partenza nell'area.
- **T-MAP-02** Elenco a destra = attività filtrate con partenza nell'area
  inquadrata (property test su aree e storici casuali), con il conteggio
  giusto.
- **T-MAP-03** Casi di confine: area a cavallo dell'antimeridiano, zoom
  minimo e massimo, molte attività nello stesso punto, nessuna attività.
- **T-MAP-04** Attività senza posizione escluse dalla mappa e contate a
  parte.
- **T-MAP-05** Prestazioni: con 50.000 attività, spostamento o zoom con
  elenco aggiornato entro 0,4 s (p95).
- **T-MAP-06** Selezione sincronizzata: clic nell'elenco → attività
  evidenziata e traccia sulla mappa; clic sulla mappa → riga selezionata.
- **T-MAP-07** Tracce: semplificazione con errore entro la tolleranza del
  livello di zoom (property test contro la traccia piena), tracce spezzate
  dai buchi del GPS, attività a cavallo dell'antimeridiano; con 50.000
  tracce il primo disegno entro 2 s e lo spostamento entro 0,4 s (p95).
- **T-MAP-08** Profilo altimetrico: punto del profilo e punto sulla mappa
  coincidono nei due sensi; attività senza quota → profilo assente con il
  motivo.
- **T-MAP-09** Traccia per zone FC: zona di ogni tratto uguale a quella del
  calcolo del carico per lo stesso campione; legenda e motivo oltre al
  colore.
- **T-MAP-10** Ripiego: senza chiave, con chiave rifiutata, senza rete e con
  credito esaurito (risposte simulate) → sfondo neutro, tracce visibili,
  avviso con il rimedio; nessun ritentativo senza limite.
- **T-MAP-11** Cache: il tetto scelto non si supera, le tile scadute si
  rinnovano, "Svuota cache" la porta a zero; nessun prefetch fuori dall'area
  guardata.

### 7.12 Alimentazione (T-NUT, fase 4b)

- **T-NUT-01** Metabolismo di base, spesa del giorno, deficit e macro contro
  valori calcolati a mano, per uomo e donna, ai confini di età, peso e
  livello di attività.
- **T-NUT-02** Limiti di sicurezza: deficit mai oltre il 20% né 750 kcal,
  mai sotto il metabolismo di base; nessun deficit sotto i 18 anni o con IMC
  sotto 18,5, con l'avviso; property test su profili e giorni casuali.
- **T-NUT-03** Calorie delle attività: dal FIT quando ci sono, stima di
  Keytel e MET quando mancano; il metabolismo di base durante l'attività non
  si conta due volte.
- **T-NUT-04** Momenti: allenamento all'alba prima della colazione, a pranzo,
  la sera tardi, due sedute in un giorno, seduta lunga con il durante;
  nessun pasto con quantità negative; totale del giorno uguale alla somma
  dei pasti.
- **T-NUT-05** Determinismo: stessi dati, stessa indicazione; cambio del
  peso o dell'obiettivo a una data che vale solo dal suo giorno.
- **T-NUT-06** (SUPERATO il 2026-11-10: tabella ed esempi tolti) Alimenti: porzioni coerenti con i grammi del pasto entro una
  tolleranza dichiarata; tabella con licenza registrata.
- **T-NUT-07** Giorni futuri (fase 5, ADR 011): le sedute e la gara del
  piano diventano le attività del giorno (ora tipica o 18:00, energia dai
  MET, momenti intorno all'allenamento); senza piano, con un rifiuto o oltre
  l'orizzonte nessuna indicazione, con il motivo; determinismo.
- **T-NUT-08** Nessun deficit nella settimana di una gara A o B e nello
  scarico che la precede (anche dopo la gara nella stessa settimana), deficit
  di nuovo fuori, nessuna pausa per le C o senza obiettivo di dimagrimento;
  avviso; property test sul modello puro.
