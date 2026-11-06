# Modello di carico

**Quando leggerlo:** scrivendo o rivedendo `load`, i test `T-CALC`, i grafici
del PMC o la documentazione dei numeri. Sezione 1 di `SKILL.md`. Fonti: BAN,
COG. ADR: `docs/adr/004-modello-carico.md`.

## 1. Formule

Per ogni campione di FC al tempo t:

- `HRr(t) = (HR(t) − FCmin) / (FCmax − FCmin)`; HR, FCmin, FCmax in bpm;
  HRr senza unità, tra 0 e 1.
- `TRIMP = Σ Δt[min] · HRr · 0,64 · e^(k · HRr)`; Δt in minuti.
  - `k = 1,92` per il sesso maschile, `k = 1,67` per il femminile (BAN;
    dal profilo).
  - Il coefficiente 0,64 è quello di Banister.
- `TRIMP_1h_soglia` = TRIMP di 60 minuti con HR pari alla FC di soglia.
- `hrTSS = 100 · TRIMP / TRIMP_1h_soglia` (COG per la scala: 100 = un'ora alla
  soglia).
- `carico = hrTSS · peso_sport` (peso senza unità).

Un'ora esatta alla FC di soglia dà hrTSS 100. HR = FCmin dà HRr = 0 e carico 0.

## 2. Parametri con data di validità

- FC minima, FC massima, soglia (e sesso) sono inseriti dall'utente.
- Ogni valore ha una data di validità. Un'attività usa i valori in vigore alla
  sua data d'inizio. Quale istante conta per un'attività a cavallo di un
  cambio: **punto aperto** (si propone: la data d'inizio).
- Quando un valore o un peso cambia, i carichi derivati si ricalcolano per
  tutte le attività dal giorno di validità in poi. Il risultato è uguale al
  calcolo da zero (T-CALC-05).
- Soglia in bici = `max(soglia della corsa − 4, FCmin + 1)` bpm, finché non
  c'è un test in bici (default del progetto). Il valore si porta al limite
  invece di rifiutare i parametri: il costruttore di `Physiology` richiede
  solo `FCmin < soglia ≤ FCmax` per la soglia della corsa.
- Intervalli ammessi (default del progetto, nei tipi di dominio): FC minima
  20-250, FC massima fino a 250 con minima < massima, soglia 40-250 con
  minima < soglia <= massima; il vincolo si verifica sul risultato di un
  aggiornamento, non sul singolo valore.
- **Misure datate** (`profiles/measurements.py`): `UpdateMeasurements` dà a
  uno o più tra peso, FC minima, FC massima e soglia una data di validità
  (non nel futuro, non prima del 1900-01-01). I valori non indicati restano
  quelli validi **quel giorno** (non gli ultimi); lo stesso giorno sostituisce;
  il sesso resta quello della creazione; tutto in una transazione. Il peso
  corporeo ha la sua serie e non entra nel carico. Altezza (100-250 cm) e data
  di nascita (dal 1900-01-01 a oggi meno 5 anni) sono fisse; il peso va da
  20,0 a 300,0 kg in decimi interi.

## 3. Campioni fuori limiti, buchi, attività senza FC

- Un campione con HR fuori da `[FCmin, FCmax]` è un errore: si scarta e si
  conta. Si scarta il campione, non l'attività. Il dettaglio dell'attività
  riporta il numero di scarti.
- Se molti campioni superano la massima, l'app propone di rivedere la FC
  massima. La soglia di "molti": **punto aperto**.
- Tra due campioni consecutivi un buco oltre 10 s non si conta (default del
  progetto).
- Attività senza FC, o con più del 30% di campioni scartati o mancanti
  (default del progetto): carico stimato da `durata × intensità tipica dello
  sport`, marcato "stimato" in tabella e nei grafici. La durata è il **tempo
  in movimento** (`total_timer_time` del FIT; se manca, `total_elapsed_time`),
  non il tempo trascorso, che include le pause (default del progetto). La
  regola del 30% confronta i secondi contati con il 70% del tempo in
  movimento, in millisecondi interi, mai in virgola mobile al limite. Le intensità tipiche per
  sport: **punto aperto**, da fissare in fase 3.
- Forma esatta della tabella di stima e trattamento di attività senza durata:
  punto aperto.

## 4. Pesi degli sport

Default del progetto (`load/domain/sport.py`), provvisori. L'utente li
cambia: i pesi scelti stanno nel profilo (tabella `sport_weights`, solo quelli
diversi dal default) e `ComputeProfileOverview` legge quelli salvati a ogni
calcolo. L'impronta di un carico comprende solo il peso dello sport di
quell'attività: cambiare il peso della bici non fa ricalcolare le corse.

| Sport | Peso |
|---|---|
| Corsa | 1,0 |
| Trail | 1,1 |
| Ciclismo | 0,8 |
| Escursionismo | 0,8 |
| Camminata | 0,6 |
| Forza | 1,2 |
| Altro | 0,8 |

Motivo per la forza: la FC sottostima il suo carico. Peso 0 dà carico 0.

## 4b. Zone di FC

Cinque zone sulla soglia dello sport (in bici la soglia della bici), secondo
Friel; percentuali di default del progetto in un solo posto
(`ZONE_STARTS_PERCENT` in `load/domain/zones.py`), da rivedere in fase 4:

| Zona | FC in % della soglia |
|---|---|
| Z1 | sotto 85% |
| Z2 | da 85% a 89% (sotto 90%) |
| Z3 | da 90% a 94% (sotto 95%) |
| Z4 | da 95% a 99% (sotto 100%) |
| Z5 | 100% e oltre |

- Il confine inferiore appartiene alla zona più alta (144,5 bpm con soglia 170 è
  Z2; 153 è esattamente il 90%: Z3). Il confronto è su interi (`bpm * 100`
  contro `percentuale * soglia`), mai su percentuali in virgola mobile.
- I **secondi in ogni zona** (`ActivityLoad.zone_seconds`, `ZoneSeconds`) sono
  quelli dei campioni contati dal carico: stessi scarti (FC fuori limiti) e
  stessi buchi (intervalli oltre 10 s esclusi). Senza serie di FC sono zero;
  per una stima fatta con pochi campioni validi sono quelli misurati.
- Si salvano nella cache dei carichi (colonne `zone1_s ... zone5_s`) legati alla
  stessa impronta del carico; l'impronta comprende le percentuali delle zone e
  `LOAD_MODEL_VERSION` (2 dalla fase 3).
- Aggregazioni per i grafici (`load/domain/aggregation.py`, pure e vettoriali):
  carico per settimana ISO (lunedì-domenica, giorni locali del profilo) e per
  sport, secondi per zona per settimana; le settimane senza attività, dalla
  prima a quella di oggi, ci sono a zero; le attività dopo oggi si ignorano
  come nel PMC. `ProfileOverview.weekly_load(oggi)` e `.weekly_zones(oggi)`.
- Dettaglio di un'attività (`load/activity_detail.py`, `GetActivityDetail`):
  legge e decodifica l'originale, restituisce tracciato di FC (con i campioni
  scartati marcati), limiti, soglia e confini delle zone validi quel giorno,
  riepilogo e carico; un guasto del file è l'esito `ExcludedActivity` con il
  motivo (mancante, alterato, troppo grande, illeggibile, non decodificabile).

