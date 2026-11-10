# Test di soglia

**Quando leggerlo:** scrivendo o rivedendo `assessment`, le istruzioni del
test di soglia nell'app o i test `T-PIANO-06`. Sezione 3 di `SKILL.md`. Fonti: FRI
(30 minuti, edizioni precedenti alla 5ª), FRI5 (20 minuti, 5ª edizione).
Tutti i numeri sono default del progetto e stanno in un solo modulo,
`assessment/domain/defaults.py`; cambiarne uno cambia l'esito di ogni test.

## 1. Protocollo

Due protocolli a scelta (`ThresholdProtocol`, parametro di `EvaluateThresholdTest` e di
`evaluate_threshold_test`, salvato con ogni test nello storico e nel risultato; default
`THIRTY_MINUTES`):

| | `THIRTY_MINUTES` (FRI) | `TWENTY_MINUTES` (FRI5) |
|---|---|---|
| Segmento | 30 minuti di tempo trascorso | 20 minuti |
| FC di soglia | media degli **ultimi 20'** del segmento | media dei **20'** × 0,95, arrotondata |
| Ritmo di soglia | ritmo medio degli ultimi 20' | ritmo medio dei 20' **+ 5% del tempo al km** |
| Finestra di copertura FC (90%) | gli ultimi 20' | i 20' |
| Rifiuto «troppo corto» | tempo trascorso sotto i 30' | sotto i 20' |

Il 5% del test di 20' si applica al **tempo al km, non alla velocità** (FRI5): con 285,7
s/km di media il ritmo di soglia è 300 s/km (applicandolo alla velocità sarebbe 301). Gli
stessi default stanno in `assessment/domain/defaults.py` (`TWENTY_MINUTE_HR_PERCENT`,
`TWENTY_MINUTE_PACE_ADD_PERCENT`). `METHOD_VERSION` è 2 (fase 5: protocollo); i test
salvati prima valgono come test di 30' con la versione 1. Il retest non cambia (sezione 4).

- Test in corsa a sforzo massimo sostenibile e costante, dopo
  riscaldamento. Se ne parla nell'app prima, con le istruzioni.
- Frequenza: all'inizio e poi ogni 6–8 settimane (default del progetto), in
  una settimana di scarico del piano (la settimana di scarico arriva col
  piano, fase 5; per ora solo la finestra di date, sezione 4).
- Il test si fa con un'attività registrata. L'utente indica quale attività è
  il test (non si riconosce da sola). Solo sport corsa (`RUNNING` o `TRAIL`).

## 2. Calcolo (`evaluate_threshold_test`)

Funzione pura e deterministica: stesse attività, limiti e protocollo, stesso esito.
Sotto, dove si dice «30 minuti» e «ultimi 20 minuti», col protocollo di 20' la finestra è
di 20 minuti e coincide con il segmento.

- **Segmento:** la finestra di 30 minuti (1800 s) di tempo trascorso con la
  maggiore distanza percorsa; senza una distanza utilizzabile (serie assente,
  più corta di 30 minuti o che non cresce mai), la finestra di 30 minuti con la
  FC media valida più alta (pesata sul tempo). A parità, la prima. La finestra
  sta dentro i dati: dentro la serie della distanza, oppure tra il secondo 0 e
  la fine dell'ultimo campione di FC. Il massimo di una funzione lineare a
  tratti sta in un punto di rottura: si provano solo quelli (nessun ciclo per
  secondo).
  - **Velocità massima plausibile** (`MAX_PLAUSIBLE_SPEED_M_S`, 12 m/s, oltre
    il record mondiale dei 100 m): un passo della distanza cumulativa più
    veloce (un glitch del GPS: 20 km in 1 s) non conta nulla per la scelta
    della finestra; se la finestra migliore ne contiene uno, o il suo ritmo sui
    30 minuti è fuori da 90–3600 s/km, la distanza non è affidabile: si
    sceglie per FC e il risultato non ha ritmo (anche la FC di soglia viene
    così dal segmento giusto).
  - **Scelta per FC e copertura:** contano solo le finestre con almeno il 90%
    di secondi validi negli ultimi 20 minuti (la stessa regola del rifiuto,
    `enough_valid_seconds`): una finestra con pochi secondi validi e FC alta non
    batte una completa. Il limite del 90% si raggiunge tra due punti di
    rottura, quindi la radice è un candidato. Se nessuna finestra ha
    copertura, vale la FC più alta e il test è rifiutato per la copertura,
    come prima.
  - **Bordo finale:** la serie della distanza vale fino a un intervallo dopo
    l'ultimo record (al massimo `DISTANCE_END_TOLERANCE_S`, 1 s), come l'ultimo
    campione di FC vale fino al successivo: un test di 30:00 a 1 Hz, con
    l'ultimo record a 29:59, ha ancora il ritmo e la scelta per distanza.
  - **Origine:** i secondi delle serie partono dal primo record, non
    dall'inizio della sessione; `segment_start` è l'istante del record da cui
    parte il segmento (`Activity.first_record_offset_s` + secondi). FC e
    distanza hanno la stessa origine; i campioni con secondo negativo (record
    con timestamp prima del primo) sono scartati in entrambe.
