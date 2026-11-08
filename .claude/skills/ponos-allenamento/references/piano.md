# Piano di allenamento

**Quando leggerlo:** scrivendo o rivedendo `planning` e `goals`, i test
`T-PIANO`, o il calendario. Sezione 2 di `SKILL.md`. Fonti: COG, SEI10, RIE
(Riegel), FRI5 (zone FC della 5ª edizione); per distribuzione dell'intensità, scarico finale e lavoro a
ritmo gara TON24, HAU22, SAN25, KNO24, TON14, MUN15 (tabella in `SKILL.md`).
Tutti i numeri sono default del progetto e stanno in un
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
  ritmo di soglia `int | None`, `after_injury`, `injuries` = istantanee dei
  casi aperti di `injury`, `retest` = esito di `assessment.retest_status`). Funzione unica:
  `build_plan(inputs, today) -> Plan | PlanRefusal`.
- Il piano parte da **domani**; l'orizzonte arriva all'ultimo obiettivo, al
  massimo 364 giorni (52 settimane). Un obiettivo oltre l'orizzonte resta
  negli ingressi e dà l'avviso `GoalBeyondHorizon`.
- `Plan`: versione del metodo, parametri usati (`parameter_snapshot`), data di
  generazione, ingressi normalizzati, giorni (fase, seduta o gara, carico),
  settimane (tipo, minuti, carico, numero di sedute, quota di tempo in Z1–Z2 e
  quota di sedute intense: le due quote dell'80/20), proiezione del PMC (CTL, ATL, TSB per
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

- **Scarico finale** (giorni prima della gara), a gradini di una settimana
  contando all'indietro dalla gara, ciascuno una quota del carico della
  settimana di riferimento (l'ultima settimana di carico, come realizzata):
  fino a 10 km 7 giorni (−40%: 0,60); fino alla mezza (21 097 m) 14 giorni
  (−25% poi −45%: 0,75, 0,55; valori del progetto tra le due fonti HAU22 e
  KNO24); oltre (maratona e più) 21 giorni (circa −25% a settimana, ultima
  settimana circa −50%: 0,75, 0,625, 0,50; KNO24, HAU22). Il mini scarico delle
  B (4 giorni) prende la quota dello scarico del 3:1.
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
  con un infortunio o una malattia aperti o chiusi da non più di 28 giorni:
  `PlanInputs.ramp_reduced`). Il carico giornaliero che dà
  quella rampa è `CTL + rampa / (1 − (41/42)^n)` (n giorni della settimana),
  al minimo 10.
- Il CTL del PMC proiettato non sale mai più del massimo in nessuna settimana
  del piano **contando solo i giorni di allenamento**: una gara non è una scelta
  di allenamento e il suo carico è fuori dal controllo della rampa. Il motore
  riduce il carico della settimana finché il vincolo regge.
- `CtlTargetNotReached` se a fine rampa il CTL proiettato è sotto la meta di
  più di 5 punti.

## 6. Mesocicli e settimana

- 3:1: tre settimane di carico e una di scarico al **70%** (−30%; SAN25:
  −25…−35%) del carico giornaliero dell'ultima settimana di carico, come realizzato. Si
  conta dall'inizio del piano; una settimana con meno di 4 giorni normali non
  conta; nessuno scarico nelle settimane di picco.
- Scarico prima di A (giorni di scarico finale) e B (mini scarico): sedute
  facili con le quote di sopra del riferimento (il mini scarico al 70%); la sera
  prima di una gara al massimo 30 minuti. **La frequenza si mantiene**: una
  seduta per ogni giorno disponibile, anche da un carico di partenza nullo (il
  budget ha come minimo il costo delle sedute più brevi, finché ore e rampa lo
  permettono). **Una seduta di qualità accorciata a settimana** nello scarico
  finale (TON14, SAN25): al massimo 45 minuti, struttura ridotta (soglia 10' + 2
  × 8' + 10', VO2max 10' + 3 × 3' + 10'; il medio è il più corto), mai nelle
  ultime 72 ore prima di una gara (valore del progetto: si guarda a ogni gara,
  A, B e C), non accanto a un giorno duro; tipologia a ritmo gara (vedi sotto).
  Nessun lungo. Il mini scarico delle B non ha qualità.
- Il carico settimanale va nei giorni disponibili; ogni giorno al massimo i
  minuti dell'utente e ogni settimana al massimo le sue ore (le gare non
  contano).
- **Dure** = lungo, medio, soglia, VO2max; **intense** = medio, soglia,
  VO2max. Mai due dure di seguito (nemmeno tra settimane, né accanto a una
  gara); mai una intensa nelle 72 ore prima di una gara (valore del progetto).
  **Distribuzione della settimana** (`week.week_ok`, `defaults`):
  - al massimo il **25%** delle sedute con obiettivo intenso (TON24: LIT 75–80%
    delle sedute; soglia, VO2max e medio contano, il lungo no), ma da 3 sedute
    in su sempre almeno 1 (KNO24: 1–2 ad alta intensità per amatori), con meno
    di 3 nessuna: tetto `max(1, ⌊25% · sedute⌋)`. Con al massimo una seduta al
    giorno vuol dire 1 intensa con 3–7 sedute e nessuna con 1–2;
  - al massimo **3 giorni duri** a settimana, lungo compreso (SAN25: 2–3 giorni
    chiave) e al massimo 2 sedute intense se le sedute sono meno di 6 (KNO24:
    1–2 HIT per amatori); con il 25% sopra sono limiti ridondanti, tenuti come
    cinture di sicurezza;
  - tetti per numero invariati: 3 intense (2 in base);
  - il motore toglie le intense, l'ultima per prima (poi la qualità dello
    scarico), finché la settimana rispetta tutto.
