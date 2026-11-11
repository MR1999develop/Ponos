# Alimentazione

**Quando leggerlo:** scrivendo o rivedendo `nutrition`, `profiles/day_nutrition.py`,
i test `T-NUT` o la pagina "alimentazione" del
giorno. Sezione 5 di `SKILL.md`. Fonti: ISSN, ACSM, IOC, GJSN, CSN, MSJ, KEYTEL,
COMPENDIUM, BOER.

Sono indicazioni generali, mai consigli medici o nutrizionali personalizzati.
Con patologie, gravidanza, disturbi alimentari o farmaci ci si rivolge a un
professionista. Ogni valore qui sotto è un **default del progetto** tratto
dalle linee guida citate, e sta in un solo file:
`src/ponos_coach/nutrition/domain/defaults.py`. Se un numero cambia, cambiano
anche questo file e i test `T-NUT`.

## Fonti

| Sigla | Fonte | Per che cosa |
|---|---|---|
| MSJ | Mifflin e altri, *A new predictive equation for resting energy expenditure* (1990) | metabolismo di base |
| PAL | Moltiplicatori del metabolismo di base per livello di attività. Qui **senza sport** (default del progetto): quelli classici di Harris-Benedict (1,375, 1,55, 1,725) includono già l'esercizio | fattore del livello fuori dallo sport |
| MININTAKE | Limiti minimi di energia giornaliera per una dieta senza supervisione che le organizzazioni sanitarie citano di norma: 1.200 kcal donne, 1.500 kcal uomini (default del progetto) | minimo assoluto del deficit |
| KEYTEL | Keytel e altri, *Prediction of energy expenditure from heart rate monitoring during submaximal exercise* (2005) | calorie da FC media, età, peso, sesso |
| COMPENDIUM | *Compendium of Physical Activities* (MET per attività) | calorie senza FC |
| ISSN | International Society of Sports Nutrition, posizioni su tempi dei nutrienti, proteine, diete e composizione corporea | carboidrati per carico, proteine, momenti |
| ACSM | ACSM, Academy of Nutrition and Dietetics e Dietitians of Canada, *Nutrition and Athletic Performance* (2016) | carboidrati, liquidi, ritmo di perdita (sotto l'1% del peso a settimana), rifornimento rapido (meno di 8 ore tra due sedute), disponibilità energetica (45 equilibrio, sotto 30 dannosa) |
| BOER | Boer (1984), stima della massa magra da peso, altezza e sesso | massa magra per la disponibilità energetica, se manca una misura |
| IOC | Consenso del CIO sulla nutrizione dello sportivo | carboidrati durante, recupero |
| GJSN | Jeukendrup, Gleeson, *Sport Nutrition* 3ª ed. (Human Kinetics 2019) | rifornimento rapido orario (1,0–1,2 g/kg all'ora per 4 ore con meno di 8 ore alla seduta successiva), proteine in dosi da 0,4 g/kg, disponibilità energetica, dimagrimento negli atleti |
| CSN | Burke, Deakin, *Clinical Sports Nutrition* 5ª ed. (McGraw-Hill 2015) | disponibilità energetica (30–45 tollerabile solo per brevi periodi di dimagrimento pianificato), massa magra misurata meglio di quella stimata |

## 1. Calorie delle attività

- Il totale dichiarato dal dispositivo (FIT `session.total_calories`) entra nel
  riepilogo (`ActivitySummary.calories_kcal`) e nell'indice (colonna
  `activities.calories_kcal`, schema v4). Zero e il valore non valido del FIT
  significano "non calcolato"; sopra 50.000 kcal non è credibile. Con più sedute
  nel file si somma solo se tutte lo dichiarano.
- Il totale dichiarato si usa solo se è credibile per la durata: **al massimo
  1.500 kcal per ora** di tempo in movimento (`MAX_ACTIVITY_KCAL_PER_HOUR`,
  default del progetto). Oltre, si scarta (mai si taglia al tetto) e si stima
  come se mancasse.
- Se mancano, in quest'ordine (`activity_energy`):
  1. **Keytel** con la FC media del carico (`ActivityLoad.avg_bpm`: pesata sul
     tempo, sui campioni dentro `[FCmin, FCmax]` con intervallo fino a 10 s, la
     stessa del dettaglio; mai la media semplice dei campioni, che con la
     registrazione intelligente sottostima: revisione del 2026-11-11), peso, età e
     sesso (FC tra 40 e 250 bpm):
     - uomo: `kJ/min = −55,0969 + 0,6309·FC + 0,1988·peso + 0,2017·età`;
     - donna: `kJ/min = −20,4022 + 0,4472·FC − 0,1263·peso + 0,074·età`;
     - `kcal/min = kJ/min / 4,184`, mai sotto zero;
  2. **MET per sport** (COMPENDIUM, lordi): corsa 9,8; trail 9,0; bici 8,0;
     escursione 6,0; camminata 3,5; forza 3,5; altro 4,0 (default del
     progetto). `kcal = MET · kg · ore`.
- Durata: il tempo in movimento (`timer_s`).
- **Calorie nette** = totali − metabolismo di base della durata
  (`BMR/86400 · secondi`), mai sotto zero. La spesa del giorno usa le nette, così
  il riposo non si conta due volte.
- Le attività già indicizzate prima della v4 hanno NULL nell'indice. Il caso
  d'uso del giorno legge comunque il file originale (serve al carico) e ne usa
  le calorie dichiarate; l'indice non si aggiorna (è una query che non salva
  nulla).
