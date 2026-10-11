#!/usr/bin/env python3
"""
Pasaulio energetikos rinkų brifingas — MB Enerconsult
======================================================
Vertina grandinę nafta → dujos → elektros kaina Lietuvoje.

  1. Rodikliai: Brent, TTF, CO2 (EUA), EUR/USD, ES ir Latvijos dujų saugyklos,
     LT elektros kaina (Nord Pool duomenys per Elering).
  2. Skaičiavimai:
       • dujinės elektrinės ribiniai kaštai (SRMC) = TTF/η + CO2·0,202/η + VOM;
       • nafta → dujos: Brent ir TTF dienos pokyčių koreliacija ir jautrumas (β);
       • dujos → LT elektra: laiko dalis, kai LT kaina ≥ 90 % CCGT SRMC,
         ir iš jos – LT kainos jautrumas TTF pokyčiui;
       • šiandienos vakaro piko (18–21 val.) ir CCGT SRMC skirtumas.
  3. Naujienos: Bloomberg, FT, CNBC, OilPrice, LNG Prime, Rigzone, EIA,
     Google News (tik patikimi leidiniai), Lietuvos žiniasklaida apie dujas
     ir naftą. Rusiškos ir kiniškos kilmės šaltiniai atmetami.
  4. Claude API: trumpa analizė lietuviškai.

Išvestis: data/pasaulio-rinkos.json ir data/pasaulio-rinkos.md

Naudojimas:
    python "scripts/pasaulio rinkos.py"                # šiandien
    python "scripts/pasaulio rinkos.py" 2026-10-09     # konkreti data (naujienos – visada dabartinės)
    python "scripts/pasaulio rinkos.py" --be-analizes  # be Claude API
    python "scripts/pasaulio rinkos.py" --json         # į stdout – JSON vietoje Markdown

Aplinkos kintamieji:
    ANTHROPIC_API_KEY – Claude API (be jo analizė praleidžiama)
    CLAUDE_MODEL      – modelis (numatytasis claude-opus-5-5)
    AGSI_KEY          – GIE AGSI+ raktas (nebūtinas)
"""

import csv
import html
import io
import json
import math
import os
import re
import statistics
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, quote_plus, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests

# ─── KONFIGŪRACIJA ───────────────────────────────────────────────────────────

LT_TZ = ZoneInfo("Europe/Vilnius")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUT_JSON = "pasaulio-rinkos.json"
OUT_MD = "pasaulio-rinkos.md"

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_FALLBACK = "claude-sonnet-5-5"
AGSI_KEY = os.environ.get("AGSI_KEY", "")

UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
UA_BOT = "Mozilla/5.0 (compatible; EnerconsultBot/1.0; +https://enerconsult.lt)"

# Ribinių kaštų (SRMC) prielaidos
GAS_EF = 0.202      # tCO2/MWh šiluminės energijos (gamtinės dujos, 56,1 t/TJ)
ETA_CCGT = 0.55     # efektyvi kombinuoto ciklo elektrinė
ETA_OLD = 0.40      # senesnis ar rezervinis blokas
VOM = 3.0           # €/MWh kintamosios eksploatacijos sąnaudos
GAS_ZONE = 0.90     # LT kaina ≥ 90 % CCGT SRMC → „dujų zona"
EVENING = range(18, 22)   # vakaro pikas 18:00–21:59 LT (kaip kainų brifinge)

DAY_NAMES_LT = ["pirmadienis", "antradienis", "trečiadienis", "ketvirtadienis",
                "penktadienis", "šeštadienis", "sekmadienis"]

# ─── NAUJIENŲ ŠALTINIAI ──────────────────────────────────────────────────────

# (leidinys, URL, kalba)
FEEDS = [
    ("Bloomberg",       "https://feeds.bloomberg.com/markets/news.rss",    "en"),
    ("Bloomberg",       "https://feeds.bloomberg.com/economics/news.rss",  "en"),
    ("Bloomberg",       "https://feeds.bloomberg.com/industries/news.rss", "en"),
    ("Bloomberg",       "https://feeds.bloomberg.com/politics/news.rss",   "en"),
    ("Financial Times", "https://www.ft.com/energy?format=rss",            "en"),
    ("CNBC",            "https://www.cnbc.com/id/19836768/device/rss/rss.html", "en"),
    ("OilPrice.com",    "https://oilprice.com/rss/main",                   "en"),
    ("LNG Prime",       "https://lngprime.com/feed/",                      "en"),
    ("Rigzone",         "https://www.rigzone.com/news/rss/rigzone_latest.aspx", "en"),
    ("EIA",             "https://www.eia.gov/rss/todayinenergy.xml",       "en"),
    # Lietuvos žiniasklaida – atrenkama tik apie dujas, naftą ir degalus
    ("Delfi",           "https://www.delfi.lt/rss/feeds/ekonomika.xml",    "lt"),
    ("Delfi",           "https://www.delfi.lt/rss/feeds/verslas.xml",      "lt"),
    ("15min",           "https://www.15min.lt/rss",                        "lt"),
    ("Verslo žinios",   "https://www.vz.lt/rss/",                          "lt"),
]

# Google News paieškos (žyma, užklausa, kalba)
GNEWS = [
    ("nafta",     '"Brent crude" OR "oil prices" OR "crude oil" OR OPEC', "en"),
    ("dujos",     '"European gas" OR "Dutch TTF" OR "TTF gas" OR "gas prices" OR LNG', "en"),
    ("Bloomberg", "site:bloomberg.com oil OR crude OR OPEC OR gas OR LNG OR Hormuz OR diesel", "en"),
    ("Reuters",   'site:reuters.com oil OR crude OR OPEC OR "natural gas" OR LNG', "en"),
    ("LT dujos",  'dujų OR SGD OR "Klaipėdos nafta" OR "Amber Grid" OR "GET Baltic" '
                  'OR Inčukalnio OR "naftos kainos" OR degalų', "lt"),
]

