// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 MR
// Leaflet prototype: canvas renderer, starts as circleMarkers grouped with markercluster.
"use strict";

(async function main() {
  const data = loadData();
  const t0 = performance.now();
  const map = L.map("map", {
    preferCanvas: true,
    zoomControl: false,
    attributionControl: false,
    minZoom: 0,
    maxZoom: 19,
    zoomAnimation: false,
    fadeAnimation: false,
    markerZoomAnimation: false,
    center: [44, 11],
    zoom: 5,
  });

  let loaded = () => {};
  const cluster = L.markerClusterGroup({
    chunkedLoading: true,
    chunkProgress: (processed, total) => {
      if (processed === total) loaded();
    },
    chunkInterval: 200,
    chunkDelay: 5,
    animate: false,
    maxClusterRadius: 50,
    disableClusteringAtZoom: 15,
    spiderfyOnMaxZoom: false,
    showCoverageOnHover: false,
    zoomToBoundsOnClick: true,
  });
  const markers = Array.from({ length: data.n }, (_, i) =>
    L.circleMarker([data.starts[2 * i], data.starts[2 * i + 1]], {
      radius: 5, color: "#ffffff", weight: 1.5, fillColor: "#3b6ea5", fillOpacity: 1,
    }));
  log("markers built");
  map.addLayer(cluster);
  await new Promise((resolve) => {
    loaded = resolve;
    cluster.addLayers(markers);
  });

  const trackLine = (i) => {
    const line = [];
    for (let k = data.offsets[i]; k < data.offsets[i + 1]; k++) line.push([data.coords[2 * k], data.coords[2 * k + 1]]);
    return L.polyline(line, { color: "#c45a1a", weight: 2, opacity: 0.7, interactive: false });
  };
  const tracks = L.layerGroup();
  map.addLayer(tracks);
  if (MODE === "tracks") {
    for (let i = 0; i < data.n; i++) tracks.addLayer(trackLine(i));
  }
  await twoFrames();
  const firstDrawMs = performance.now() - t0;

  const bounds = () => {
    const b = map.getBounds();
    return [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()];
  };
  const perform = async (op) => {
    map.setView([op.lat, op.lon], op.zoom, { animate: false });
    await twoFrames();
  };
  const afterOp = async (op, inside) => {
    if (MODE !== "visible") return;
    tracks.clearLayers();
    if (op.zoom >= VISIBLE_MIN_ZOOM && inside.length <= VISIBLE_MAX_ACTIVITIES) {
      for (const i of inside) tracks.addLayer(trackLine(i));
    }
    await twoFrames();
  };
  const result = await runOps(data, buildOps(data, OPS), perform, bounds, afterOp);
  finish("leaflet", data, { renderer: "canvas 2d", decodeMs: data.decodeMs, firstDrawMs, ...result });
})();
