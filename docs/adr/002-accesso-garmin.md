# 002. Accesso a Garmin Connect

- Stato: Accettato
- Data: 2026-11-05

## Contesto

Garmin non offre un'API ufficiale ai privati. Le attività si possono ottenere
solo con un accesso non ufficiale o tramite un servizio terza parte. A marzo
2026 Garmin ha cambiato il login e ha rotto la libreria `garth`. La libreria
`garminconnect` dalla versione 0.3 ha un login proprio, con MFA. L'accesso può
rompersi ancora e può essere contrario ai termini d'uso di Garmin.

## Opzioni

1. **Login diretto non ufficiale con `garminconnect`.**
   - Pro: nessun intermediario, dati completi (FIT originali, calendario),
     MFA supportato.
   - Contro: può smettere di funzionare a ogni cambio di Garmin; possibile
     violazione dei termini d'uso; rischio di blocco temporaneo (429); la
     password passa dall'app.
2. **intervals.icu come intermediario (API ufficiale di una terza parte).**
   - Pro: API documentata e stabile, nessuna password Garmin nell'app.
   - Contro: serve un secondo account; i dati passano da un terzo; dipende
     dalla sincronizzazione tra i due servizi e dalla sua disponibilità; i FIT
     originali e il calendario Garmin potrebbero non essere disponibili
     (da verificare).
3. **Entrambi selezionabili dall'utente.**
   - Pro: ripiego se uno dei due si rompe.
   - Contro: due adattatori da mantenere e testare; più superficie d'attacco.

## Decisione

Opzione 1, `garminconnect`, con versione bloccata nel lockfile e aggiornata
solo dopo prova. L'opzione 3 non è esclusa: se la porta `ActivitySource` lo
permette, un secondo adattatore si potrà aggiungere con un nuovo ADR.

Compensazioni:

- adattatore isolato dietro la porta `ActivitySource` (e `WorkoutCalendar`);
- test di contratto su risposte registrate e anonimizzate, e su un fake;
- import manuale di file FIT e da cartella sempre disponibile;
- poche richieste: solo all'avvio e a mano, pause con jitter, backoff con
  tetto, nessun ritentativo infinito;
- la password si inserisce in un dialogo dell'app, va solo a `garminconnect`
  e non si salva; i token stanno nel portachiavi del sistema.

Questa decisione è anche un'eccezione scritta in `CLAUDE.md`: il login si fa
nell'app invece che nel browser di sistema con PKCE, perché non esiste un flusso
ufficiale.

## Conseguenze

- La sincronizzazione può fermarsi senza preavviso; l'app resta utilizzabile
  con i file FIT.
- Il README avvisa che l'accesso non è ufficiale.
- La password è trattata come dato sensibile in memoria e nei log (mai
  registrata).
- Ogni aggiornamento di `garminconnect` è un lavoro con prova e test di
  contratto.

## Come se ne verifica il rispetto

- `lint-imports`: solo il pacchetto `garmin` importa `garminconnect`.
- T-SYNC-03 e T-SYNC-04 (errori, backoff, login); T-SEC-03 (risposte
  ostili); T-SEC-07 (nessun segreto in cartella, configurazione e log).
- Spike della fase 0 e test di contratto sulle risposte registrate a ogni
  aggiornamento della libreria.
