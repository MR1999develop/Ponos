# Piano di allenamento

**Quando leggerlo:** scrivendo o rivedendo `planning` e `goals`, i test
`T-PIANO`, o il calendario. Sezione 2 di `SKILL.md`. Fonti: COG, SEI, RIE
(Riegel), FRI (zone). Tutti i numeri sono default del progetto e stanno in un
solo modulo, `planning/domain/defaults.py`; cambiarne uno cambia il piano di
ogni utente e fa salire `METHOD_VERSION`.

Il piano è un valore: il nucleo lo restituisce, il guscio lo salva o lo
invia. Nessun I/O nel motore.

## 1. Ingressi e uscita

- `goals/domain`: `RaceGoal` (id, data, priorità A/B/C, sport, distanza in
  metri 400–500 000, tempo obiettivo facoltativo 60 s–7 giorni), `FitnessGoal`
  (id, data, CTL 0–200), `WeeklyAvailability` (minuti massimi per giorno della
  settimana, lunedì = 0, con 0 = non disponibile, massimo 720; giorno del
  lungo; minuti massimi a settimana, ore × 60), `SessionChoices`
  (tipologie scelte per sport: facile, lungo, medio, soglia, VO2max,
  recupero, forza, alternativa). Tutti validi per costruzione.
- `planning/domain`: `PlanInputs` (obiettivi, disponibilità, tipologie,
  `Physiology`, `FitnessState` = CTL e ATL a fine di oggi, pesi degli sport,
  ritmo di soglia `int | None`, `after_injury`, `retest` = esito di
  `assessment.retest_status`). Funzione unica:
  `build_plan(inputs, today) -> Plan | PlanRefusal`.
- Il piano parte da **domani**; l'orizzonte arriva all'ultimo obiettivo, al
  massimo 364 giorni (52 settimane). Un obiettivo oltre l'orizzonte resta
  negli ingressi e dà l'avviso `GoalBeyondHorizon`.
- `Plan`: versione del metodo, parametri usati (`parameter_snapshot`), data di
  generazione, ingressi normalizzati, giorni (fase, seduta o gara, carico),
  settimane (tipo, minuti, carico), proiezione del PMC (CTL, ATL, TSB per
  giorno), giudizi sui tempi obiettivo, avvisi, proposta di retest.
  `Plan.to_json()` è la forma canonica.
- Sport del piano: quello del primo obiettivo A (altrimenti della prima
  gara, altrimenti corsa). Le tipologie valgono per quello sport; forza ha lo
  sport `STRENGTH`, l'alternativa lo sport `OTHER`. Una seduta al giorno.

## 2. Determinismo

- Stessi ingressi e `today` → piano identico byte per byte una volta
  serializzato (T-PIANO-01). `PlanInputs.normalised()` ordina gli obiettivi
  per (data, priorità, id): l'ordine di inserimento non conta.
- Serializzazione (`serialization.py`): JSON con chiavi ordinate, senza
  spazi, solo ASCII, float arrotondati a 3 decimali, dataclass con `_type`.
- Niente casualità né orologio nel motore.

## 3. Rifiuti e avvisi

`PlanRefusal(reason, goal_ids)`, motivi in `RefusalReason`, in quest'ordine:

| Motivo | Quando |
|---|---|
| `NO_GOALS` | nessun obiettivo |
| `GOAL_NOT_IN_FUTURE` | un obiettivo oggi o nel passato (gli id in `goal_ids`) |
| `GOALS_ON_SAME_DAY` | due gare nello stesso giorno |
| `NO_AVAILABILITY` | nessun giorno disponibile o zero minuti a settimana |
| `NO_SESSION_TYPES` | nessuna tipologia scelta per lo sport del piano |

Altrimenti il piano esiste sempre; ciò che non va sta negli avvisi
(`Plan.warnings`, un tipo per avviso):

| Avviso | Effetto da dire all'utente |
|---|---|
| `MissingLongSession` | mezza (da 21 097 m) o più senza lungo: la resistenza ne risente |
| `MissingIntenseSessions` | né soglia né VO2max: la costruzione senza lavoro di qualità (gare A e B; mai per le C) |
| `CloseARaces` | due A senza spazio per un blocco: scarico, recupero e mantenimento |
| `CtlTargetNotReached` | il CTL richiesto non si raggiunge alla rampa massima (scarto oltre 5 punti) |
| `TargetTimeUnreachable` | tempo obiettivo non raggiungibile entro la data |
| `RecoveryInserted` | TSB o ATL/CTL fuori soglia: i primi giorni sono di recupero |
| `LongDayUnavailable` | lungo scelto ma il giorno del lungo non ha tempo: nessun lungo |
| `GoalBeyondHorizon` | obiettivo oltre le 52 settimane |

