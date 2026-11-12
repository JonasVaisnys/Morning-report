#!/usr/bin/env python3
"""
Istorinių duomenų rinkimas kainos veiksnių analizei — MB Enerconsult
=====================================================================
Surenka valandinius duomenis (UTC) nuo PRADŽIA iki dabar:

  • day-ahead kainos: LT, LV, EE, FI, SE4, PL, DE (ENTSO-E) ir LT, LV, EE, FI (Elering);
  • vartojimas (faktas ir kitos paros prognozė): LT, LV, EE;
  • gamyba pagal tipą (faktas): LT, LV, EE; vėjas ir saulė: SE4, FI, PL, DE;
  • vėjo ir saulės kitos paros prognozė: LT, LV, EE, SE4, FI, PL, DE;
  • jungtys: fiziniai srautai, kitos paros prekybos grafikai, pralaidumai (NTC);
  • dujos (TTF, artimiausio mėnesio sandoris) ir CO2 (EUA aukcionai) – dienos eilutės.

Išvestis: data/istorija/*.csv.gz, dienos.csv ir rinkimo-žurnalas.txt

Naudojimas:
    python "scripts/istorijos rinkimas.py"               # nuo 2025-01-01
    python "scripts/istorijos rinkimas.py" 2025-10-01    # nuo nurodytos datos

Aplinkos kintamasis: ENTSOE_TOKEN
"""

import csv
import gzip
import io
import os
import re
import statistics
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests

ENTSOE_URL = "https://web-api.tp.entsoe.eu/api"
ELERING = "https://dashboard.elering.ee"
TOKEN = os.environ.get("ENTSOE_TOKEN", "")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "istorija")
UA = "Mozilla/5.0 (compatible; EnerconsultBot/1.0; +https://enerconsult.lt)"
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

EIC = {
    "LT": "10YLT-1001A0008Q", "LV": "10YLV-1001A00074", "EE": "10Y1001A1001A39I",
    "FI": "10YFI-1--------U", "SE4": "10Y1001A1001A47J", "PL": "10YPL-AREA-----S",
    "DE": "10Y1001A1001A82H",
}
BORDERS = [("LT", "SE4"), ("LT", "PL"), ("LT", "LV"), ("EE", "FI"), ("EE", "LV")]
RES_MIN = {"PT15M": 15, "PT30M": 30, "PT60M": 60}

_log_lock = threading.Lock()
LOG: list[str] = []


def log(msg: str) -> None:
    with _log_lock:
        LOG.append(msg)
        print(msg, file=sys.stderr, flush=True)


def clean(text: str) -> str:
    """Iš klaidos teksto pašalinti saugos raktą."""
    return re.sub(r"securityToken=[^&\s'\")]+", "securityToken=***", str(text))


# ─── ENTSO-E ─────────────────────────────────────────────────────────────────

def entsoe_get(params: dict, start: datetime, end: datetime) -> tuple[str | None, str]:
    """Grąžina (xml_tekstas | None, būsena). Būsena: ok / tuščia / klaida:..."""
    p = dict(params, periodStart=start.strftime("%Y%m%d%H%M"),
             periodEnd=end.strftime("%Y%m%d%H%M"), securityToken=TOKEN)
    last = ""
    for attempt in range(3):
        try:
            r = requests.get(ENTSOE_URL, params=p, timeout=240)
            if r.status_code == 200:
                if "Acknowledgement_MarketDocument" in r.text[:600]:
                    return None, "tuščia"
                return r.text, "ok"
            if r.status_code == 400:
                m = re.search(r"<text>(.*?)</text>", r.text, re.S)
                reason = (m.group(1) if m else r.text)[:160]
                if "No matching data" in reason:
                    return None, "tuščia"
                return None, f"klaida:400 {clean(reason)}"
            last = f"HTTP {r.status_code}"
            if r.status_code in (401, 403):
                return None, f"klaida:{last}"
        except requests.RequestException as exc:
            last = type(exc).__name__
        time.sleep(5 * (attempt + 1))
    return None, f"klaida:{last}"