- **FC di soglia:** media pesata sul tempo dei campioni di FC validi (dentro
  `[FCmin, FCmax]` del profilo per il giorno del test, intervalli tra campioni
  fino a 10 s, come per il carico) negli ultimi 20 minuti (1200 s) del
  segmento, arrotondata al bpm intero (metà verso l'alto, dopo aver tolto il
  rumore dei decimali).
- **Ritmo di soglia:** tempo / distanza degli stessi 20 minuti, in secondi
  interi per km (metà verso l'alto); assente senza distanza che copra tutti i
  20 minuti, con distanza nulla o fuori da 90–3600 s/km (un glitch del GPS o
  una camminata non sono un ritmo). Un passo più veloce della velocità
  massima plausibile dentro i 20 minuti rende il ritmo assente (la FC resta
  valida): il ritmo non si ripara togliendo il salto.
- **Giorno del test:** il giorno locale (fuso del profilo) dell'inizio
  dell'attività; i limiti FC sono quelli in vigore quel giorno.
- **Zone FC:** quelle del carico (`load/domain/zones.py`, scelta dell'utente del
  2026-11-10: Z1 sotto 75% della soglia, 75–85, 85–95, 95–103, Z5 da 103%), con `zone_starts_bpm(soglia)`.
- **Zone di ritmo** (`assessment/domain/pace_zones.py`): cinque zone allineate
  alle zone FC, come percentuale della **velocità** di soglia: Z1 sotto 78%,
  Z2 78–88%, Z3 88–94%, Z4 94–100%, Z5 100% e oltre (nel ritmo, circa: più
  lento del 128% del ritmo di soglia, 114–128%, 106–114%, 100–106%, più veloce
  del ritmo di soglia; default derivati da Friel; non seguono le zone FC dell'utente del 2026-11-10,
  che sono un'altra scelta e non si applicano al ritmo). Il limite inferiore
  appartiene alla zona più veloce; confronti su interi (`ritmo_soglia × 100 >=
  percentuale × ritmo`), mai una percentuale in virgola mobile al confine.
- Dopo il test si aggiornano soglia e zone con una data di validità (vedi
  `modello-carico.md`, sezione 2): la soglia diventa una misura datata al
  giorno del test, con le regole di `UpdateMeasurements`.

## 3. Rifiuto (`ThresholdRejection`)

Un test che non è affidabile è rifiutato con il motivo, mai riparato. Nell'ordine
in cui il codice li controlla:

1. `WRONG_SPORT`: non è una corsa.
2. `TOO_SHORT`: tempo trascorso sotto i 30 minuti (1799 s è troppo corto, 1800
   basta), oppure nessuna finestra di 30 minuti nei dati.
3. `TOO_FEW_HEART_RATE_SAMPLES` (senza serie di FC): nessuna FC, nessun test.
4. `TOO_MANY_PAUSES`: buchi tra record oltre 10 s che nel segmento sommano più
   di 60 s (60 s esatti sono ammessi). Contano i record di FC e di distanza
   insieme. Le pause fuori dal segmento non contano.
5. `TOO_FEW_HEART_RATE_SAMPLES` (copertura): FC valida sotto il 90% del tempo
   degli ultimi 20 minuti (confronto in millisecondi interi: il 90% esatto
   basta, 121 s non validi su 1200 no).
6. `THRESHOLD_OUT_OF_RANGE`: la FC di soglia non è strettamente tra `FCmin` e
   `FCmax` del profilo (o è sotto i 40 bpm, il limite del tipo
   `ThresholdHeartRate`).

Se il file dell'attività non si legge (mancante, alterato, troppo grande),
`EvaluateThresholdTest` restituisce `ExcludedActivity` con il motivo del
carico, non un rifiuto del test.

## 4. Retest (`retest_status`)

Proposto da 42 a 56 giorni dopo l'ultimo test applicato (estremi inclusi);
esito: `NoTest`, `NotYet(first_day, last_day)` (fino a 41 giorni dopo),
`Recommended(first_day, last_day)` (42–56), `Overdue(days, last_day)` (dal 57º
giorno: 1 giorno di ritardo). Conta solo il test più recente. Lo storico dei
test si conserva (tabella `threshold_test`, schema v5).

Dalla versione 5 del piano un test dovuto (nessun test, finestra aperta o passata) è una
seduta di quel protocollo nella prima settimana del piano, e l'attività del giorno usata come
test lo fa considerare fatto: regole in `references/piano.md`, sezione 10, e ADR 015.

## 5. Soglia in bici

Dal 2026-11-10 la bici non ha una soglia propria: è nel gruppo «altri sport», con la
LT2 inserita per quel gruppo e, senza valore, la LT2 della corsa (prima corsa − 4 bpm).
`threshold_for_sport` resta l'unico punto. Un test in bici è fuori dal piano attuale.

## 6. Casi d'uso

- `assessment.EvaluateThresholdTest(attività)`: legge l'originale
  dall'archivio, restituisce `ThresholdTestResult | ThresholdRejection |
  ExcludedActivity`; non salva nulla.
- `profiles.ApplyThresholdTest(risultato)` → `AppliedThresholdTest`: nella
  stessa transazione salva il test nello storico e la soglia come misura
  datata (riusa `UpdateMeasurements.save_in_open_transaction`, senza copiarne
  le regole: data non futura, FC coerente, validità fino alla voce
  successiva). Lo stesso giorno di una voce esistente la sostituisce.
- `profiles.RemoveThresholdTest(applicato)`: l'annulla; toglie il test e la
  voce che aveva creato, ripristinando quella sostituita (stato precedente
  esatto). Se l'utente ha modificato la voce dopo il test, rifiuta
  (`ThresholdTestChangedError`).
- `assessment.ListThresholdTests()` e `assessment.NextRetest()`.
- I carichi dipendono dalla soglia attraverso l'impronta dei parametri: si
  ricalcolano da soli alla prossima panoramica, come per ogni misura. Il
  carico e l'impronta della cache non sono cambiati.

## 7. Dati

La serie della distanza (`DistanceSeries`, secondi dal primo record come la FC,
metri cumulativi non decrescenti) viene dal FIT (`record.distance`, o in
mancanza `speed` integrata, senza inventare distanza sui buchi oltre 10 s). Una
distanza che va all'indietro non è affidabile: niente serie (e niente ritmo),
l'attività resta valida. Gli sport ammessi al test sono
`defaults.THRESHOLD_TEST_SPORTS`, usato anche dall'interfaccia.

## 8. Test attesi

`T-PIANO-06` in `PLAN.md` sezione 7.4: tracciati sintetici
(`tests/assessment/`, anche `test_threshold_twenty.py` per il protocollo di 20': FC × 0,95 con
la metà verso l'alto, ritmo + 5% del tempo, copertura, pause, soglia fuori limiti, confronto
con un'implementazione ingenua a frazioni; finestra e media giuste con riscaldamento, test
interrotto, troppo corto, FC mancante o fuori limiti, senza distanza, pause,
retest ai confini 41/42/56/57 giorni), property test (la FC di soglia sta tra
minimo e massimo dei campioni validi della finestra; la finestra scelta ha la
distanza massima, confronto con un'implementazione ingenua), fixture FIT con
distanza (`run_threshold_*.fit`), migrazione v5 e v7 (`tests/collaudo/`) e contract test dello
storico, protocollo compreso.
