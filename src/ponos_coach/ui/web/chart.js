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

  // Date range of the x axis after a zoom or a pan by the user: `xrange|START|END|N`, or
  // `xrange|auto|N` after a reset. A range set by `ponosSetXRange` is not reported back, so linked
  // charts do not echo each other.
  var applyingRange = 0;  // relayouts started by the app and not yet completed
  var rangeCount = 0;

  function reportedRange(event) {
    var keys = Object.keys(event);
    for (var i = 0; i < keys.length; i += 1) {
      var match = /^(xaxis\d*)\.range\[0\]$/.exec(keys[i]);
      if (match !== null && (match[1] + ".range[1]") in event) {
        return [event[keys[i]], event[match[1] + ".range[1]"]];
      }
      match = /^(xaxis\d*)\.range$/.exec(keys[i]);
      if (match !== null && Array.isArray(event[keys[i]])) {
        return [event[keys[i]][0], event[keys[i]][1]];
      }
      if (/^xaxis\d*\.autorange$/.test(keys[i]) && event[keys[i]] === true) {
        return "auto";
      }
    }
    return null;
  }

  function bindRange(gd) {
    if (gd.ponosRangeBound) { return; }
    gd.ponosRangeBound = true;
    gd.on("plotly_relayout", function (event) {
      if (applyingRange > 0) { return; }
      var range = reportedRange(event);
      if (range === null) { return; }
      rangeCount += 1;
      document.title = range === "auto"
        ? "xrange|auto|" + rangeCount
        : "xrange|" + String(range[0]) + "|" + String(range[1]) + "|" + rangeCount;
    });
  }

  // Show the dates from `start` to `end` (ISO strings); with null the whole series. A burst of
  // calls (one per wheel step on the linked chart) redraws at most once per frame, with the latest.
  var wantedRange;
  var rangeFrame = null;

  function applyWantedRange() {
    rangeFrame = null;
    if (host.data === undefined) { return; }
    var update = wantedRange === null
      ? { "xaxis.autorange": true }
      : { "xaxis.range": wantedRange };
    // A counter, not a flag: two relayouts can overlap, and the first to finish must not
    // re-enable the report while the other one is still being applied.
    applyingRange += 1;
    Plotly.relayout(host, update).then(
      function () { applyingRange -= 1; },
      function () { applyingRange -= 1; }
    );
  }

  window.ponosSetXRange = function (start, end) {
    wantedRange = start === null ? null : [start, end];
    if (rangeFrame === null) { rangeFrame = window.requestAnimationFrame(applyWantedRange); }
  };

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

  // The y axis follows the data in view (the user's request of 2026-11-10): after every change
  // of the dates shown, by the user or by the linked chart, the y range is the lowest and the
  // highest value drawn between those dates, stacked bars summed per date, bars from zero, with
  // a small margin. Without data in view the range stays as it is.
  var Y_MARGIN = 0.06;

  function timeOf(value) {
    return typeof value === "number" ? value : Date.parse(String(value).replace(" ", "T"));
  }

  function visibleYExtent(gd) {
    var range = gd._fullLayout.xaxis.range;
    var first = timeOf(range[0]);
    var last = timeOf(range[1]);
    var stacked = gd._fullLayout.barmode === "stack" || gd._fullLayout.barmode === "relative";
    var low = Infinity;
    var high = -Infinity;
    var sums = {};
    // `_fullData`: the series as drawn, with the numbers decoded (in `data` they can still be
    // Plotly's base64 typed arrays).
    gd._fullData.forEach(function (trace) {
      if (trace.visible !== true) { return; }
      if (!trace.x || !trace.y || (trace.yaxis && trace.yaxis !== "y")) { return; }
      var bar = trace.type === "bar";
      for (var i = 0; i < trace.x.length; i += 1) {
        var when = timeOf(trace.x[i]);
        var value = Number(trace.y[i]);
        if (!(when >= first && when <= last) || !isFinite(value)) { continue; }
        if (bar && stacked) {
          var sum = sums[when] || { up: 0, down: 0 };
          if (value >= 0) { sum.up += value; } else { sum.down += value; }
          sums[when] = sum;
        } else {
          low = Math.min(low, value);
          high = Math.max(high, value);
        }
        if (bar) { low = Math.min(low, 0); high = Math.max(high, 0); }
      }
    });
    Object.keys(sums).forEach(function (key) {
      low = Math.min(low, sums[key].down);
      high = Math.max(high, sums[key].up);
    });
    return isFinite(low) && isFinite(high) ? [low, high] : null;
  }

  var fitFrame = null;

  function fitY(gd) {
    fitFrame = null;
    if (gd._fullData === undefined || gd._fullLayout.xaxis === undefined) { return; }
    var extent = visibleYExtent(gd);
    if (extent === null) { return; }
    var span = extent[1] - extent[0] || Math.abs(extent[1]) || 1;
    var low = extent[0] - (extent[0] < 0 ? span * Y_MARGIN : 0);
    var high = extent[1] + span * Y_MARGIN;
    Plotly.relayout(gd, { "yaxis.autorange": false, "yaxis.range": [low, high] });
  }

  function bindAutoY(gd) {
    if (gd.ponosAutoY) { return; }
    gd.ponosAutoY = true;
    gd.on("plotly_relayout", function (event) {
      var xChanged = Object.keys(event).some(function (key) { return /^xaxis\d*\./.test(key); });
      if (xChanged && fitFrame === null) {
        fitFrame = window.requestAnimationFrame(function () { fitY(gd); });
      }
    });
  }

  // Right button held and dragged: up stretches the chart vertically, down squeezes it (the
  // user's request of 2026-11-09). Charts that start at zero keep their floor; the others scale
  // around their middle. The y axes stay closed to Plotly's own drags.
  var STRETCH_PER_PIXEL = 0.006;

  function bindStretch(gd) {
    if (gd.ponosStretch) { return; }
    gd.ponosStretch = true;
    gd.addEventListener("contextmenu", function (event) { event.preventDefault(); });
    gd.addEventListener("mousedown", function (event) {
      if (event.button !== 2) { return; }
      event.preventDefault();
      event.stopPropagation();
      var range = gd._fullLayout.yaxis.range.slice();
      var low = Number(range[0]);
      var high = Number(range[1]);
      var anchor = low >= 0 ? low : (low + high) / 2;
      var startY = event.clientY;
      function move(moved) {
        var factor = Math.exp((moved.clientY - startY) * STRETCH_PER_PIXEL);
        Plotly.relayout(gd, {
          "yaxis.autorange": false,
          "yaxis.range": [anchor + (low - anchor) * factor, anchor + (high - anchor) * factor],
        });
      }
      function up(released) {
        move(released);
        window.removeEventListener("mousemove", move, true);
        window.removeEventListener("mouseup", up, true);
      }
      window.addEventListener("mousemove", move, true);
      window.addEventListener("mouseup", up, true);
    }, true);
  }

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
      // With `zoom` the wheel zooms and a left drag pans the x axis (the figure sets
      // `dragmode` and fixes the y axes); the other charts keep the toolbar only.
      scrollZoom: payload.zoom === true,
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
      if (payload.linkedX === true) { bindRange(gd); }
      if (payload.zoom === true && payload.fixedY !== true) {
        bindStretch(gd);
        bindAutoY(gd);
        fitY(gd);
      }
      // Signal to Python without channels: the page title changes at every draw.
      drawCount += 1;
      document.title = "drawn-" + drawCount;
      return gd;
    });
  };

})();