# Patikimi leidiniai (Google News atrankai pagal <source url>)
TRUSTED = {
    "bloomberg.com": "Bloomberg", "bnnbloomberg.ca": "Bloomberg",
    "reuters.com": "Reuters", "ft.com": "Financial Times", "wsj.com": "WSJ",
    "cnbc.com": "CNBC", "apnews.com": "AP", "economist.com": "The Economist",
    "bbc.com": "BBC", "bbc.co.uk": "BBC", "theguardian.com": "The Guardian",
    "politico.eu": "Politico Europe", "politico.com": "Politico",
    "montelnews.com": "Montel", "argusmedia.com": "Argus", "icis.com": "ICIS",
    "spglobal.com": "S&P Global", "energyintel.com": "Energy Intelligence",
    "upstreamonline.com": "Upstream", "tradewindsnews.com": "TradeWinds",
    "lloydslist.com": "Lloyd's List", "rigzone.com": "Rigzone",
    "oilprice.com": "OilPrice.com", "lngprime.com": "LNG Prime",
    "naturalgasworld.com": "Natural Gas World", "energynow.com": "EnergyNow",
    "energynow.ca": "EnergyNow", "worldoil.com": "World Oil",
    "offshore-energy.biz": "Offshore Energy", "iea.org": "IEA", "eia.gov": "EIA",
    "marketwatch.com": "MarketWatch", "barrons.com": "Barron's", "axios.com": "Axios",
    "nikkei.com": "Nikkei", "euronews.com": "Euronews", "dw.com": "DW",
    "err.ee": "ERR", "lsm.lv": "LSM",
    # Lietuva
    "lrt.lt": "LRT", "delfi.lt": "Delfi", "15min.lt": "15min", "vz.lt": "Verslo žinios",
    "elta.lt": "ELTA", "bns.lt": "BNS", "lrytas.lt": "Lrytas", "diena.lt": "Kauno diena",
    "tv3.lt": "TV3", "alfa.lt": "Alfa", "vakaru.lt": "Vakarų ekspresas",
    "energetika.lt": "Energetika.lt", "kn.lt": "KN", "ambergrid.lt": "Amber Grid",
    "lrv.lt": "LR Vyriausybė", "getbaltic.com": "GET Baltic",
}
BLOCKED = ("tass.", "rt.com", "sputnik", "ria.ru", "interfax", "kommersant", "vedomosti",
           "rbc.ru", "xinhua", "globaltimes", "cgtn", "chinadaily", "people.cn",
           "scmp.com", "caixin", "yicai", "ecns.cn")

EN_RELEVANT = re.compile(
    r"\boil\b|\bcrude\b|\bbrent\b|\bwti\b|\bopec|\bgas\b|\blng\b|\bttf\b|\bdiesel\b|"
    r"\bgasoline\b|\bfuel|\brefin|\btanker|\bhormuz\b|\biran|\bsaudi|\bqatar|\bpipeline|"
    r"\belectricity\b|\bpower (price|market|grid|plant)|\benergy\b|\bcarbon\b|\bemission|"
    r"\bcoal\b|\bnuclear\b|\bwindfall\b|\bbarrel|\bshale\b|\bpetrol|\bequinor\b|"
    r"\bgazprom\b|\bsanction", re.I)
LT_RELEVANT = re.compile(
    r"duj|\bsgd\b|suskystint|naft|degal|benzin|dyzelin|amber grid|get baltic|"
    r"inčukaln|\bttf\b|\bbrent\b|\bopec|elektros kain|energijos kain|šildym", re.I)

GAS_TOPIC = re.compile(r"\bgas\b|\blng\b|\bttf\b|duj|\bsgd\b|suskystint|inčukaln|amber grid", re.I)
OIL_TOPIC = re.compile(r"\boil\b|\bcrude\b|\bbrent\b|\bopec|\bdiesel\b|\bgasoline\b|\brefin|"
                       r"\btanker|\bbarrel|naft|degal|benzin|dyzelin", re.I)
POWER_TOPIC = re.compile(r"electricity|\bpower\b|elektr|\bcarbon\b|\bemission|\bcoal\b", re.I)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ─── HTTP ────────────────────────────────────────────────────────────────────

