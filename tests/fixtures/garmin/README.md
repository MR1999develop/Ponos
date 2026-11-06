# Fixture delle risposte di Garmin

**Sono sintetiche, non registrate da Garmin.** I nomi delle chiavi e la forma
delle voci vengono dai campi del modello `Activity` di `garminconnect.typed`
(0.3.20: `activityId`, `startTimeGMT`, `activityType.typeKey`, `duration`,
`elapsedDuration`, `averageHR`...), letti dal sorgente della libreria. I modelli
non si possono istanziare nel progetto perché richiedono `pydantic`, che non è
una dipendenza: i file sono scritti a mano seguendo quegli alias. Valori e
nomi sono inventati ("Atleta di prova", date di maggio 2026).

**Da sostituire con la struttura reale** quando l'utente avrà lanciato
`experiments/garmin_spike.py`: `experiments/garmin_spike_shapes.json` dà le
chiavi, i tipi e gli ordini di grandezza di una voce vera dell'elenco, della
risposta del conteggio e dei token. Allora si confrontano le chiavi di queste
fixture con quelle reali (in particolare il formato di `startTimeGMT`, se
`elapsedDuration` c'è e se `averageHR` è `null` o assente quando manca la FC) e
si correggono i file e `src/ponos_coach/garmin/listing.py`.

| File | Contenuto |
|---|---|
| `activity_list_page.json` | Una pagina valida di 5 voci, dalla più recente: corsa, ciclismo (id come stringa, orario con decimale), camminata con FC `null`, pesi, nuoto con FC 0; chiavi extra ignorate |
| `activity_list_hostile.json` | Voci ostili (T-SEC-03): non oggetti, id non numerici o con percorsi, orari assenti, malformati o fuori finestra, durate assenti, nulle, negative, non finite o testuali, tipo di sport assente o con `../`, id ripetuto, nomi con HTML o SQL (che sono testo e non contano). Contiene i letterali `NaN` e `Infinity`, che `json` di Python accetta |
| `count_activities.json` | Risposta del conteggio (`totalCount`) |

`load_json(nome)` in `__init__.py` legge un file. La struttura profonda o
enorme non si può versionare e si costruisce nei test.
