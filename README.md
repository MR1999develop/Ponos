# Ponos Coach

Ponos Coach è un'app desktop per Windows e Ubuntu, con più profili utente.
Scarica le attività da Garmin Connect, calcola il carico dalla frequenza
cardiaca e mostra la forma (ATL, CTL, TSB). Genera un piano di allenamento
adattivo verso uno o più obiettivi nel calendario e guida il recupero dagli
infortuni.

**Stato:** in sviluppo, fase 1 (profili, archivio e import). Non ci sono
ancora versioni distribuite: si avvia da sorgente.

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
- Sincronizzazione delle attività da Garmin Connect, riprendibile (fase 2), e
  import di file FIT e GPX, anche in archivi ZIP, da file o da una cartella.
- Per provare l'app senza dati propri: `uv run python -m
  tests.fixtures.generators.demo_history <cartella>` crea uno storico
  sintetico di file FIT, da caricare in un profilo con "Importa da cartella".
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

## Primo profilo

Al primo avvio si apre il dialogo "Nuovo profilo". Servono:

1. il nome del profilo;
2. la cartella dei dati, da scegliere con "Sfoglia…": se è vuota la usa così
   com'è, altrimenti crea dentro una sottocartella con il nome del profilo e
   mostra dove andranno i dati; meglio evitare cartelle sincronizzate come
   OneDrive (su Windows spesso anche Documenti);
3. la frequenza cardiaca a riposo e la massima; la soglia è stimata al 90%
   della massima finché non la si fissa con il test;
4. il sesso (serve alla formula del carico).

Altri profili: File, Nuovo profilo (Ctrl+N). Un profilo spostato o copiato su
un altro PC si apre con File, Apri profilo da cartella (Ctrl+O). Con File,
Cambia profilo si passa da uno all'altro. "Rimuovi profilo dall'elenco" non
tocca i dati; "Elimina cartella del profilo" li cancella per sempre, dopo una
conferma, e solo se nella cartella ci sono soltanto file creati dall'app: se
c'è altro (per esempio file tuoi) non cancella nulla e lo dice. Alla chiusura
dell'app, se un lavoro è in corso (un import), l'app lo ferma e aspetta che
finisca; se non riesce entro 10 secondi lascia la finestra aperta e avvisa, e
basta chiuderla di nuovo. L'app ricorda la sezione aperta, la finestra e le
ultime cartelle usate.

## Importare le attività

Serve un profilo aperto. Si possono importare file FIT e GPX, anche dentro file
ZIP, come l'archivio che si scarica dal sito Garmin.

- File, Importa file… (Ctrl+I): si scelgono uno o più file `.fit`, `.gpx` o
  `.zip`.
- File, Importa da cartella… (Ctrl+Maiusc+I): si sceglie una cartella; l'app
  cerca i file anche nelle sottocartelle. Se ce ne sono troppi (oltre 50.000
  file di attività, 500.000 voci o 32 livelli) non importa nulla e chiede di
  scegliere una sottocartella.
- In alternativa si trascinano file o una cartella sulla finestra.

I file originali non vengono modificati: l'app ne tiene una copia nel profilo.
Le attività già presenti si saltano, quindi rilanciare l'import è sicuro. Alla
fine un riepilogo dice quante attività sono state importate e, per ogni file
non importato, il motivo. Se un'attività c'era già ma il suo file nell'archivio
mancava o era alterato, reimportare lo stesso file lo rimette a posto: il
riepilogo lo conta come "riparato". Le attività compaiono nella pagina Attività e nel
grafico della Panoramica. I file TCX non sono supportati.

## Dove stanno i dati

Tutti i dati di un profilo stanno nella cartella scelta alla creazione:

- `ponos.sqlite3`: il database (con i file `-wal` e `-shm` mentre l'app è
  aperta);
- `archivio/<anno>/`: i file originali, con il nome dato dal loro SHA-256;
- `backup/`: le copie del database fatte prima di un aggiornamento del formato
  (`ponos-v{da}-v{a}.sqlite3`);
- `ponos.lock`: impedisce di aprire lo stesso profilo due volte.

Non spostare né modificare a mano questi file mentre l'app è aperta. L'elenco
dei profili è nella cartella di configurazione dell'app (`profiles.json`). I
token Garmin non stanno mai nella cartella del profilo.

## Dati e privacy

I dati di ogni profilo (attività, frequenza cardiaca, posizioni GPS,
infortuni) restano sul computer, nella cartella scelta per il profilo, non
cifrati e leggibili solo dall'utente del sistema. Per proteggerli anche dal
furto del disco attiva la cifratura del disco (BitLocker su Windows, LUKS su
Ubuntu). Evita cartelle di rete o sincronizzate (OneDrive, NAS): se ne scegli
una, l'app avverte, ma il database può rovinarsi. Anche `archivio/` e
`backup/` contengono dati di salute e posizioni GPS.

## Log

L'app scrive un log con rotazione (al massimo circa 4 MB) nella cartella dei
log dell'utente. Per aprirla: Aiuto, Apri la cartella dei log (Ctrl+Maiusc+L).
Il log ha eventi e tipi di errore, senza percorsi, nomi di file né dati di
salute. Se qualcosa non va, allegalo a una segnalazione dopo averlo riletto.

## Avvio da sorgente

```
uv sync
uv run --locked nox -s i18n
uv run ponos-coach
```

Il comando `nox -s i18n` genera le traduzioni italiane (`.qm`); senza, l'app
parte in inglese. `uv run ponos-coach --lang en` o `--lang it` forza la lingua.

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