Una tipologia mancante non si aggiunge mai.

## 4. Macrocycle (`macrocycle.py`)

Fasi per giorno: mantenimento, base, costruzione, picco, recupero, mini
scarico, scarico, gara. Una fase di rango più alto non viene mai
sovrascritta (la gara vince su tutto).

All'indietro da ogni A (e da ogni obiettivo di forma, che non ha scarico né
picco):

- **Scarico finale** (giorni prima della gara): fino a 10 km 7, fino alla
  mezza (21 097 m) 10, oltre 14.
- **Recupero dopo la gara A:** 3 / 5 / 7 giorni con le stesse soglie.
- **Picco** 14 giorni; **costruzione** fino a 8 settimane; **base** fino a 12
  settimane; il resto prima è **mantenimento** (quindi oltre 22 settimane
  prima solo mantenimento). Se i giorni liberi (dopo recupero e scarico) sono
  meno di 14 il blocco non c'è e restano mantenimento; con più di 14 si
  accorcia prima la base, poi la costruzione.
- Due A a 3 settimane: tra le due solo recupero, mantenimento e scarico
  prima della seconda, più l'avviso `CloseARaces`.
- **B:** 4 giorni di mini scarico prima della gara. **C:** nessuno.
- I giorni di recupero dell'adattamento (sezione 8) sono i primi giorni.

## 5. CTL obiettivo e rampa

- CTL richiesto da un A per distanza (tabella, interpolazione lineare):
  5 km 40, 10 km 45, mezza 55, maratona 70, 50 km 80, oltre 80; sotto i 5 km
  40. Un obiettivo di forma nello stesso giorno dell'A ne sostituisce il CTL;
  gli altri obiettivi di forma sono mete a sé.
- La meta si cerca su base e costruzione (`RampSpan`); picco e mantenimento
  tengono il livello. Ogni settimana: `rampa = (CTL meta − CTL attuale) /
  (settimane rimaste × 0,6)`, tra 0 e il massimo (+5 CTL a settimana, +3
  dopo un infortunio: parametro `after_injury`). Il carico giornaliero che dà
  quella rampa è `CTL + rampa / (1 − (41/42)^n)` (n giorni della settimana),
  al minimo 10.
- Il CTL del PMC proiettato non sale mai più del massimo in nessuna settimana
  del piano **contando solo i giorni di allenamento**: una gara non è una scelta
  di allenamento e il suo carico è fuori dal controllo della rampa. Il motore
  riduce il carico della settimana finché il vincolo regge.
- `CtlTargetNotReached` se a fine rampa il CTL proiettato è sotto la meta di
  più di 5 punti.

## 6. Mesocicli e settimana

- 3:1: tre settimane di carico e una di scarico al **65%** (60–70%) del
  carico giornaliero dell'ultima settimana di carico, come realizzato. Si
  conta dall'inizio del piano; una settimana con meno di 4 giorni normali non
  conta; nessuno scarico nelle settimane di picco.
- Scarico prima di A (giorni di scarico finale) e B (mini scarico): sedute
  facili al 65% dello stesso riferimento; la sera prima di una gara al massimo
  30 minuti. Nessuna seduta dura.
- Il carico settimanale va nei giorni disponibili; ogni giorno al massimo i
  minuti dell'utente e ogni settimana al massimo le sue ore (le gare non
  contano).
- **Dure** = lungo, medio, soglia, VO2max; **intense** = medio, soglia,
  VO2max. Mai due dure di seguito (nemmeno tra settimane, né accanto a una
  gara); al massimo 3 intense a settimana (2 in base).
- Composizione: lungo nel giorno scelto; qualità per fase: mantenimento
  soglia; base soglia e medio; costruzione e picco soglia e VO2max; le
  intense si distanziano il più possibile; forza una volta a settimana se
  scelta; gli altri giorni disponibili facile (altrimenti alternativa,
  altrimenti recupero). Nelle settimane di scarico solo lungo e facili.
- **80/20 per tempo:** in base e mantenimento almeno l'80% dei minuti in
  Z1–Z2; se non regge si tolgono le sedute intense, l'ultima per prima.
- Dimensionamento (`week.Sizer`): ogni seduta ha almeno la sua durata minima;
  se il budget di carico non le paga tutte si tolgono le meno importanti
  (forza e recupero, poi facile, medio, VO2max, soglia, lungo); il resto si
  divide per quote (lungo 2, facile e soglia 1, VO2max 0,8…) con durate a
  passi di 5 minuti arrotondate per difetto.

## 7. Modelli di seduta (`defaults.py`, `sessions.py`)

Zone FC e di ritmo sono quelle esistenti (`load.zones`, `assessment.pace_zones`);
ogni blocco ha il suo intervallo in bpm e in s/km (ritmo solo per corsa con
ritmo di soglia).

