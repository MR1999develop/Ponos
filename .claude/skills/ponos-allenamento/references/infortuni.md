# Infortuni

**Quando leggerlo:** scrivendo o rivedendo `injury`, l'integrazione con il
piano, il questionario o i test `T-INF`. Sezione 4 di `SKILL.md`. Fonti: SIL,
CSM1, CSM2, FER25 (tabella delle fonti di `SKILL.md`). Decisione: ADR 012.

Ponos Coach non è un dispositivo medico. Non fa diagnosi: chiede, ferma o
riduce l'allenamento e invita a una visita. L'app non decide il rientro (CSM1):
mostra i dati e lascia la scelta all'utente e al medico. I dati di salute
restano in locale e non vanno mai nei log né negli errori. Nei testi
dell'interfaccia: carico, tolleranza e progressione; mai "guarito", mai nomi di
malattie ("un segnale che merita una visita") (CSM1).

Tutti i numeri sono in `injury/domain/defaults.py`, ognuno con la fonte o
"project default"; qui sotto i valori e il perché.

## 1. Dati e tipi

- Dominio puro in `injury/domain`: `Questionnaire` (zona, lato, insorgenza e
  data, `PainReport` a riposo / camminando / correndo o `None` / il mattino
  dopo, attività senza dolore, segni ticcati, note del medico: testo libero,
  al massimo 2000 caratteri, mai letto da nessuno), `CheckIn` (giorno, dolore
  durante l'attività o `None` se non ce n'è stata, dolore di questa mattina,
  segni), `Injury` (questionario, check-in, giorno della visita medica, giorno
  di chiusura) e `Illness` (gruppo di sintomi, inizio, fine). Il gradino **non
  si salva**: si ricalcola dai check-in (`follow`).
- `PainScore` 0–10 intero valido per costruzione; `InjuryId`; zone chiuse
  (piede, caviglia, Achille, polpaccio, tibia, ginocchio, coscia, anca,
  schiena, parte alta); lato; insorgenza; attività senza dolore (camminata,
  bici, nuoto, corsa in acqua).
- Schema v10 (`injury`, `injury_pain_free`, `injury_checkin`, `injury_sign`).
  Il piano riceve solo un'istantanea senza testo libero (`PlanInjury`).

## 2. Segnali d'allarme

Quindici segni (`WarningSign`), ognuno con il suo test (T-INF-01): dolore
notturno, dolore a riposo forte (derivato: **riposo ≥ 5/10**, default del
progetto: le fonti non danno il numero), gonfiore o deformità, formicolii o
perdita di forza, impossibilità di caricare il peso, dolore osseo localizzato
anche camminando o di notte, polpaccio dolente, gonfio e caldo, dolore in bici
che costringe a fermarsi e passa subito (CSM1); sintomi di vescica o intestino
o debolezza che peggiora con il mal di schiena (CSM1); febbre, malessere,
dolore al petto, palpitazioni, svenimento (qualunque, durante o dopo lo sforzo),
fiato corto o stanchezza sproporzionati e nuovi (CSM2).

- **Ambito:** febbre, malessere e i quattro segni del cuore fermano **tutto**
  il piano; gli altri la zona (`Scope`).
- **Effetto sul piano:** nessuna seduta, con il motivo (`PlanHalted`:
  `ILLNESS`, `WHOLE_PLAN_SIGN`, `ZONE_SIGN`) e l'invito a un medico. Fermare
  solo ciò che carica la zona richiederebbe una tabella di che cosa carica che
  cosa che nessuna fonte dà (ADR 012).
- Il percorso non parte (stato `HALTED`) e non si muove, qualunque cosa dicano
  i check-in. Dopo la visita l'utente lo conferma (`ConfirmMedicalReview`):
  il percorso ricomincia dal primo gradino (default del progetto, il più
  prudente) e i segni di prima non contano più.

## 3. Percorso a gradini

Ordine: **attivazione** (movimento leggero, breve e frequente, RPE 1–2, senza
obiettivi numerici: FER25) → camminata → corsa e cammino (Z1) → corsa continua
facile (Z2) → volume → intensità. Mai un salto, un gradino alla volta
(`advance` cambia il gradino di al massimo uno). Dentro ogni gradino:
frequenza, poi durata, poi intensità (FER25).

Regole di un giorno (`injury/domain/path.py`, T-INF-02):

- **Giorno buono:** dolore durante l'attività ≤ **3/10** (SIL, CSM1) e il
  mattino dopo **non sopra il livello di base**. Il livello di base è il dolore
  più basso del mattino dopo visto finora (parte da quello del questionario):
  scende, non sale mai.
- **Sale** di un gradino dopo N giorni buoni **consecutivi**, di cui almeno M
  con un'attività. N ed M per gradino (default del progetto; CSM1 non li dà):
  attivazione 3 e 2, camminata 3 e 2, corsa e cammino 4 e 2, corsa continua 4 e
  2, volume 6 e 3, intensità 6 e 3 (a fine percorso dice solo `ready_to_close`).
