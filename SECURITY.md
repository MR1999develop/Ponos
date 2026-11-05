# Sicurezza

## Come segnalare una vulnerabilità

Non aprire una issue pubblica.

1. Usa "Report a vulnerability" nella scheda Security del repository GitHub
   (private vulnerability reporting).
2. Se non è disponibile, scrivi a `MR1999develop@users.noreply.github.com`.

Indica la versione, i passi per riprodurre il problema e l'effetto. Non
allegare dati di salute reali, password o token: se servono, usa dati di
prova. Non è previsto un canale cifrato.

## Cosa è nel perimetro

- Lettore di file FIT e import da cartella.
- Login Garmin e gestione dei token nel portachiavi del sistema.
- Elaborazione delle risposte JSON di Garmin.
- Pagina web dei grafici (`QWebEngineView`).
- Cartella del profilo e database.
- Pacchetti distribuiti e processo di rilascio.

Fuori perimetro: vulnerabilità di Garmin Connect, di Qt o di altre
dipendenze (vanno segnalate ai rispettivi progetti; segnalaci però se
l'app le rende sfruttabili), macchine già compromesse.

## Tempi indicativi

Sono tempi di un progetto personale, non impegni contrattuali.

- Conferma di ricezione: entro 7 giorni.
- Prima valutazione: entro 30 giorni.
- Correzione: dipende dalla gravità; le corruzioni di memoria e le fughe di
  credenziali hanno la precedenza.

La correzione viene pubblicata prima del dettaglio della vulnerabilità.

## Ricompense

Non c'è nessuna ricompensa. Chi lo desidera può essere citato nelle note di
rilascio.
