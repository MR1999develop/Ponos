// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 MR
"use strict";

// The activity-detail map page. Python calls `ponosTrack.show` once with a single JSON object made
// by json.dumps (no data enters as HTML, no text is composed here). There is no message channel:
// the page only draws. Tiles come from ponos://tiles/{z}/{x}/{y}, answered by the app; the page
// makes no network request and never stores a position.
(function () {
  var MAX_ZOOM = 19;
  var PADDING = [24, 24];
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var map = null;
  var layers = null;
  var last = null; // what the page shows now, for the diagnostics hook

  function finite(value) { return typeof value === "number" && isFinite(value); }

  function applyColors(colors) {
    var root = document.documentElement.style;
    root.setProperty("--map-bg", colors.background);
    root.setProperty("--map-fg", colors.text);
    root.setProperty("--map-panel", colors.panel);
  }

  function build(config) {
    var container = document.getElementById("map");
    container.setAttribute("aria-label", config.strings.map);
    map = L.map(container, {
      preferCanvas: true, zoomControl: false, attributionControl: false, minZoom: 0,
      maxZoom: MAX_ZOOM, zoomAnimation: !reduceMotion, fadeAnimation: !reduceMotion,
      markerZoomAnimation: !reduceMotion, keyboard: true, center: [0, 0], zoom: 2,
    });
    L.control.zoom({ zoomInTitle: config.strings.zoomIn, zoomOutTitle: config.strings.zoomOut })
      .addTo(map);
    L.control.attribution({ prefix: false }).addTo(map).addAttribution(config.attribution);
    L.tileLayer(config.tileUrl, {
      minZoom: 0, maxZoom: MAX_ZOOM, tileSize: 256, keepBuffer: 0, updateWhenZooming: false,
    }).addTo(map);
    layers = L.layerGroup().addTo(map);
  }

  // Stretches of the track: a new one starts where `segment` changes (a gap: no line across it).
  function stretches(payload) {
    var out = [];
    var run = [];
    for (var i = 0; i < payload.lat.length; i++) {
      if (!finite(payload.lat[i]) || !finite(payload.lon[i])) { continue; }
      if (run.length > 0 && payload.segment[i] !== payload.segment[i - 1]) {
        out.push(run);
        run = [];
      }
      run.push([payload.lat[i], payload.lon[i]]);
    }
    if (run.length > 0) { out.push(run); }
    return out;
  }

  function pin(at, kind, title) {
    L.marker(at, {
      title: title, keyboard: false, interactive: false,
      icon: L.divIcon({ className: "", html: '<div class="ponos-pin ponos-pin-' + kind + '"></div>',
                        iconSize: [16, 16], iconAnchor: [8, 8] }),
    }).addTo(layers);
  }

  function show(payload) {
    if (map === null) { applyColors(payload.colors); build(payload); }
    layers.clearLayers();
    var lines = stretches(payload);
    if (lines.length === 0) { return; }
    var all = [];
    lines.forEach(function (line) {
      // A halo in the background colour under the line keeps it readable on any tile.
      L.polyline(line, { color: payload.colors.background, weight: 8, opacity: 0.9, interactive: false })
        .addTo(layers);
      L.polyline(line, { color: payload.colors.track, weight: 4, opacity: 1, interactive: false })
        .addTo(layers);
      all = all.concat(line);
    });
    pin(all[0], "start", payload.strings.start);
    pin(all[all.length - 1], "finish", payload.strings.finish);
    map.fitBounds(L.latLngBounds(all), { padding: PADDING, maxZoom: 17, animate: false });
    last = { points: all.length, stretches: lines.length, trackColor: payload.colors.track };
  }

  // Diagnostics hook: what the page shows (used by tests).
  function state() {
    var bounds = map === null ? null : map.getBounds();
    return {
      drawn: last, zoom: map === null ? null : map.getZoom(),
      bounds: bounds === null ? null : [bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast()],
      tileUrl: map === null ? null : Object.values(map._layers).filter(function (l) { return l._url; })[0]._url,
      pins: document.querySelectorAll(".ponos-pin").length,
    };
  }

  window.ponosTrack = { show: show, state: state };
})();
