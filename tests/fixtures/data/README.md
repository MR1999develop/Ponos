# Fixture FIT

Tutti i file sono generati, con seme fisso e date inventate (marzo 2026,
produttore `development`, numeri di serie fittizi). Si rigenerano dalla radice
del repository con:

    python -m tests.fixtures.generators.make_fixtures

Rieseguito, produce gli stessi byte. Gli script usano solo la libreria standard.
Lo storico di attività per i test (`generators/history.py`,
`generate_history(out_dir, days, seed, start_date)`) non è versionato: lo
scrivono i test in una cartella temporanea.

## `fit/` (script: `generators/make_fixtures.py`)

| File | Cosa prova |
|---|---|
| `run_constant_160.fit` | Corsa di 60' esatti a 160 bpm, senza rumore: T-CALC-01 (valori a mano) |
| `copy_of_run_constant_160.fit` | Stessi byte e stesso `file_id` del precedente, nome diverso: duplicato per T-IMP |
| `run_intervals.fit` | Corsa con riscaldamento, 6 x (3' forte, 2' recupero), deriva e defaticamento |
| `bike_easy.fit` | Bici facile di 55' con lieve deriva cardiaca |
| `strength_low_hr.fit` | Forza (`sport` training, `sub_sport` strength_training), FC bassa |
| `walk_no_hr.fit` | Camminata senza FC (tutti i `heart_rate` = 0xFF): stima marcata, T-CALC-06 |
| `run_hr_gaps.fit` | Corsa con 12 s di FC non valida e 15 s senza record: buchi oltre 10 s, T-CALC-07 |
| `run_hr_out_of_range.fit` | Corsa con campioni a 0 e 250 bpm: campioni scartati e contati, T-CALC-03 |
| `run_one_second.fit` | Attività di 1 s (un solo record): T-CALC-07 |
| `hike_24h.fit` | Escursione di 24 h, un campione ogni 5 s: durata massima, T-CALC-07 |

## `fit-ostili/` (script: `generators/make_fixtures.py`, derivati da un FIT valido di 5 campioni)

Tutti devono essere rifiutati dal lettore con un errore, mai con un crash o
un'allocazione enorme (T-SEC, T-IMP-01). Eccezione: `empty.fit` non è un errore
per `fitdecode` (zero messaggi), ma l'app deve scartarlo.

| File | Cosa prova |
|---|---|
| `header_size_too_large.fit` | Intestazione dichiara 1000 byte di dati in più del vero |
| `header_size_too_small.fit` | Dimensione dichiarata 10, dati reali più lunghi |
| `header_size_max_uint32.fit` | Dimensione dichiarata 0xFFFFFFFF |
| `header_size_huge.fit` | Dimensione dichiarata ~4 GiB, file di 22 byte |
| `truncated_in_header.fit` | File di 9 byte, intestazione incompleta |
| `truncated_mid_record.fit` | Troncato a metà di un messaggio |
| `truncated_no_crc.fit` | Manca il CRC finale |
| `bad_file_crc.fit` | CRC finale errato |
| `bad_header_crc.fit` | CRC dell'intestazione errato |
| `bad_signature.fit` | Firma `.FIX` al posto di `.FIT` |
| `undefined_local_message.fit` | Messaggio di dati senza definizione |
| `huge_field_definition.fit` | Definizione con 255 campi da 255 byte e dati troncati |
| `empty.fit` | File vuoto (0 byte) |