| Tipologia | Durata | Struttura |
|---|---|---|
| Facile | 20–90' | 10% Z1, 90% Z2 |
| Recupero | 20–40' (30' nei giorni di recupero) | Z1 |
| Lungo | 60–240' | 85% Z2, 15% Z3 |
| Medio | 40–90' | 20% Z2, 60% Z3, 20% Z1 |
| Soglia | 43–82' | 15' Z2, 2–4 × (8–12' Z4, rec 2–3' Z1; 2–3 ripetute in mantenimento), 10' Z1 |
| VO2max | 46–64' | 15' Z2, 4–6 × (3–4' Z5, rec 3' Z1), 10' Z1 |
| Forza | 30–60' | Z2 (sport forza, peso 1,2) |
| Alternativa | 30–120' | Z2 (sport altro) |

Le ripetute: soglia 8×2 in base e mantenimento, 10×3 in costruzione, 12×3 in
picco; VO2max 3×3 in costruzione, 4×3 in picco. Il recupero segue ogni
ripetizione tranne l'ultima.

**Carico stimato** con lo stesso modello del carico: hrTSS al minuto alla FC
che rappresenta la zona (75, 87, 92, 97, 103% della soglia, limitata alla FC
massima), con TRIMP di Banister e `TRIMP_1h_soglia` dello sport, per il peso
dello sport; carico = Σ minuti per zona × tasso della zona. Gara: stessa
formula con la miscela di zone per durata (fino a 60' Z3/Z4/Z5 al 20/60/20%;
fino a 150' Z3/Z4 30/70; oltre Z2/Z3 40/60); durata prevista = tempo obiettivo,
altrimenti il miglior tempo possibile, altrimenti 6:00/km.

## 8. Tempo obiettivo (T-PIANO-05)

- Distanza percorribile in 60' al ritmo di soglia: `3 600 000 / ritmo`
  metri. Tempo previsto oggi con Riegel: `3600 · (D / d60)^1,06`.
- Miglioramento ammesso fino alla data: 0,5% a settimana, al massimo 8%
  (`giorni/7 · 5‰`, tetto 80‰). Miglior tempo = previsto × (1 − miglioramento).
- Realistico se il tempo obiettivo ≥ miglior tempo; ambizioso se entro un
  ulteriore 3% (`obiettivo · 100 ≥ migliore · 97`, interi); altrimenti non
  raggiungibile (con avviso). Il confine appartiene alla classe più facile.
- Senza ritmo di soglia o per sport non di corsa: non stimabile.

## 9. Adattamento (T-PIANO-04)

Il piano si ricalcola da domani con `build_plan` sul PMC reale; il passato
resta storia (`compare_with_actual` dà per ogni giorno fino a oggi il carico
pianificato, il fatto e il perso; il fatto in più non annulla un giorno
saltato).

- TSB di domani sotto −30 (stretto) oppure ATL/CTL oltre 1,5 (stretto, con CTL
  almeno 10: sotto il rapporto non significa nulla) → i primi **2 giorni** sono
  di recupero: riposo, o una seduta di recupero di 30' se la tipologia è
  scelta; avviso `RecoveryInserted`.
- Il carico perso non si recupera: il piano riparte dal CTL reale e la rampa
  massima vale comunque. Attività non pianificate e attività vecchie arrivate
  tardi entrano nel PMC e quindi nel nuovo piano.
- Resta da fare nel guscio: caso d'uso che dopo ogni sincronizzazione legge il
  PMC, chiama `build_plan` e salva il piano.

## 10. Test di soglia nel piano

`Plan.retest_proposal`: senza test (`NoTest`) `RetestBeforePlan`; nella
finestra di `NextRetest` (o scaduta: da domani in poi) la prima settimana di
scarico del 3:1 che la tocca, `RetestInUnloadWeek(week_start)`; altrimenti
`RetestNotPlaceable`. Le settimane di scarico finale prima di una gara non
valgono.

## 11. Semplificazioni da ricordare

- Una seduta al giorno, un solo sport per piano (le tipologie sono quelle dello
  sport del piano).
- Forza e alternativa non sono mai "dure"; il recupero è sempre in Z1.
- Le ore massime non contano la gara. Le sedute dei giorni di scarico finale
  sono solo facili (o alternativa o recupero se manca la facile).
- Nessun deficit calorico, nessun infortunio attivo: arrivano con le fasi 5
  (alimentazione) e 6 (infortuni).

## 12. Test

`T-PIANO-01…05` in `PLAN.md` sezione 7.4: `tests/planning/` (determinismo,
proprietà con Hypothesis su ingressi casuali con `invariants.violations`,
scenari, adattamento, classi del tempo obiettivo ai confini, modelli di
seduta, prestazioni: 52 settimane in meno di 1 s).
