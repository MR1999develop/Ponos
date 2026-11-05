# 001. Stack: Python, PySide6 e Plotly

- Stato: Accettato
- Data: 2026-11-05

## Contesto

Ponos Coach è un'app desktop per Windows e Ubuntu con tabelle di attività,
grafici interattivi del carico (PMC, zone, FC) e un adattatore verso Garmin
Connect. Il cambio di stack dopo l'inizio è costoso. I grafici e le tabelle
sono il cuore dell'interfaccia; l'accesso a Garmin dipende da una libreria
esistente.

## Opzioni

1. **Python + PySide6 + pyqtgraph.**
   - Pro: pacchetto leggero, grafici nativi veloci.
   - Contro: grafici meno curati e meno interattivi (legende, tooltip,
     esportazione vettoriale, temi) da costruire a mano.
2. **Python + PySide6 + Plotly in `QWebEngineView`.**
   - Pro: grafici migliori e interattivi, esportazione SVG e PNG, stesso
     linguaggio della libreria Garmin.
   - Contro: pacchetto più pesante (circa 250 MB); un motore web dentro l'app,
     da isolare (nessuna rete, nessun ponte verso il codice); dati dei grafici
     da passare come JSON.
3. **Rust + egui.**
   - Pro: è lo stack usuale dei progetti desktop, un solo eseguibile leggero.
   - Contro: il login Garmin va riscritto da zero; grafici e tabelle più
     spartani; più lavoro per le stesse funzioni.
4. **Tauri (Rust) + TypeScript + ECharts.**
   - Pro: grafici ricchi, pacchetto più leggero di Qt WebEngine.
   - Contro: due linguaggi e due toolchain; nessuna skill per il frontend; il
     login Garmin va comunque riscritto.

## Decisione

Opzione 2: Python 3.13, PySide6 (Qt 6), grafici Plotly in un `QWebEngineView`
con `plotly.js` incluso. Scelta dell'utente: grafici migliori contro un
pacchetto più pesante. Il resto dello stack è in `PLAN.md` sezione 4.1.

## Conseguenze

- Il pacchetto pesa circa 250 MB: il valore va misurato in fase 8.
- La pagina dei grafici è un confine di fiducia: solo contenuto locale,
  richieste di rete bloccate, dati come JSON codificato, nessun `QWebChannel`
  salvo necessità dichiarata (vedi `docs/threat-model.md`).
- Il dominio resta puro e indipendente da Qt: se lo stack va cambiato, si
  riscrivono interfaccia e adattatori, non il dominio.
- Le dipendenze dirette si validano e si registrano in `docs/dipendenze.md`.

## Come se ne verifica il rispetto

- `lint-imports` in `uv run --locked nox`: il dominio non importa Qt, rete o I/O; le
  dipendenze vanno solo `ui → casi d'uso → dominio`.
- Test T-SEC-06 sulla pagina dei grafici; T-UI-04 sui grafici.
- Fase 8: dimensione del pacchetto misurata e registrata.
- Un cambio di stack richiede un nuovo ADR che sostituisce questo.
