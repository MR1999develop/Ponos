# Ponos Coach

Ponos Coach è un'app desktop per Windows e Ubuntu, con più profili utente.
Scarica le attività da Garmin Connect, calcola il carico dalla frequenza
cardiaca e mostra la forma (ATL, CTL, TSB). Genera un piano di allenamento
adattivo verso uno o più obiettivi nel calendario e guida il recupero dagli
infortuni.

**Stato:** in sviluppo, fase 0 (scheletro e documenti). Non ci sono ancora
versioni utilizzabili.

## Avviso: non è un dispositivo medico

Ponos Coach non è un dispositivo medico. Non fa diagnosi e non sostituisce un
medico o un allenatore. Con i segnali d'allarme di un infortunio il piano
della zona colpita si ferma e l'app invita a rivolgersi a un medico.

## Avviso: accesso a Garmin Connect

Garmin non offre un'API ufficiale ai privati. L'app usa un accesso non
ufficiale (libreria `garminconnect`). Può smettere di funzionare quando Garmin
cambia il login o le API. Può essere contrario ai termini d'uso di Garmin:
l'uso è a rischio dell'utente. L'import manuale di file FIT resta sempre
disponibile.

## Funzioni previste

- Più profili, ciascuno con una cartella scelta dall'utente per tutti i suoi
  dati.
- Sincronizzazione delle attività da Garmin Connect, riprendibile, e import di
  file FIT da una cartella.
- Carico da frequenza cardiaca e grafici di ATL, CTL e TSB.
- Test di soglia di 30 minuti e zone di allenamento.
- Obiettivi (gare A/B/C, tempo obiettivo, livello di forma) e piano adattivo.
- Percorso di recupero dagli infortuni.
- Invio degli allenamenti al calendario Garmin (fase successiva).
- Interfaccia in italiano e inglese.

Il comportamento previsto è descritto in `docs/specifica.md`, le decisioni in
`docs/adr/` e il piano di lavoro in `PLAN.md`.

## Requisiti

- Windows o Ubuntu.
- Python 3.13.
- [uv](https://docs.astral.sh/uv/).

## Avvio da sorgente

```
uv sync
uv run ponos-coach
```

## Controlli

```
uv run --locked nox
```

Esegue formattazione e lint, `mypy --strict`, controllo delle dipendenze tra
moduli, test, test delle traduzioni e `pip-audit`.

## Sicurezza

Per segnalare una vulnerabilità vedi `SECURITY.md`.

## Licenza

GPL-3.0-only. Il testo completo è nel file `LICENSE`.
