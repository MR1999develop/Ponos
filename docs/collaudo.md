# Collaudo: esiti

Registro degli esiti del collaudo descritto nella sezione 7 di `PLAN.md`.
Ogni riga cita l'identificativo del test.

## Come si compila

- Una riga per ogni esecuzione, mai riscritta: un nuovo esito è una nuova
  riga.
- Data in formato AAAA-MM-GG.
- Piattaforma: Windows o Ubuntu, con la versione del sistema.
- Versione: quella dell'eseguibile provato.
- Esito: superato, non superato, superato con riserve.
- Note: problemi trovati, riferimento alla correzione, misure (con ambiente).

## Campagna lunga (`uv run --locked nox -s collaudo`)

| Test | Data | Piattaforma | Versione | Esito | Note |
|---|---|---|---|---|---|
| Fase 1, `tests/collaudo/` (83 test) | 2026-11-06 | Windows 10 Pro 22H2, 12 CPU | ramo `fase-1-profili-archivio`, da sorgente | superato | Difetti D-F1-01…10 trovati il 2026-11-05 e corretti (tabella dei casi chiusi in `docs/threat-model.md`). Symlink, FIFO e permessi POSIX saltati su Windows: da eseguire in CI su Ubuntu. |
| T-PERF-01 (parziale) | 2026-11-06 | Windows 10 Pro 22H2, 12 CPU | ramo `fase-1-profili-archivio`, da sorgente | superato | Import di 1.000 FIT sintetici: 14,0 s; per file p50 14 ms, p95 15 ms, max 19 ms. Panoramica a freddo 10,1 s; a caldo p50 28 ms, p95 29 ms. |
| T-UI-02 / D-F1-10 | 2026-11-06 | Windows 10 Pro 22H2, 12 CPU | ramo `fase-1-profili-archivio`, da sorgente | superato con riserve | Fermo del thread della UI durante import e panoramica: p95 ≤ 15 ms; primo disegno 21–25 ms. L'avvio del motore web (circa 180 ms) resta nel thread della UI subito dopo l'apertura della finestra: vedi `TIDY.md`. |

## Collaudo dell'eseguibile

| Test | Data | Piattaforma | Versione | Esito | Note |
|---|---|---|---|---|---|
| | | | | | |

## T-MAN-01: collaudo manuale su sistemi puliti

Windows e Ubuntu puliti, dall'eseguibile. Il login con MFA lo fa l'utente.

| Passo | Data | Piattaforma | Versione | Esito | Note |
|---|---|---|---|---|---|
| Primo avvio guidato | | | | | |
| Login reale con MFA | | | | | |
| Prima sincronizzazione reale | | | | | |
| Import da una cartella reale | | | | | |
| Piano verso un obiettivo reale | | | | | |
| Invio di un allenamento al calendario Garmin | | | | | |
| Cancellazione di un allenamento dal calendario Garmin | | | | | |

## T-MAN-02: test d'usabilità

2–3 persone, compiti precisi. Nessun nome: si registrano solo i partecipanti
come "P1", "P2", "P3".

| Compito | Partecipante | Data | Riuscito | Problema osservato | Correzione |
|---|---|---|---|---|---|
| Creare un profilo | | | | | |
| Aggiungere una gara | | | | | |
| Leggere la forma di oggi | | | | | |
| Descrivere un infortunio | | | | | |
