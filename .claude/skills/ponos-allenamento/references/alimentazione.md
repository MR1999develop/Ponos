# Alimentazione

**Quando leggerlo:** scrivendo o rivedendo `nutrition`, `profiles/day_nutrition.py`,
la tabella degli alimenti, i test `T-NUT` o la pagina "alimentazione" del
giorno. Sezione 5 di `SKILL.md`. Fonti: ISSN, ACSM, IOC, MSJ, KEYTEL, COMPENDIUM.

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
| PAL | Moltiplicatori classici del metabolismo di base per livello di attività (famiglia Harris-Benedict) | fattore del livello fuori dallo sport |
| KEYTEL | Keytel e altri, *Prediction of energy expenditure from heart rate monitoring during submaximal exercise* (2005) | calorie da FC media, età, peso, sesso |
| COMPENDIUM | *Compendium of Physical Activities* (MET per attività) | calorie senza FC |
| ISSN | International Society of Sports Nutrition, posizioni su tempi dei nutrienti, proteine, diete e composizione corporea | carboidrati per carico, proteine, momenti |
| ACSM | ACSM, Academy of Nutrition and Dietetics e Dietitians of Canada, *Nutrition and Athletic Performance* | carboidrati, liquidi, ritmo di perdita di peso |
| IOC | Consenso del CIO sulla nutrizione dello sportivo | carboidrati durante, recupero |
| FDC | USDA FoodData Central (dominio pubblico, CC0 1.0) | valori degli alimenti |

## 1. Calorie delle attività

- Il totale dichiarato dal dispositivo (FIT `session.total_calories`) entra nel
  riepilogo (`ActivitySummary.calories_kcal`) e nell'indice (colonna
  `activities.calories_kcal`, schema v4). Zero e il valore non valido del FIT
  significano "non calcolato"; sopra 50.000 kcal non è credibile. Con più sedute
  nel file si somma solo se tutte lo dichiarano. Il GPX non ha calorie.
- Se mancano, in quest'ordine (`activity_energy`):
  1. **Keytel** con la FC media dei campioni dentro `[FCmin, FCmax]` (e tra 40 e
     250 bpm), peso, età e sesso:
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
- Le attività già indicizzate prima della v4 hanno NULL: finché non si rilegge
  il file si stimano.

## 2. Spesa del giorno

- **Metabolismo di base** (MSJ), kcal/giorno:
  `10·kg + 6,25·cm − 5·età + 5` (uomo), `… − 161` (donna). L'età è in anni
  compiuti nel giorno; il peso è quello valido quel giorno (un cambio vale dal
  suo giorno; prima della prima voce vale la prima, come per la FC).
- **Livello fuori dallo sport** (con data di validità): sedentario 1,2; leggero
  1,375; attivo 1,55; molto attivo 1,725 (PAL). Senza livello: sedentario e
  avviso `ACTIVITY_LEVEL_ASSUMED`.
- **Spesa** = `base · fattore + Σ calorie nette`, arrotondata al kcal intero.

## 3. Obiettivo e deficit

- Tre obiettivi per **periodi** senza sovrapposizioni (inizio e fine inclusi, la
  fine può mancare = fino al periodo successivo); senza periodo vale il
  mantenimento: **mantenimento** (apporto = spesa), **performance** (apporto =
  spesa, carboidrati in cima alla fascia), **dimagrimento** con ritmo da 100 a
  1000 g a settimana.
- Deficit richiesto = `g/settimana / 1000 · 7700 / 7` kcal al giorno (500 g →
  550 kcal; default del progetto: 7700 kcal per kg).
