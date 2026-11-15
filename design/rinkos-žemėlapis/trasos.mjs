import { readFileSync, writeFileSync } from "fs";
import { feature } from "topojson-client";
import { geoMercator, geoPath, geoContains } from "d3-geo";
const W = 760, H = 570;
const topo = JSON.parse(readFileSync("node_modules/world-atlas/countries-50m.json", "utf8"));
const fc = feature(topo, topo.objects.countries);
const bbox = { type: "Feature", geometry: { type: "MultiPoint", coordinates: [[13.7, 53.15], [13.7, 58.3], [28.45, 58.3], [28.45, 53.15], [21.0, 53.15], [21.0, 58.3]] } };
const proj = geoMercator().fitExtent([[0, 0], [W, H]], bbox);
const LT = fc.features.find(f => f.id === "440"), SE = fc.features.find(f => f.id === "752"), LV = fc.features.find(f => f.id === "428"), PL = fc.features.find(f => f.id === "616");
const P = ([lon, lat]) => proj([lon, lat]).map(n => Math.round(n * 10) / 10);
const routes = {
  nordbalt: [[21.20, 55.70], [20.60, 55.72], [19.80, 55.77], [18.80, 55.84], [17.80, 55.92], [17.00, 55.99], [16.55, 56.05], [16.28, 56.15], [16.12, 56.30], [16.06, 56.40], [15.98, 56.58], [15.91, 56.74]],
  litpol: [[24.05, 54.40], [23.80, 54.37], [23.58, 54.30], [23.40, 54.27], [23.18, 54.24], [22.95, 54.18], [22.68, 54.11], [22.50, 54.02], [22.38, 53.90], [22.32, 53.80]],
};
const pts = { klaipeda: [21.20, 55.70], nybro: [15.91, 56.74], alytus: [24.05, 54.40], elk: [22.32, 53.80], oland_s: [16.40, 56.196], bergkvara: [16.09, 56.39],
  darbenai: [21.27, 56.02], grobina: [21.16, 56.53], musa: [23.60, 56.24], viskali: [23.75, 56.60], panevezys: [24.36, 55.73], aizkraukle: [25.25, 56.60], ignalina: [26.56, 55.60], liksna: [26.38, 55.99],
  riga: [24.10, 56.95], hubA: [24.30, 56.85], hubB: [24.60, 56.80], hubC: [24.00, 56.75] };
const out = { routes: {}, pts: {}, geoIn: {} };
for (const [k, v] of Object.entries(routes)) { out.routes[k] = v.map(P); out.geoIn[k] = v.map(c => (geoContains(LT, c) ? "LT" : geoContains(SE, c) ? "SE" : geoContains(PL, c) ? "PL" : geoContains(LV, c) ? "LV" : "-")).join(" "); }
for (const [k, v] of Object.entries(pts)) { out.pts[k] = P(v); out.geoIn[k] = geoContains(LT, v) ? "LT" : geoContains(SE, v) ? "SE" : geoContains(PL, v) ? "PL" : geoContains(LV, v) ? "LV" : "-"; }
// Lietuvos kontūras pikseliais (didžiausias žiedas)
const ring = LT.geometry.type === "Polygon" ? LT.geometry.coordinates[0] : LT.geometry.coordinates.map(p => p[0]).sort((a, b) => b.length - a.length)[0];
out.ltRing = ring.map(P);
writeFileSync("routes.json", JSON.stringify(out));
console.log(JSON.stringify({ routes: out.routes, pts: out.pts, geoIn: out.geoIn, ltRingN: out.ltRing.length }));
