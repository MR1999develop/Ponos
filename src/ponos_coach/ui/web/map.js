// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 MR
"use strict";

// The map page. Python calls the functions of `window.ponosMap` with a single JSON object made by
// json.dumps (no data enters as HTML, no text is composed here); the page answers through the
// `bridge` object of the QWebChannel, whose slots take only numbers (indexes into the lists that
// Python sent). Tiles come from ponos://tiles/{z}/{x}/{y}, answered by the app: the page makes
// no network request. Positions are never stored by the page.
(function () {
  var MAX_ZOOM = 19;
  var MOVE_DEBOUNCE_MS = 120;
  var HOVER_PIXELS = 12;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var cfg = null;
  var map = null;
  var bridge = null;
  var cluster = null;
  var tileLayer = null;
  var trackLayer = null;
  var selectionLayer = null;
  var profileLayer = null;
  var cursor = null;
  var startsByIdx = new Map(); // activity index -> [lat, lon, sport]
  var tracksByIdx = new Map(); // activity index -> [polyline, ...]
  var selected = null;
  var profile = null; // { idx, lat, lon, segment, zone, mode }
  var moveTimer = null;
  var hoverFrame = 0;
  var hovering = false;

  function finite(value) { return typeof value === "number" && isFinite(value); }

  function sendView() {
    moveTimer = null;
    if (bridge === null || map === null) { return; }
    var b = map.getBounds();
    bridge.viewChanged(b.getSouth(), b.getWest(), b.getNorth(), b.getEast(), map.getZoom());
  }

  function scheduleView() {
    if (moveTimer !== null) { clearTimeout(moveTimer); }
    moveTimer = setTimeout(sendView, MOVE_DEBOUNCE_MS);
  }

  function sportStyle(sport) {
    return cfg.sports[sport] || cfg.sports[cfg.sports.length - 1];
  }

  function applyColors(colors) {
    var root = document.documentElement.style;
    root.setProperty("--map-bg", colors.background);
    root.setProperty("--map-fg", colors.text);
    root.setProperty("--map-panel", colors.panel);
    root.setProperty("--cluster-bg", colors.text);
    root.setProperty("--cluster-fg", colors.background);
  }

  function clusterIcon(group) {
    var n = group.getChildCount();
    var size = n < 10 ? 34 : (n < 100 ? 40 : 46);
    return L.divIcon({
      html: "<div><span>" + n.toLocaleString(cfg.locale) + "</span></div>",
      className: "ponos-cluster",
      iconSize: [size, size],
    });
  }

  // Close up, groups are tight (near starts part), and starts at the very same place (most
  // activities start from home) fan out around their group on a click at the highest zoom, so
  // each one can be clicked (user's report of 2026-11-09).
  var fannedOut = 0;

  function clusterRadius(zoom) { return zoom >= 15 ? 15 : 50; }

  function makeCluster() {
    var group = L.markerClusterGroup({
      chunkedLoading: true,
      animate: !reduceMotion,
      maxClusterRadius: clusterRadius,
      spiderfyOnMaxZoom: true,
      spiderfyDistanceMultiplier: 1.6,
      showCoverageOnHover: false,
      zoomToBoundsOnClick: true,
      iconCreateFunction: clusterIcon,
    });
    group.on("spiderfied", function (event) { fannedOut = event.markers.length; });
    group.on("unspiderfied", function () { fannedOut = 0; });
    return group;
  }

  function init(config) {
    cfg = config;
    applyColors(cfg.colors);
    var container = document.getElementById("map");
    container.setAttribute("aria-label", cfg.strings.map);
    map = L.map(container, {
      preferCanvas: true,
      renderer: L.canvas({ tolerance: 6 }),  // clicks a few pixels off a start still hit it
      zoomControl: false,
      attributionControl: false,
      minZoom: 0,
      maxZoom: MAX_ZOOM,
      zoomAnimation: !reduceMotion,
      fadeAnimation: !reduceMotion,
      markerZoomAnimation: !reduceMotion,
      keyboard: true,
      center: [cfg.view.lat, cfg.view.lon],
      zoom: cfg.view.zoom,
    });
    L.control.zoom({ zoomInTitle: cfg.strings.zoomIn, zoomOutTitle: cfg.strings.zoomOut }).addTo(map);
    L.control.attribution({ prefix: false }).addTo(map).addAttribution(cfg.attribution);
    tileLayer = L.tileLayer(cfg.tileUrl, {
      minZoom: 0, maxZoom: MAX_ZOOM, tileSize: 256, keepBuffer: 0, updateWhenZooming: false,
    }).addTo(map);
    trackLayer = L.layerGroup().addTo(map);
    selectionLayer = L.layerGroup().addTo(map);
    profileLayer = L.layerGroup().addTo(map);
    cluster = makeCluster();
    map.addLayer(cluster);
    map.on("moveend zoomend", scheduleView);
    map.on("zoomend", resizeStarts);
    map.on("mousemove", onMouseMove);
    map.on("mouseout", leaveProfile);
    scheduleView();
  }

  // --- Start points -------------------------------------------------------------------

  // A single start grows with the zoom: close up it is a target easy to click (user's request).
  var startMarkers = [];

  function startRadius() {
    var zoom = map.getZoom();
    return zoom >= 15 ? 12 : (zoom >= 13 ? 9 : 6);
  }

  function resizeStarts() {
    var radius = startRadius();
    startMarkers.forEach(function (marker) { marker.setRadius(radius); });
  }

  function setStarts(payload) {
    var data = payload.data;
    var markers = [];
    var radius = startRadius();
    startsByIdx = new Map();
    for (var k = 0; k + 3 < data.length; k += 4) {
      var idx = data[k + 2];
      var sport = data[k + 3];
      startsByIdx.set(idx, [data[k], data[k + 1], sport]);
      var marker = L.circleMarker([data[k], data[k + 1]], {
        radius: radius, color: cfg.colors.background, weight: 1.5,
        fillColor: sportStyle(sport).color, fillOpacity: 1,
      });
      marker.ponosIdx = idx;
      marker.ponosGen = payload.gen;
      marker.on("click", onItemClick);
      markers.push(marker);
    }
    startMarkers = markers;
    cluster.clearLayers();
    cluster.addLayers(markers);
    drawSelection(false);
  }

  function onItemClick(event) {
    if (bridge !== null) { bridge.activityClicked(event.target.ponosIdx, event.target.ponosGen); }
  }

  // --- Tracks -------------------------------------------------------------------------

  function toLatLngs(flat) {
    var out = [];
    for (var k = 0; k + 1 < flat.length; k += 2) { out.push([flat[k], flat[k + 1]]); }
    return out;
  }

  function setTracks(payload) {
    trackLayer.clearLayers();
    tracksByIdx = new Map();
    payload.tracks.forEach(function (track) {
      var style = sportStyle(track.sport);
      var lines = track.segments.map(function (flat) {
        var line = L.polyline(toLatLngs(flat), {
          color: style.color, weight: 3, opacity: 0.85, dashArray: style.dash || null,
        });
        line.ponosIdx = track.idx;
        line.ponosGen = payload.gen;
        // Kept, not drawn: only the selected activity's track is on the map (user's request).
        return line;
      });
      tracksByIdx.set(track.idx, lines);
    });
    drawSelection(false);
    tryFit();
  }

  // --- Selection ----------------------------------------------------------------------

  // The selected activity's track: from its profile points (they arrive at any zoom), otherwise
  // from the track of the visible area while the points are on their way.
  function selectedLatLngs() {
    if (profile !== null && profile.idx === selected) {
      var runs = [];
      var run = [];
      for (var i = 0; i < profile.lat.length; i++) {
        if (i > 0 && profile.segment[i] !== profile.segment[i - 1]) {
          if (run.length >= 2) { runs.push(run); }
          run = [];
        }
        if (finite(profile.lat[i]) && finite(profile.lon[i])) { run.push([profile.lat[i], profile.lon[i]]); }
      }
      if (run.length >= 2) { runs.push(run); }
      return runs;
    }
    var lines = tracksByIdx.get(selected);
    return lines === undefined ? null : lines.map(function (line) { return line.getLatLngs(); });
  }

  function drawSelectedTrack(runs, sport) {
    var style = sportStyle(sport);
    runs.forEach(function (run) {
      L.polyline(run, {
        color: cfg.colors.background, weight: 9, opacity: 0.9, interactive: false,
      }).addTo(selectionLayer);
      L.polyline(run, {
        color: style.color, weight: 5, opacity: 1, dashArray: style.dash || null, interactive: false,
      }).addTo(selectionLayer);
    });
  }

  function drawSelection(reveal) {
    selectionLayer.clearLayers();
    if (selected === null) { return; }
    var start = startsByIdx.get(selected);
    var runs = selectedLatLngs();
    var zonesShown = profile !== null && profile.mode === "zones" && profile.idx === selected;
    if (runs !== null && !zonesShown) {
      drawSelectedTrack(runs, start === undefined ? 0 : start[2]);
    }
    if (start === undefined) { return; }
    L.circleMarker([start[0], start[1]], {
      radius: 11, color: cfg.colors.text, weight: 3, fill: false, interactive: false,
    }).addTo(selectionLayer);
    if (reveal && !map.getBounds().contains([start[0], start[1]])) {
      map.panTo([start[0], start[1]], { animate: !reduceMotion });
    }
  }

  function setSelected(payload) {
    selected = payload.idx;
    if (selected === null) { fitPending = false; }
    drawSelection(payload.reveal === true);
    tryFit();
  }

  // A double click in the list: the map shows the whole track of the selected activity. The
  // points may arrive later: the start is shown at once and the track fitted when it comes.
  var fitPending = false;

  function focusSelected() {
    fitPending = true;
    tryFit();
  }

  function tryFit() {
    if (!fitPending || selected === null) { return; }
    var runs = selectedLatLngs();
    var points = runs === null ? [] : [].concat.apply([], runs);
    if (points.length >= 2) {
      fitPending = false;
      map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 15, animate: false });
      return;
    }
    var start = startsByIdx.get(selected);
    if (start !== undefined) {
      map.setView([start[0], start[1]], Math.max(map.getZoom(), 13), { animate: false });
    }
  }

  // --- Profile of the selected activity (zones and cursor) ----------------------------

  function clearProfile() {
    profile = null;
    profileLayer.clearLayers();
    cursor = null;
    hovering = false;
  }

  function drawZones() {
    var run = [];
    var runZone = null;
    function flush() {
      if (run.length >= 2) {
        var known = runZone !== null && runZone >= 0;
        L.polyline(run, {
          color: known ? cfg.zoneColors[runZone] : cfg.noZoneColor,
          weight: 5, opacity: 1, dashArray: known ? null : "4 6", interactive: false,
        }).addTo(profileLayer);
      }
      run = [];
    }
    for (var i = 0; i + 1 < profile.lat.length; i++) {
      if (profile.segment[i] !== profile.segment[i + 1]) { flush(); continue; }
      var zone = profile.zone[i];
      if (run.length === 0 || zone !== runZone) {
        flush();
        runZone = zone;
        run = [[profile.lat[i], profile.lon[i]]];
      }
      run.push([profile.lat[i + 1], profile.lon[i + 1]]);
    }
    flush();
  }

  // The points arrive once (`setProfile`); which activity they belong to and the colour mode
  // arrive with `setProfileView`, which may come again when the list or the mode changes.
  function setProfile(payload) {
    clearProfile();
    profile = {
      gen: payload.gen, idx: null, lat: payload.lat, lon: payload.lon, segment: payload.segment,
      zone: payload.zone, mode: "sport",
    };
  }

  function setProfileView(payload) {
    if (profile === null) { return; }
    profile.idx = payload.idx;
    profile.mode = profile.zone !== null && payload.mode === "zones" ? "zones" : "sport";
    profileLayer.clearLayers();
    cursor = null;
    if (profile.mode === "zones") { drawZones(); }
    drawSelection(false);
    tryFit();
  }

  function setCursor(payload) {
    var i = payload.index;
    if (profile === null || !(i >= 0 && i < profile.lat.length)) {
      if (cursor !== null) { profileLayer.removeLayer(cursor); cursor = null; }
      return;
    }
    var at = [profile.lat[i], profile.lon[i]];
    if (cursor === null) {
      cursor = L.circleMarker(at, {
        radius: 7, color: cfg.colors.text, weight: 3, fillColor: cfg.colors.background,
        fillOpacity: 1, interactive: false,
      }).addTo(profileLayer);
    } else {
      cursor.setLatLng(at);
    }
  }

  function nearestProfilePoint(at) {
    var best = -1;
    var bestDistance = HOVER_PIXELS * HOVER_PIXELS;
    for (var i = 0; i < profile.lat.length; i++) {
      var p = map.latLngToContainerPoint([profile.lat[i], profile.lon[i]]);
      var d = (p.x - at.x) * (p.x - at.x) + (p.y - at.y) * (p.y - at.y);
      if (d < bestDistance) { bestDistance = d; best = i; }
    }
    return best;
  }

  function onMouseMove(event) {
    if (profile === null || bridge === null || hoverFrame) { return; }
    var point = event.containerPoint;
    hoverFrame = requestAnimationFrame(function () {
      hoverFrame = 0;
      if (profile === null) { return; }
      var i = nearestProfilePoint(point);
      if (i < 0) { leaveProfile(); return; }
      hovering = true;
      setCursor({ index: i });
      bridge.pointHovered(i, profile.gen);
    });
  }

  function leaveProfile() {
    if (hovering && bridge !== null) { bridge.pointLeft(); }
    hovering = false;
  }

  // --- Other commands --------------------------------------------------------------------------

  function setTheme(colors) {
    cfg.colors = colors;
    applyColors(colors);
    drawSelection(false);
  }

  function fitAll() {
    var points = [];
    startsByIdx.forEach(function (start) { points.push([start[0], start[1]]); });
    if (points.length === 0) { return; }
    map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 13, animate: false });
  }

  function setView(payload) {
    if (finite(payload.lat) && finite(payload.lon) && finite(payload.zoom)) {
      map.setView([payload.lat, payload.lon], payload.zoom, { animate: false });
    }
  }

  function zoomBy(payload) {
    map.setZoom(map.getZoom() + payload.delta, { animate: !reduceMotion });
  }

  // Test and diagnostics hook: count and position of every group or single start the cluster
  // layer shows right now.
  function visibleGroups() {
    var seen = new Map();
    cluster.eachLayer(function (layer) {
      var parent = cluster.getVisibleParent(layer);
      if (parent && !seen.has(parent._leaflet_id)) {
        var count = typeof parent.getChildCount === "function" ? parent.getChildCount() : 1;
        var at = parent.getLatLng();
        seen.set(parent._leaflet_id, { count: count, lat: at.lat, lon: at.lng });
      }
    });
    return Array.from(seen.values());
  }

  // Diagnostics hook: where the profile point `index` is drawn, in pixels of the map container.
  function pixelOf(payload) {
    var at;
    if (payload.start !== undefined) {
      // Where the start is drawn now: fanned out around its group, or at its own place.
      var marker = startMarkers.find(function (m) { return m.ponosIdx === payload.start; });
      var where = marker.getLatLng();
      at = [where.lat, where.lng];
      if (payload.zoom !== undefined) { map.setView(at, payload.zoom, { animate: false }); }
    } else {
      at = [profile.lat[payload.index], profile.lon[payload.index]];
    }
    var p = map.latLngToContainerPoint(at);
    var box = document.getElementById("map").getBoundingClientRect();
    return { x: p.x + box.left, y: p.y + box.top };
  }

  // Diagnostics hook: what the page shows (used by tests and by the bug reports).
  function state() {
    var centre = map.getCenter();
    var zoneLines = profileLayer.getLayers().filter(function (layer) { return layer !== cursor; });
    return {
      zoom: map.getZoom(), center: [centre.lat, centre.lng], tracks: tracksByIdx.size,
      starts: startsByIdx.size, selected: selected, trackLines: trackLayer.getLayers().length,
      startRadius: startRadius(), fannedOut: fannedOut,
      channelOpen: bridge !== null, viewPending: moveTimer !== null,
      attribution: document.querySelector(".leaflet-control-attribution").textContent,
      attributionVisible: document.querySelector(".leaflet-control-attribution").offsetWidth > 0,
      tileUrl: tileLayer._url,
      cursor: cursor === null ? null : [cursor.getLatLng().lat, cursor.getLatLng().lng],
      zoneColors: zoneLines.map(function (layer) { return layer.options.color; }),
      zoneDashes: zoneLines.map(function (layer) { return layer.options.dashArray || ""; }),
      selectedColors: selectionLayer.getLayers().map(function (layer) { return layer.options.color; }),
    };
  }

  window.ponosMap = {
    init: init, setStarts: setStarts, setTracks: setTracks, setSelected: setSelected,
    focusSelected: focusSelected,
    setProfile: setProfile, setProfileView: setProfileView, setCursor: setCursor, setTheme: setTheme, fitAll: fitAll,
    setView: setView, zoomBy: zoomBy, visibleGroups: visibleGroups, state: state, pixelOf: pixelOf,
    clearProfile: function () { clearProfile(); drawSelection(false); },
    refreshTiles: function () { if (tileLayer !== null) { tileLayer.redraw(); } },
  };

  if (typeof qt !== "undefined" && typeof QWebChannel !== "undefined") {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridge = channel.objects.bridge;
      bridge.pageReady();
      if (map !== null) { scheduleView(); }
    });
  }
})();
