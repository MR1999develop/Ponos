# 007. Dati del profilo, misure datate, pesi degli sport e zone di FC

- Stato: Accettato
- Data: 2026-11-06

## Contesto

La fase 3 aggiunge al profilo altezza, data di nascita e peso, rende datate
anche FC minima, FC massima e soglia, lascia scegliere i pesi degli sport e
calcola il tempo nelle zone di FC. Cinque scelte restano nei file dell'utente
o nei numeri che vede, e sono difficili da cambiare dopo il rilascio: quali
dati sono fissi e quali datati, per quanto tempo vale una misura, come si
salva il peso, come si salvano i pesi degli sport e quando un carico va
ricalcolato, come si definiscono le zone. Una sesta riguarda la lingua
dell'interfaccia.

Questo ADR aggiunge la versione v3 dello schema all'ADR 005 e precisa l'ADR
004 (modello di carico). Il resto di entrambi resta valido.

Vincoli:

- altezza, peso e data di nascita sono dati di salute: solo nel database del
  profilo, mai in log, messaggi o catene d'errore;
- un profilo creato prima della v3 resta valido e non perde nulla;
- il database è un indice e i dati dell'utente; i carichi e le zone sono
  derivati e si rigenerano dai file originali;
- un cambio di parametri non deve lasciare carichi vecchi mescolati ai nuovi;
- l'app non è un dispositivo medico: le zone sono un'indicazione di
  allenamento.

## Opzioni

### Dati fissi e dati datati

1. **Fissi: altezza e data di nascita. Datati: peso, FC minima, FC massima,
   soglia.**
   - Pro: ciò che non cambia nel tempo non ha storico; ciò che cambia lo ha,
     e il carico di un'attività usa i valori del suo giorno. Un trigger
     rende i dati fissi immutabili anche per un bug del codice.
   - Contro: un errore di battitura in altezza o data di nascita non si
     corregge dall'app; la distinzione va spiegata all'utente.
2. **Tutto datato, anche altezza e data di nascita.**
   - Pro: una regola sola; un errore si corregge con una nuova voce.
   - Contro: storico senza senso per la data di nascita; più righe da
     validare e da mostrare; la sua modifica cambierebbe l'età nel passato.
3. **Una riga di profilo modificabile, senza storico.**
   - Pro: schema minimo.
   - Contro: un cambio di FC o soglia riscriverebbe il passato: il carico di
     un'attività di tre anni fa verrebbe ricalcolato con i valori di oggi.

### Per quanto tempo vale una misura

1. **Una misura vale dal suo giorno fino alla voce successiva della stessa
   serie; il peso e i parametri di FC sono due serie. Stesso giorno: la voce
   si sostituisce. I valori non indicati restano quelli in vigore quel
   giorno.**
   - Pro: inserire una misura passata non cambia ciò che vale dopo una voce
     più recente; è la regola che chi guarda lo storico si aspetta; il caso
     d'uso restituisce il periodo effettivo e l'interfaccia lo mostra.
   - Contro: una misura con data passata può valere solo pochi giorni, e
     l'utente che la inserisce può aspettarsi che valga "da allora in poi":
     per questo "Modifica profilo" mostra il periodo effettivo e avvisa
     quando finisce prima del presente.
2. **Una misura vale da quel giorno in poi e sostituisce le voci successive.**
   - Pro: l'effetto è quello che l'utente scrive.
   - Contro: cancella dati veri senza dirlo; contro la regola "i dati
     dell'utente non si sovrascrivono".
3. **Vale solo l'ultima misura inserita, per tutta la storia.**
   - Pro: nessun periodo da mostrare.
   - Contro: come l'opzione 3 dei dati fissi e datati, riscrive il passato.

### Come si salva il peso

1. **Intero in decimi di chilogrammo (`tenths_kg`), da 20,0 a 300,0 kg;
   cifra decimale singola; testo letto con una grammatica stretta (cifre
   ASCII, virgola o punto).**
   - Pro: nessun confronto in virgola mobile binaria; il database lo limita
     con un `CHECK` sugli interi; `70,5` e `70.5` sono lo stesso valore.
   - Contro: una bilancia che dà due decimali non è accettata (`70,55`
     è rifiutato, non arrotondato); la conversione sta in un solo punto.
2. **`REAL` in chilogrammi.**
   - Pro: nessuna conversione.
   - Contro: `70,1` non è esatto in binario; confronti e uguaglianze di
     voci diventano fragili.