## 5. PMC

- `ATL` e `CTL` sono medie esponenziali giornaliere del carico del giorno,
  con costanti di tempo 7 e 42 giorni (COG, BAN).
- `TSB(giorno) = CTL(giorno − 1) − ATL(giorno − 1)`: la forma del mattino usa i
  valori del giorno prima.
- Il carico del giorno è la somma dei carichi delle attività di quel giorno.
  Un giorno senza attività ha carico 0.
- Quale giorno si assegna a un'attività (fuso, mezzanotte, ora legale):
  **punto aperto**; si propone il giorno locale dell'ora d'inizio, con test
  T-CALC-07.
- Forma esatta dell'aggiornamento esponenziale: **punto aperto**. Gli esempi
  sotto usano `X(g) = X(g−1) + (L(g) − X(g−1)) / τ`, con τ = 7 per ATL e 42
  per CTL, valori iniziali 0. Se si sceglie `1 − e^(−1/τ)`, gli esempi
  cambiano.

## 6. Esempi verificabili a mano

Dati: FCmin 50, FCmax 190, soglia 170, sesso maschile (k = 1,92).

**Esempio A: un'ora alla soglia.**
HRr = (170 − 50) / 140 = 0,857143.
Per minuto: 0,857143 · 0,64 · e^(1,92 · 0,857143) = 0,548571 · e^1,645714 =
0,548571 · 5,18471 = 2,8442 TRIMP/min.
Un'ora: TRIMP_1h_soglia = 60 · 2,8442 ≈ 170,65. hrTSS = 100.

**Esempio B: 30 minuti a 120 bpm, corsa.**
HRr = (120 − 50) / 140 = 0,5.
Per minuto: 0,5 · 0,64 · e^0,96 = 0,32 · 2,611696 = 0,83574.
30 minuti: TRIMP = 25,07. hrTSS = 100 · 25,07 / 170,65 ≈ 14,69.
Carico corsa (peso 1,0) ≈ 14,7. Stessa seduta di forza (peso 1,2) ≈ 17,6.

**Esempio C: FC uguale alla minima.** HR = 50, HRr = 0, TRIMP = 0, carico 0.

**Esempio D: PMC con un solo giorno di carico 100** (giorno 0), poi zero.
- Fine giorno 0: ATL = 100/7 ≈ 14,286; CTL = 100/42 ≈ 2,381.
- TSB del mattino del giorno 1 = CTL(0) − ATL(0) ≈ −11,905.
- Fine giorno 1 (carico 0): ATL = 14,286 · 6/7 ≈ 12,245; CTL = 2,381 · 41/42
  ≈ 2,324.

**Esempio E: carico costante L = 50 ogni giorno.** CTL e ATL tendono a 50;
TSB tende a 0.

## 7. Test attesi

Vedi `T-CALC-01…07` in `PLAN.md` sezione 7.3. I valori degli esempi A–E sono
test di accettazione. Tolleranza del confronto tra implementazione vettoriale
e ingenua: dichiarata nel test (T-CALC-02).
