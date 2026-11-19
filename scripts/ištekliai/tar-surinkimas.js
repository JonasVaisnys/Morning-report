// Rytinis TAR surinkimas per Claude in Chrome (lietuviškas IP – data.gov.lt užkarda nepraleidžia GitHub serverių).
// Vykdoma kortelėje, atidarytoje https://get.data.gov.lt/ (tas pats šaltinis, todėl CORS netrukdo).
// Prieš šį tekstą įterpiama: const R = <scripts/ištekliai/teisės-aktų-atranka.json>; const NUO = "YYYY-MM-DD"; const IKI = "YYYY-MM-DD";
// Grąžina JSON eilutę, kurią reikia be pakeitimų įrašyti į data/šaltiniai/tar.json.
const D = "/datasets/gov/lrsk/teises_aktai/Dokumentas";
const F = "select(_id,nuoroda,pavadinimas,rusis,priemusi_inst,parengusi_inst,priimtas,paskelbta_tar,isigalioja,tar_kodas,atv_dok_nr,galioj_busena)";
const url = `${D}?${F}&paskelbta_tar>="${NUO}"&paskelbta_tar<"${IKI}"&limit(5000)`;
const resp = await fetch(url, { headers: { Accept: "application/json" } });
if (!resp.ok) throw new Error(`data.gov.lt HTTP ${resp.status}`);
const rows = (await resp.json())._data || [];
const low = s => (s || "").toLowerCase();
function sritis(d) {
  const t = low(d.pavadinimas), pi = low(d.priemusi_inst) + " " + low(d.parengusi_inst);
  if (R["neįtraukti_pavadinime"].some(k => t.includes(k))) return null;
  if (R["neįtraukti_institucijos"].some(k => pi.includes(k))) return null;
  if (pi.includes("valstybinė energetikos reguliavimo taryba") && R["vert_ne_energetika"].some(k => t.includes(k))) return null;
  for (const [s, kws] of R["sritys"]) if (kws.some(k => t.includes(k))) return s;
  return R["visada_institucijos"].some(k => pi.includes(k)) ? "Kita energetika" : null;
}
const order = R["sritys"].map(x => x[0]);
const aktai = rows
  .map(d => ({ ...d, sritis: sritis(d) }))
  .filter(d => d.sritis)
  .sort((a, b) => (a.paskelbta_tar || "").localeCompare(b.paskelbta_tar || "") || order.indexOf(a.sritis) - order.indexOf(b.sritis))
  .map(d => ({
    pavadinimas: d.pavadinimas, sritis: d.sritis, rusis: d.rusis, priemusi_inst: d.priemusi_inst,
    priimtas: d.priimtas, paskelbta_tar: d.paskelbta_tar, isigalioja: d.isigalioja,
    tar_kodas: d.tar_kodas, atv_dok_nr: d.atv_dok_nr, nuoroda: d.nuoroda,
  }));
const perDay = {};
for (const d of rows) perDay[d.paskelbta_tar] = (perDay[d.paskelbta_tar] || 0) + 1;
JSON.stringify({ "šaltinis": "data.gov.lt TAR", nuo: NUO, iki: IKI, surinkta: new Date().toISOString(), viso: rows.length, pagal_dieną: perDay, aktai }, null, 1);