3. **Testo con il numero decimale (`Decimal`).**
   - Pro: esatto.
   - Contro: il database non può limitarlo con un `CHECK` numerico; un tipo
     `STRICT` di testo accetta qualunque stringa.

### Pesi degli sport e quando si ricalcola un carico

1. **Tabella `sport_weights` con solo i pesi diversi dal default; l'impronta
   del carico di un'attività comprende solo il peso del suo sport.**
   - Pro: cambiare il default del progetto in una versione futura raggiunge
     chi non l'ha mai toccato; cambiare il peso della bici non fa ricalcolare
     le corse (un ricalcolo rilegge i file: con 5.000 attività è lavoro lungo).
   - Contro: l'insieme salvato dipende dai default del codice: un default
     cambiato cambia anche il carico, e `LOAD_MODEL_VERSION` va alzata quando
     si vuole che tutti i carichi si ricalcolino.
2. **Tabella con i pesi di tutti gli sport.**
   - Pro: ciò che si usa è tutto nel database.
   - Contro: i default diventano dati dell'utente: un default migliore non
     arriva più a nessuno senza una migrazione.
3. **Impronta unica con tutti i pesi.**
   - Pro: più semplice da calcolare.
   - Contro: cambiare un peso invalida ogni carico di ogni sport.

### Zone di FC

1. **Cinque zone sulla soglia dello sport, secondo Friel: Z1 sotto 85%, Z2
   85-89%, Z3 90-94%, Z4 95-99%, Z5 dal 100%. Default del progetto, da
   rivedere in fase 4 con il test di soglia. Le percentuali stanno solo in
   `load/domain/zones.py`; i secondi in zona si salvano con il carico.**
   - Pro: la soglia è già il parametro del carico (hrTSS); in bici vale la
     soglia della bici; nessun dato in più da chiedere.
   - Contro: la soglia inserita prima del test della fase 4 può essere una
     stima (90% della massima alla creazione), e con essa le zone; le
     percentuali sono di una sola fonte e non si adattano a chi ha un
     profilo diverso.
2. **Percentuali della FC di riserva (Karvonen).**
   - Pro: tiene conto della FC a riposo.
   - Contro: un secondo insieme di parametri da spiegare; non coincide con la
     soglia usata dal carico.
3. **Percentuali della FC massima.**
   - Pro: nessuna soglia necessaria.
   - Contro: la FC massima è il valore meno affidabile tra quelli inseriti;
     le zone non seguirebbero il test di soglia.
4. **Zone configurabili dall'utente.**
   - Pro: adatte a chi ha già le proprie.
   - Contro: più interfaccia e più casi di input da validare; rimandato a
     quando serve (YAGNI).

### Schema v3

1. **Migrazione additiva: tabelle `profile_body`, `weight`, `sport_weights`;
   cinque colonne `zone1_s ... zone5_s` in `load_cache`; la cache si svuota
   nella stessa migrazione. Un trigger rifiuta ogni `UPDATE` e `DELETE` di
   `profile_body`.**
   - Pro: la cache è derivata: svuotarla garantisce che nessun carico resti
     senza zone e costa solo un ricalcolo; i vincoli `CHECK` stanno nel
     database; la migrazione passa dalla copia di sicurezza come le altre.
   - Contro: il primo avvio dopo l'aggiornamento ricalcola tutti i carichi
     (con 5.000 attività circa 23 s con i FIT reali, stimati); il trigger
     impedisce anche a un futuro codice di correggere i dati fissi, che
     richiederebbe una nuova migrazione.
2. **Valori di zona in una tabella separata.**
   - Pro: `load_cache` non cambia.
   - Contro: una tabella e una chiave esterna in più per un dato che nasce e
     muore con il carico.
3. **Zone calcolate al volo dai file.**
   - Pro: nessun dato in più.
   - Contro: il grafico del tempo nelle zone rileggerebbe tutti i file a ogni
     apertura.

### Lingua dell'interfaccia

1. **Inglese predefinito; l'italiano si sceglie dal menu Lingua e la scelta si
   ricorda per il prossimo avvio; `--lang` ha la precedenza.**
   - Pro: nessuna dipendenza dalla lingua del sistema, quindi stesso
     comportamento ovunque e nei test; un testo sorgente inglese non richiede
     catalogo.
   - Contro: chi usa l'italiano deve scegliere la lingua una volta; il
     cambio vale al riavvio.
2. **Lingua del sistema.**
   - Pro: nessuna scelta per l'utente.
   - Contro: dipende dall'ambiente, difficile da provare; una lingua del
     sistema non tradotta ripiega in modo poco chiaro.

