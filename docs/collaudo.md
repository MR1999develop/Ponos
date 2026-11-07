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
| Fase 2, `tests/collaudo/test_fase2_*` e T-SYNC-02 (144 test con la fase 1) | 2026-11-06 | Windows 10 Pro 22H2, 12 CPU | ramo `fase-2-sincronizzazione`, da sorgente | superato | Difetti D-F2-01…04 trovati e corretti (tabella dei casi chiusi in `docs/threat-model.md`). Solo client Garmin finti: il login reale resta per T-MAN-01. |
| T-SYNC-01 su 20.000 attività | 2026-11-06 | Windows 10 Pro 22H2, 12 CPU | ramo `fase-2-sincronizzazione`, da sorgente | superato | Elenco p50 2,3 ms, max 25 ms per pagina; abbinamento p50 0,39 ms, max 2,9 ms per voce; giro completo 8,7 s con sorgente finta. |
| T-UI-02 durante la sincronizzazione | 2026-11-06 | Windows 10 Pro 22H2, 12 CPU | ramo `fase-2-sincronizzazione`, da sorgente | superato | 120 FIT reali scaricati da una sorgente finta: fermo del thread della UI p50 9 ms, max 14 ms. |
| Fase 3, `tests/collaudo/test_fase3_*` (149 test di collaudo) | 2026-11-06 | Windows 10 Pro 22H2, 12 CPU | ramo `fase-3-carico-e-grafici`, da sorgente | superato con riserve | Difetti D-F3-02…11 trovati (tabella dei casi chiusi in `docs/threat-model.md`); i test dei difetti nascono come previsti-fallito: quando passano senza marcatura si aggiunge una nuova riga con l'esito. Copre T-CALC-01…07, T-DATI-01 (migrazione v3), T-QUAL-02, T-SEC-08 (dati personali), T-UI-04…07. Nessun login reale. |
| T-PERF-01 con 5.000 attività | 2026-11-06 | Windows 10 Pro 22H2, 12 CPU | ramo `fase-3-carico-e-grafici`, da sorgente | superato | Panoramica dalla cache p50 141 ms; figure del PMC 127 ms; carico settimanale 17 ms; zone 17 ms. Ridisegno del PMC a 3.650 giorni 720 ms nel rendering software di prova. Ricalcolo da zero stimato circa 23 s con i FIT reali (stima dal costo di decodifica di un FIT vero, non una misura del giro completo). Attività sintetiche, tranne la decodifica. |
| Fase 3, `tests/collaudo/test_fase3_*` dopo le correzioni | 2026-11-06 | Windows 10 Pro 22H2, 12 CPU | ramo `fase-3-carico-e-grafici`, da sorgente | superato | D-F3-01…11 corretti, nessun `xfail` rimasto; `nox` con 3398 test e collaudo con 149 test verdi. |

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