- **Limiti di sicurezza** (sempre, in quest'ordine, su interi):
  1. nessun deficit sotto i 18 anni né con IMC < 18,5 (avvisi `UNDER_18` e
     `LOW_BMI`, più `DEFICIT_NOT_APPLIED`);
  2. al massimo il 20% della spesa e 750 kcal;
  3. ridotto nei giorni di carico alto: × 0,5 (alto), × 0,25 (molto alto)
     (`DEFICIT_REDUCED_FOR_LOAD`);
  4. mai sotto il metabolismo di base: `apporto ≥ base` (`DEFICIT_LIMITED`
     quando un limite taglia la richiesta).
- Un minorenne o un IMC basso ricevono l'avviso anche con altri obiettivi.

## 4. Livello del giorno e macro

- **Livello** = il più alto tra quello del carico del giorno (somma dei carichi:
  ≥ 60 moderato, ≥ 120 alto, ≥ 200 molto alto) e quello del tempo (≥ 60 min
  moderato, ≥ 120 alto, ≥ 240 molto alto); qualsiasi seduta è almeno leggera;
  senza sedute è riposo (default del progetto, che traduce le fasce orarie di
  ISSN/ACSM nel carico dell'app: 100 = un'ora alla soglia).
- **Carboidrati**, g/kg/giorno: riposo e leggero 3–5; moderato 5–7; alto 6–10;
  molto alto 8–12. Dimagrimento: estremo basso; mantenimento: metà; performance:
  estremo alto.
- **Proteine** 1,6 g/kg (2,0 in deficit). **Grassi** = il resto, almeno il 20%
  dell'energia. Se l'energia non basta, cedono i carboidrati
  (`CARBS_LIMITED_BY_ENERGY`). Grammi interi; 4/4/9 kcal per grammo.

## 5. Momenti e pasti (`nutrition/domain/meals.py`)

Per ogni seduta di almeno 30 minuti (le più brevi non cambiano nulla):

- **Pre** (oltre 60 min): carboidrati 1 g/kg, 2 ore prima (1 ora se 2 ore prima
  sarebbe prima delle 05:00).
- **Durante** (oltre 75 min): 45 g/h di carboidrati fino a 2,5 h, 75 g/h oltre
  (fino a 90 g/h in letteratura: IOC); liquidi 600 ml/h e sodio 300 mg/h,
  indicativi (ACSM).
- **Post**, 30 min dopo la fine: proteine 0,3 g/kg; carboidrati 1,1 g/kg se la
  seduta dura almeno 90 min o un'altra arriva entro 24 h (un'altra del giorno
  dopo si passa come `minutes_to_next_session`).

Il resto del giorno si divide in colazione 25%, pranzo 40%, cena 35% agli orari
tipici (default 07:30, 13:00, 20:00; almeno due ore tra l'uno e l'altro):

- un pasto dall'ora prima dell'inizio all'ora dopo la fine di una seduta si
  sposta subito dopo (o subito prima, se dopo sarebbe oltre le 23:00);
- gli apporti a meno di 90 minuti l'uno dall'altro si **fondono** (l'elemento
  ha più `slots` e l'ora dell'ultimo);
- i totali sono ripartiti con il metodo del resto più grande: la somma degli
  elementi è sempre il totale del giorno, mai quantità negative.

## 6. Alimenti (`nutrition/foods.json`, `food_table.py`)

- 43 alimenti comuni con kcal e macro per 100 g e l'id di USDA FoodData Central
  di ciascuno; sorgente dichiarata nel file (`source`, licenza CC0-1.0).
- **Da verificare**: valori e id sono stati scritti senza accesso a FDC;
  `source.verified_on` è `null` finché qualcuno non li controlla voce per voce e
  scrive la data. Il test `T-NUT-06` ricorda che manca.
- Ogni alimento ha gruppo (carboidrati, proteine, grassi, verdure), pasti adatti,
  passo e massimo della porzione. Il caricatore rifiuta energia incoerente con i
  macro (±15% o 15 kcal), file oltre 256 KiB, id ripetuti, meno di 40 o più di 200
  voci.
- **Esempi**: per ogni pasto 2–3 combinazioni (carboidrato + proteina, eventuale
  grasso, contorno di verdura per pranzo e cena) con porzioni arrotondate al passo;
  ogni macro dell'esempio dista dal pasto al massimo il 15% (almeno 5 g).
  Scelta deterministica (per scostamento, a parità per id); alimenti diversi tra
  gli esempi. Se nessuna combinazione rispetta la tolleranza, nessun esempio.

## 7. Dati e casi d'uso

- Schema v4: `activities.calories_kcal`; `activity_level` (data di validità),
  `meal_times` (una riga), `nutrition_goal` (periodi, con trigger contro le
  sovrapposizioni). Migrazione con copia in `backup/ponos-v3-v4.sqlite3`.
- Porte in `nutrition/ports.py` (`ActivityLevelStore`, `MealTimesStore`,
  `GoalPeriodStore`); casi d'uso in `nutrition/use_cases.py`
  (`SetActivityLevel`, `SetMealTimes`, `AddGoalPeriod`, `ReplaceGoalPeriod`,
  `RemoveGoalPeriod`, `ReadNutritionSettings`).
- `profiles/day_nutrition.py`: `PlanDayNutrition(sources, decode, timezone,
  foods)(day)` raccoglie dal profilo aperto e restituisce `DayNutritionReport` o
  `NutritionUnavailable(reason)`. È una query: non salva nulla.

## Punti aperti

- Verifica degli alimenti contro FDC (vedi sopra) e traduzione dei loro nomi.
- Allergie e restrizioni alimentari: non per ora (decisione del 2026-11-07).
- Giorni futuri: arrivano con il piano (fase 5).
