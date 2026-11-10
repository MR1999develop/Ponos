# Modello di carico

**Quando leggerlo:** scrivendo o rivedendo `load`, i test `T-CALC`, i grafici
del PMC o la documentazione dei numeri. Sezione 1 di `SKILL.md`. Fonti: BAN,
COG. ADR: `docs/adr/004-modello-carico.md`; misure datate e zone:
`docs/adr/007-dati-del-profilo-e-zone.md` (qui le regole operative, non le
motivazioni).

## 1. Formule

L'unica misura di carico è il **TRIMPeq** (decisione del 2026-11-10, emendamento
all'ADR 004): Banister sull'intensità equivalente di corsa. Non ci sono più
hrTSS né pesi per sport. Codice: `load/domain/banister.py`.

Per ogni campione di FC al tempo t, per un'attività di sport `s`:

- `HRr(t) = (HR(t) − FCmin) / (FCmax − FCmin)`; HR, FCmin, FCmax in bpm;
  HRr senza unità, tra 0 e 1.
- `HRr_T(x) = (Soglia_x − FCmin) / (FCmax − FCmin)`, rapporto della soglia
  dello sport x (`threshold_reserve_ratio`).
- `HRr_eq = min(1, HRr · HRr_T(corsa) / HRr_T(s))`
  (`equivalent_reserve_ratio`; il fattore è `equivalence_factor`). Equivale a
  `FCeq = FCmin + (FC − FCmin) · (Soglia_corsa − FCmin) / (Soglia_s − FCmin)`.
  Il clamp a 1 vale per la FC massima; HRr = 0 resta 0.
- `w = HRr_eq · 0,64 · e^(k · HRr_eq)` (`trimp_per_minute`).
  - `k = 1,92` per il sesso maschile, `k = 1,67` per il femminile (BAN;
    dal profilo).
  - Il coefficiente 0,64 è quello di Banister.
- `carico = 100 · Σ(Δt[min] · w) / (60 · w(HRr_T(corsa)))`; il denominatore è
  `reference_hour_weight`: un'ora alla soglia del proprio sport vale 100 in
  ogni sport (COG per la scala).
- Soglie per sport: solo da `threshold_for_sport` (`heart_rate.py`), unico punto
  da cambiare, per gruppo (`SportGroup`, `sport_group`): corsa e trail, ciclismo,
  altre attività (camminata, escursionismo, forza, altro). Il valore inserito nel
  profilo vince; se manca, ciclismo = `max(corsa − 4, FCmin + 1)` e altre = corsa
  (rapporto 1).

HR = FCmin dà carico 0. Per la corsa e per gli sport senza soglia propria il
carico coincide con l'hrTSS dei modelli precedenti. Perché questa scelta e le
alternative scartate: ADR 004, emendamento. Prezzo: il carico dipende dalla
soglia di corsa, quindi un errore di 4 bpm vale circa il 10%.

## 2. Parametri con data di validità

