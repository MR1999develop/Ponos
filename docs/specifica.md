# Specifica: cosa vede e fa l'utente

Descrive il comportamento osservabile di Ponos Coach, versione prevista a fine
sviluppo. Formule, soglie e regole tecniche non stanno qui: sono nella skill
del progetto, in
[`.claude/skills/ponos-allenamento/`](../.claude/skills/ponos-allenamento/SKILL.md).
Le fasi di lavoro sono in `PLAN.md`.

Ponos Coach non è un dispositivo medico e non fa diagnosi.

## 1. Profili e cartella

- Un profilo è una persona che usa l'app. Si possono avere più profili.
- Alla creazione l'utente sceglie la cartella del profilo. Lì stanno tutti i
  suoi dati: database, file FIT, backup.
- La cartella si può spostare o aprire da un altro PC con "Apri profilo da
  cartella".
- Se la cartella è su un disco di rete o sincronizzato, l'app avverte del
  rischio di dati rovinati.
- Un profilo si apre in una sola istanza alla volta. Una seconda istanza è
  rifiutata con un messaggio.
- Menu File: Nuovo profilo, Apri profilo da cartella, Cambia profilo, Rimuovi
  profilo dall'elenco. Il nome del profilo attivo sta nel titolo della finestra.
- "Rimuovi" toglie il profilo solo dall'elenco. Cancellare la cartella è un
  comando a parte, con conferma che mostra cartella e contenuto.
- I dati di salute restano in locale. Le credenziali Garmin non stanno nella
  cartella.

Regole tecniche: [dati e sincronizzazione](../.claude/skills/ponos-allenamento/references/dati-e-sincronizzazione.md).

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

## 3. Sincronizzazione con Garmin

- Parte all'avvio e con il pulsante Sincronizza (F5).
- La prima sincronizzazione scarica tutto lo storico. Può durare a lungo:
  l'app mostra avanzamento, tempo stimato e Annulla.
- Le successive scaricano solo le attività mancanti.
- Se l'app si chiude a metà, alla successiva sincronizzazione riprende da dove
  era rimasta.
- "Verifica completa" rielenca tutto lo storico, senza riscaricare, per
  trovare attività caricate in ritardo con date vecchie.
- Un'attività già importata da file non viene riscaricata né duplicata.
- A fine sincronizzazione: notifica di sistema con il numero di attività
  nuove e messaggio nella barra di stato. Non c'è area di notifica.
- Se il login è scaduto o manca, si apre il dialogo di login con MFA. La
  password non viene salvata.
- Con errori temporanei di Garmin l'app aspetta e riprova poche volte, poi
  mostra un errore comprensibile.

## 4. Import da cartella

- Comando "Importa da cartella…", su richiesta. Si sceglie una cartella.
- L'app la esamina prima di importare: file `.fit` anche nelle sottocartelle.
- Tetti: 50.000 file FIT, 500.000 voci esaminate, 32 livelli di
  sottocartelle, 50 MB per file. Se si supera uno dei primi tre, non si
  importa nulla e l'app chiede di scegliere una sottocartella. I file oltre
  50 MB sono segnalati e saltati.
- I file si copiano nell'archivio del profilo. La cartella d'origine non
  viene mai modificata.
- I duplicati si saltano. Rilanciare l'import non cambia nulla.
- Alla fine un riepilogo: importate, già presenti, ignorate, con errore e
  motivo per ogni file.
- L'app ricorda l'ultima cartella usata.

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
- La tabella delle attività si ordina e si filtra; il dettaglio mostra FC e
  zone.
- I grafici non accedono alla rete.

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
- L'app ricorda finestra, pannelli, ultimo profilo e ultime cartelle.
- Le operazioni lunghe non bloccano la finestra e si possono annullare.
- Un errore imprevisto mostra un messaggio generico; il dettaglio va nel log,
  senza dati di salute.

## Punti aperti

- Formato e contenuto del file di backup (fase 8).