def parse_doc(text: str) -> list[tuple[dict, datetime, datetime, str, list[tuple[int, float]]]]:
    root = ET.fromstring(text)
    ns = root.tag[1:root.tag.index("}")] if root.tag.startswith("{") else ""

    def q(name: str) -> str:
        return f"{{{ns}}}{name}" if ns else name

    out = []
    for ts in root.findall(q("TimeSeries")):
        meta: dict[str, str] = {}
        for child in ts:
            name = child.tag.split("}")[-1]
            if name == "Period":
                continue
            if name == "MktPSRType":
                psr = child.find(q("psrType"))
                if psr is not None and psr.text:
                    meta["psrType"] = psr.text
            elif len(child) == 0 and child.text:
                meta[name] = child.text
        for period in ts.findall(q("Period")):
            ti = period.find(q("timeInterval"))
            start = datetime.strptime(ti.find(q("start")).text, "%Y-%m-%dT%H:%MZ")
            end = datetime.strptime(ti.find(q("end")).text, "%Y-%m-%dT%H:%MZ")
            res = period.find(q("resolution")).text
            pts = []
            for pt in period.findall(q("Point")):
                pos = int(pt.find(q("position")).text)
                val = None
                for vt in ("quantity", "price.amount"):
                    el = pt.find(q(vt))
                    if el is not None and el.text:
                        val = float(el.text)
                        break
                if val is not None:
                    pts.append((pos, val))
            out.append((meta, start, end, res, pts))
    return out


