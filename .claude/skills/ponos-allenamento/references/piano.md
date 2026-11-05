# Piano di allenamento

**Quando leggerlo:** scrivendo o rivedendo `planning` e `goals`, i test
`T-PIANO`, o il calendario. Sezione 2 di `SKILL.md`. Fonti: COG, SEI.

Il piano è un valore: il nucleo lo restituisce, il guscio lo salva o lo
invia. Nessun I/O nel motore.

## 1. Ingressi

PMC di oggi, obiettivi, disponibilità settimanale (giorni disponibili, ore
massime a settimana, giorno del lungo), tipologie di seduta scelte, infortuni
attivi, ultimo test di soglia (FC e ritmo), pesi degli sport.

## 2. Determinismo

- Stessi ingressi e parametri → piano identico byte per byte una volta
  serializzato (T-PIANO-01).
- L'ordine di inserimento degli obiettivi non conta: si ordinano con una
  chiave stabile (data, priorità, id).
- Niente casualità né orologio nel motore: la data di oggi è un ingresso.
- Il piano si salva con la versione delle regole e con i parametri che l'hanno
  generato.

## 3. Macrociclo

Per ogni obiettivo A, all'indietro dalla data: scarico, picco, costruzione,
base.

- Scarico finale: 7–14 giorni secondo la distanza (default del progetto).
- Durata di picco, costruzione e base e come dividono il tempo disponibile:
  **punto aperto**.
- CTL obiettivo: dal livello di forma richiesto o da una stima per distanza e
  tempo. La stima: **punto aperto**.
- Rampa massima del CTL: +5 a settimana (default del progetto); +3 dopo un
  infortunio (default del progetto).

## 4. Mesocicli

- 3:1: tre settimane di carico e una di scarico (default del progetto).
- Settimana di scarico al 60–70% del carico (default del progetto).
- Obiettivo B: 3–4 giorni di mini-scarico. Obiettivo C: nessuno scarico.
- Scarico prima di ogni obiettivo A e B (T-PIANO-02).

## 5. Settimana

- Il carico obiettivo si distribuisce nei soli giorni disponibili.
- Alternanza duro/facile; mai due sedute dure di seguito.
- Al massimo 3 sedute intense a settimana (T-PIANO-02; l'obiettivo di
  progetto è 2–3).
- Lungo nel giorno scelto dall'utente.
- Distribuzione 80/20 dell'intensità in fase di base (SEI; default del
  progetto la sua applicazione): circa l'80% del tempo facile, il 20% intenso.
  Il modo di misurarla (per tempo o per carico): **punto aperto**.
- Le ore settimanali non superano mai il massimo dell'utente.

## 6. Sedute

- Modelli per sport e fase: facile, lungo, medio, soglia a intervalli, VO2max,
  recupero, forza, alternativa.
- Solo tra le tipologie scelte dall'utente. Se a un obiettivo manca una
  tipologia necessaria (esempio: mezza maratona senza lunghi), il piano lo
  segnala e spiega l'effetto, ma non la aggiunge.
- Ogni seduta ha durata, zone FC o di ritmo e carico stimato. Il carico
  stimato alimenta la proiezione del PMC.
- Contenuto esatto dei modelli (durate, ripetute, recuperi): **punto aperto**.
- Zone FC e di ritmo: vedi `test-soglia.md` (punto aperto sulla definizione).

## 7. Tempo obiettivo

Dal ritmo di soglia si stima il tempo possibile e si classifica l'obiettivo:
realistico, ambizioso o non raggiungibile entro la data. Le soglie tra le
classi e la formula di stima: **punto aperto**. Il test ai confini è
T-PIANO-05.

## 8. Adattamento

- Dopo ogni sincronizzazione si confronta fatto e pianificato e si ricalcola
  da domani. Il passato resta storia.
- TSB sotto −30 oppure rapporto ATL/CTL oltre 1,5 (default del progetto):
  si inserisce recupero.
- Il carico perso (sedute saltate) non si recupera oltre la rampa massima.
- Attività non pianificate entrano nel PMC come le altre.
- Attività vecchie arrivate tardi cambiano il passato e quindi il ricalcolo.
- Durata e forma del recupero inserito: **punto aperto**.

## 9. Casi limite

Obiettivo oggi o nel passato, nessuna disponibilità, CTL di partenza 0 o 100,
due obiettivi A a 3 settimane, obiettivo irraggiungibile (avviso, nessuna
rampa oltre il limite): scenari T-PIANO-03. Il comportamento esatto per ciascun
caso (avviso, rifiuto, piano ridotto): **punto aperto** da fissare scrivendo
prima il test.

## 10. Test attesi

`T-PIANO-01…05` in `PLAN.md` sezione 7.4; i vincoli della sezione 5 sono
property test su ingressi casuali.
