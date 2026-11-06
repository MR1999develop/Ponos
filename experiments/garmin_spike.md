# Esito dello spike Garmin

Generato da `experiments/garmin_spike.py`. Solo dati non personali.

## Ambiente

- Python: 3.13.16
- Sistema: Windows 10
- garminconnect: 0.3.20
- fitdecode: 0.11.0
- keyring: 25.7.0
- Data: 2026-11-06

## Misure

- Durata del login (s): 9.2
- MFA richiesta: no
- Login: riuscito
- Dimensione token serializzati (byte UTF-8): 2255
- Dimensione token serializzati (caratteri): 2255
- Dimensione token serializzati (byte UTF-16): 4510
- Token oltre il limite del Credential Manager (UTF-16): si
- Limite Credential Manager generico (byte): 2560
- Backend portachiavi: WinVaultKeyring
- Salvataggio nel portachiavi: FALLITO (error, causa OSError)
- Ultime attivita' elencate: 5
- Attivita' contate a pagine: 1552
- Pagine richieste: 16
- Dimensione pagina: 100
- Richieste dell'elenco: 16
- Risposte 429 durante l'elenco: 0
- Tempo totale dell'elenco (s): 24.9
- Totale dichiarato da count_activities: 1552
- Dimensione zip (byte): 68349
- Dimensione FIT estratto (byte): 148947
- Campioni di FC (messaggi record con FC): 1836
- Messaggi record totali: 1836
- Intervallo mediano tra record (s): 1.0
- Campi di file_id presenti: serial_number, time_created, unknown_7, manufacturer, garmin_product, number, type
- file_id.serial_number presente: si
- file_id.time_created presente: si
- session.start_time presente: si
- Download ORIGINAL eseguiti: 1
- Tempo medio di un download (s): 0.27
- Risposte 429 durante i download: 0
- Differenza session.start_time FIT - startTimeGMT elenco (s): n=1, min=0.0, mediana=0.0, max=0.0
- Allenamenti programmati nei prossimi 30 giorni: 0
- Metodi per creare allenamenti: `upload_workout(workout_json: dict[str, typing.Any] | list[typing.Any] | str) -> dict[str, typing.Any]`; `upload_running_workout(workout: Any) -> dict[str, typing.Any]`; `upload_cycling_workout(workout: Any) -> dict[str, typing.Any]`; `upload_swimming_workout(workout: Any) -> dict[str, typing.Any]`; `upload_strength_workout(workout: Any) -> dict[str, typing.Any]`; `copy_workout(workout_id: int | str, name: str) -> Any`; `update_workout(workout_id: int | str, workout_json: dict[str, typing.Any] | str) -> dict[str, typing.Any]`
- Metodi per programmare: `schedule_workout(workout_id: int | str, date_str: str) -> dict[str, typing.Any]`; `reschedule_workout(scheduled_workout_id: int | str, cdate: str) -> Any`; `create_calendar_event(event: dict[str, typing.Any]) -> Any`
- Metodi per deprogrammare: `unschedule_workout(scheduled_workout_id: int | str) -> Any`; `delete_calendar_event(event_id: int | str) -> Any`
- Metodi per eliminare allenamenti: `delete_workout(workout_id: int | str) -> Any`
- Metodi di lettura: `get_workouts(start: int = 0, limit: int = 100, *, my_workouts_only: bool | None = None, shared_workouts_only: bool | None = None, include_atp: bool | None = None, order_by: str | None = None, order_seq: str | None = None, sport_type_keys: list[str] | None = None) -> list[dict[str, typing.Any]]`; `get_scheduled_workouts(year: int | str, month: int | str) -> dict[str, typing.Any]`; `get_scheduled_workouts_by_period(start: str, end: str) -> list[dict[str, typing.Any]]`; `get_calendar_events(start: str, end: str) -> list[dict[str, typing.Any]]`
- Stima della prima sincronizzazione completa: circa 43 minuti (1552 download x 1.6 s + elenco 25 s)

## Passi

- Riusciti: login, ultime-5, storico, download-fit, calendario
- Falliti: portachiavi

## Errori incontrati

- portachiavi: salvataggio nel portachiavi fallito: error, causa OSError