def to_hourly(start: datetime, end: datetime, res: str, pts: list[tuple[int, float]],
              ffill: bool) -> dict[datetime, list[float]]:
    """Išskleidžia periodą į valandas. Kai kreivės tipas A03, praleistos pozicijos
    reiškia „reikšmė nepasikeitė" – jos užpildomos ankstesne reikšme."""
    step = RES_MIN.get(res)
    if not step:
        return {}
    n = int((end - start).total_seconds() // 60 // step)
    vals: list[float | None] = [None] * n
    for pos, v in pts:
        if 1 <= pos <= n:
            vals[pos - 1] = v
    if ffill:
        prev = None
        for i in range(n):
            if vals[i] is None:
                vals[i] = prev
            else:
                prev = vals[i]
    acc: dict[datetime, list[float]] = defaultdict(list)
    for i, v in enumerate(vals):
        if v is None:
            continue
        t = start + timedelta(minutes=i * step)
        acc[t.replace(minute=0)].append(v)
    return acc


def fetch_series(label: str, params: dict, chunks: list[tuple[datetime, datetime]],
                 split: str = "none") -> dict[str, dict[datetime, float]]:
    """Viena eilutė (arba kelios, kai split='psr') per visus laikotarpius."""
    # acc[sub_label][res][hour] -> list
    acc: dict[str, dict[str, dict[datetime, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list)))
    states = []
    todo = list(chunks)
    while todo:
        a, b = todo.pop(0)
        text, state = entsoe_get(params, a, b)
        if state.startswith("klaida") and (b - a).days > 45:
            # didelį laikotarpį bandome dalimis po mėnesį
            cur = a
            while cur < b:
                nxt = min(cur + timedelta(days=31), b)
                todo.append((cur, nxt))
                cur = nxt
            states.append(f"{a:%Y-%m}:dalinama")
            continue
        states.append(f"{a:%Y-%m}:{state}")
        if text is None:
            continue
        try:
            for meta, start, end, res, pts in parse_doc(text):
                seq = meta.get("classificationSequence_AttributeInstanceComponent.position")
                if seq not in (None, "1"):
                    continue
                sub = label
                if split == "psr":
                    sub = f"{label}_{meta.get('psrType', 'X')}"
                    if "outBiddingZone_Domain.mRID" in meta:
                        sub += "_vart"      # suvartojimas (pvz. HAE siurblių režimas)
                ffill = meta.get("curveType") == "A03"
                for hour, vals in to_hourly(start, end, res, pts, ffill).items():
                    acc[sub][res][hour].extend(vals)
        except Exception as exc:
            states.append(f"{a:%Y-%m}:parse {type(exc).__name__}")
    out: dict[str, dict[datetime, float]] = {}
    for sub, by_res in acc.items():
        merged: dict[datetime, float] = {}
        # pirmenybė smulkesnei rezoliucijai (15 min vidurkis per valandą)
        for res in ("PT60M", "PT30M", "PT15M"):
            for hour, vals in by_res.get(res, {}).items():
                merged[hour] = round(statistics.mean(vals), 3)
        out[sub] = merged
    n = sum(len(v) for v in out.values())
    res_seen = sorted({r for by_res in acc.values() for r in by_res})
    log(f"{label:<26} {n:>7} val.  {','.join(res_seen) or '-':<12} "
        f"eilučių {len(out):>2}  [{' '.join(states)}]")
    return out


def build_tasks() -> list[tuple[str, dict, str]]:
    t: list[tuple[str, dict, str]] = []
    for z in ("LT", "LV", "EE", "FI", "SE4", "PL", "DE"):
        t.append((f"kaina_{z}", {"documentType": "A44", "in_Domain": EIC[z],
                                 "out_Domain": EIC[z]}, "none"))
    for z in ("LT", "LV", "EE"):
        t.append((f"apkrova_{z}", {"documentType": "A65", "processType": "A16",
                                   "outBiddingZone_Domain": EIC[z]}, "none"))
        t.append((f"apkrova_progn_{z}", {"documentType": "A65", "processType": "A01",
                                         "outBiddingZone_Domain": EIC[z]}, "none"))
        t.append((f"gamyba_{z}", {"documentType": "A75", "processType": "A16",
                                  "in_Domain": EIC[z]}, "psr"))
    for z in ("SE4", "FI", "PL", "DE"):
        for psr in ("B19", "B18", "B16"):          # vėjas sausumoje, jūroje, saulė
            t.append((f"gamyba_{z}", {"documentType": "A75", "processType": "A16",
                                      "in_Domain": EIC[z], "psrType": psr}, "psr"))
    for z in ("LT", "LV", "EE", "SE4", "FI", "PL", "DE"):
        t.append((f"progn_{z}", {"documentType": "A69", "processType": "A01",
                                 "in_Domain": EIC[z]}, "psr"))
    for a, b in BORDERS:
        for frm, to in ((a, b), (b, a)):
            # ENTSO-E: in_Domain – kur energija įteka, out_Domain – iš kur išteka
            dom = {"in_Domain": EIC[to], "out_Domain": EIC[frm]}
            t.append((f"srautas_{frm}_{to}", dict(dom, documentType="A11"), "none"))
            t.append((f"prekyba_{frm}_{to}", dict(dom, documentType="A09",
                      **{"contract_MarketAgreement.Type": "A01"}), "none"))
            t.append((f"ntc_{frm}_{to}", dict(dom, documentType="A61",
                      **{"contract_MarketAgreement.Type": "A01"}), "none"))
            t.append((f"siulomas_{frm}_{to}", dict(dom, documentType="A31",
                      **{"Auction.Type": "A01", "contract_MarketAgreement.Type": "A01"}), "none"))
    return t


# ─── ELERING ─────────────────────────────────────────────────────────────────

def iso_z(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def elering_prices(start: datetime, end: datetime) -> dict[str, dict[datetime, float]]:
    acc: dict[str, dict[datetime, list[float]]] = defaultdict(lambda: defaultdict(list))
    cur, fails = start, 0
    while cur < end:
        nxt = min(cur + timedelta(days=31), end)
        url = (f"{ELERING}/api/nps/price?start={quote(iso_z(cur))}"
               f"&end={quote(iso_z(nxt - timedelta(seconds=1)))}")
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            for zone, rows in (r.json().get("data") or {}).items():
                for row in rows or []:
                    ts, price = row.get("timestamp"), row.get("price")
                    if ts is None or price is None:
                        continue
                    t = datetime(1970, 1, 1) + timedelta(seconds=int(ts))
                    acc[zone][t.replace(minute=0, second=0)].append(float(price))
        except Exception as exc:
            fails += 1
            log(f"  ⚠ Elering {cur:%Y-%m}: {type(exc).__name__}")
        cur = nxt
    out = {f"elering_{z.upper()}": {h: round(statistics.mean(v), 3) for h, v in hours.items()}
           for z, hours in acc.items()}
    log(f"elering kainos: " + ", ".join(f"{k} {len(v)} val." for k, v in sorted(out.items()))
        + f"; nepavykusių užklausų {fails}")
    return out


def elering_probe(now: datetime) -> None:
    """Pažiūrėti, kokius dar duomenis duoda Elering (pralaidumai, dujos) – tik žurnalui."""
    a, b = iso_z(now - timedelta(days=3)), iso_z(now - timedelta(days=2))
    rng = f"?start={quote(a)}&end={quote(b)}"
    for path in ("/api/transmission/cross-border-capacity" + rng,
                 "/api/transmission/cross-border-capacity/day-ahead" + rng,
                 "/api/transmission/cross-border-planned-trade" + rng,
                 "/api/transmission/cross-border" + rng,
                 "/api/system/with-plan" + rng,
                 "/api/gas-trade" + rng,
                 "/api/gas-balance/price" + rng,
                 "/api/balance" + rng,
                 "/v3/api-docs", "/v2/api-docs", "/api-docs", "/assets/api-doc.html"):
        try:
            r = requests.get(ELERING + path, headers={"User-Agent": UA}, timeout=40)
            body = re.sub(r"\s+", " ", r.text)[:700]
            log(f"ZONDAS {path.split('?')[0]} → {r.status_code} ({len(r.text)} simb.): {body}")
            if r.status_code == 200 and "api-doc" in path:
                paths = sorted(set(re.findall(r"/api/[a-zA-Z0-9/\-{}_]+", r.text)))
                log("ZONDAS keliai: " + " ".join(paths)[:6000])
        except Exception as exc:
            log(f"ZONDAS {path.split('?')[0]} → {type(exc).__name__}")


# ─── DUJOS IR CO2 ────────────────────────────────────────────────────────────

def yahoo_daily(symbol: str, rng: str = "3y") -> dict[date, float]:
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/"
                             f"{quote(symbol)}?range={rng}&interval=1d",
                             headers={"User-Agent": UA_BROWSER, "Accept": "*/*"}, timeout=40)
            if r.status_code != 200:
                continue
            res = r.json()["chart"]["result"][0]
            closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
            out = {}
            for ts, c in zip(res.get("timestamp") or [], closes):
                if c is not None:
                    out[datetime.fromtimestamp(ts, timezone.utc).date()] = float(c)
            if out:
                return out
        except Exception as exc:
            log(f"  ⚠ Yahoo {symbol} ({host}): {type(exc).__name__}")
    return {}


def eex_eua(years: list[int]) -> dict[date, float]:
    try:
        import warnings
        import openpyxl
        warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
    except ImportError:
        log("  ⚠ Nėra openpyxl – EUA praleidžiama")
        return {}
    pts: dict[date, list[float]] = defaultdict(list)
    for y in years:
        url = ("https://public.eex-group.com/eex/eua-auction-report/"
               f"emission-spot-primary-market-auction-report-{y}-data.xlsx")
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            if r.status_code != 200:
                log(f"  ⚠ EEX {y}: HTTP {r.status_code}")
                continue
            wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
            rows = list(wb.worksheets[0].iter_rows(values_only=True))
            hdr_i = next(i for i, row in enumerate(rows)
                         if row and "Date" in row
                         and any(isinstance(c, str) and c.startswith("Auction Price") for c in row))
            hdr = rows[hdr_i]
            i_date = hdr.index("Date")
            i_price = next(j for j, c in enumerate(hdr)
                           if isinstance(c, str) and c.startswith("Auction Price"))
            i_status = hdr.index("Status") if "Status" in hdr else None
            for row in rows[hdr_i + 1:]:
                d, p = row[i_date], row[i_price]
                if not isinstance(d, datetime) or not isinstance(p, (int, float)):
                    continue
                if i_status is not None and str(row[i_status]).lower() != "successful":
                    continue
                pts[d.date()].append(float(p))
        except Exception as exc:
            log(f"  ⚠ EEX {y}: {type(exc).__name__}")
    return {d: round(statistics.mean(v), 2) for d, v in pts.items()}


# ─── IŠVESTIS ────────────────────────────────────────────────────────────────

def write_wide(name: str, series: dict[str, dict[datetime, float]]) -> None:
    series = {k: v for k, v in series.items() if v}
    if not series:
        log(f"{name}: nėra duomenų")
        return
    cols = sorted(series)
    hours = sorted({h for v in series.values() for h in v})
    path = os.path.join(OUT_DIR, name)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["utc"] + cols)
        cur, last = hours[0], hours[-1]
        while cur <= last:
            w.writerow([cur.strftime("%Y-%m-%dT%H:%M")]
                       + [series[c].get(cur, "") for c in cols])
            cur += timedelta(hours=1)
    log(f"{name}: {len(cols)} stulp., {hours[0]:%Y-%m-%d %H:%M} – {hours[-1]:%Y-%m-%d %H:%M} UTC, "
        f"{os.path.getsize(path) // 1024} kB")