- La **fine** di una seduta è sempre inizio + tempo trascorso (`elapsed_s`),
  calcolata in UTC e poi mostrata in ora locale: così il cambio dell'ora legale
  non sposta il recupero. Il tempo in movimento serve per le soglie e per il
  "durante".

## 2. Spesa del giorno

- **Metabolismo di base** (MSJ), kcal/giorno:
  `10·kg + 6,25·cm − 5·età + 5` (uomo), `… − 161` (donna). L'età è in anni
  compiuti nel giorno; il peso è quello valido quel giorno (un cambio vale dal
  suo giorno; prima della prima voce vale la prima, come per la FC).
- **Livello fuori dallo sport** (con data di validità), fattori **senza sport**
  (default del progetto: lo sport registrato dall'app si somma a parte, i
  fattori classici 1,375/1,55/1,725 lo contano già e lo conterebbero due volte):
  sedentario 1,2 (vita da scrivania); leggero 1,3 (un po' di cammino); attivo
  1,4 (lavoro in piedi o a passo); molto attivo 1,5 (lavoro manuale pesante).
  Senza livello: sedentario e avviso `ACTIVITY_LEVEL_ASSUMED`.
- **Spesa** = `base · fattore + Σ calorie nette`, arrotondata al kcal intero.

## 3. Obiettivo e deficit

- Tre obiettivi per **periodi** senza sovrapposizioni (inizio e fine inclusi, la
  fine può mancare = fino al periodo successivo); senza periodo vale il
  mantenimento: **mantenimento** (apporto = spesa), **performance** (apporto =
  spesa, carboidrati in cima alla fascia), **dimagrimento** con ritmo da 100 a
  1000 g a settimana.
- **Ritmo ammesso** = il minore tra l'obiettivo, `MAX_WEEKLY_LOSS_G` (1000 g,
  default del progetto) e l'**1% del peso del giorno** (ACSM: sotto l'1% a
  settimana per conservare la massa magra). Deficit richiesto = `ritmo / 1000 ·
  7700 / 7` kcal al giorno (500 g con 80 kg → 550 kcal; 800 g con 50 kg → 500 g →
  550 kcal; default del progetto: 7700 kcal per kg). Se il ritmo è tagliato
  c'è `DEFICIT_LIMITED`.
