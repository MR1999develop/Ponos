# Infortuni

**Quando leggerlo:** scrivendo o rivedendo `injury`, l'integrazione con il
piano, il questionario o i test `T-INF`. Sezione 4 di `SKILL.md`. Fonte: SIL.

Ponos Coach non è un dispositivo medico. Non fa diagnosi. I dati di salute
restano in locale e non vanno mai nei log.

## 1. Questionario

- Zona e lato.
- Data e modo d'insorgenza: improvviso o graduale.
- Dolore da 0 a 10: a riposo, camminando, correndo, il mattino dopo.
- Attività che non fanno male.
- Diagnosi e indicazioni del medico: testo libero, solo in locale.

## 2. Segnali d'allarme

Dolore notturno o a riposo forte, gonfiore o deformità, formicolii o perdita
di forza, impossibilità di caricare il peso, febbre, dolore al petto o
palpitazioni.

Con uno qualsiasi:

- il piano della zona colpita si ferma;
- l'app invita a rivolgersi a un medico;
- il percorso a gradini non parte.

Un test per segnale (T-INF-01). Il valore di "dolore a riposo forte" (soglia
numerica) e quali segnali valgono per la sola zona e quali per tutto il
piano (esempio: dolore al petto): **punti aperti**.

## 3. Percorso a gradini

Ordine: riposo o attività alternative senza dolore → camminata → corsa e
cammino → corsa continua facile → volume → intensità.

- Si sale di un gradino se il dolore resta ≤ 3/10 durante l'attività e il
  mattino dopo torna al livello di base (SIL: dolore tollerato ma sotto
  controllo; il limite di 3/10 è default del progetto).
- Si scende se peggiora.
- Nessun salto di gradino; un check-in mancante vale nessuna salita.
- Durante il percorso l'app fa domande ogni giorno (check-in).
- Definizione di "livello di base", numero minimo di giorni per gradino e
  criterio esatto di "peggiora": **punti aperti**.

## 4. Integrazione con il piano

- La zona colpita è sospesa nelle sedute che la caricano.
- Le attività alternative senza dolore mantengono il CTL.
- Dopo il rientro la rampa massima del CTL è +3 a settimana (default del
  progetto; vedi `piano.md`).
- Quali sedute caricano quale zona: **punto aperto**, serve una tabella per
  zona e tipologia.

## 5. Test attesi

`T-INF-01…03` in `PLAN.md` sezione 7.4: ogni segnale d'allarme ferma la zona;
salita, discesa e assenza di salti; integrazione con il piano.