# ─── ILGOJI ISTORIJA SEZONINĖMS NORMOMS ──────────────────────────────────────

def build_tasks_long() -> list[tuple[str, dict, str]]:
    """Tik LT eilutės, kurių reikia sezoninėms normoms (vartojimas, gamyba, prognozės, kaina)."""
    lt = EIC["LT"]
    return [
        ("kaina_LT", {"documentType": "A44", "in_Domain": lt, "out_Domain": lt}, "none"),
        ("apkrova_LT", {"documentType": "A65", "processType": "A16", "outBiddingZone_Domain": lt}, "none"),
        ("apkrova_progn_LT", {"documentType": "A65", "processType": "A01", "outBiddingZone_Domain": lt}, "none"),
        ("gamyba_LT", {"documentType": "A75", "processType": "A16", "in_Domain": lt}, "psr"),
        ("progn_LT", {"documentType": "A69", "processType": "A01", "in_Domain": lt}, "psr"),
    ]


def installed_capacity(years: list[int]) -> list[tuple[int, str, float]]:
    """Įrengtoji galia pagal tipą metų pradžioje (ENTSO-E A68)."""
    out = []
    for y in years:
        text, state = entsoe_get({"documentType": "A68", "processType": "A33", "in_Domain": EIC["LT"]},
                                 datetime(y - 1, 12, 31, 23), datetime(y, 12, 31, 23))
        if text is None:
            log(f"galia {y}: {state}")
            continue
        try:
            root = ET.fromstring(text)
            ns = root.tag[1:root.tag.index("}")]
            for ts in root.findall(f"{{{ns}}}TimeSeries"):
                psr = ts.find(f"{{{ns}}}MktPSRType/{{{ns}}}psrType")
                qty = ts.find(f"{{{ns}}}Period/{{{ns}}}Point/{{{ns}}}quantity")
                if psr is not None and qty is not None:
                    out.append((y, psr.text, float(qty.text)))
        except Exception as exc:
            log(f"galia {y}: parse {type(exc).__name__}")
    log(f"įrengtoji galia: {len(out)} įrašų")
    return out