- **Limiti di sicurezza** (sempre, in quest'ordine, su interi):
  1. nessun deficit con IMC < 18,5 (avvisi `LOW_BMI` e
     `DEFICIT_NOT_APPLIED`); sotto i 18 anni il modello non dà numeri (vedi
     sotto);
  2. al massimo il 20% della spesa e 750 kcal;
  3. ridotto nei giorni di carico alto: × 0,5 (alto), × 0,25 (molto alto)
     (`DEFICIT_REDUCED_FOR_LOAD`);
  4. mai sotto il **massimo tra il metabolismo di base e il minimo assoluto per
     sesso** (MININTAKE: 1.200 kcal donne, 1.500 kcal uomini):
     `apporto ≥ max(base, minimo)` (`DEFICIT_LIMITED` quando un limite taglia la
     richiesta). Se la spesa è già sotto il minimo il deficit è 0 e l'avviso c'è;
  5. **disponibilità energetica** `EA = (apporto − energia dell'esercizio) /
     massa magra` (kcal per kg di massa magra al giorno) mai sotto **30**
     (`MIN_ENERGY_AVAILABILITY_KCAL_PER_KG_FFM`; ACSM: 45 è l'equilibrio, sotto
     30 si hanno danni). L'energia dell'esercizio è la stessa energia netta che
     la spesa somma, quindi il limite è `base · fattore − 30 · massa magra` e
     non cambia con l'allenamento. Massa magra stimata con **Boer (1984)**:
     uomo `0,407·kg + 0,267·cm − 19,2`, donna `0,252·kg + 0,473·cm − 48,3`,
     limitata a `[0,5·peso, peso]`. **Se l'utente ha una massa magra misurata
     valida quel giorno (sezione 7), sostituisce la stima di Boer** (CSN: una
     misura vale più di una stima; `fat_free_mass_kg(persona)`). Vale il limite
     più restrittivo; il deficit non è mai negativo (`DEFICIT_LIMITED`). Per
     molte persone è questo, non il metabolismo di base, a mordere per primo.
  La disponibilità del giorno è `energy_availability(persona, apporto, energia
  netta dell'esercizio)`; è una stima, non una misura.
- **Dimagrimento lungo** (`Advisory.LONG_WEIGHT_LOSS`, in coda all'elenco degli
  avvisi): se il periodo di dimagrimento in corso dura da **più di 12
  settimane** (`LONG_WEIGHT_LOSS_WEEKS`; più di 84 giorni dal primo giorno del
  periodo, che è il giorno 0), il giorno ha un deficit applicato e la
  disponibilità del giorno `(apporto − energia netta dell'esercizio) / massa
  magra` è **sotto 45** (`BALANCED_ENERGY_AVAILABILITY_KCAL_PER_KG_FFM`),
  l'avviso invita a una pausa a mantenimento. CSN e GJSN dicono che 30–45 è
  tollerabile solo per periodi brevi ma senza un numero di settimane (CSN: «8–12»):
  **12 è un default del progetto**. Il dominio dà la categoria, il testo è dell'interfaccia
  (neutro, nessuna diagnosi). `DayInput.weight_loss_days` dice da quanto dura il
  periodo (`None` fuori da un dimagrimento); lo calcola `PlanDayNutrition`.
- **Minorenni**: sotto i 18 anni il modello non produce kcal, macro né porzioni.
  `PlanDayNutrition` restituisce `NutritionUnavailable(UNDER_18)` (prima di
  chiedere peso o FC); la pagina non mostra numeri e invita a rivolgersi a un
  medico o a un dietista sportivo. Le funzioni pure del dominio (`plan_day`, `energy_target`) mantengono
  l'avviso `UNDER_18` e non applicano deficit, ma non vengono chiamate per
  i minorenni.
- Età oltre 130 anni dalla nascita (giorno lontano nel futuro):
  `NutritionUnavailable(AGE_OUT_OF_RANGE)`, mai un'eccezione.

## 4. Livello del giorno e macro

- **Livello** = il più alto tra quello del carico del giorno (somma dei carichi:
  ≥ 60 moderato, ≥ 120 alto, ≥ 200 molto alto) e quello del tempo (≥ 60 min
  moderato, ≥ 120 alto, ≥ 240 molto alto); qualsiasi seduta è almeno leggera;
  senza sedute è riposo (default del progetto, che traduce le fasce orarie di
  ISSN/ACSM nel carico dell'app: 100 = un'ora alla soglia).
- **Carboidrati**, g/kg/giorno: riposo e leggero 3–5; moderato 5–7; alto 6–10;
  molto alto 8–12. Dimagrimento: estremo basso; mantenimento: metà; performance:
  estremo alto.
- **Proteine** 1,6 g/kg (2,0 in deficit); dopo la seduta 0,4 g/kg (GJSN, era 0,3). **Grassi** = il resto, almeno il 20%
  dell'energia. Se l'energia non basta, cedono i carboidrati
  (`CARBS_LIMITED_BY_ENERGY`). Grammi interi; 4/4/9 kcal per grammo.

## 5. Momenti e pasti (`nutrition/domain/meals.py`)

Per ogni seduta di almeno 30 minuti (le più brevi non cambiano nulla):

- **Pre** (oltre 60 min): carboidrati 1 g/kg, 2 ore prima (1 ora se 2 ore prima
  sarebbe prima delle 05:00).
- **Durante** (oltre 75 min, sul tempo in movimento): 45 g/h di carboidrati
  fino a 2,5 h, **60 g/h** oltre. Il tetto è 60 g/h: oltre (fino a 90 g/h, IOC)
  servono miscele glucosio-fruttosio, che la tabella non distingue. Liquidi
  600 ml/h e sodio 300 mg/h, indicativi (ACSM).
- **Post**, proteine 0,4 g/kg (GJSN). Due casi:
  - **un'altra seduta a meno di 8 ore** (`SPEEDY_REFUEL_WITHIN_MIN`, ACSM,
    ISSN, GJSN; a 8 h esatte no): rifornimento rapido, **1,1 g/kg di carboidrati
    all'ora per le prime 4 ore** (`REFUEL_HOURS`, `REFUEL_CARBS_G_PER_KG_H`, il
    mezzo di 1,0–1,2), servito in **2 apporti a 2 ore di distanza**
    (`REFUEL_INTAKES`, `REFUEL_STEP_MIN`), ciascuno con i carboidrati delle sue
    due ore (176 g a 80 kg). Scelta dell'utente del 2026-11-09: un apporto ogni
    ora era ingestibile; con un totale adeguato pasti frequenti e meno frequenti
    riforniscono allo stesso modo. Il primo apporto è 30 minuti dopo la fine e
    c'è sempre, il secondo almeno 30 minuti prima della seduta successiva. Le
    proteine stanno nel primo. Ogni apporto è un `MealItem` con `refuel_intake`
    1 o 2 (`refuel_hours` dà le ore coperte, 1–2 o 3–4) e **si fonde** come gli
    altri apporti con un pasto o un apporto a meno di 90 minuti (l'elemento fuso
    perde il numero ed elenca i suoi slot). Stanno **dentro il budget dei
    carboidrati del giorno**: pranzo e cena tengono i loro minimi e gli apporti
    si servono in ordine di tempo (durante, dopo, prima); un apporto rimasto
    senza carboidrati e senza proteine
    sparisce. Scelta del progetto: scatta per qualunque seduta successiva entro 8 h, anche
    breve;
  - **altrimenti** il «dopo» resta **una dose** 30 minuti dopo la fine: carboidrati
    1,1 g/kg solo se la seduta dura almeno 90 min. Una seduta del giorno dopo si
    passa come `minutes_to_next_session` e di norma supera le 8 ore.

