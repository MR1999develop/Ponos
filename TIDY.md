# Pulizie rimandate

Pulizie del codice rimandate: una voce per riga, con data e motivo.

- 2026-11-05: la pagina dei grafici (`QWebEngineView`) può assorbire il trascinamento di file lasciati sul grafico; il trascinamento funziona sul resto della finestra. Da verificare a mano e, se serve, intercettare il drop a livello di applicazione.
- 2026-11-06: un file d'archivio senza record nell'indice (rimasto dopo un commit fallito, perché l'archivio si scrive prima e il record dopo) non viene né ripreso né cancellato. Da riconciliare con il giornale della sincronizzazione in fase 2.