- Composizione: lungo nel giorno scelto; qualità per fase: mantenimento
  soglia; base soglia e medio; costruzione soglia e VO2max; le tipologie della
  fase **si alternano di settimana in settimana** (una per settimana per il
  limite del 25%); le intense si distanziano il più possibile; forza una volta a settimana se
  scelta; gli altri giorni disponibili facile (altrimenti alternativa,
  altrimenti recupero). Nelle settimane di scarico solo lungo e facili.
- **80/20 per tempo:** in base e mantenimento almeno l'80% dei minuti in
  Z1–Z2; se non regge si tolgono le sedute intense, l'ultima per prima.
  `PlanWeek.low_zone_share` e `PlanWeek.intense_session_share` espongono le due
  quote di ogni settimana (1,0 e 0,0 senza sedute).
- **Picco e scarico per gara lunga (MUN15)**, classe di gara per distanza
  (`RaceClass`: fino a 10 km, mezza, maratona e oltre; nessuna nuova tipologia):
  - maratona e oltre: **niente VO2max nel picco**; la qualità è il **medio** con
    blocchi al ritmo previsto di maratona (zona 3), altrimenti la soglia (che
    non è ritmo maratona e non è marcata ritmo gara);
  - mezza: niente VO2max nel picco; la **soglia** con blocchi al ritmo mezza
    (zona 4), altrimenti il medio;
  - fino a 10 km: il picco alterna VO2max e soglia a ritmo gara (zona 4);
  - lo scarico finale usa la stessa tipologia a ritmo gara (fino a 10 km:
    soglia, altrimenti VO2max).
  Il ritmo gara (`Block.race_pace_s_per_km`, sui blocchi di lavoro nella zona
  della gara; `PlannedSession.race_pace` marca la seduta) viene dal tempo
  obiettivo o, senza, dal tempo previsto (Riegel con il miglioramento) e vale
  solo per le gare di corsa con il ritmo di soglia o il tempo obiettivo; senza
  nessuno dei due la seduta va per zona e il ritmo è `None`.
- Dimensionamento (`week.Sizer`): ogni seduta ha almeno la sua durata minima;
  se il budget di carico non le paga tutte si tolgono le meno importanti
  (forza e recupero, poi facile, medio, VO2max, soglia, lungo); il resto si
  divide per quote (lungo 2, facile e soglia 1, VO2max 0,8…) con durate a
  passi di 5 minuti arrotondate per difetto.

## 7. Modelli di seduta (`defaults.py`, `sessions.py`)

