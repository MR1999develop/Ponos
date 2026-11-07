---
name: ponos-allenamento
description: Conoscenza di dominio di Ponos Coach, app di allenamento con dati Garmin e frequenza cardiaca. Modello di carico (TRIMP di Banister, hrTSS, pesi degli sport, ATL, CTL, TSB), regole del piano adattivo (macrociclo, mesocicli 3:1, rampa, 80/20), test di soglia di 30 minuti, protocollo degli infortuni con segnali d'allarme e percorso a gradini, formato dei dati (cartella del profilo, SQLite, archivio FIT, identità file_id), sincronizzazione riprendibile con Garmin e import da cartella con i tetti; da usare quando si scrive, si rivede o si documenta codice di carico, piano, test, infortuni, sincronizzazione o archivio di Ponos Coach.
---

# Ponos Allenamento

**Scopo:** raccogliere in un posto solo le formule, le regole e i formati di
Ponos Coach, così che dominio, test e documenti dicano la stessa cosa. Le
decisioni e le fasi sono in `PLAN.md`; il comportamento per le persone è in
`docs/specifica.md`.

**Si usa insieme a:** software-design-core, python-design

Ponos Coach non è un dispositivo medico: nessuna diagnosi, con i segnali
d'allarme il piano si ferma e si invita a un medico.

Ogni valore marcato "default del progetto" è una scelta del progetto, non
della letteratura, e si può cambiare. Ciò che `PLAN.md` non decide è scritto
come "punto aperto" nei file `references/`.

## Fonti

| Sigla | Fonte | Per che cosa |
|---|---|---|
| BAN | Banister e altri, modello impulso-risposta e TRIMP (anni 1975–1991) | TRIMP, costanti di tempo, forma e fatica |
| COG | Coggan e Allen, *Training and Racing with a Power Meter* | TSS, ATL, CTL, TSB, PMC |
| FRI | Friel, *The Triathlete's Training Bible* | test di soglia di 30 minuti |
| SEI | Seiler, distribuzione dell'intensità (2010) | distribuzione 80/20 |
| SIL | Silbernagel e altri, modello di monitoraggio del dolore (2007) | ripresa guidata dal dolore |

## 1. Modello di carico

TRIMP, hrTSS e carico per sport; parametri e misure con data di validità
(regola "vale fino alla successiva"); campioni fuori limiti, buchi, attività
senza FC; pesi di default e pesi salvati; zone di FC sulla soglia; PMC con TSB
del mattino; esempi numerici verificabili a mano. Fonti: BAN, COG.
Dettagli: `references/modello-carico.md`; decisioni in
`docs/adr/007-dati-del-profilo-e-zone.md`.

## 2. Piano

Ingressi, macrociclo all'indietro dalla gara, mesocicli 3:1, obiettivi
A/B/C, settimana tipo, tipologie scelte dall'utente, tempo obiettivo,
adattamento dopo ogni sincronizzazione, determinismo. Fonti: COG, SEI.
Dettagli: `references/piano.md`.

## 3. Test di soglia

Protocollo di 30', FC di soglia come media degli ultimi 20', retest ogni
6–8 settimane, soglia in bici. Fonte: FRI.
Dettagli: `references/test-soglia.md`.

## 4. Infortuni

Questionario, segnali d'allarme, percorso a gradini, check-in giornaliero,
attività alternative. Fonte: SIL.
Dettagli: `references/infortuni.md`.

## 5. Dati e sincronizzazione

Cartella del profilo e elenco dei profili, SQLite, archivio dei file
originali (FIT e GPX), identità (`file_id` o SHA-256), giornale della
sincronizzazione, abbinamento, backoff, import di file, cartelle e ZIP con i
tetti, esiti dell'import, lock, dati personali e schema v3. Dettagli:
`references/dati-e-sincronizzazione.md`; schema e archivio:
`docs/adr/005-schema-sqlite-e-archivio.md`; dati del profilo:
`docs/adr/007-dati-del-profilo-e-zone.md`.

## Checklist di revisione

- Le formule e le soglie del codice coincidono con i file `references/`; una
  modifica cambia anche il file e i test `T-CALC`, `T-PIANO`, `T-INF`.
- Il dominio è puro: nessun I/O, Qt, rete o log; nessun colore o testo
  formattato.
- Date e durate usano orologio iniettato e UTC; nessuna ora locale nel
  dominio.
- Un campione di FC fuori da `[FCmin, FCmax]` si scarta e si conta; mai
  ripararlo.
- Il piano è deterministico: stessi ingressi, stesso piano serializzato.
- Il piano non supera mai disponibilità, rampa, numero di sedute intense né
  tipologie scelte dall'utente.
- Con un segnale d'allarme la zona si ferma: nessuna eccezione.
- Dati di salute mai nei log; altezza, peso e data di nascita compresi, anche
  nei messaggi e nelle catene d'errore.
- Altezza e data di nascita si scrivono una volta; il peso è un intero in
  decimi di chilo; una misura vale fino alla voce successiva della sua serie.
- Le percentuali delle zone stanno solo in `ZONE_STARTS_PERCENT`; se cambiano,
  si alza `LOAD_MODEL_VERSION`.
- Un file originale (FIT o GPX) si scrive su temporaneo, flush, rinomina; il
  record SQLite dopo il file; il token non sta mai nella cartella del profilo.
- La cancellazione di una cartella di profilo tocca solo i nomi noti e mai un
  profilo aperto.
- Ogni limite (dimensione, record, tetti di import) viene prima della lettura
  e vive in un solo modulo.
- Ogni valore scelto dal progetto è marcato "default del progetto".
