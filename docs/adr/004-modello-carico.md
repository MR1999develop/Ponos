# 004. Modello di carico

- Stato: Accettato
- Data: 2026-11-05

## Contesto

L'app calcola il carico di ogni attività dalla frequenza cardiaca, senza
potenza né ritmo, e ne ricava ATL, CTL e TSB. Il piano si adatta solo a
questi valori. La formula determina tutti i numeri mostrati e tutte le
decisioni del piano. Cambiarla dopo il rilascio obbliga a ricalcolare lo
storico.

## Opzioni

1. **hrTSS da TRIMP di Banister sul tracciato FC.**
   - Pro: usa ogni campione (non zone), continuo, scala come il TSS (100 =
     un'ora alla soglia), nota in letteratura.
   - Contro: richiede FC minima, massima e soglia affidabili; il coefficiente
     dipende dal sesso; sottostima il carico della forza e delle attività con
     FC poco rappresentativa.
2. **TRIMP di Edwards a zone.**
   - Pro: più semplice da spiegare.
   - Contro: a gradini (effetti di soglia tra zone), meno sensibile.
3. **Entrambe selezionabili.**
   - Pro: confronto tra metodi.
   - Contro: due serie di carichi e PMC da ricalcolare, spiegare e testare.
4. **Carico calcolato da Garmin.**
   - Pro: nessun calcolo.
   - Contro: formula opaca e non sempre disponibile; può cambiare senza
     avviso; non copre i FIT importati da altre fonti.

## Decisione

Opzione 1. Il carico è l'hrTSS moltiplicato per il peso dello sport. Le
opzioni 2 e 3 restano possibili come aggiunta futura, con un nuovo ADR.

Parametri:

- FC minima e massima inserite dall'utente, con data di validità; un
  campione fuori da questi limiti è un errore e si scarta (si conta);
- soglia in bici = FC di soglia della corsa − 4 bpm, finché non c'è un test
  in bici;
- pesi per sport (valori provvisori, da tarare in fase 3);
- attività senza FC o con troppi campioni scartati: carico stimato e marcato.

Formule, unità ed esempi: skill `ponos-allenamento`, file
`references/modello-carico.md`.

## Conseguenze

- Il cambio di un parametro ricalcola i carichi derivati.
- Dati sbagliati dell'utente (FC massima troppo bassa) producono scarti:
  l'app li segnala e propone di rivedere il valore.
- I pesi degli sport sono scelte del progetto, non della letteratura, e
  vanno dichiarati tali.
- Le formule stanno in un solo modulo del dominio.

## Come se ne verifica il rispetto

- T-CALC-01 (valori a mano), T-CALC-02 (vettoriale contro ingenua),
  T-CALC-03 (invarianti), T-CALC-04 (PMC), T-CALC-05 (data di validità),
  T-CALC-06 (soglia bici e stime), T-CALC-07 (casi limite).
- Test di mutazione T-QUAL-01 su `load`.