Il resto del giorno si divide in colazione 25%, pranzo 40%, cena 35% agli orari
tipici (default 07:30, 13:00, 20:00; almeno due ore tra l'uno e l'altro):

- un pasto dall'ora prima dell'inizio all'ora dopo la fine di una seduta si
  sposta subito dopo (o subito prima, se dopo sarebbe oltre le 23:00);
- gli apporti a meno di 90 minuti l'uno dall'altro si **fondono** (l'elemento
  ha più `slots` e l'ora dell'ultimo);
- i totali sono ripartiti con il metodo del resto più grande: la somma degli
  elementi è sempre il totale del giorno, mai quantità negative;
- **se i carboidrati del giorno non bastano a tutti gli apporti**: pranzo e cena
  tengono prima almeno 1 g/kg ciascuno (`MAIN_MEAL_MIN_CARBS_G_PER_KG`, default
  del progetto; la colazione cede per prima); il resto va agli apporti in
  quest'ordine: durante, dopo, prima (avviso `INTAKES_SCALED`). Il riquadro
  orario del "durante" si riallinea ai grammi dell'elemento (g/h = grammi / ore,
  arrotondati per difetto; senza carboidrati resta senza riquadro): riquadro e
  somma coincidono sempre.

## 6. Alimenti: tolti (2026-11-10)

