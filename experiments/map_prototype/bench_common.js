// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 MR
// Shared part of the two prototypes: synthetic data, operation sequence, statistics, report.
"use strict";

const PARAMS = new URLSearchParams(location.search);
// starts = clustered starts only; tracks = starts + all tracks; visible = starts + the tracks of
// the visible activities only (zoom >= 11 and at most 300 activities), the most realistic design.
const MODE = PARAMS.get("mode") || "starts";
const OPS = Number(PARAMS.get("ops") || 40);
const VISIBLE_MIN_ZOOM = 11;
const VISIBLE_MAX_ACTIVITIES = 300;
const OP_TIMEOUT_MS = 20000;

function report(kind, payload) {
  console.log("PONOS_" + kind + " " + JSON.stringify(payload));
}
function log(text) {
  console.log("PONOS_LOG " + text);
}
window.addEventListener("error", (e) => report("ERROR", { message: String(e.message), line: e.lineno }));
window.addEventListener("unhandledrejection", (e) => report("ERROR", { message: String(e.reason) }));

function base64ToBuffer(text) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function loadData() {
  const t0 = performance.now();
  const d = window.PONOS_DATA;
  const data = {
    n: d.n,
    starts: new Float32Array(base64ToBuffer(d.starts)), // lat, lon interleaved
    offsets: new Int32Array(base64ToBuffer(d.offsets)),
    coords: new Float32Array(base64ToBuffer(d.coords)), // lat, lon interleaved
  };
  data.decodeMs = performance.now() - t0;
  return data;
}

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Deterministic sequence: jumps to a start at different zooms, and small pans.
function buildOps(data, count) {
  const random = mulberry32(12345);
  const zooms = [3, 5, 7, 9, 11, 13];
  const ops = [];
  let center = [data.starts[0], data.starts[1]];
  let zoom = 5;
  for (let k = 0; k < count; k++) {
    if (k % 2 === 0) {
      const i = Math.floor(random() * data.n);
      center = [data.starts[2 * i], data.starts[2 * i + 1]];
      zoom = zooms[Math.floor(random() * zooms.length)];
      ops.push({ kind: "jump", lat: center[0], lon: center[1], zoom });
    } else {
      const span = (360 * 1000) / (256 * Math.pow(2, zoom)); // degrees of longitude in 1000 px
      const angle = random() * 2 * Math.PI;
      center = [
        Math.max(-80, Math.min(80, center[0] + 0.3 * span * 0.6 * Math.sin(angle))),
        center[1] + 0.3 * span * Math.cos(angle),
      ];
      ops.push({ kind: "pan", lat: center[0], lon: center[1], zoom });
    }
  }
  return ops;
}

// List on the right: activities starting in the area (scan of 50,000 starts).
function visibleIndices(data, south, west, north, east) {
  const found = [];
  const s = data.starts;
  for (let i = 0; i < data.n; i++) {
    const lat = s[2 * i];
    const lon = s[2 * i + 1];
    if (lat >= south && lat <= north && lon >= west && lon <= east) found.push(i);
  }
  return found;
}

function percentile(sorted, p) {
  if (sorted.length === 0) return null;
  const rank = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.max(0, rank)];
}

function summarize(values) {
  const sorted = [...values].sort((a, b) => a - b);
  return {
    n: sorted.length,
    p50: percentile(sorted, 50),
    p95: percentile(sorted, 95),
    max: sorted.length ? sorted[sorted.length - 1] : null,
  };
}

// `perform(op)` brings the map to the requested state and returns a promise resolved when the
// drawing is done; `bounds()` returns [south, west, north, east].
async function runOps(data, ops, perform, bounds, afterOp) {
  const jump = [];
  const pan = [];
  const list = [];
  let timeouts = 0;
  for (const op of ops) {
    const t0 = performance.now();
    const outcome = await Promise.race([
      perform(op).then(() => "ok"),
      new Promise((resolve) => setTimeout(() => resolve("timeout"), OP_TIMEOUT_MS)),
    ]);
    const renderMs = performance.now() - t0;
    if (outcome === "timeout") timeouts++;
    const t1 = performance.now();
    const [south, west, north, east] = bounds();
    const inside = visibleIndices(data, south, west, north, east);
    const listMs = performance.now() - t1;
    list.push(listMs);
    (op.kind === "jump" ? jump : pan).push(renderMs + listMs);
    if (afterOp) await afterOp(op, inside);
  }
  return { jump: summarize(jump), pan: summarize(pan), list: summarize(list), timeouts };
}

function twoFrames() {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

function finish(lib, data, result) {
  const heap = performance.memory ? performance.memory.usedJSHeapSize : null;
  report("RESULT", { lib, mode: MODE, activities: data.n, points: data.coords.length / 2, ...result, jsHeapBytes: heap });
}