## Decisione

- **Dati fissi:** altezza (intera, 100-250 cm) e data di nascita (dal
  1900-01-01 a oggi meno 5 anni, limite superiore verificato quando la inserisce
  l'utente). Si scrivono una volta (`FixedDataStore.set_once`); un profilo
  creato prima della v3 li completa una volta in "Modifica profilo".
- **Misure datate:** peso (decimi di chilo, 20,0-300,0 kg), FC minima, FC
  massima, soglia. Un giorno di validità non nel futuro e non prima del
  1900-01-01. Il vincolo `FC minima < soglia <= FC massima` si verifica sul
  risultato. Una modifica di "Modifica profilo" è un solo caso d'uso e una
  sola transazione, dati fissi compresi.
- **Validità:** vale la regola dell'opzione 1; il caso d'uso restituisce il
  periodo effettivo (`ValidityPeriod`) e l'interfaccia lo mostra e avvisa se la
  data passata vale solo fino alla misura successiva. Il peso corporeo non
  entra nel carico.
- **Pesi degli sport:** da 0 a 5, due decimali; si salvano solo quelli
  diversi dal default; l'impronta di un carico comprende il peso del solo
  sport, le percentuali delle zone e `LOAD_MODEL_VERSION` (2 dalla fase 3).
- **Zone:** opzione 1. I secondi in zona sono quelli dei campioni contati dal
  carico (stessi scarti e stessi buchi). Un campione sul confine appartiene alla
  zona più alta; il confronto è su interi.
- **Schema v3:** opzione 1.
- **Lingua:** opzione 1.
- **Dati personali:** i tipi di dominio e gli store non ripetono i valori
  rifiutati nei messaggi; nessuno di questi valori entra in un log.

## Conseguenze

- Un errore in altezza o data di nascita non si corregge dall'app. Se la
  richiesta diventa frequente, si decide con un nuovo ADR (non si modifica
  questo).
- Le percentuali delle zone sono provvisorie: cambiarle in fase 4 richiede
  una nuova versione del modello di carico, quindi un ricalcolo di tutti i
  carichi, e un ADR che sostituisce la parte sulle zone.
- Il primo avvio dopo l'aggiornamento a v3 ricalcola i carichi con
  avanzamento e Interrompi.
- I default dei pesi degli sport sono parte del comportamento visibile:
  cambiarli senza alzare `LOAD_MODEL_VERSION` lascia carichi vecchi in cache.
- Il peso corporeo ha uno storico ma nessun uso nei calcoli; è pronto per
  fasi successive senza altre migrazioni.
- Ogni cambio dell'insieme di dati fissi richiede una migrazione, una copia
  di sicurezza e un test sul database della versione precedente (come per
  ogni schema, ADR 005).

## Come se ne verifica il rispetto

- T-DATI-01: database v2 scritto a mano portato a v3 con dati intatti e
  copia di sicurezza coerente; migrazione interrotta a ogni istruzione;
  limiti dei `CHECK`, trigger dei dati fissi
  (`tests/collaudo/test_fase3_migrazione.py`,
  `tests/collaudo/test_fase3_dati_personali.py`); due istanze e doppio
  completamento dei dati fissi con un solo vincitore.
- T-QUAL-02: partizioni e confini di altezza, data di nascita, peso,
  triple di FC, pesi degli sport e giorno di validità, anche attraverso il
  caso d'uso e il database (`tests/collaudo/test_fase3_dati_personali.py`).
- Periodi di validità per serie: `tests/profiles/test_measurements.py`.
- T-CALC-01…07: confronto tra la panoramica incrementale dopo una serie di
  modifiche e il calcolo da zero; l'impronta dipende da ogni ingresso del
  carico e da nient'altro; zone con oracolo scritto a parte
  (`tests/collaudo/test_fase3_carico.py`, `tests/load/test_parameters.py`,
  `tests/load/test_sport_weights.py`).
- T-SEC-08: nessun valore di altezza, peso, data di nascita o FC in log,
  messaggi o catene d'errore (`tests/collaudo/test_fase3_log_salute.py`).
- T-UI-06 e T-UI-07: stessi testi nelle due lingue, lingua ricordata
  (`tests/ui/test_translations.py`, `tests/collaudo/test_fase3_interfaccia.py`).
- T-PERF-01: panoramica con 5.000 attività, ricalcolo completo e grafici
  entro i budget (`tests/collaudo/test_fase3_prestazioni.py`).
- `import-linter` nella CI: il dominio resta puro.
