# Fixture delle risposte di Garmin

**Costruite sulla struttura reale registrata dallo spike del 2026-11-06**
(`experiments/garmin_spike_shapes.json`): stesse chiavi, tipi, annidamenti e
lunghezze delle stringhe di una voce vera dell'elenco; i valori sono inventati
(id, date, numeri, nomi come "Atleta di prova"), nessuno è reale. Le tre chiavi
con un id nel nome sono anonimizzate nella registrazione e qui restano
con il segnaposto `<id-key-N>` della registrazione. Nella risposta reale `averageHR` è sempre un numero; i casi
`null`, 0, assente, id come stringa e orario con decimali sono coperti dai test
con voci costruite a mano.

| File | Contenuto |
|---|---|
| `activity_list_page.json` | Una pagina valida di 5 voci con la struttura reale completa, dalla più recente; le chiavi non lette sono ignorate |
| `activity_list_hostile.json` | Voci ostili (T-SEC-03): non oggetti, id non numerici o con percorsi, orari assenti, malformati o fuori finestra, durate assenti, nulle, negative, non finite o testuali, tipo di sport assente o con `../`, id ripetuto, nomi con HTML o SQL (che sono testo e non contano). Contiene i letterali `NaN` e `Infinity`, che `json` di Python accetta |
| `count_activities.json` | Risposta del conteggio: un intero nudo, come nella realtà |

`load_json(nome)` in `__init__.py` legge un file. La struttura profonda o
enorme non si può versionare e si costruisce nei test.
