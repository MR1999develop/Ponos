// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 MR
"use strict";

// Draws a Plotly figure. The only entry point is `ponosRender(payload)`, where `payload` is
// a JSON object produced by Python with json.dumps: { figure, labels, locale, filename, hover }.
// No data enters as HTML: Plotly receives only objects. With `hover` true the chart tells Python
// which point is under the pointer through the page title (`hover-I-N`, `leave-N`: no channel),
// and `ponosCursor(x)` draws (or, with null, removes) a vertical cursor line at x.
(function () {
  var host = document.getElementById("chart");
  var drawCount = 0;
  var hoverCount = 0;
  var cursorColor = "#888888";

  function bindHover(gd) {
    if (gd.ponosHoverBound) { return; }
    gd.ponosHoverBound = true;
    gd.on("plotly_hover", function (event) {
      var main = event.points.filter(function (point) { return point.curveNumber === 0; })[0];
      if (main === undefined) { return; }
      hoverCount += 1;
      document.title = "hover-" + main.pointIndex + "-" + hoverCount;
    });
    gd.on("plotly_unhover", function () {
      hoverCount += 1;
      document.title = "leave-" + hoverCount;
    });
  }

  window.ponosCursor = function (x) {
    var shapes = [];
    if (x !== null) {
      shapes.push({
        type: "line", xref: "x", yref: "paper", x0: x, x1: x, y0: 0, y1: 1,
        line: { color: cursorColor, width: 2, dash: "dot" },
      });
    }
    Plotly.relayout(host, { shapes: shapes });
  };

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
    // Toolbar titles translated by Python: Plotly language dictionary, no texts in JS.
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
    cursorColor = payload.figure.layout.font.color;
    Plotly.react(host, payload.figure.data, payload.figure.layout, config).then(function (gd) {
      if (payload.hover === true) { bindHover(gd); }
      // Signal to Python without channels: the page title changes at every draw.
      drawCount += 1;
      document.title = "drawn-" + drawCount;
      return gd;
    });
  };

})();
