// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 MR
// MapLibre GL JS prototype: GeoJSON clusters (supercluster in the worker), neutral background.
"use strict";

(async function main() {
  const data = loadData();
  maplibregl.setWorkerUrl("ponos://app/vendor/maplibre-gl/maplibre-gl-csp-worker.js");
  const canvas = document.createElement("canvas");
  const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
  const debug = gl && gl.getExtension("WEBGL_debug_renderer_info");
  const renderer = debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : "n/d";

  const t0 = performance.now();
  const map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {},
      layers: [{ id: "bg", type: "background", paint: { "background-color": "#e6e8eb" } }],
    },
    center: [11, 44],
    zoom: 5,
    fadeDuration: 0,
    attributionControl: false,
    canvasContextAttributes: { antialias: true },
  });
  const idle = () => new Promise((resolve) => map.once("idle", resolve));
  await new Promise((resolve) => map.once("load", resolve));

  const starts = {
    type: "FeatureCollection",
    features: Array.from({ length: data.n }, (_, i) => ({
      type: "Feature",
      properties: { i },
      geometry: { type: "Point", coordinates: [data.starts[2 * i + 1], data.starts[2 * i]] },
    })),
  };
  map.addSource("starts", { type: "geojson", data: starts, cluster: true, clusterRadius: 50, clusterMaxZoom: 14 });
  map.addLayer({
    id: "clusters",
    type: "circle",
    source: "starts",
    filter: ["has", "point_count"],
    paint: {
      "circle-color": "#3b6ea5",
      "circle-opacity": 0.85,
      "circle-radius": ["step", ["get", "point_count"], 14, 10, 18, 100, 24, 1000, 30],
      "circle-stroke-width": 2,
      "circle-stroke-color": "#ffffff",
    },
  });
  map.addLayer({
    id: "single",
    type: "circle",
    source: "starts",
    filter: ["!", ["has", "point_count"]],
    paint: { "circle-color": "#3b6ea5", "circle-radius": 5, "circle-stroke-width": 1.5, "circle-stroke-color": "#ffffff" },
  });

  const trackFeature = (i) => {
    const line = [];
    for (let k = data.offsets[i]; k < data.offsets[i + 1]; k++) line.push([data.coords[2 * k + 1], data.coords[2 * k]]);
    return { type: "Feature", properties: { i }, geometry: { type: "LineString", coordinates: line } };
  };
  const emptyTracks = { type: "FeatureCollection", features: [] };
  if (MODE === "tracks") {
    map.addSource("tracks", { type: "geojson", data: { type: "FeatureCollection", features: Array.from({ length: data.n }, (_, i) => trackFeature(i)) } });
  } else {
    map.addSource("tracks", { type: "geojson", data: emptyTracks });
  }
  if (MODE !== "starts") {
    map.addLayer({
      id: "tracks",
      type: "line",
      source: "tracks",
      paint: { "line-color": "#c45a1a", "line-width": 2, "line-opacity": 0.7 },
    }, "clusters");
  }
  await idle();
  const firstDrawMs = performance.now() - t0;

  const bounds = () => {
    const b = map.getBounds();
    return [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()];
  };
  const perform = (op) => {
    const done = idle();
    map.jumpTo({ center: [op.lon, op.lat], zoom: op.zoom });
    return done;
  };
  const afterOp = async (op, inside) => {
    if (MODE !== "visible") return;
    const show = op.zoom >= VISIBLE_MIN_ZOOM && inside.length <= VISIBLE_MAX_ACTIVITIES;
    const done = idle();
    map.getSource("tracks").setData({ type: "FeatureCollection", features: show ? inside.map(trackFeature) : [] });
    await done;
  };
  const result = await runOps(data, buildOps(data, OPS), perform, bounds, afterOp);
  finish("maplibre", data, { renderer, decodeMs: data.decodeMs, firstDrawMs, ...result });
})();