- FC minima, FC massima, soglia (e sesso) sono inseriti dall'utente.
- Ogni valore ha una data di validità. Un'attività usa i valori in vigore alla
  sua data d'inizio. Quale istante conta per un'attività a cavallo di un
  cambio: **punto aperto** (si propone: la data d'inizio).
- Quando un valore cambia, i carichi derivati si ricalcolano per
  tutte le attività dal giorno di validità in poi. Il risultato è uguale al
  calcolo da zero (T-CALC-05).
- Soglie del ciclismo e delle altre attività (schema v15): facoltative in
  `Physiology` (`bike_threshold`, `other_threshold`, `None` = default), nella
  stessa serie datata di FCmin, FCmax e soglia di corsa, con lo stesso limite
  `FCmin < soglia ≤ FCmax` per tutte e tre. Default del ciclismo
  `max(soglia della corsa − 4, FCmin + 1)` bpm (default del progetto, portato al
  limite invece di rifiutare). Nel caso d'uso `ThresholdReset.DEFAULT` torna al
  default, `None` lascia invariato. Entrano nell'impronta del carico solo se
  diverse dal default (nessun ricalcolo per chi non le imposta). Il test dei 30
  minuti aggiorna solo la soglia di corsa.
- Intervalli ammessi (default del progetto, nei tipi di dominio): FC minima
  20-250, FC massima fino a 250 con minima < massima, soglia 40-250 con
  minima < soglia <= massima; il vincolo si verifica sul risultato di un
  aggiornamento, non sul singolo valore.
- **Misure datate** (`profiles/measurements.py`): `UpdateMeasurements` dà a
  uno o più tra peso, FC minima, FC massima e soglia una data di validità
  (non nel futuro, non prima del 1900-01-01). I valori non indicati restano
  quelli validi **quel giorno** (non gli ultimi); lo stesso giorno sostituisce;
  il sesso resta quello della creazione; tutto in una transazione, insieme ai
  dati fissi da completare (un solo caso d'uso per "Modifica profilo").
  **Periodo di validità**: una misura vale dal suo giorno fino alla voce
  successiva già esistente della *stessa serie* (peso e parametri di FC sono due
  serie); quindi una misura con data passata non cambia ciò che vale dopo una
  voce più recente. Il caso d'uso restituisce il periodo effettivo
  (`ValidityPeriod`: da, fino al giorno prima della voce successiva o aperto)
  perché l'interfaccia lo mostri. Il peso
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
  sport` (`ore × 100 × IF²`, invariato dal TRIMPeq), marcato "stimato" in tabella e nei grafici. La durata è il **tempo
  in movimento** (`total_timer_time` del FIT; se manca, `total_elapsed_time`),
  non il tempo trascorso, che include le pause (default del progetto). La
  regola del 30% confronta i secondi contati con il 70% del tempo in
  movimento, in millisecondi interi, mai in virgola mobile al limite. Le intensità tipiche per
  sport: **punto aperto**, da fissare in fase 3.
- Forma esatta della tabella di stima e trattamento di attività senza durata:
  punto aperto.

## 4. Pesi degli sport (tolti il 2026-11-10)

Non esistono più pesi per sport: né nel calcolo, né nelle impostazioni, né nei
casi d'uso e nelle porte. L'equivalenza tra gli sport la dà il rapporto delle
soglie (sezione 1). Effetti: la forza non vale più ×1,2, le sedute alternative
non valgono più ×0,8, la bici è valutata sulla propria soglia.

- La tabella `sport_weights` resta nel database ma non è più né letta né
  scritta (expand-contract: la toglierà una migrazione futura).
- In `load_cache` la colonna `hr_tss` riceve 0; `trimp` e `load` ricevono il
  TRIMPeq.
- `LOAD_MODEL_VERSION` = 3; l'impronta `load_fingerprint(physiology)` non ha
  pesi. Tutti i carichi si ricalcolano da zero (T-CALC-05).

## 4b. Zone di FC

Cinque zone sulla soglia dello sport (in bici la soglia della bici), secondo
Friel, **5ª edizione** (FRI5, tab. 4.1; dalla fase 5; prima erano le zone di corsa delle
edizioni precedenti, 85/90/95/100); percentuali in un solo posto
(`ZONE_STARTS_PERCENT` in `load/domain/zones.py`):

| Zona | FC in % della soglia |
|---|---|
| Z1 | sotto 78% |
| Z2 | da 78% a 86% (sotto 87%) |
| Z3 | da 87% a 93% (sotto 94%) |
| Z4 | da 94% a 99% (sotto 100%) |
| Z5 | 100% e oltre |

- Il confine inferiore appartiene alla zona più alta (132,6 bpm con soglia 170 è
  il confine di Z2: 133 è Z2, 132 è Z1; 170 è esattamente il 100%: Z5). Il confronto è su interi (`bpm * 100`
  contro `percentuale * soglia`), mai su percentuali in virgola mobile.
- I **secondi in ogni zona** (`ActivityLoad.zone_seconds`, `ZoneSeconds`) sono
  quelli dei campioni contati dal carico: stessi scarti (FC fuori limiti) e
  stessi buchi (intervalli oltre 10 s esclusi). Senza serie di FC sono zero;
  per una stima fatta con pochi campioni validi sono quelli misurati.
- Si salvano nella cache dei carichi (colonne `zone1_s ... zone5_s`) legati alla
  stessa impronta del carico; l'impronta comprende le percentuali delle zone e
  `LOAD_MODEL_VERSION` (3 dal TRIMPeq). Cambiando le percentuali (fase 5: dalle zone
  85/90/95/100 a 78/87/94/100) l'impronta cambia, quindi i **secondi per zona si
  ricalcolano** alla prossima panoramica, mentre il carico **resta identico**
  (le zone classificano soltanto i secondi); `LOAD_MODEL_VERSION` non cambia perché la
  formula del carico non cambia. Le zone di ritmo dell'`assessment` (78/88/94/100
  della velocità di soglia) restano.
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

**Esempio A: peso di un'ora alla soglia di corsa** (`reference_hour_weight`, il
denominatore del carico, non un carico).
HRr_T(corsa) = (170 − 50) / 140 = 0,857143.
Per minuto: 0,857143 · 0,64 · e^(1,92 · 0,857143) = 0,548571 · e^1,645714 =
0,548571 · 5,18471 = 2,8442.
Un'ora: 60 · 2,8442 ≈ 170,65. Un'ora esatta alla soglia dà carico 100.

**Esempio B: 30 minuti a 120 bpm, sport senza soglia propria** (corsa, forza,
altro: rapporto 1).
HRr = HRr_eq = (120 − 50) / 140 = 0,5.
Per minuto: 0,5 · 0,64 · e^0,96 = 0,32 · 2,611696 = 0,83574.
30 minuti: 25,07. Carico = 100 · 25,07 / 170,65 ≈ 14,69, uguale in ogni sport
senza soglia propria (la forza non vale più 17,6).

**Esempio B2: un'ora in bici alla soglia della bici.** Soglia bici = 170 − 4 =
166; HRr_T(bici) = 116 / 140 = 0,828571; fattore = 0,857143 / 0,828571 ≈
1,03448. A 166 bpm HRr = 0,828571, HRr_eq = 0,857143: peso di un'ora 170,65,
carico 100. Valore congelato di `test_fase4_distanza` per la bici: 40,1935
(con i pesi era 32,18).

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