def capacity_probe(now: datetime) -> None:
    """Kur dar skelbiami LT–PL ir LT–LV pralaidumai (ENTSO-E A31 nutrūko 2026-10-14) – tik žurnalui."""
    a, b = (now - timedelta(days=3)).replace(hour=0), (now - timedelta(days=1)).replace(hour=0)
    dom = {"in_Domain": EIC["LT"], "out_Domain": EIC["PL"]}
    combos = [
        ("A31 impl. dienos", dict(dom, documentType="A31", **{"Auction.Type": "A01", "contract_MarketAgreement.Type": "A01"})),
        ("A31 impl. dienos eigos", dict(dom, documentType="A31", **{"Auction.Type": "A01", "contract_MarketAgreement.Type": "A07"})),
        ("A31 ekspl. dienos", dict(dom, documentType="A31", **{"Auction.Type": "A02", "contract_MarketAgreement.Type": "A01"})),
        ("A61 dienos", dict(dom, documentType="A61", **{"contract_MarketAgreement.Type": "A01"})),
        ("A61 dienos eigos", dict(dom, documentType="A61", **{"contract_MarketAgreement.Type": "A07"})),
        ("A26 dienos", dict(dom, documentType="A26", businessType="A29", **{"contract_MarketAgreement.Type": "A01"})),
    ]
    for name, prm in combos:
        text, state = entsoe_get(prm, a, b)
        n = len(parse_doc(text)) if text else 0
        log(f"ZONDAS ENTSO-E PL→LT {name}: {state}, eilučių {n}")
    day = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    urls = [
        f"https://dataportal-api.nordpoolgroup.com/api/DayAheadCapacities?date={day}&market=DayAhead&deliveryArea=LT",
        f"https://dataportal-api.nordpoolgroup.com/api/DayAheadCapacities?date={day}&deliveryArea=LT",
        f"https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices?date={day}&market=DayAhead&deliveryArea=LT&currency=EUR",
        f"https://api-baltic.transparency-dashboard.eu/api/v1/export?id=ntc_day_ahead&start_date={day}T00:00&end_date={day}T23:00&output_time_zone=UTC&output_format=json",
        f"https://api-baltic.transparency-dashboard.eu/api/v1/export?id=cross_border_capacities&start_date={day}T00:00&end_date={day}T23:00&output_time_zone=UTC&output_format=json",
        f"{ELERING}/api/transmission/cross-border-capacity?start={quote(iso_z(a))}&end={quote(iso_z(b))}",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers={"User-Agent": UA_BROWSER, "Accept": "application/json"}, timeout=40)
            body = re.sub(r"\s+", " ", r.text)[:500]
            log(f"ZONDAS {url.split('?')[0]} → {r.status_code} ({len(r.text)} simb.): {body}")
            if r.status_code == 200 and "elering" in url:
                try:
                    log("ZONDAS elering raktai: " + ", ".join(sorted((r.json().get("data") or {}).keys())))
                except Exception:
                    pass
        except Exception as exc:
            log(f"ZONDAS {url.split('?')[0]} → {type(exc).__name__}")