- Per richiesta dell'utente ogni pasto mostra solo kcal e grammi di carboidrati,
  proteine e grassi (`MealItem`). Tabella degli alimenti (`foods.json`, fonte
  ANSES-CIQUAL), esempi, porzioni, tolleranze e ruoli del pasto sono stati tolti
  dal codice, dal pacchetto e dagli strumenti (`verify_foods.py`); ADR 008
  emendato. `DayNutritionReport.meals` non esiste più: i pasti si leggono da
  `report.nutrition.meals`. `PlanDayNutrition` non riceve più la tabella.

## 7. Dati e casi d'uso

- **Massa magra misurata** (schema v7): misura datata facoltativa (da DXA o
  bilancia impedenziometrica), in decimi di chilo tra 15,0 e 200,0 kg
  (`LeanMass`, `LeanMassEntry`, tabella `lean_mass`); vale dal suo giorno fino
  alla voce successiva, **non** per i giorni prima della prima misura. Ha senso solo
  se compatibile col peso: tra il 40% e il 95% del peso del giorno
  (`is_plausible_lean_mass`, default del progetto; GJSN non scendere sotto il 5%
  di grasso per gli uomini e il 12% per le donne). `PlanDayNutrition` la usa solo
  se è plausibile col peso di quel giorno, altrimenti vale Boer. Porta
  `LeanMassStore` (`nutrition/ports.py`, `OpenProfile.lean_masses`); casi d'uso
  in `profiles/lean_mass.py`: `SetLeanMass(attività, pesi, store, oggi)(massa,
  dal_giorno) → AppliedLeanMass(entry, replaced)`, `UndoLeanMass(applied)` (annulla
  esatto: toglie la voce e rimette quella sostituita; rifiuta con
  `LeanMassChangedError` se la voce è cambiata dopo), `RemoveLeanMass(giorno) →
  voce tolta`, `RestoreLeanMass(voce, attesa=None) → AppliedLeanMass` (rimette la
  voce esatta in una transazione, senza ricontrollare il peso, se il giorno contiene
  `attesa`, cioè libero per default; è l'annulla di `RemoveLeanMass` e il redo di un
  `SetLeanMass`), `ListLeanMass`. `SetLeanMass` rifiuta una data futura, un peso
  assente e una massa non plausibile. `DayNutritionReport.lean_mass_measured` dice
  se il giorno ha usato la massa misurata; la pagina legge quel campo.

- Schema v4: `activities.calories_kcal`; `activity_level` (data di validità),
  `meal_times` (una riga), `nutrition_goal` (periodi, con trigger contro le
  sovrapposizioni). Migrazione con copia in `backup/ponos-v3-v4.sqlite3`.
- Porte in `nutrition/ports.py` (`ActivityLevelStore`, `MealTimesStore`,
  `GoalPeriodStore`); casi d'uso in `nutrition/use_cases.py`
  (`SetActivityLevel`, `SetMealTimes`, `AddGoalPeriod`, `ReplaceGoalPeriod`,
  `RemoveGoalPeriod`, `ReadNutritionSettings`).
- `NutritionSources.lean_masses` è facoltativo (`None`: vale sempre Boer); il
  Composition Root passa `profile.lean_masses`.
- `profiles/day_nutrition.py`: `PlanDayNutrition(sources, decode, timezone,
  foods)(day)` raccoglie dal profilo aperto e restituisce `DayNutritionReport` o
  `NutritionUnavailable(reason)` (`MISSING_BODY_DATA`, `BEFORE_BIRTH`,
  `AGE_OUT_OF_RANGE`, `UNDER_18`, `MISSING_WEIGHT`, `MISSING_PHYSIOLOGY`, `NO_PLAN_FOR_DAY`). È una
  query: non salva nulla.
- Le attività del giorno si leggono con una query per intervallo sull'indice
  (`ActivityIndex.list_started_between`, finestra di un giorno per lato, poi il
  filtro esatto sul giorno locale) e la seduta successiva con
  `first_start_at_or_after`: mai la lista intera a ogni giorno.
- Il trigger `nutrition_goal_no_overlap_on_update` (accanto a quello su
  `INSERT`) impedisce la sovrapposizione anche con un `UPDATE` delle date.
