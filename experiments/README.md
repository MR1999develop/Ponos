# Esperimenti

`garmin_spike.py` e' lo spike della fase 0: verifica dal vivo l'accesso a Garmin
Connect con `garminconnect` (login con MFA, token nel portachiavi, ultime
attivita', conteggio e tempi dello storico, un download FIT originale con
lettura del tracciato, allenamenti programmati in sola lettura).

- Lancio, nel proprio terminale (chiede email, password ed eventuale codice
  MFA; nessuno dei tre viene salvato): `uv run --locked python experiments/garmin_spike.py`.
  `--downloads N` scarica fino a 5 file per la statistica d'orario.
- Scrive `experiments/garmin_spike.md` (solo dati non personali) e, in
  `experiments/spike-data/` (ignorata da git), lo zip e il FIT scaricati.
  Nel portachiavi mette la voce `PonosCoach-spike`.
- Attenzione: `experiments/spike-data/` contiene FC e GPS in chiaro; a fine prova
  va cancellata con `--logout`.
- Pulizia: `uv run --locked python experiments/garmin_spike.py --logout` cancella la voce del
  portachiavi e i file di `spike-data/`.
- Esce con 0 se tutti i passi riescono, con 1 se qualcuno fallisce.
