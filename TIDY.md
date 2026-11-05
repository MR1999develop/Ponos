# Pulizie rimandate

Pulizie del codice rimandate: una voce per riga, con data e motivo.


- 2026-11-05: `--fit-folder` e "Apri cartella FIT…" sono provvisori: in fase 1 diventano profili e import da cartella. Anche i parametri fisiologici dello scheletro (`PROVISIONAL_PHYSIOLOGY` in `__main__.py`) si sostituiscono con quelli del profilo.
- 2026-11-05: un FIT con tempo in movimento maggiore del tempo trascorso oggi non è rifiutato; decidere in fase 1 (rifiuto oltre 1 s di differenza, altrimenti si porta al trascorso).