- **CREA** (alimentinutrizione.it, tabelle 2019): il sito non permette di
  riprodurre i dati senza citare la fonte, non offre scaricamento né licenza
  aperta: i valori non si copiano. `experiments/crea_mapping.json` ha solo
  codice e titolo della voce di ogni alimento; `--crea-fetch` (o `--crea-dir`)
  stampa le differenze come rapporto, mai come errore (rese di cottura e metodi
  diversi: la maggioranza degli alimenti differisce oltre il 10%).

## 8. Giorni futuri dal piano (fase 5, ADR 011)

- `PlanDayNutrition(..., plan_context=PlanContext(plans, oggi))`: per un giorno
  **dopo oggi** le attività sono le sedute del piano salvato (`CurrentPlan`):
  la seduta e/o la gara del giorno, con durata, carico e sport del piano e
  l'energia dai MET dello sport (nessuna FC, `EnergySource.MET`; i MET sono uno
  per sport, non per zona). Il risultato ha `from_plan=True`, la pagina scrive
  «Estimate from the plan: the activities are the planned sessions.». Senza
  `plan_context` ogni giorno si calcola dalle attività registrate, come prima.
- **Ora della seduta**: media (minuti dalla mezzanotte, arrotondata) degli inizi
  delle attività degli ultimi 60 giorni fino a oggi nello stesso giorno della
  settimana; senza attività 18:00 (`TYPICAL_TIME_LOOKBACK_DAYS`,
  `DEFAULT_TRAINING_MINUTE`, default del progetto). La seduta successiva
  (`minutes_to_next_session`) è la prima seduta del piano nei giorni dopo, alla
  sua ora tipica.
- Peso, livello, massa magra, obiettivo e pasti: come per i giorni passati
  (l'ultimo valore valido quel giorno).
- **Nessun deficit attorno alle gare** (ACSM 2016: dimagrire lontano dalle
  gare): `DayInput.deficit_paused` è vero nei giorni di fase `TAPER` (scarico
  finale dell'A) o `MINI_TAPER` (B) e in tutta la settimana lunedì-domenica di una
  gara A o B (anche dopo la gara). Le gare C non fermano nulla. Con obiettivo di
  dimagrimento il modello calcola energia e macro come per il mantenimento
  (deficit 0) e aggiunge `Advisory.DEFICIT_PAUSED_FOR_RACE` (dopo
  `DEFICIT_REDUCED_FOR_LOAD`); con altri obiettivi non cambia nulla e non c'è
  avviso. L'obiettivo mostrato resta quello scelto. **Anche oggi**, che non è
  nel piano (parte da domani), non ha deficit se cade nella settimana
  lunedì-domenica di una gara A o B del piano salvato o se il primo giorno del
  piano (domani) è in `TAPER` o `MINI_TAPER`; i giorni prima di oggi non cambiano
  (`_deficit_paused` in `profiles/day_nutrition.py`; la settimana si legge da
  `Plan.inputs.races`, che comprende anche le gare già passate della settimana).
- `NutritionUnavailable(NO_PLAN_FOR_DAY)`: nessun piano salvato, un rifiuto, un
  piano illeggibile o un giorno oltre l'orizzonte (il piano arriva all'ultimo
  obiettivo). Prima si controllano i dati del corpo e l'età.
- **Rifornimento orario** (`nutrition/domain/meals.py`): la prima assunzione
  aspetta 30' dopo la fine (`POST_LEAD_MIN`), le successive sono una ogni ora, l'ultima
  almeno 30' prima della seduta dopo: quante ne stanno =
  `floor((minuti_alla_prossima - 30 - 30) / 60) + 1`, da 1 a 4. Quelle che cadrebbero
  dopo la mezzanotte si omettono (il giorno dopo non le eredita; la prima resta anche
  se cade oltre, fissata a 23:59). Un apporto ridotto a 0 g di carboidrati e proteine
  (per esempio il «prima» di un giorno con pochi carboidrati) non si genera.
- Il calendario apre la pagina di un giorno futuro solo se il piano ha quel
  giorno (`CalendarDay.can_open`).

## Punti aperti

- Le calorie lette dal file per le attività indicizzate prima della v4 non
  aggiornano l'indice (la pagina usa il valore letto).
- Allergie e restrizioni alimentari: non per ora (decisione del 2026-11-07).
