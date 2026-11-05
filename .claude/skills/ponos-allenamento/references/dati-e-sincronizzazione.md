# Dati e sincronizzazione

**Quando leggerlo:** scrivendo o rivedendo `storage`, `profiles`, `garmin`,
`activities` (import e sincronizzazione), i test `T-DATI`, `T-SYNC`, `T-IMP`.
Sezione 5 di `SKILL.md`. ADR: `docs/adr/003-formato-dati.md`.

## 1. Cartella del profilo

Scelta dall'utente alla creazione; autosufficiente.

```
<cartella del profilo>/
  ponos.sqlite3     database (WAL, foreign_keys = ON)
  fit/<anno>/<id_attività>.fit
  backup/
  ponos.lock        creato in modo esclusivo
```

- I token Garmin non stanno qui: stanno nel portachiavi del sistema, legati
  all'id del profilo.
- La configurazione dell'app (`platformdirs`) ha l'elenco dei profili con le
  loro cartelle e l'ultimo usato. Lo stato della UI è in `QSettings`.
- Rimuovere un profilo lo toglie solo dall'elenco; cancellare la cartella è un
  comando a parte con conferma.
- Cartella su rete o sincronizzata (OneDrive, NAS): avviso alla scelta.
- Cartella verificata scrivibile; file creati in modo esclusivo; permessi
  solo per l'utente (descrittore esplicito su Windows, 0700/0600 su Ubuntu).

## 2. SQLite

- Parametri sempre con `?`, mai SQL composto.
- Schema con `user_version` e migrazioni versionate nel repository. Il
  database contiene l'id del profilo e la versione del formato.
- Contiene: indice delle attività, parametri, obiettivi, piani, infortuni,
  impostazioni, giornale della sincronizzazione. Carichi e PMC sono derivati e
  rigenerabili.
- Campi sconosciuti conservati al risalvataggio.
- Elenco preciso di tabelle e colonne: **punto aperto**, lo fissa la fase 1.

## 3. Archivio FIT

- I FIT originali sono la fonte di verità delle attività.
- Ogni FIT ha il suo SHA-256 registrato.
- Scrittura: file temporaneo, flush, rinomina; il record in SQLite si scrive
  dopo il file. Il nome lo genera il programma, mai un dato esterno.
- All'avvio: nessun record senza file, nessun temporaneo orfano.
- Un FIT alterato (hash diverso) è segnalato, mai usato in silenzio.
- Limiti prima della lettura: dimensione massima 50 MB, numero massimo di
  record, durata massima dell'attività. Il numero massimo di record e la
  durata massima: **punti aperti** (valori da fissare in fase 1, costanti in
  un solo modulo).

## 4. Identità dell'attività

- `file_id` del FIT: produttore, numero di serie, ora di creazione. È uguale
  per lo stesso file scaricato da Garmin o copiato dall'orologio.
- L'id Garmin si aggiunge quando è noto.
- Così la stessa attività non entra due volte, qualunque sia l'origine.

## 5. Sincronizzazione riprendibile

1. Token dal portachiavi; se mancano o sono scaduti, dialogo di login con MFA.
   La password va solo a `garminconnect` e non si salva.
2. Prima sincronizzazione: elenco di tutto lo storico a pagine, con il punto
   raggiunto nel giornale. Successive: elenco dalle più recenti finché si
   trova una pagina di attività già note, più le voci in sospeso o fallite.
3. Ogni id nuovo diventa una voce "da scaricare" nel giornale (idempotente
   per id Garmin).
4. "Verifica completa": rielenca tutto lo storico senza riscaricare, per
   trovare attività con date vecchie caricate in ritardo.
5. Per ogni voce: download del FIT originale (zip), controlli dei limiti,
   scrittura durevole, lettura, calcolo del carico; una transazione segna la
   voce completata.
6. Pause tra le richieste con jitter; su 429 o errori transitori, backoff
   esponenziale con tetto e numero massimo di tentativi; poi errore chiaro.
   Valori di pausa, tetto e tentativi: **punti aperti** (costanti in un solo
   posto, da fissare con lo spike della fase 0).
7. Chiusura a metà: all'avvio successivo si riparte dalle voci in sospeso e si
   cancellano i temporanei orfani.
8. Il lavoro gira in un worker con annullamento (si ferma entro 1 s); la UI
   mostra avanzamento e Annulla. A fine: notifica di sistema, barra di stato,
   PMC e piano aggiornati.
9. Abbinamento: prima di scaricare, un'attività dell'elenco Garmin con la
   stessa ora d'inizio e lo stesso sport di una già importata da file si
   abbina a quella senza riscaricarla. Tolleranza sull'ora d'inizio:
   **punto aperto**.

## 6. Import da cartella

1. Si sceglie una cartella e la si esamina prima (solo nomi e dimensioni):
   file `.fit` anche nelle sottocartelle, solo file regolari che stanno
   davvero dentro la cartella (niente collegamenti o reparse point verso
   fuori).
2. Tetti (default del progetto, costanti in un solo modulo):

   | Tetto | Valore |
   |---|---|
   | File FIT | 50.000 |
   | Voci esaminate (file e cartelle di ogni tipo) | 500.000 |
   | Livelli di sottocartelle | 32 |
   | Dimensione di un file | 50 MB |

3. Se si supera uno dei primi tre, l'esame si ferma, non si importa nulla e
   l'app chiede una sottocartella: niente import parziali. I file oltre 50 MB
   sono segnalati e saltati.
4. Ogni file passa dagli stessi controlli dei FIT scaricati e si copia
   nell'archivio. La cartella d'origine non si modifica mai.
5. I duplicati (stesso `file_id`) si saltano. L'import è idempotente.
6. Worker con avanzamento e Annulla; alla fine un riepilogo (importate, già
   presenti, ignorate, con errore e motivo per ogni file).

## 7. Lock

- `ponos.lock` si crea in modo esclusivo: un solo processo per profilo.
- Una seconda istanza è rifiutata con un messaggio.
- Un lock rimasto da un crash è riconosciuto e superato (T-DATI-04). Come si
  riconosce (id del processo, ora, altro): **punto aperto**.

## 8. Test attesi

`T-DATI-01…05`, `T-SYNC-01…06`, `T-IMP-01…03`, `T-SEC-02`, `T-SEC-04` in
`PLAN.md` sezione 7.
