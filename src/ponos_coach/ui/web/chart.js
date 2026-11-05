// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 MR
"use strict";

// Disegna una figura Plotly. L'unico ingresso e `ponosRender(payload)`, dove `payload` e
// un oggetto JSON prodotto da Python con json.dumps: { figure, labels, locale, filename }.
// Nessun dato entra come HTML: Plotly riceve solo oggetti.
(function () {
  var host = document.getElementById("chart");
  var drawCount = 0;

  function exportButton(name, title, format, filename) {
    return {
      name: name,
      title: title,
      icon: Plotly.Icons.camera,
      click: function (gd) {
        Plotly.downloadImage(gd, { format: format, filename: filename, scale: 2 });
      },
    };
  }

  window.ponosRender = function (payload) {
    var labels = payload.labels;
    // Titoli della barra tradotti da Python: dizionario di lingua di Plotly, senza testi in JS.
    Plotly.register({
      moduleType: "locale",
      name: payload.locale.name,
      dictionary: payload.locale.dictionary,
    });
    var config = {
      locale: payload.locale.name,
      displaylogo: false,
      responsive: true,
      scrollZoom: false,
      modeBarButtonsToRemove: ["toImage", "select2d", "lasso2d"],
      modeBarButtonsToAdd: [
        exportButton("ponosPng", labels.png, "png", payload.filename),
        exportButton("ponosSvg", labels.svg, "svg", payload.filename),
      ],
    };
    document.body.style.background = payload.figure.layout.paper_bgcolor;
    Plotly.react(host, payload.figure.data, payload.figure.layout, config).then(function (gd) {
      // Segnale verso Python senza canali: il titolo della pagina cambia a ogni disegno.
      drawCount += 1;
      document.title = "drawn-" + drawCount;
      return gd;
    });
  };

})();