def http_get(url: str, *, ua: str = UA_BROWSER, timeout: int = 25,
             retries: int = 2, headers: dict | None = None) -> requests.Response | None:
    hdrs = {"User-Agent": ua, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    last = ""
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=hdrs, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except requests.RequestException as exc:
            last = type(exc).__name__
        time.sleep(2 * (attempt + 1))
    log(f"  ⚠ {url[:110]} → {last}")
    return None


def to_float(x) -> float | None:
    try:
        v = float(str(x).replace(",", "."))
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ─── LAIKO EILUTĖS ───────────────────────────────────────────────────────────

Series = list[tuple[date, float]]


def value_on_or_before(series: Series, d: date) -> tuple[date, float] | None:
    best = None
    for dd, v in series:
        if dd <= d:
            best = (dd, v)
        else:
            break
    return best


def value_before(series: Series, d: date) -> float | None:
    hit = value_on_or_before(series, d - timedelta(days=1))
    return hit[1] if hit else None


def pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return (new / old - 1) * 100


def indicator(series: Series, unit: str, source: str, nd: int = 2) -> dict | None:
    """Paskutinė reikšmė ir pokyčiai: per dieną, 7 d., 30 d., metus."""
    if not series:
        return None
    d_last, v_last = series[-1]
    prev = series[-2][1] if len(series) > 1 else None

    def back(days):
        hit = value_on_or_before(series, d_last - timedelta(days=days))
        return hit[1] if hit else None

    return {
        "reikšmė": round(v_last, nd), "data": d_last.isoformat(), "vienetai": unit,
        "pokytis_d_proc": round(pct(v_last, prev), 2) if prev else None,
        "pokytis_7d_proc": round(pct(v_last, back(7)), 2) if back(7) else None,
        "pokytis_30d_proc": round(pct(v_last, back(30)), 2) if back(30) else None,
        "pokytis_metai_proc": round(pct(v_last, back(365)), 2) if back(365) else None,
        "šaltinis": source,
    }


def truncate(series: Series, report_day: date, live: bool) -> Series:
    """Ataskaitai už praeities datą imame tik iki jos išvakarių uždarymo."""
    return series if live else [(d, v) for d, v in series if d < report_day]


# ─── RINKOS DUOMENYS ─────────────────────────────────────────────────────────

def yahoo_series(symbol: str, rng: str = "2y") -> dict | None:
    path = f"/v8/finance/chart/{quote(symbol)}?range={rng}&interval=1d"
    r = None
    for host in ("query1", "query2"):
        r = http_get(f"https://{host}.finance.yahoo.com{path}")
        if r is not None:
            break
    if r is None:
        return None
    try:
        res = r.json()["chart"]["result"][0]
        meta = res.get("meta", {})
        try:
            tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
        except Exception:
            tz = timezone.utc
        ts = res.get("timestamp") or []
        closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        pts = {}
        for t, c in zip(ts, closes):
            if c is not None:
                pts[datetime.fromtimestamp(t, tz).date()] = float(c)
        rmt = meta.get("regularMarketTime")
        return {
            "series": sorted(pts.items()),
            "currency": meta.get("currency"),
            "quote_time": datetime.fromtimestamp(rmt, timezone.utc) if rmt else None,
        }
    except Exception as exc:
        log(f"  ⚠ Yahoo {symbol}: {exc}")
        return None


def ecb_eurusd() -> Series:
    url = ("https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
           "?lastNObservations=400&format=csvdata")
    r = http_get(url, ua=UA_BOT)
    if r is None:
        return []
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        v = to_float(row.get("OBS_VALUE"))
        if v and row.get("TIME_PERIOD"):
            out.append((date.fromisoformat(row["TIME_PERIOD"]), v))
    return sorted(out)


def eex_eua(year: int) -> Series:
    """EUA pirminių aukcionų kainos (EEX viešas metų failas)."""
    try:
        import openpyxl
    except ImportError:
        log("  ⚠ Nėra openpyxl – EUA praleidžiama")
        return []
    pts: dict[date, list[float]] = defaultdict(list)
    for y in (year, year - 1):
        url = ("https://public.eex-group.com/eex/eua-auction-report/"
               f"emission-spot-primary-market-auction-report-{y}-data.xlsx")
        r = http_get(url, ua=UA_BOT, retries=1)
        if r is None:
            continue
        try:
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
            log(f"  ⚠ EEX {y}: {exc}")
        if len(pts) >= 40:          # pakanka einamųjų metų duomenų
            break
    return sorted((d, statistics.mean(v)) for d, v in pts.items())


def iso_z(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def elering_lt(start_utc: datetime, end_utc: datetime) -> list[tuple[datetime, float]]:
    """Nord Pool LT day-ahead kainos per Elering atvirą API (15 min arba 60 min)."""
    out: dict[datetime, float] = {}
    cur = start_utc
    while cur < end_utc:
        nxt = min(cur + timedelta(days=31), end_utc)
        url = ("https://dashboard.elering.ee/api/nps/price?"
               + urlencode({"start": iso_z(cur), "end": iso_z(nxt - timedelta(seconds=1))}))
        r = http_get(url, ua=UA_BOT)
        if r is not None:
            try:
                for e in r.json().get("data", {}).get("lt", []):
                    p = to_float(e.get("price"))
                    if p is not None:
                        out[datetime.fromtimestamp(int(e["timestamp"]), timezone.utc)] = p
            except Exception as exc:
                log(f"  ⚠ Elering: {exc}")
        cur = nxt
    return sorted(out.items())


def nordpool_lt_day(d_lt: date) -> list[tuple[datetime, float]]:
    """Atsarginis šaltinis vienai LT dienai (Nord Pool duomenų portalas)."""
    out: dict[datetime, float] = {}
    for d in (d_lt - timedelta(days=1), d_lt):        # LT para apima dvi CET pristatymo dienas
        url = ("https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices?"
               f"date={d}&market=DayAhead&deliveryArea=LT&currency=EUR")
        r = http_get(url, ua=UA_BOT, retries=1)
        if r is None:
            continue
        try:
            for e in r.json().get("multiAreaEntries", []):
                p = to_float(e.get("entryPerArea", {}).get("LT"))
                if p is not None:
                    t = datetime.fromisoformat(e["deliveryStart"].replace("Z", "+00:00"))
                    out[t] = p
        except Exception as exc:
            log(f"  ⚠ Nord Pool: {exc}")
    return sorted((t, p) for t, p in out.items() if t.astimezone(LT_TZ).date() == d_lt)


def agsi(params: dict) -> dict | None:
    """GIE AGSI+ dujų saugyklų užpildymas (naujausia dujų diena)."""
    headers = {"x-key": AGSI_KEY} if AGSI_KEY else None
    r = http_get("https://agsi.gie.eu/api?" + urlencode(params), ua=UA_BOT, headers=headers)
    if r is None:
        return None
    try:
        j = r.json()
        data = j.get("data", []) if isinstance(j, dict) else j
        if isinstance(data, dict):
            data = [data]
        recs = [x for x in data if isinstance(x, dict) and to_float(x.get("full")) is not None]
        if not recs:
            log(f"  ⚠ AGSI {params}: nėra užpildymo duomenų; raktai "
                f"{list(j.keys()) if isinstance(j, dict) else type(j).__name__}")
            return None
        rec = max(recs, key=lambda x: str(x.get("gasDayStart", "")))
        inj, wd = to_float(rec.get("injection")), to_float(rec.get("withdrawal"))
        return {
            "dujų_diena": rec.get("gasDayStart"),
            "užpildymas_proc": round(to_float(rec.get("full")), 2),
            "paros_pokytis_pp": to_float(rec.get("trend")),
            "saugykloje_twh": to_float(rec.get("gasInStorage")),
            "talpa_twh": to_float(rec.get("workingGasVolume")),
            "grynasis_srautas_gwh_d": round(inj - wd, 1) if inj is not None and wd is not None else None,
        }
    except Exception as exc:
        log(f"  ⚠ AGSI {params}: {exc}")
        return None


# ─── SKAIČIAVIMAI ────────────────────────────────────────────────────────────

def srmc_gas(ttf: float, eua: float, eta: float) -> dict:
    fuel = ttf / eta
    co2 = eua * GAS_EF / eta
    return {"iš_viso": round(fuel + co2 + VOM, 1), "kuras": round(fuel, 1),
            "co2": round(co2, 1), "vom": VOM, "eta": eta}


def returns_link(s_x: Series, s_y: Series, n: int) -> dict | None:
    """Dienos log-pokyčių koreliacija ir β (y pokytis, kai x keičiasi 1 %)."""
    dx, dy = dict(s_x), dict(s_y)
    common = sorted(set(dx) & set(dy))
    rx, ry = [], []
    for a, b in zip(common[:-1], common[1:]):
        if dx[a] > 0 and dx[b] > 0 and dy[a] > 0 and dy[b] > 0:
            rx.append(math.log(dx[b] / dx[a]))
            ry.append(math.log(dy[b] / dy[a]))
    rx, ry = rx[-n:], ry[-n:]
    if len(rx) < max(20, n // 2):
        return None
    try:
        corr = statistics.correlation(rx, ry)
        beta = statistics.covariance(rx, ry) / statistics.variance(rx)
    except statistics.StatisticsError:
        return None
    return {"koreliacija": round(corr, 2), "beta": round(beta, 2), "dienų": len(rx)}


def strength_corr(r: float | None) -> str:
    if r is None:
        return "–"
    a = abs(r)
    return "silpnas" if a < 0.3 else "vidutinis" if a < 0.6 else "stiprus"


def strength_share(s: float | None) -> str:
    if s is None:
        return "–"
    return "silpnas" if s < 5 else "vidutinis" if s < 15 else "stiprus"


def lt_day_stats(points: list[tuple[datetime, float]]) -> dict | None:
    if not points:
        return None
    prices = [p for _, p in points]
    eve = [p for t, p in points if t.astimezone(LT_TZ).hour in EVENING]
    mid = [p for t, p in points if 10 <= t.astimezone(LT_TZ).hour <= 15]
    return {
        "vidurkis": round(statistics.mean(prices), 2),
        "vakaro_pikas_18_21": round(statistics.mean(eve), 2) if eve else None,
        "vidurdienis_10_15": round(statistics.mean(mid), 2) if mid else None,
        "min": round(min(prices), 2), "max": round(max(prices), 2),
        "intervalų": len(points),
    }


def gas_zone(lt_points: list[tuple[datetime, float]], ttf: Series, eua: Series,
             first_day: date, last_day: date) -> dict | None:
    """Kokią laiko dalį LT kaina buvo ≥ 90 % CCGT SRMC (pagal išvakarių TTF ir EUA)."""
    by_day: dict[date, list[tuple[datetime, float]]] = defaultdict(list)
    for t, p in lt_points:
        lt = t.astimezone(LT_TZ)
        if first_day <= lt.date() <= last_day:
            by_day[lt.date()].append((lt, p))
    n_all = n_zone = n_eve = n_eve_zone = 0
    days = 0
    for d, pts in by_day.items():
        g, c = value_before(ttf, d), value_before(eua, d)
        if g is None or c is None:
            continue
        thr = GAS_ZONE * srmc_gas(g, c, ETA_CCGT)["iš_viso"]
        days += 1
        for lt, p in pts:
            hit = p >= thr
            n_all += 1
            n_zone += hit
            if lt.hour in EVENING:
                n_eve += 1
                n_eve_zone += hit
    if not n_all:
        return None
    share = 100 * n_zone / n_all
    share_eve = 100 * n_eve_zone / n_eve if n_eve else None
    return {
        "dienų": days,
        "laiko_dalis_proc": round(share, 1),
        "vakaro_dalis_proc": round(share_eve, 1) if share_eve is not None else None,
        # +10 €/MWh TTF → +10/η €/MWh dujų zonos intervalais; vidurkiui – svoriu pagal dalį
        "jautrumas_vid_eur_mwh_per_10": round(share / 100 * 10 / ETA_CCGT, 2),
        "jautrumas_dujų_zonoje_eur_mwh_per_10": round(10 / ETA_CCGT, 1),
    }


# ─── NAUJIENOS ───────────────────────────────────────────────────────────────

def _host(url: str | None) -> str:
    h = urlparse(url or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


def outlet_for(host: str) -> str | None:
    if not host or any(b in host for b in BLOCKED) or host.endswith((".ru", ".cn")):
        return None
    for dom, name in TRUSTED.items():
        if host == dom or host.endswith("." + dom):
            return name
    return None


def _txt(el: ET.Element, tag: str, ns: dict | None = None) -> str:
    x = el.find(tag, ns) if ns else el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def _clean(s: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_feed(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    out = []
    items = root.findall(".//item")
    if items:
        for it in items:
            src = it.find("source")
            out.append({
                "title": _clean(_txt(it, "title")),
                "link": _txt(it, "link") or _txt(it, "guid"),
                "desc": _clean(_txt(it, "description")),
                "pub": _date(_txt(it, "pubDate")
                             or _txt(it, "{http://purl.org/dc/elements/1.1/}date")),
                "src_name": (src.text or "").strip() if src is not None else None,
                "src_url": src.get("url") if src is not None else None,
            })
    else:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for e in root.findall(".//a:entry", ns):
            link = e.find("a:link", ns)
            out.append({
                "title": _clean(_txt(e, "a:title", ns)),
                "link": link.get("href", "") if link is not None else "",
                "desc": _clean(_txt(e, "a:summary", ns)),
                "pub": _date(_txt(e, "a:published", ns) or _txt(e, "a:updated", ns)),
                "src_name": None, "src_url": None,
            })
    return out


def gnews_url(query: str, lang: str, when: str) -> str:
    q = quote_plus(f"{query} when:{when}")
    if lang == "lt":
        return f"https://news.google.com/rss/search?q={q}&hl=lt&gl=LT&ceid=LT:lt"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def topic_of(text: str) -> str:
    if GAS_TOPIC.search(text):
        return "dujos"
    if OIL_TOPIC.search(text):
        return "nafta"
    if POWER_TOPIC.search(text):
        return "elektra ir CO2"
    return "geopolitika ir makro"


def collect_news(now_utc: datetime, monday: bool) -> tuple[list[dict], dict]:
    since = now_utc - timedelta(hours=80 if monday else 32)
    when_en, when_lt = ("3d", "4d") if monday else ("1d", "2d")
    raw: list[dict] = []
    stats: dict[str, int] = defaultdict(int)

    def take(items, outlet_default, lang, via_gnews):
        for it in items:
            outlet = outlet_default
            if via_gnews:
                outlet = outlet_for(_host(it["src_url"]))
                if not outlet:
                    continue
                if it["src_name"] and it["title"].endswith(" - " + it["src_name"]):
                    it["title"] = it["title"][: -len(it["src_name"]) - 3].strip()
                it["desc"] = ""                       # Google News aprašas – tik nuorodų sąrašas
            else:
                host = _host(it["link"])
                if host and (any(b in host for b in BLOCKED) or host.endswith((".ru", ".cn"))):
                    continue
            if not it["title"] or it["pub"] is None or it["pub"] < since:
                continue
            text = f"{it['title']} {it['desc']}"
            if not (LT_RELEVANT if lang == "lt" else EN_RELEVANT).search(text):
                continue
            it.update(outlet=outlet, lang=lang, topic=topic_of(text))
            raw.append(it)

    for outlet, url, lang in FEEDS:
        r = http_get(url, ua=UA_BOT, retries=1)
        if r is None:
            r = http_get(url, ua=UA_BROWSER, retries=0)
        if r is None:
            continue
        try:
            items = parse_feed(r.content)
        except ET.ParseError as exc:
            log(f"  ⚠ {outlet}: XML klaida {exc}")
            continue
        before = len(raw)
        take(items, outlet, lang, via_gnews=False)
        log(f"  ✓ {outlet}: {len(items)} → atrinkta {len(raw) - before}")

    for tag, query, lang in GNEWS:
        r = http_get(gnews_url(query, lang, when_lt if lang == "lt" else when_en),
                     ua=UA_BOT, retries=1)
        if r is None:
            continue
        try:
            items = parse_feed(r.content)
        except ET.ParseError as exc:
            log(f"  ⚠ Google News {tag}: XML klaida {exc}")
            continue
        before = len(raw)
        take(items, None, lang, via_gnews=True)
        log(f"  ✓ Google News {tag}: {len(items)} → atrinkta {len(raw) - before}")

    # Deduplikacija pagal normalizuotą pavadinimą, naujausi pirmiau
    raw.sort(key=lambda x: x["pub"], reverse=True)
    seen, out = set(), []
    per_outlet: dict[str, int] = defaultdict(int)
    for it in raw:
        key = re.sub(r"[^a-z0-9ąčęėįšųūž]", "", it["title"].lower())[:70]
        if key in seen or per_outlet[it["outlet"]] >= 15:
            continue
        seen.add(key)
        per_outlet[it["outlet"]] += 1
        out.append(it)
    for it in out:
        stats[it["outlet"]] += 1
    return out[:80], dict(sorted(stats.items(), key=lambda kv: -kv[1]))


# ─── FORMATAVIMAS ────────────────────────────────────────────────────────────

def num(x: float | None, nd: int = 2, sign: bool = False) -> str:
    if x is None:
        return "–"
    s = f"{x:+,.{nd}f}" if sign else f"{x:,.{nd}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−")


def pct_s(x: float | None) -> str:
    return "–" if x is None else f"{num(x, 1, sign=True)} %"


def pp_s(x: float | None) -> str:
    return "–" if x is None else f"{num(x, 1, sign=True)} pp"


# ─── CLAUDE API ──────────────────────────────────────────────────────────────

def data_block(ctx: dict) -> str:
    r, g = ctx["rodikliai"], ctx["grandinė"]
    lines = []

    def ind(label, key, unit, nd=2):
        x = r.get(key)
        if not x:
            lines.append(f"- {label}: duomenų nėra")
            return
        lines.append(
            f"- {label}: {num(x['reikšmė'], nd)}{' ' + unit if unit else ''} ({x['data']}; per dieną "
            f"{pct_s(x['pokytis_d_proc'])}, per 7 d. {pct_s(x['pokytis_7d_proc'])}, "
            f"per 30 d. {pct_s(x['pokytis_30d_proc'])}, per metus {pct_s(x['pokytis_metai_proc'])})")

    ind("Brent nafta", "brent", "USD/bbl")
    if r.get("brent_eur"):
        lines.append(f"- Brent eurais: {num(r['brent_eur'], 2)} €/bbl")
    ind("TTF dujos (artimiausio mėnesio ateities sandoris)", "ttf", "€/MWh")
    ind("CO2 (EUA pirminis aukcionas)", "eua", "€/t")
    ind("EUR/USD (ECB)", "eurusd", "", 4)
    ind("JAV Henry Hub dujos", "henry_hub", "USD/MMBtu")
    for key, label in (("saugyklos_es", "ES dujų saugyklos"), ("saugyklos_lv", "Latvijos Inčukalnio saugykla")):
        s = r.get(key)
        if s:
            yy = s.get("prieš_metus_proc")
            lines.append(
                f"- {label}: {num(s['užpildymas_proc'], 1)} % (dujų diena {s['dujų_diena']}; "
                f"paros pokytis {pp_s(s.get('paros_pokytis_pp'))}"
                + (f"; prieš metus {num(yy, 1)} %" if yy is not None else "") + ")")
        else:
            lines.append(f"- {label}: duomenų nėra")
    lt = r.get("lt_elektra")
    if lt and lt.get("šiandien"):
        t = lt["šiandien"]
        lines.append(
            f"- LT elektros kaina {lt['data']}: vidurkis {num(t['vidurkis'])}, vakaro pikas 18–21 val. "
            f"{num(t['vakaro_pikas_18_21'])}, vidurdienis 10–15 val. {num(t['vidurdienis_10_15'])}, "
            f"min {num(t['min'])}, max {num(t['max'])} €/MWh; 7 d. vidurkis "
            f"{num(lt.get('vidurkis_7d'))} €/MWh")
    else:
        lines.append("- LT elektros kaina šiandien: duomenų nėra")
    s = g.get("srmc")
    if s:
        lines.append(
            f"- Dujinės elektrinės ribiniai kaštai (SRMC): η 55 % – {num(s['ccgt']['iš_viso'], 1)} €/MWh "
            f"(kuras {num(s['ccgt']['kuras'], 1)}, CO2 {num(s['ccgt']['co2'], 1)}); "
            f"η 40 % – {num(s['senas']['iš_viso'], 1)} €/MWh")
    nd = g.get("nafta_dujos")
    if nd:
        base = nd.get("bazė_250d")
        lines.append(
            f"- Nafta→dujos ryšys: {nd['dienų']} d. dienos pokyčių koreliacija "
            f"{num(nd['koreliacija'])} ({strength_corr(nd['koreliacija'])}), β {num(nd['beta'])} "
            f"(Brent pakilus 1 %, TTF vidutiniškai kinta {num(nd['beta'])} %)"
            + (f"; ilgesnio laikotarpio ({base['dienų']} d.) koreliacija {num(base['koreliacija'])}"
               if base else ""))
    dz = g.get("dujos_elektra")
    if dz:
        lines.append(
            f"- Dujos→LT elektra: per {dz['dienų']} d. LT kaina ≥ 90 % CCGT SRMC buvo "
            f"{num(dz['laiko_dalis_proc'], 1)} % laiko (vakaro 18–21 val. – "
            f"{num(dz['vakaro_dalis_proc'], 1)} %); jautrumas: TTF +10 €/MWh → LT vidutinė kaina "
            f"≈ +{num(dz['jautrumas_vid_eur_mwh_per_10'])} €/MWh, dujų zonos intervalais ≈ "
            f"+{num(dz['jautrumas_dujų_zonoje_eur_mwh_per_10'], 1)} €/MWh. Rodiklis apima tik "
            f"tiesioginį poveikį; netiesioginis eina per importo kainas (PL, SE4) ir CO2")
    sp = g.get("šiandien")
    if sp and sp.get("vakaro_piko_skirtumas") is not None:
        lines.append(
            f"- Šiandienos vakaro piko ir CCGT SRMC skirtumas: {num(sp['vakaro_piko_skirtumas'], 1, True)} €/MWh "
            f"({sp['vertinimas']})")
    return "\n".join(lines)


def build_prompt(ctx: dict, news: list[dict]) -> str:
    news_lines = "\n".join(
        f"[{i + 1}] {it['outlet']} | {it['pub'].astimezone(LT_TZ):%Y-%m-%d %H:%M} | "
        f"{it['title']}" + (f" — {it['desc'][:280]}" if it["desc"] else "")
        for i, it in enumerate(news)) or "(šiandien naujienų nerasta)"
    return f"""Tu esi MB Enerconsult energetikos rinkų analitikas. Rašai rytinio „Morning Report"
skyrių „Pasaulio energetikos rinkos" Lietuvos atsinaujinančios energetikos ir kaupiklių (BESS)
projektų vystytojams, investuotojams ir savininkams.

Šiandien {ctx['data']} ({ctx['savaitės_diena']}). Užduotis – paaiškinti grandinę
nafta → dujos → elektros kaina Lietuvoje, remiantis DUOMENIMIS ir NAUJIENOMIS žemiau.

Griežtos taisyklės:
1. Skaičius imk tik iš DUOMENŲ arba tiesiogiai iš naujienų teksto. Savų skaičių ir prognozių neišgalvok.
2. Teiginius iš naujienų pagrįsk leidinio pavadinimu skliaustuose, pvz. (Bloomberg) arba (Reuters; FT). Nuorodų nerašyk.
3. Antraštes perpasakok savais žodžiais, necituok jų pažodžiui.
4. Jei naujienos prieštarauja viena kitai, pateik abi puses.
5. Jei apie kurią sritį šiandien naujienų nėra, parašyk tai vienu sakiniu ir nieko neišgalvok.
6. Lietuvos kontekstą (Klaipėdos SGD terminalas, GIPL, Inčukalnio saugykla, Elektrėnų kombinuoto ciklo blokas, NordBalt ir LitPol jungtys) minėk tik tada, kai tai pagrįsta duomenimis arba naujienomis.
7. Stilius: lietuvių kalba, glaustai, dalykiškai; dešimtainis kablelis; nerašyk didžiosiomis raidėmis; nevartok žodžių „mielai" ir „padarysiu"; jei sakinyje jungtukas „ir" vienas, kableliu jo neskirk.

Atsakyk Markdown formatu, tik šiomis antraštėmis:

### Santrauka
2–3 sakiniai: kas šiandien svarbiausia grandinėje nafta → dujos → LT elektra.

### 🛢️ Nafta
3–5 sakiniai: kainos judėjimas ir jo priežastys.

### 🔥 Dujos ir SGD
3–5 sakiniai: TTF, saugyklos, SGD tiekimas; Lietuvos ir Baltijos kontekstas, jei yra.

### ⚡ Poveikis Lietuvai
3–5 sakiniai: ką tai reiškia LT elektros kainai. Remkis SRMC, dujų zonos laiko dalimi, jautrumu
ir šiandienos vakaro piko skirtumu. Jei aktualu, paaiškink poveikį BESS kainų skirtumams
(vakaro pikas ir vidurdienis) bei saulės elektrinių užfiksuotai kainai.

### 👀 Ką stebėti
2–4 punktai: artimiausi įvykiai ir rizikos iš naujienų.

DUOMENYS:
{data_block(ctx)}

NAUJIENOS ({len(news)}):
{news_lines}
"""


def analyze(ctx: dict, news: list[dict]) -> tuple[str | None, str | None]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("  ⚠ ANTHROPIC_API_KEY nenustatytas – analizė praleidžiama")
        return None, None
    try:
        import anthropic
    except ImportError:
        log("  ⚠ Nėra anthropic bibliotekos – analizė praleidžiama")
        return None, None
    client = anthropic.Anthropic()
    prompt = build_prompt(ctx, news)
    for model in dict.fromkeys([CLAUDE_MODEL, CLAUDE_FALLBACK]):
        try:
            msg = client.messages.create(model=model, max_tokens=2500,
                                         messages=[{"role": "user", "content": prompt}])
            text = "".join(getattr(b, "text", "") for b in msg.content).strip()
            if text:
                log(f"  ✓ Analizė: {model}, {msg.usage.input_tokens}→{msg.usage.output_tokens} žetonų")
                return text, model
        except Exception as exc:
            log(f"  ⚠ Claude API ({model}): {exc}")
    return None, None


# ─── ATASKAITA ───────────────────────────────────────────────────────────────

def render_md(ctx: dict, analysis: str | None) -> str:
    r, g = ctx["rodikliai"], ctx["grandinė"]
    out = [f"# 🌍 Pasaulio energetikos rinkos — {ctx['data']}", "", "## Rodikliai", "",
           "| Rodiklis | Reikšmė | Per dieną | Per 7 d. | Per metus |",
           "|---|---|---|---|---|"]

    def row(label, key, unit, nd=2, extra=""):
        x = r.get(key)
        if not x:
            out.append(f"| {label} | – | – | – | – |")
            return
        out.append(f"| {label} | {num(x['reikšmė'], nd)}{unit}{extra} | {pct_s(x['pokytis_d_proc'])} | "
                   f"{pct_s(x['pokytis_7d_proc'])} | {pct_s(x['pokytis_metai_proc'])} |")

    row("Brent nafta, USD/bbl", "brent", "",
        extra=f" ({num(r['brent_eur'])} €/bbl)" if r.get("brent_eur") else "")
    row("TTF dujos, €/MWh", "ttf", "")
    row("CO2 (EUA aukcionas), €/t", "eua", "")
    row("EUR/USD", "eurusd", "", nd=4)
    for key, label in (("saugyklos_es", "ES dujų saugyklos"), ("saugyklos_lv", "Inčukalnio saugykla (LV)")):
        s = r.get(key)
        if s:
            yy = s.get("prieš_metus_proc")
            out.append(f"| {label} | {num(s['užpildymas_proc'], 1)} % | {pp_s(s.get('paros_pokytis_pp'))} | – | "
                       f"{pp_s(s['užpildymas_proc'] - yy) if yy is not None else '–'} |")
        else:
            out.append(f"| {label} | – | – | – | – |")
    lt = r.get("lt_elektra")
    if lt and lt.get("šiandien"):
        t = lt["šiandien"]
        out += ["", f"**LT elektra {lt['data']}:** vidurkis {num(t['vidurkis'])} €/MWh "
                    f"(7 d. vidurkis {num(lt.get('vidurkis_7d'))}) · vakaro pikas 18–21 val. "
                    f"{num(t['vakaro_pikas_18_21'])} · vidurdienis 10–15 val. {num(t['vidurdienis_10_15'])} · "
                    f"min–max {num(t['min'])}–{num(t['max'])} €/MWh"]

    out += ["", "## Grandinė nafta → dujos → elektra LT", "",
            "| Grandis | Rodiklis | Reikšmė | Ryšys |", "|---|---|---|---|"]
    nd = g.get("nafta_dujos")
    if nd:
        base = nd.get("bazė_250d")
        out.append(f"| Nafta → dujos | Brent ir TTF dienos pokyčių koreliacija ({nd['dienų']} d.) | "
                   f"{num(nd['koreliacija'])}" + (f" (ilgalaikė {num(base['koreliacija'])})" if base else "")
                   + f" | {strength_corr(nd['koreliacija'])} |")
        out.append(f"| Nafta → dujos | TTF pokytis, kai Brent kinta 1 % (β) | {num(nd['beta'])} % | |")
    s = g.get("srmc")
    if s:
        out.append(f"| Dujos → gamybos kaštai | Dujinės elektrinės SRMC, η 55 % / 40 % | "
                   f"{num(s['ccgt']['iš_viso'], 0)} / {num(s['senas']['iš_viso'], 0)} €/MWh | |")
    dz = g.get("dujos_elektra")
    if dz:
        out.append(f"| Dujos → LT kaina | Laiko dalis, kai LT kaina ≥ 90 % SRMC ({dz['dienų']} d.; vakare) | "
                   f"{num(dz['laiko_dalis_proc'], 1)} % ({num(dz['vakaro_dalis_proc'], 1)} %) | "
                   f"{strength_share(dz['laiko_dalis_proc'])} |")
        out.append(f"| Dujos → LT kaina | TTF +10 €/MWh → LT vidurkis (dujų zonoje) | "
                   f"+{num(dz['jautrumas_vid_eur_mwh_per_10'])} (+{num(dz['jautrumas_dujų_zonoje_eur_mwh_per_10'], 1)}) €/MWh | |")
    sp = g.get("šiandien")
    if sp and sp.get("vakaro_piko_skirtumas") is not None:
        out.append(f"| Šiandien | Vakaro pikas minus SRMC (η 55 %) | "
                   f"{num(sp['vakaro_piko_skirtumas'], 1, True)} €/MWh | {sp['vertinimas']} |")

    out.append("")
    if analysis:
        out += [analysis.strip(), ""]
    else:
        out += ["### Svarbiausios antraštės", ""]
        for it in ctx.get("naujienos", [])[:10]:
            out.append(f"- {it['pavadinimas']} ({it['leidinys']})")
        if not ctx.get("naujienos"):
            out.append("Šiandien naujienų nerasta.")
        out.append("")

    srcs = ", ".join(f"{k} ({v})" for k, v in ctx.get("naujienų_statistika", {}).items()) or "–"
    out += ["---",
            "**Duomenys:** Yahoo Finance (Brent, TTF, Henry Hub), EEX (CO2 aukcionai), ECB (EUR/USD), "
            "GIE AGSI+ (saugyklos), Nord Pool per Elering (LT kaina). TTF – artimiausio mėnesio "
            "ateities sandoris; SRMC – apytikris, be dujų transportavimo į Lietuvą.",
            f"**Naujienos:** {srcs}.", "",
            f"_Generuota automatiškai {ctx['data']} · MB Enerconsult_"]
    return "\n".join(out)


# ─── PAGRINDINIS SRAUTAS ─────────────────────────────────────────────────────

def build(report_day: date, live: bool, with_ai: bool) -> tuple[dict, str]:
    now_utc = datetime.now(timezone.utc)
    warnings: list[str] = []
    ctx: dict = {
        "data": report_day.isoformat(),
        "savaitės_diena": DAY_NAMES_LT[report_day.weekday()],
        "sugeneruota": datetime.now(LT_TZ).isoformat(timespec="seconds"),
        "rodikliai": {}, "grandinė": {}, "naujienos": [], "įspėjimai": warnings,
    }
    r, g = ctx["rodikliai"], ctx["grandinė"]

    log("→ Naftos, dujų ir valiutų kainos (Yahoo Finance, ECB)...")
    yh = {sym: yahoo_series(sym) for sym in ("BZ=F", "TTF=F", "NG=F", "EURUSD=X")}
    brent = truncate((yh["BZ=F"] or {}).get("series", []), report_day, live)
    ttf = truncate((yh["TTF=F"] or {}).get("series", []), report_day, live)
    hh = truncate((yh["NG=F"] or {}).get("series", []), report_day, live)
    fx = truncate(ecb_eurusd(), report_day, live) or \
        truncate((yh["EURUSD=X"] or {}).get("series", []), report_day, live)
    r["brent"] = indicator(brent, "USD/bbl", "Yahoo Finance (BZ=F)")
    r["ttf"] = indicator(ttf, "€/MWh", "Yahoo Finance (TTF=F)", nd=3)
    r["henry_hub"] = indicator(hh, "USD/MMBtu", "Yahoo Finance (NG=F)", nd=3)
    r["eurusd"] = indicator(fx, "USD už 1 €", "ECB", nd=4)
    if r["brent"] and fx:
        r["brent_eur"] = round(r["brent"]["reikšmė"] / fx[-1][1], 2)
    for key, sym in (("brent", "BZ=F"), ("ttf", "TTF=F")):
        qt = (yh[sym] or {}).get("quote_time")
        if r.get(key) and qt and live:
            r[key]["kotiruotės_laikas"] = qt.astimezone(LT_TZ).isoformat(timespec="minutes")
    for key in ("brent", "ttf", "eurusd"):
        if not r.get(key):
            warnings.append(f"Nepavyko gauti: {key}")
    log(f"   Brent {len(brent)} d., TTF {len(ttf)} d., EUR/USD {len(fx)} d.")

    log("→ CO2 (EEX aukcionai)...")
    eua = truncate(eex_eua(report_day.year), report_day, live)
    r["eua"] = indicator(eua, "€/t", "EEX pirminiai aukcionai")
    if not eua:
        warnings.append("Nepavyko gauti: EUA")
    log(f"   EUA {len(eua)} aukcionų dienų")

    log("→ Dujų saugyklos (GIE AGSI+)...")
    for key, params in (("saugyklos_es", {"type": "eu"}), ("saugyklos_lv", {"country": "LV"})):
        p = dict(params)
        if not live:
            p["date"] = (report_day - timedelta(days=1)).isoformat()
        cur = agsi(p)
        if cur and cur.get("dujų_diena"):
            try:
                ly = date.fromisoformat(cur["dujų_diena"][:10]) - timedelta(days=365)
                prev = agsi({**params, "date": ly.isoformat()})
                # Tikriname, ar API tikrai grąžino prašytą dieną, o ne naujausią
                if prev and abs((date.fromisoformat(prev["dujų_diena"][:10]) - ly).days) <= 3:
                    cur["prieš_metus_proc"] = prev["užpildymas_proc"]
                else:
                    log(f"   AGSI {params}: metų senumo duomenų negauta")
            except (ValueError, TypeError):
                pass
        r[key] = cur
        if cur:
            cur["šaltinis"] = "GIE AGSI+"
    log(f"   ES: {(r.get('saugyklos_es') or {}).get('užpildymas_proc')} %, "
        f"LV: {(r.get('saugyklos_lv') or {}).get('užpildymas_proc')} %")

    log("→ LT elektros kainos (Elering / Nord Pool)...")
    day_start = datetime.combine(report_day, datetime.min.time(), LT_TZ)
    hist_start = day_start - timedelta(days=30)
    lt_pts = elering_lt(hist_start, day_start + timedelta(days=1))
    today = [(t, p) for t, p in lt_pts if t.astimezone(LT_TZ).date() == report_day]
    if not today:
        log("   Elering šiandienos kainų neturi – bandomas Nord Pool")
        today = nordpool_lt_day(report_day)
        lt_pts += today
    week = [p for t, p in lt_pts
            if report_day - timedelta(days=7) <= t.astimezone(LT_TZ).date() < report_day]
    r["lt_elektra"] = {
        "data": report_day.isoformat(),
        "šiandien": lt_day_stats(today),
        "vidurkis_7d": round(statistics.mean(week), 2) if week else None,
        "šaltinis": "Nord Pool per Elering",
    }
    if not today:
        warnings.append("Nepavyko gauti: LT šiandienos kainos")
    log(f"   LT intervalų: istorija {len(lt_pts)}, šiandien {len(today)}")

    # ── Grandinės rodikliai ──
    if r.get("ttf") and r.get("eua"):
        g["srmc"] = {"ccgt": srmc_gas(r["ttf"]["reikšmė"], r["eua"]["reikšmė"], ETA_CCGT),
                     "senas": srmc_gas(r["ttf"]["reikšmė"], r["eua"]["reikšmė"], ETA_OLD),
                     "ttf_data": r["ttf"]["data"], "eua_data": r["eua"]["data"]}
    link = returns_link(brent, ttf, 60)
    if link:
        link["bazė_250d"] = returns_link(brent, ttf, 250)
        g["nafta_dujos"] = link
    if ttf and eua:
        g["dujos_elektra"] = gas_zone(lt_pts, ttf, eua, report_day - timedelta(days=30), report_day)
    t = (r.get("lt_elektra") or {}).get("šiandien")
    if t and g.get("srmc") and t.get("vakaro_pikas_18_21") is not None:
        srmc_ccgt = g["srmc"]["ccgt"]["iš_viso"]
        spread = round(t["vakaro_pikas_18_21"] - srmc_ccgt, 1)
        if spread >= 0:
            verdict = "dujinė elektrinė vakaro pikui atsiperka"
        elif t["vakaro_pikas_18_21"] >= GAS_ZONE * srmc_ccgt:
            verdict = "vakaro kaina dujų zonoje"
        else:
            verdict = "dujos vakaro kainos nenustato"
        g["šiandien"] = {"vakaro_piko_skirtumas": spread, "vertinimas": verdict}

    log("→ Naujienos...")
    news, stats = collect_news(now_utc, monday=report_day.weekday() == 0)
    ctx["naujienos"] = [{"leidinys": it["outlet"], "pavadinimas": it["title"], "nuoroda": it["link"],
                         "laikas": it["pub"].astimezone(LT_TZ).isoformat(timespec="minutes"),
                         "tema": it["topic"], "kalba": it["lang"]} for it in news]
    ctx["naujienų_statistika"] = stats
    log(f"   Atrinkta naujienų: {len(news)} · {stats}")

    analysis, model = (analyze(ctx, news) if with_ai else (None, None))
    ctx["analizė_md"] = analysis
    ctx["modelis"] = model
    md = render_md(ctx, analysis)
    return ctx, md


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    today_lt = datetime.now(LT_TZ).date()
    report_day = date.fromisoformat(args[0]) if args else today_lt
    live = report_day >= today_lt

    log(f"🌍 Pasaulio rinkų brifingas {report_day} ({DAY_NAMES_LT[report_day.weekday()]})")
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        ctx, md = build(report_day, live, with_ai="--be-analizes" not in flags)
    except Exception:
        log(traceback.format_exc())
        ctx = {"data": report_day.isoformat(), "klaida": traceback.format_exc(limit=3)}
        md = (f"# 🌍 Pasaulio energetikos rinkos — {report_day}\n\n"
              "Šiandien skyriaus sugeneruoti nepavyko (žr. pasaulio-rinkos-debug.log).\n")

    with open(os.path.join(DATA_DIR, OUT_JSON), "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(DATA_DIR, OUT_MD), "w", encoding="utf-8") as f:
        f.write(md)

    print(json.dumps(ctx, ensure_ascii=False, indent=2, default=str) if "--json" in flags else md)
    log(f"✅ Išsaugota → data/{OUT_JSON}, data/{OUT_MD}")


if __name__ == "__main__":
    main()