def main_long() -> None:
    """Ilgoji LT istorija 2022-01-01…2025-01-01 → ilgoji.csv.gz, galios.csv."""
    first, last = datetime(2022, 1, 1), datetime(2025, 1, 1)
    now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    os.makedirs(OUT_DIR, exist_ok=True)
    chunks, cur = [], first
    while cur < last:
        nxt = datetime(cur.year + (cur.month > 6), 7 if cur.month <= 6 else 1, 1)
        chunks.append((cur, min(nxt, last)))
        cur = nxt
    log(f"Laikotarpis (ilgoji): {first:%Y-%m-%d} – {last:%Y-%m-%d} UTC, dalių {len(chunks)}; paleista {now:%Y-%m-%d %H:%M} UTC")
    results: dict[str, dict[datetime, float]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fetch_series, lbl, prm, chunks, split): lbl for lbl, prm, split in build_tasks_long()}
        for fut in as_completed(futs):
            try:
                for k, v in fut.result().items():
                    results.setdefault(k, {}).update(v)
            except Exception as exc:
                log(f"  ⚠ {futs[fut]}: {type(exc).__name__}: {clean(exc)[:200]}")
    write_wide("ilgoji.csv.gz", results)
    caps = installed_capacity(list(range(2022, now.year + 1)))
    with open(os.path.join(OUT_DIR, "galios.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metai", "psr", "mw"])
        w.writerows(caps)
    capacity_probe(now)
    with open(os.path.join(OUT_DIR, "rinkimo-žurnalas-ilga.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG) + "\n")


def main_nordpool(days: int = 36) -> None:
    """Day-ahead pralaidumai LT sienoms iš Nord Pool duomenų portalo (palyginimui su ENTSO-E A31)."""
    os.makedirs(OUT_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).date()
    rows, sample = [], ""
    for i in range(days, -2, -1):
        day = today - timedelta(days=i)
        url = f"https://dataportal-api.nordpoolgroup.com/api/DayAheadCapacities?date={day}&market=DayAhead&deliveryArea=LT"
        try:
            r = requests.get(url, headers={"User-Agent": UA_BROWSER, "Accept": "application/json"}, timeout=40)
            if r.status_code != 200 or not r.text.strip():
                log(f"nordpool {day}: HTTP {r.status_code}")
                continue
            js = r.json()
            caps = js.get("capacities") or []
            if caps and not sample:
                sample = re.sub(r"\s+", " ", str(caps[0]))[:900]
            for c in caps:
                start = c.get("deliveryStart")
                for direction, key in (("importas", "importsByConnection"), ("eksportas", "exportsByConnection")):
                    for conn, mw in (c.get(key) or {}).items():
                        rows.append((start, direction, conn, mw))
        except Exception as exc:
            log(f"nordpool {day}: {type(exc).__name__}")
        time.sleep(0.4)
    with open(os.path.join(OUT_DIR, "nordpool-pralaidumai.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["utc_pradžia", "kryptis", "jungtis", "mw"])
        w.writerows(rows)
    log(f"nordpool pralaidumai: {len(rows)} eilučių; pavyzdys: {sample}")
    with open(os.path.join(OUT_DIR, "rinkimo-žurnalas-nordpool.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG) + "\n")


def main_neighbours() -> None:
    """Kaimynų kainos 2022–2024 ir ilgesnė dujų bei CO2 istorija – kaimynų kainų sezoninėms normoms."""
    first, last = datetime(2022, 1, 1), datetime(2025, 1, 1)
    os.makedirs(OUT_DIR, exist_ok=True)
    chunks, cur = [], first
    while cur < last:
        nxt = datetime(cur.year + (cur.month > 6), 7 if cur.month <= 6 else 1, 1)
        chunks.append((cur, min(nxt, last)))
        cur = nxt
    tasks = [(f"kaina_{z}", {"documentType": "A44", "in_Domain": EIC[z], "out_Domain": EIC[z]}, "none")
             for z in ("SE4", "PL", "FI", "LV", "EE")]
    results: dict[str, dict[datetime, float]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fetch_series, lbl, prm, chunks, split): lbl for lbl, prm, split in tasks}
        for fut in as_completed(futs):
            try:
                for k, v in fut.result().items():
                    results.setdefault(k, {}).update(v)
            except Exception as exc:
                log(f"  ⚠ {futs[fut]}: {type(exc).__name__}: {clean(exc)[:200]}")
    write_wide("ilgoji-kaimynai.csv.gz", results)
    ttf = yahoo_daily("TTF=F", "5y")
    eua = eex_eua([2022, 2023, 2024, 2025, 2026])
    log(f"TTF {len(ttf)} d. ({min(ttf, default='-')} – {max(ttf, default='-')}), "
        f"EUA {len(eua)} d. ({min(eua, default='-')} – {max(eua, default='-')})")
    with open(os.path.join(OUT_DIR, "dienos-ilga.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["data", "ttf_eur_mwh", "eua_eur_t"])
        for d in sorted(set(ttf) | set(eua)):
            w.writerow([d.isoformat(), ttf.get(d, ""), eua.get(d, "")])
    with open(os.path.join(OUT_DIR, "rinkimo-žurnalas-kaimynai.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG) + "\n")


def main_outages() -> None:
    """Jungčių neprieinamumo pranešimai (ENTSO-E A78) LT sienoms: pastarosios 110 d. ir 14 d. į priekį."""
    import zipfile
    os.makedirs(OUT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    a, b = now - timedelta(days=110), now + timedelta(days=14)
    rows = []
    for x, y in (("LT", "SE4"), ("LT", "PL"), ("LT", "LV")):
        for frm, to in ((x, y), (y, x)):
            prm = {"documentType": "A78", "in_Domain": EIC[to], "out_Domain": EIC[frm],
                   "periodStart": a.strftime("%Y%m%d%H%M"), "periodEnd": b.strftime("%Y%m%d%H%M"),
                   "securityToken": TOKEN}
            try:
                r = requests.get(ENTSOE_URL, params=prm, timeout=180)
            except requests.RequestException as exc:
                log(f"A78 {frm}→{to}: {type(exc).__name__}")
                continue
            if r.status_code != 200 or r.content[:2] != b"PK":
                m = re.search(r"<text>(.*?)</text>", r.text, re.S)
                log(f"A78 {frm}→{to}: HTTP {r.status_code} {clean((m.group(1) if m else r.text)[:160])}")
                continue
            z = zipfile.ZipFile(io.BytesIO(r.content))
            n = 0
            for name in z.namelist():
                try:
                    root = ET.fromstring(z.read(name))
                except ET.ParseError:
                    continue
                vals: dict[str, list[str]] = defaultdict(list)
                parent = {c: p_ for p_ in root.iter() for c in p_}
                for el in root.iter():
                    tag = el.tag.split("}")[-1]
                    if el.text and el.text.strip():
                        ptag = parent[el].tag.split("}")[-1] if el in parent else ""
                        vals[f"{ptag}/{tag}"].append(el.text.strip())
                def one(key: str) -> str:
                    v = vals.get(key) or [""]
                    return v[0]
                qty = [float(q) for q in vals.get("Point/quantity", []) if re.fullmatch(r"-?[0-9.]+", q)]
                rows.append([f"{frm}→{to}", one("timeInterval/start"), one("timeInterval/end"),
                             one("TimeSeries/businessType"), one("docStatus/value"),
                             "; ".join(sorted(set(vals.get("Asset_RegisteredResource/name", []))))[:120],
                             min(qty) if qty else "", max(qty) if qty else "",
                             one("Unavailability_MarketDocument/revisionNumber"),
                             one("Unavailability_MarketDocument/createdDateTime"),
                             " | ".join(vals.get("Reason/text", []))[:160], one("Unavailability_MarketDocument/mRID")])
                n += 1
            log(f"A78 {frm}→{to}: dokumentų {n}")
    with open(os.path.join(OUT_DIR, "atjungimai.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kryptis", "pradžia", "pabaiga", "tipas", "būsena", "įrenginys", "galia_min", "galia_max",
                    "versija", "sukurta", "priežastis", "id"])
        w.writerows(sorted(rows, key=lambda r_: (r_[1], r_[0])))
    with open(os.path.join(OUT_DIR, "rinkimo-žurnalas-atjungimai.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG) + "\n")


def main() -> None:
    if not TOKEN:
        print("Klaida: nenustatytas ENTSOE_TOKEN.", file=sys.stderr)
        sys.exit(1)
    if "--ilga" in sys.argv[1:]:
        main_long()
        return
    if "--nordpool" in sys.argv[1:]:
        main_nordpool()
        return
    if "--kaimynai" in sys.argv[1:]:
        main_neighbours()
        return
    if "--atjungimai" in sys.argv[1:]:
        main_outages()
        return
    first = datetime(2025, 1, 1)
    for arg in sys.argv[1:]:
        first = datetime.strptime(arg, "%Y-%m-%d")
    now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    last = (now + timedelta(days=2)).replace(hour=0)       # rytojaus kainos, jei jau paskelbtos
    os.makedirs(OUT_DIR, exist_ok=True)

    # pusmečio dalys
    chunks, cur = [], first
    while cur < last:
        nxt = datetime(cur.year + (cur.month > 6), 7 if cur.month <= 6 else 1, 1)
        chunks.append((cur, min(nxt, last)))
        cur = nxt
    log(f"Laikotarpis: {first:%Y-%m-%d} – {last:%Y-%m-%d} UTC, dalių {len(chunks)}; "
        f"paleista {now:%Y-%m-%d %H:%M} UTC")

    results: dict[str, dict[datetime, float]] = {}
    tasks = build_tasks()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fetch_series, lbl, prm, chunks, split): lbl
                for lbl, prm, split in tasks}
        for fut in as_completed(futs):
            try:
                for k, v in fut.result().items():
                    results.setdefault(k, {}).update(v)
            except Exception as exc:
                log(f"  ⚠ {futs[fut]}: {type(exc).__name__}: {clean(exc)[:200]}")

    results.update(elering_prices(first, last))
    elering_probe(now)

    def pick(*prefixes: str) -> dict[str, dict[datetime, float]]:
        return {k: v for k, v in results.items() if k.startswith(prefixes)}

    write_wide("kainos.csv.gz", pick("kaina_", "elering_"))
    write_wide("apkrova.csv.gz", pick("apkrova_"))
    write_wide("gamyba.csv.gz", pick("gamyba_", "progn_"))
    write_wide("jungtys.csv.gz", pick("srautas_", "prekyba_", "ntc_", "siulomas_"))

    ttf = yahoo_daily("TTF=F")
    eua = eex_eua(list(range(first.year, now.year + 1)))
    log(f"TTF {len(ttf)} d. ({min(ttf, default='-')} – {max(ttf, default='-')}), "
        f"EUA {len(eua)} d. ({min(eua, default='-')} – {max(eua, default='-')})")
    with open(os.path.join(OUT_DIR, "dienos.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["data", "ttf_eur_mwh", "eua_eur_t"])
        for d in sorted(set(ttf) | set(eua)):
            if d >= first.date() - timedelta(days=10):
                w.writerow([d.isoformat(), ttf.get(d, ""), eua.get(d, "")])

    with open(os.path.join(OUT_DIR, "rinkimo-žurnalas.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(l for l in LOG if not l.startswith(("ZONDAS", "Laikotarpis")))
                          ) + "\n\n")
        f.write("\n".join(l for l in LOG if l.startswith(("ZONDAS", "Laikotarpis"))) + "\n")


if __name__ == "__main__":
    main()