- **Tiene** il gradino con dolore durante tra 4 e 5 (fino a 5/10 solo per un
  momento: CSM1); la serie di giorni buoni ricomincia.
- **Scende** con dolore durante > 5/10, oppure mattino sopra il livello di
  base ("peggiora"): all'**ultimo gradino completato** (uno sotto, CSM1), mai a
  uno arbitrario; sul primo resta lì.
- **Check-in mancante = nessuna salita:** un giorno senza check-in spezza ogni
  serie.
- **Ingresso nella corsa:** il primo gradino di corsa chiede **5 giorni
  consecutivi senza alcun dolore** (CSM1 cap. 4); **28** per un dolore graduale
  del tallone (CSM1 cap. 43; i 10 km di camminata senza dolore non sono
  tracciati). Sedute a **giorni alterni** nei primi due gradini di corsa (CSM1).
- **Decorso fuori dai tempi:** 21 giorni sullo stesso gradino (`Overdue.STEP`,
  default del progetto dal suggerimento di CSM1) o 84 dall'apertura
  (`Overdue.TOTAL`): promemoria a una visita, non diagnosi. Non vale per un
  percorso fermato (è già dal medico).
- Check-in giornaliero: `checkin_due(injury, oggi)`; la lista
  (`ListInjuries`) dà per ogni caso il gradino, il check-in mancante e il
  ritardo.

## 4. Malattia

"Non sto bene" (`Illness`, una sola aperta) con due gruppi di sintomi (CSM2):
solo sopra il collo e senza febbre (`NECK_UP`: nel piano solo recupero breve e
facile, niente intensità né forza) e febbre, malessere o dolori muscolari
(`SYSTEMIC`: il piano si ferma finché l'utente non dice che sta bene). Dopo la
fine vale la rampa ridotta (sezione 5). Non costruiti: prova di 10 minuti,
giorni di ripresa facile, FC a riposo come avviso.

## 5. Integrazione con il piano

- `PlanInputs.injuries` (istantanee dei casi aperti, ordinate per id) e
  `after_injury` (un caso chiuso da non più di **28 giorni**: CSM1 cap. 12,
  finestra default del progetto) → rampa massima **+3 CTL a settimana**
  invece di +5 (`ramp_reduced`).
- `restriction_for(injuries)` → `PlanRestriction`: con più casi vince la regola
  più restrittiva di ogni tipo (CSM1 cap. 19, un test con due zone).
- Corsa per grado: attivazione e camminata nessuna; corsa e cammino solo
  recupero (Z1); corsa continua facile; volume con il lungo; intensità tutto.
  **Nessuna intensità prima dell'ultimo gradino.** Per un piano in bici o a piedi
  valgono i flag della bici e della camminata (stesse tabelle).
- **La bici non è neutra** (CSM1): per ginocchio, anca e schiena è un gradino
  del percorso (da "camminata"), non un'alternativa libera; per tibia, piede,
  Achille e gli altri è libera. Nuoto e corsa in acqua sempre; camminata dal suo
  gradino. Si propone solo ciò che l'utente ha dichiarato senza dolore e che
  **tutti** i casi aperti tollerano.
- La forza torna con il primo gradino di corsa, salvo la parte alta del corpo.
- Le **alternative** (`ALTERNATIVE`, sport "altro") riempiono i giorni al posto
  della corsa e mantengono il carico, se l'utente le ha scelte; altrimenti
  giorni di riposo con l'avviso `NoSessionPossible`. Avvisi del piano:
  `PlanHalted`, `PlanAdapted`, `NoSessionPossible`.
- Il piano non indovina i gradini futuri: si rifà a ogni check-in
  (`RebuildPlan` legge i casi aperti e chiusi di recente).

## 6. Casi d'uso (annulla esatto)

`OpenInjury`, `ReportIllness`, `RecordCheckIn`, `ConfirmMedicalReview`,
`CloseProblem`, `RemoveProblem`, `RestoreProblem`, `ListInjuries`. Ogni comando
dà ciò che serve a disfarlo: `ProblemOpened` (annulla con `RemoveProblem`),
`ProblemChanged(before, after)` (annulla e rifai con `RestoreProblem`),
`ProblemRemoved`. Gli errori sono tutti `InjuryStoreError`.

## 7. Test attesi

`T-INF-01…03` in `PLAN.md` sezione 7.4: `tests/injury/` (questionario, segni,
percorso con property test e un'implementazione ingenua, restrizioni, casi
d'uso), `tests/planning/test_injuries.py` (T-INF-03 e T-PIANO-02 con infortuni
casuali), contratti dell'archivio sul finto e su SQLite, migrazione v9
interrotta a ogni istruzione, nessun dato di salute nei log
(`tests/collaudo/test_fase6_*`).