Zone FC e di ritmo sono quelle esistenti (`load.zones`, della 5ª edizione di Friel:
Z1 sotto 78% della soglia, Z2 78–86%, Z3 87–93%, Z4 94–99%, Z5 da 100%;
`assessment.pace_zones`, invariate); gli intervalli di FC di ogni blocco seguono da sole le
zone, e così quelli di ritmo; le strutture della tabella qui sotto non cambiano, ma una zona
è più larga (la Z2 va da 78% a 87%). Ogni blocco ha il suo intervallo in bpm e in s/km (ritmo solo per corsa con
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
che rappresenta la zona (70, 82, 90, 97, 103% della soglia, limitata alla FC
massima: circa il mezzo di ogni zona delle zone nuove, 103 per la Z5, default del progetto;
prima della versione 3 erano 75, 87, 92, 97, 103), con TRIMP di Banister e `TRIMP_1h_soglia` dello sport, per il peso
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
- Il guscio (sezione 13) fa questo dopo ogni panoramica: legge il PMC reale,
  chiama `build_plan`, salva il piano se è cambiato e congela il passato.

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
- Gli infortuni attivi (fase 6) restringono le tipologie scelte (`planning/domain/
  health.py`): corsa per grado, bici e forza per zona, niente intensità prima
  dell'ultimo gradino, nessuna seduta con un segnale d'allarme o una malattia
  sistemica; avvisi `PlanHalted`, `PlanAdapted`, `NoSessionPossible`. Dettagli in
  `references/infortuni.md`.

## 12. Test

`T-PIANO-01…05` in `PLAN.md` sezione 7.4: `tests/planning/` (determinismo,
proprietà con Hypothesis su ingressi casuali con `invariants.violations`,
scenari, adattamento, classi del tempo obiettivo ai confini, modelli di
seduta, distribuzione e scarico in `test_distribution.py`, prestazioni: 52
settimane in meno di 1 s, nella pratica circa 10 ms).

Invarianti di `invariants.py` oltre a quelli di prima: al massimo
`max(1, ⌊25%⌋)` sedute intense a settimana da 3 sedute in su, nessuna con meno (e 2 con meno di 6 sedute), al massimo 3 giorni
duri, nessuna intensa nei 3 giorni prima di una gara, nessun VO2max nel picco
di mezza e maratona, quote esposte coerenti con le sedute, settimana intera di
scarico con una seduta per ogni giorno disponibile (non controllato con ore
settimanali inferiori ai minuti dei giorni disponibili, dopo infortunio, con CTL
sotto 10 o senza tipologia facile: lì le ore o la rampa decidono quali sedute
togliere).

Versione del metodo: **4** (infortuni e malattie aperti; la 3 aveva le zone FC della 5ª edizione e FC rappresentative delle zone
per il carico stimato; la 2 aveva scarico a gradini, distribuzione 80/20 per sedute,
giorni duri, ritmo gara, scarico del 3:1 al 70%). I piani salvati con la
versione 1 non si rileggono (il formato ha campi in più), né quelli della 3 (il campo
`injuries` in più): il guscio li ricalcola; quello della 2 resta leggibile (stesso formato) finché il piano non si
rigenera (a ogni sincronizzazione e ogni giorno). Con le zone più larghe un minuto facile pesa meno nel
carico stimato: per lo stesso CTL il piano chiede più minuti (o non arriva al livello se
le ore sono poche; `test_a_plan_from_ctl_100…` tiene il CTL sopra 65).

## 13. Guscio: obiettivi, piano salvato, passato (fase 5)

- `goals/use_cases.py`: `AddGoal`, `ChangeGoal`, `RemoveGoal`, `ListGoals`,
  `SetAvailability`, `ReadAvailability`, `SetSessionChoices`,
  `ReadSessionChoices`. Ogni comando restituisce ciò che serve per annullarlo
  in modo esatto (`GoalAdded`, `GoalChanged(before, after)`, `GoalRemoved`,
  `AvailabilityChanged(before, after)`, `SessionChoicesChanged(before, after)`);
  l'id di un obiettivo lo sceglie chi chiama. Errori: `GoalNotFoundError`,
  `GoalAlreadyExistsError`, `RaceDayTakenError` (due gare nello stesso giorno),
  radice `GoalStoreError`.
- `profiles/rebuild_plan.py`: `RebuildPlan(sources, overview)(today)` legge
  disponibilità, tipologie, obiettivi, fisiologia del giorno, pesi, ultimo
  ritmo di soglia (il test più recente che ne ha uno, non oltre oggi), stato del
  retest e il PMC (stessa `compute_pmc` della panoramica sui carichi già in
  cache), chiama `build_plan` e salva se l'esito differisce dall'ultimo
  (confronto sul JSON canonico). Esiti: `PlanBuilt(plan, saved)`,
  `PlanRefused(refusal, saved)` (anche il rifiuto è uno stato salvato),
  `PlanNotBuilt(MISSING_PHYSIOLOGY)` (nulla salvato). `PlanAdherence(plans,
  overview, today)(first, last)` dà `DayAdherence` per i giorni pianificati
  fino a oggi, con il pianificato di allora.
- `planning/use_cases.py`: `CurrentPlan()` (ultimo piano o rifiuto con la data
  di generazione, `None` se manca o è illeggibile) e `PastPlan()(first, last)`.
- **Il passato resta storia.** Un piano copre solo i giorni dopo la sua data di
  generazione. Quando un nuovo esito sostituisce quello salvato, nella stessa
  transazione i giorni del vecchio piano diventati passati (dopo la sua data di
  generazione, fino a oggi) si congelano in `plan_past_day`, ciascuno con la data
  del piano da cui viene (`FrozenDay`); un giorno già congelato non si
  riscrive. Quindi per un giorno passato vale ciò che era pianificato in quel
  giorno, qualunque cosa direbbe il piano di oggi. Schema scelto perché è il più
  semplice che lo garantisca: una riga per giorno, immutabile per trigger, senza
  versioni intere del piano da conservare.
- **Aggancio al ricalcolo:** `ui/workspace.py` (`WorkspaceController`), dopo
  ogni panoramica calcolata con successo (apertura del profilo, import,
  sincronizzazione, cambio di misure o pesi) mette in coda un secondo lavoro sul
  thread dei dati che ricostruisce il piano dai carichi appena calcolati
  (`_start_plan`); segnale `plan_rebuilt(PlanBuilt | PlanRefused | PlanNotBuilt)`.
  Un nuovo calcolo della panoramica annulla il lavoro del piano in coda; se il
  piano fallisce il thread dei dati registra l'errore una sola volta (solo i
  tipi, nessun dato di salute) e la sincronizzazione non ne risente.
