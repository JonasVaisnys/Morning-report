#!/usr/bin/env python3
"""
Lietuvos elektros rinkos kainų brifingas
=========================================
Automatiškai pasiima day-ahead kainas ir tarpsisteminius srautus
iš ENTSO-E Transparency Platform ir sugeneruoja dienos brifingą.

Zonos: LT, LV, PL, SE4
Srautai: NordBalt (LT↔SE4), LitPol Link (LT↔PL), LT↔LV
LT gamyba/vartojimas: vartojimas, gamyba (saulė / vėjas / kita)

Naudojimas:
    python "kainų brifingas.py"                  # šiandien
    python "kainų brifingas.py" 2026-10-09       # konkreti data
    python "kainų brifingas.py" --json            # JSON išvestis

Reikia: requests (pip install requests)
"""

import sys
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional
import os

try:
    import requests
except ImportError:
    print("Reikia 'requests' bibliotekos: pip install requests")
    sys.exit(1)

# ─── KONFIGŪRACIJA ───────────────────────────────────────────────────────────

ENTSOE_URL = "https://web-api.tp.entsoe.eu/api"
TOKEN = os.environ.get("ENTSOE_TOKEN", "")
if not TOKEN:
    print("Klaida: nustatyk ENTSOE_TOKEN aplinkos kintamąjį.", file=sys.stderr)
    print("  export ENTSOE_TOKEN='tavo-token-čia'", file=sys.stderr)
    sys.exit(1)

ZONES = {
    "LT": "10YLT-1001A0008Q",
    "LV": "10YLV-1001A00074",
    "PL": "10YPL-AREA-----S",
    "SE4": "10Y1001A1001A47J",
}

# ─── LT REFERENCINIAI PROFILIAI ──────────────────────────────────────────────

REFERENCE_PROFILES = {
    "Spring Weekday":  {"night": 66.8, "m_ramp": 101.3, "m_peak": 116.3, "dip": 43.8, "a_ramp": 55.5, "e_peak": 130.1, "l_eve": 104.0, "daily_avg": 82},
    "Spring Weekend":  {"night": 69.5, "m_ramp": 67.1,  "m_peak": 50.5,  "dip": 11.0, "a_ramp": 27.7, "e_peak": 98.4,  "l_eve": 92.7,  "daily_avg": 56},
    "Summer Weekday":  {"night": 51.2, "m_ramp": 61.5,  "m_peak": 80.1,  "dip": 38.3, "a_ramp": 40.5, "e_peak": 105.1, "l_eve": 94.8,  "daily_avg": 64},
    "Summer Weekend":  {"night": 46.9, "m_ramp": 37.7,  "m_peak": 31.4,  "dip": 10.5, "a_ramp": 13.4, "e_peak": 70.1,  "l_eve": 78.8,  "daily_avg": 39},
    "Autumn Weekday":  {"night": 59.9, "m_ramp": 106.0, "m_peak": 174.6, "dip": 108.7,"a_ramp": 128.6,"e_peak": 169.9, "l_eve": 90.7,  "daily_avg": 115},
    "Autumn Weekend":  {"night": 46.0, "m_ramp": 45.9,  "m_peak": 58.4,  "dip": 45.2, "a_ramp": 81.9, "e_peak": 114.9, "l_eve": 65.7,  "daily_avg": 64},
    "Winter Weekday":  {"night": 83.9, "m_ramp": 108.7, "m_peak": 180.7, "dip": 177.6,"a_ramp": 184.4,"e_peak": 158.2, "l_eve": 108.6, "daily_avg": 143},
    "Winter Weekend":  {"night": 66.7, "m_ramp": 66.5,  "m_peak": 83.4,  "dip": 109.1,"a_ramp": 130.5,"e_peak": 116.4, "l_eve": 87.7,  "daily_avg": 95},
}

CURVE_DESCRIPTIONS = {
    "Spring Weekday":  "M formos. Du pikai. Gilus vidurdienio saulės nuosmukis. Vakaras > rytas.",
    "Spring Weekend":  "U formos. Tik vakaro pikas. Labai gilus vidurdienio nuosmukis.",
    "Summer Weekday":  "M formos. Vidutinis ryto pikas. Gilus saulės nuosmukis. Dominuoja vakaro pikas.",
    "Summer Weekend":  "Gilus U. Vidurdienis artimas nuliui. Tik vakaro pikas. Pigiausias tipas.",
    "Autumn Weekday":  "M formos. Beveik lygūs ryto ir vakaro pikai. Seklus vidurdienio nuosmukis.",
    "Autumn Weekend":  "Kylanti kreivė. Plokščias rytas, kylant iki vakaro piko.",
    "Winter Weekday":  "Plokščiakalnis. Nėra vidurdienio nuosmukio. Aukštos kainos 07–21.",
    "Winter Weekend":  "Laipsniškas kilimas. Apverstas nuosmukis. Vidutinis vakaro pikas.",
}

INTERVAL_KEYS = ["night", "m_ramp", "m_peak", "dip", "a_ramp", "e_peak", "l_eve"]
INTERVAL_LABELS = {
    "night":  ("Naktis",          "00–05"),
    "m_ramp": ("Ryto kilimas",    "06"),
    "m_peak": ("Ryto pikas",      "07–09"),
    "dip":    ("Vidurdienio dub.", "10–15"),
    "a_ramp": ("Popietės kilimas","16–17"),
    "e_peak": ("Vakaro pikas",    "18–21"),
    "l_eve":  ("Vėlus vakaras",   "22–23"),
}

DAY_NAMES_LT = ["pirmadienis", "antradienis", "trečiadienis", "ketvirtadienis",
                 "penktadienis", "šeštadienis", "sekmadienis"]

# ─── LAIKO JUOSTA ─────────────────────────────────────────────────────────────

def lt_utc_offset(dt_utc: datetime) -> int:
    y = dt_utc.year
    mar31 = datetime(y, 3, 31)
    last_sun_mar = mar31 - timedelta(days=(mar31.weekday() + 1) % 7)
    oct31 = datetime(y, 10, 31)
    last_sun_oct = oct31 - timedelta(days=(oct31.weekday() + 1) % 7)
    if last_sun_mar.replace(hour=1) <= dt_utc < last_sun_oct.replace(hour=1):
        return 3  # EEST
    return 2  # EET

# ─── ENTSOE API ──────────────────────────────────────────────────────────────

def cet_boundary_hour(dt_utc: datetime) -> int:
    """CET/CEST pristatymo dienos riba UTC: 22 (CEST vasarą) arba 23 (CET žiemą)."""
    y = dt_utc.year
    mar31 = datetime(y, 3, 31)
    last_sun_mar = mar31 - timedelta(days=(mar31.weekday() + 1) % 7)
    oct31 = datetime(y, 10, 31)
    last_sun_oct = oct31 - timedelta(days=(oct31.weekday() + 1) % 7)
    if last_sun_mar.replace(hour=1) <= dt_utc < last_sun_oct.replace(hour=1):
        return 22  # CEST
    return 23  # CET


def get_period(target_date: datetime) -> tuple:
    """Grąžina (periodStart, periodEnd) apimant visas 24 val. Lietuvos laiku.

    ENTSO-E API grąžina duomenis pagal CET/CEST pristatymo dienas.
    periodStart prasideda nuo LT vidurnakčio UTC (ankstesnis nei CET riba),
    o periodEnd baigiasi CET/CEST pristatymo dienos riba, kad API tikrai
    grąžintų pilnus tikslinės dienos duomenis.
    """
    midnight_utc = datetime(target_date.year, target_date.month, target_date.day)
    lt_offset = lt_utc_offset(midnight_utc)
    cet_bnd = cet_boundary_hour(midnight_utc)

    # periodStart: LT vidurnaktis (21:00 EEST / 22:00 EET) arba CET riba — kas ankščiau
    start_hour = min(24 - lt_offset, cet_bnd)
    period_start = (target_date - timedelta(days=1)).strftime("%Y%m%d") + f"{start_hour:02d}00"

    # periodEnd: CET/CEST pristatymo dienos pabaiga (22:00 CEST / 23:00 CET)
    period_end = target_date.strftime("%Y%m%d") + f"{cet_bnd:02d}00"

    return period_start, period_end


def fetch_entsoe(params: dict) -> Optional[ET.Element]:
    params["securityToken"] = TOKEN
    try:
        resp = requests.get(ENTSOE_URL, params=params, timeout=30)
        doc_type = params.get("documentType", "?")
        domain = params.get("in_Domain") or params.get("outBiddingZone_Domain") or "?"
        if resp.status_code == 400:
            # API grąžino „No matching data" — tai normalu kai duomenų nėra
            print(f"  ⚠ {doc_type} {domain[:12]}: 400 (nėra duomenų)", file=sys.stderr)
            return None
        if resp.status_code != 200:
            print(f"  ⚠ {doc_type} {domain[:12]}: HTTP {resp.status_code}", file=sys.stderr)
            print(f"    Atsakymas: {resp.text[:300]}", file=sys.stderr)
        resp.raise_for_status()
        return ET.fromstring(resp.text)
    except requests.exceptions.HTTPError:
        return None
    except Exception as e:
        print(f"  ⚠ Klaida ({doc_type} {domain[:12]}): {e}", file=sys.stderr)
        return None


def parse_timeseries(root, target_date, value_tag="price.amount"):
    """Universalus XML parser — tinka ir kainoms, ir srautams."""
    ns = {"ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"}
    hourly = {}
    target = target_date.date()

    for ts in root.findall("ns:TimeSeries", ns):
        for period in ts.findall("ns:Period", ns):
            start_str = period.find("ns:timeInterval/ns:start", ns).text
            resolution = period.find("ns:resolution", ns).text
            dt_utc = datetime.strptime(start_str, "%Y-%m-%dT%H:%MZ")

            if resolution == "PT15M":
                quarter = {}
                for point in period.findall("ns:Point", ns):
                    pos = int(point.find("ns:position", ns).text)
                    val_el = point.find(f"ns:{value_tag}", ns)
                    if val_el is None:
                        # Bandome be namespace
                        val_el = point.find(value_tag)
                    if val_el is None:
                        continue
                    val = float(val_el.text)
                    quarter.setdefault((pos - 1) // 4, []).append(val)
                for hi, vals in quarter.items():
                    ts_utc = dt_utc + timedelta(hours=hi)
                    ts_lt = ts_utc + timedelta(hours=lt_utc_offset(ts_utc))
                    if ts_lt.date() == target:
                        hourly[ts_lt.hour] = round(sum(vals) / len(vals), 2)
            elif resolution == "PT60M":
                for point in period.findall("ns:Point", ns):
                    pos = int(point.find("ns:position", ns).text)
                    val_el = point.find(f"ns:{value_tag}", ns)
                    if val_el is None:
                        val_el = point.find(value_tag)
                    if val_el is None:
                        continue
                    val = float(val_el.text)
                    ts_utc = dt_utc + timedelta(hours=pos - 1)
                    ts_lt = ts_utc + timedelta(hours=lt_utc_offset(ts_utc))
                    if ts_lt.date() == target:
                        hourly[ts_lt.hour] = round(val, 2)

    return hourly


def fetch_prices(domain: str, target_date: datetime) -> dict:
    period_start, period_end = get_period(target_date)
    root = fetch_entsoe({
        "documentType": "A44",
        "in_Domain": domain, "out_Domain": domain,
        "periodStart": period_start, "periodEnd": period_end,
    })
    if root is None:
        return {}

    # Debug: XML struktūros analizė
    ns = {"ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"}
    ts_list = root.findall("ns:TimeSeries", ns)
    zone_name = [k for k, v in ZONES.items() if v == domain]
    zone_label = zone_name[0] if zone_name else domain[:12]
    print(f"  DEBUG {zone_label}: root tag={root.tag}, {len(ts_list)} TimeSeries", file=sys.stderr)

    # Jei namespace nesutampa, bandome be namespace
    if not ts_list:
        ts_list_noNS = root.findall("TimeSeries")
        print(f"  DEBUG {zone_label}: be NS: {len(ts_list_noNS)} TimeSeries", file=sys.stderr)
        # Parodome root namespace
        print(f"  DEBUG {zone_label}: root.tag = '{root.tag}'", file=sys.stderr)

    for i, ts in enumerate(ts_list):
        periods = ts.findall("ns:Period", ns)
        for j, period in enumerate(periods):
            start_el = period.find("ns:timeInterval/ns:start", ns)
            end_el = period.find("ns:timeInterval/ns:end", ns)
            res_el = period.find("ns:resolution", ns)
            pts = period.findall("ns:Point", ns)
            start_t = start_el.text if start_el is not None else "MISSING"
            end_t = end_el.text if end_el is not None else "MISSING"
            res_t = res_el.text if res_el is not None else "MISSING"
            print(f"    TS{i}.P{j}: start={start_t} end={end_t} res={res_t} points={len(pts)}", file=sys.stderr)

            # Pirmųjų 3 taškų detalės
            for pt in pts[:3]:
                pos_el = pt.find("ns:position", ns)
                price_el = pt.find("ns:price.amount", ns)
                pos_v = pos_el.text if pos_el is not None else "?"
                price_v = price_el.text if price_el is not None else "MISSING"
                print(f"      pos={pos_v} price.amount={price_v}", file=sys.stderr)

    return parse_timeseries(root, target_date, "price.amount")


def fetch_flow(from_domain: str, to_domain: str, target_date: datetime) -> dict:
    period_start, period_end = get_period(target_date)
    root = fetch_entsoe({
        "documentType": "A11", "processType": "A16",
        "in_Domain": from_domain, "out_Domain": to_domain,
        "periodStart": period_start, "periodEnd": period_end,
    })
    if root is None:
        return {}
    return parse_timeseries(root, target_date, "quantity")


def fetch_load(domain: str, target_date: datetime) -> dict:
    """Faktinis vartojimas (Actual Total Load) — documentType A65."""
    period_start, period_end = get_period(target_date)
    root = fetch_entsoe({
        "documentType": "A65", "processType": "A16",
        "outBiddingZone_Domain": domain,
        "periodStart": period_start, "periodEnd": period_end,
    })
    if root is None:
        return {}
    return parse_timeseries(root, target_date, "quantity")


def fetch_generation_by_type(domain: str, target_date: datetime) -> dict:
    """Faktinė gamyba pagal tipą (Actual Generation per Type) — documentType A75.
    Grąžina dict: {"solar": {h: MW}, "wind": {h: MW}, "other": {h: MW}, "total": {h: MW}}
    """
    period_start, period_end = get_period(target_date)
    root = fetch_entsoe({
        "documentType": "A75", "processType": "A16",
        "in_Domain": domain,
        "periodStart": period_start, "periodEnd": period_end,
    })
    if root is None:
        return {"solar": {}, "wind": {}, "other": {}, "total": {}}

    ns = {"ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"}
    target = target_date.date()

    # PSR tipai
    SOLAR_TYPES = {"B16"}          # Solar
    WIND_TYPES = {"B18", "B19"}    # Wind Offshore, Wind Onshore
    # Visa kita — biomass, fossil, hydro, nuclear, etc.

    by_type = defaultdict(lambda: defaultdict(float))  # {psr_type: {hour: MW}}

    for ts in root.findall("ns:TimeSeries", ns):
        psr_el = ts.find("ns:MktPSRType/ns:psrType", ns)
        psr_type = psr_el.text if psr_el is not None else "UNKNOWN"

        for period in ts.findall("ns:Period", ns):
            start_str = period.find("ns:timeInterval/ns:start", ns).text
            resolution = period.find("ns:resolution", ns).text
            dt_utc = datetime.strptime(start_str, "%Y-%m-%dT%H:%MZ")

            if resolution == "PT15M":
                quarter = defaultdict(list)
                for point in period.findall("ns:Point", ns):
                    pos = int(point.find("ns:position", ns).text)
                    val_el = point.find("ns:quantity", ns)
                    if val_el is None:
                        continue
                    quarter[(pos - 1) // 4].append(float(val_el.text))
                for hi, vals in quarter.items():
                    ts_utc = dt_utc + timedelta(hours=hi)
                    ts_lt = ts_utc + timedelta(hours=lt_utc_offset(ts_utc))
                    if ts_lt.date() == target:
                        by_type[psr_type][ts_lt.hour] += round(sum(vals) / len(vals), 2)
            elif resolution == "PT60M":
                for point in period.findall("ns:Point", ns):
                    pos = int(point.find("ns:position", ns).text)
                    val_el = point.find("ns:quantity", ns)
                    if val_el is None:
                        continue
                    val = float(val_el.text)
                    ts_utc = dt_utc + timedelta(hours=pos - 1)
                    ts_lt = ts_utc + timedelta(hours=lt_utc_offset(ts_utc))
                    if ts_lt.date() == target:
                        by_type[psr_type][ts_lt.hour] += round(val, 2)

    # Grupuojame į solar / wind / other
    solar = defaultdict(float)
    wind = defaultdict(float)
    other = defaultdict(float)
    total = defaultdict(float)

    for psr, hourly in by_type.items():
        for h, mw in hourly.items():
            if psr in SOLAR_TYPES:
                solar[h] += mw
            elif psr in WIND_TYPES:
                wind[h] += mw
            else:
                other[h] += mw
            total[h] += mw

    return {"solar": dict(solar), "wind": dict(wind),
            "other": dict(other), "total": dict(total)}


# ─── ANALIZĖ ─────────────────────────────────────────────────────────────────

def zone_stats(hourly: dict) -> dict:
    if not hourly:
        return {"avg": None, "min": None, "max": None, "min_hour": None, "max_hour": None, "spread": None}
    prices = list(hourly.values())
    avg = round(sum(prices) / len(prices), 2)
    min_p = min(prices)
    max_p = max(prices)
    min_h = [h for h, p in hourly.items() if p == min_p][0]
    max_h = [h for h, p in hourly.items() if p == max_p][0]
    return {"avg": avg, "min": min_p, "max": max_p,
            "min_hour": f"{min_h:02d}:00", "max_hour": f"{max_h:02d}:00",
            "spread": round(max_p - min_p, 2)}


def compute_intervals(hourly: dict) -> dict:
    def avg(hours):
        vals = [hourly[h] for h in hours if h in hourly]
        return round(sum(vals) / len(vals), 2) if vals else None
    return {
        "night": avg([0, 1, 2, 3, 4, 5]), "m_ramp": avg([6]),
        "m_peak": avg([7, 8, 9]), "dip": avg([10, 11, 12, 13, 14, 15]),
        "a_ramp": avg([16, 17]), "e_peak": avg([18, 19, 20, 21]),
        "l_eve": avg([22, 23]),
    }


def classify_lt(target_date: datetime, intervals: dict, daily_avg: float) -> dict:
    daytype = "Weekend" if target_date.weekday() >= 5 else "Weekday"
    month = target_date.month
    if month in [12, 1, 2]: calendar_season = "Winter"
    elif month in [3, 4, 5]: calendar_season = "Spring"
    elif month in [6, 7, 8]: calendar_season = "Summer"
    else: calendar_season = "Autumn"

    dip = intervals.get("dip") or 0
    dip_ratio = dip / daily_avg if daily_avg and daily_avg > 5 else 0

    if daily_avg and daily_avg < 5:
        detected_season = calendar_season
    elif dip_ratio > 1.0:
        detected_season = "Winter"
    elif dip_ratio > 0.8:
        detected_season = "Autumn"
    elif daily_avg and daily_avg > 80 and dip < 50:
        detected_season = "Spring"
    elif daily_avg and daily_avg < 55:
        detected_season = "Summer"
    else:
        detected_season = calendar_season

    ref_ratios = {}
    for name, prof in REFERENCE_PROFILES.items():
        a = prof["daily_avg"]
        ref_ratios[name] = {k: round(v / a, 2) for k, v in prof.items() if k != "daily_avg"}

    today_ratios = {k: round((intervals.get(k) or 0) / daily_avg, 2)
                    if daily_avg and daily_avg > 5 else 0 for k in INTERVAL_KEYS}

    distances = {}
    for pname, rr in ref_ratios.items():
        dist = sum(abs(today_ratios[k] - rr[k]) for k in INTERVAL_KEYS)
        distances[pname] = round(dist, 3)

    matching = {k: v for k, v in distances.items() if daytype in k}
    calendar_key = f"{calendar_season} {daytype}"

    if daily_avg and daily_avg < 5 and calendar_key in distances:
        best = calendar_key
        confidence = "Žemas (ekstremalios kainos)"
    elif matching:
        best = min(matching, key=matching.get)
        confidence = "Aukštas" if distances[best] < 1.5 else "Vidutinis" if distances[best] < 2.0 else "Žemas"
    else:
        best = min(distances, key=distances.get)
        confidence = "Žemas"

    ref = REFERENCE_PROFILES[best]
    anomalies = []
    for k in INTERVAL_KEYS:
        t = intervals.get(k) or 0
        r = ref[k]
        if r:
            dev = (t - r) / r * 100
            if abs(dev) > 30:
                label, hours = INTERVAL_LABELS[k]
                anomalies.append({"intervalas": label, "valandos": hours,
                                  "šiandien": round(t, 1), "nuoroda": r,
                                  "nuokrypis": round(dev, 1)})

    return {
        "tipas": best, "pasitikėjimas": confidence,
        "kreivė": CURVE_DESCRIPTIONS.get(best, ""), "anomalijos": anomalies,
    }


def compute_net_flows(flow_pairs: dict) -> dict:
    results = {}
    connectors = [
        ("NordBalt (LT↔SE4)", "LT→SE4", "SE4→LT"),
        ("LitPol Link (LT↔PL)", "LT→PL", "PL→LT"),
        ("LT↔LV", "LT→LV", "LV→LT"),
    ]
    for name, export_key, import_key in connectors:
        export_data = flow_pairs.get(export_key, {})
        import_data = flow_pairs.get(import_key, {})

        if not export_data and not import_data:
            results[name] = {"net_avg_mw": None, "kryptis": "nėra duomenų"}
            continue

        all_hours = set(list(export_data.keys()) + list(import_data.keys()))
        net_hourly = {}
        for h in all_hours:
            imp = import_data.get(h, 0)
            exp = export_data.get(h, 0)
            net_hourly[h] = round(imp - exp, 1)

        if net_hourly:
            vals = list(net_hourly.values())
            net_avg = round(sum(vals) / len(vals), 1)
            direction = "importas į LT" if net_avg > 0 else "eksportas iš LT" if net_avg < 0 else "subalansuota"
            results[name] = {
                "net_avg_mw": net_avg, "kryptis": direction,
                "max_importas": round(max(vals), 1),
                "max_eksportas": round(min(vals), 1),
                "valandinis": net_hourly,
            }
        else:
            results[name] = {"net_avg_mw": None, "kryptis": "nėra duomenų"}

    return results


def compute_gen_load_summary(load_hourly: dict, gen_data: dict) -> dict:
    """Apskaičiuoja vidutinį vartojimą, gamybą ir gamybos struktūrą."""
    def avg_mw(hourly):
        vals = list(hourly.values())
        return round(sum(vals) / len(vals), 1) if vals else None

    load_avg = avg_mw(load_hourly)
    gen_total_avg = avg_mw(gen_data.get("total", {}))
    gen_solar_avg = avg_mw(gen_data.get("solar", {}))
    gen_wind_avg = avg_mw(gen_data.get("wind", {}))
    gen_other_avg = avg_mw(gen_data.get("other", {}))

    # Apsirūpinimo dalis
    self_sufficiency = None
    if load_avg and gen_total_avg and load_avg > 0:
        self_sufficiency = round(gen_total_avg / load_avg * 100, 1)

    return {
        "vartojimas_avg_mw": load_avg,
        "gamyba_avg_mw": gen_total_avg,
        "saulė_avg_mw": gen_solar_avg,
        "vėjas_avg_mw": gen_wind_avg,
        "kita_avg_mw": gen_other_avg,
        "apsirūpinimas_%": self_sufficiency,
        "valandinis_vartojimas": load_hourly,
        "valandinė_gamyba": gen_data,
    }


# ─── ATASKAITA ────────────────────────────────────────────────────────────────

def generate_briefing(target_date: datetime, output_json: bool = False):
    date_str = target_date.strftime("%Y-%m-%d")
    dow = DAY_NAMES_LT[target_date.weekday()]

    print(f"Renku duomenis: {date_str} ({dow})...", file=sys.stderr)

    # 1. Kainos
    print("  → Kainos...", file=sys.stderr)
    zone_prices = {}
    for zone, domain in ZONES.items():
        print(f"    {zone}...", end=" ", file=sys.stderr, flush=True)
        zone_prices[zone] = fetch_prices(domain, target_date)
        n = len(zone_prices[zone])
        print(f"{n} val.", file=sys.stderr)

    # 2. Srautai
    print("  → Tarpsisteminiai srautai...", file=sys.stderr)
    flow_pairs = {}
    flow_map = {
        "LT→SE4": (ZONES["LT"], ZONES["SE4"]),
        "SE4→LT": (ZONES["SE4"], ZONES["LT"]),
        "LT→PL":  (ZONES["LT"], ZONES["PL"]),
        "PL→LT":  (ZONES["PL"], ZONES["LT"]),
        "LT→LV":  (ZONES["LT"], ZONES["LV"]),
        "LV→LT":  (ZONES["LV"], ZONES["LT"]),
    }
    for label, (from_d, to_d) in flow_map.items():
        print(f"    {label}...", end=" ", file=sys.stderr, flush=True)
        flow_pairs[label] = fetch_flow(from_d, to_d, target_date)
        n = len(flow_pairs[label])
        print(f"{n} val.", file=sys.stderr)

    # 3. LT vartojimas ir gamyba
    print("  → LT vartojimas/gamyba...", file=sys.stderr)
    print("    Vartojimas...", end=" ", file=sys.stderr, flush=True)
    lt_load = fetch_load(ZONES["LT"], target_date)
    print(f"{len(lt_load)} val.", file=sys.stderr)
    print("    Gamyba pagal tipą...", end=" ", file=sys.stderr, flush=True)
    lt_gen = fetch_generation_by_type(ZONES["LT"], target_date)
    print(f"{len(lt_gen.get('total', {}))} val.", file=sys.stderr)

    # 4. Analizė
    print("  → Analizė...", file=sys.stderr)

    zone_summaries = {}
    for zone in ZONES:
        zone_summaries[zone] = zone_stats(zone_prices[zone])

    lt_intervals = compute_intervals(zone_prices.get("LT", {}))
    lt_avg = zone_summaries["LT"]["avg"]
    lt_class = classify_lt(target_date, lt_intervals, lt_avg) if lt_avg else None

    net_flows = compute_net_flows(flow_pairs)
    gen_load = compute_gen_load_summary(lt_load, lt_gen)

    # 5. JSON išvestis
    if output_json:
        report = {
            "data": date_str, "savaitės_diena": dow,
            "zonos": {}, "lt_klasifikacija": lt_class, "lt_intervalai": {},
            "tarpsisteminiai_srautai": {},
            "lt_gamyba_vartojimas": {
                "vartojimas_avg_mw": gen_load["vartojimas_avg_mw"],
                "gamyba_avg_mw": gen_load["gamyba_avg_mw"],
                "saulė_avg_mw": gen_load["saulė_avg_mw"],
                "vėjas_avg_mw": gen_load["vėjas_avg_mw"],
                "kita_avg_mw": gen_load["kita_avg_mw"],
                "apsirūpinimas_%": gen_load["apsirūpinimas_%"],
            },
        }
        for zone in ZONES:
            report["zonos"][zone] = {
                "valandinės": {f"{h:02d}:00": zone_prices[zone].get(h) for h in range(24)},
                "statistika": zone_summaries[zone],
            }
        ref = REFERENCE_PROFILES.get(lt_class["tipas"], {}) if lt_class else {}
        for k in INTERVAL_KEYS:
            label, hours = INTERVAL_LABELS[k]
            report["lt_intervalai"][label] = {
                "valandos": hours, "kaina": lt_intervals.get(k), "nuoroda": ref.get(k),
            }
        for name, data in net_flows.items():
            report["tarpsisteminiai_srautai"][name] = {
                "vidutinis_mw": data.get("net_avg_mw"), "kryptis": data.get("kryptis"),
                "max_importas_mw": data.get("max_importas"),
                "max_eksportas_mw": data.get("max_eksportas"),
            }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    # 5. Tekstinė ataskaita
    L = []
    L.append("═" * 65)
    L.append(f"  Lietuvos elektros rinkos kainų brifingas")
    L.append("═" * 65)
    L.append(f"  Data:  {date_str} ({dow})")
    if lt_class:
        L.append(f"  Tipas: {lt_class['tipas']} (pasitikėjimas: {lt_class['pasitikėjimas']})")
        L.append(f"  Kreivė: {lt_class['kreivė']}")
    L.append("")

    # Zonų suvestinė
    L.append("─" * 65)
    L.append("  Kainų suvestinė (€/MWh)")
    L.append("─" * 65)
    L.append(f"  {'Zona':<6} {'Vidurkis':>10} {'Min':>10} {'(val.)':>7} {'Max':>10} {'(val.)':>7} {'Spread':>8}")
    L.append(f"  {'─'*6} {'─'*10} {'─'*10} {'─'*7} {'─'*10} {'─'*7} {'─'*8}")
    for zone in ZONES:
        s = zone_summaries[zone]
        if s["avg"] is not None:
            L.append(f"  {zone:<6} {s['avg']:>10.2f} {s['min']:>10.2f} {s['min_hour']:>7} {s['max']:>10.2f} {s['max_hour']:>7} {s['spread']:>8.2f}")
        else:
            L.append(f"  {zone:<6} {'duomenų nėra':>40}")
    L.append("")

    # LT 7 intervalai
    if lt_class and lt_avg:
        ref = REFERENCE_PROFILES.get(lt_class["tipas"], {})
        L.append("─" * 65)
        L.append(f"  LT intervalai vs nuoroda ({lt_class['tipas']})")
        L.append("─" * 65)
        L.append(f"  {'Intervalas':<20} {'Val.':>6} {'Šiandien':>10} {'Nuoroda':>10} {'Nuokr.':>8}")
        L.append(f"  {'─'*20} {'─'*6} {'─'*10} {'─'*10} {'─'*8}")
        for k in INTERVAL_KEYS:
            label, hours = INTERVAL_LABELS[k]
            today_val = lt_intervals.get(k) or 0
            ref_val = ref.get(k, 0)
            dev = (today_val - ref_val) / ref_val * 100 if ref_val else 0
            flag = " ⚠" if abs(dev) > 30 else ""
            L.append(f"  {label:<20} {hours:>6} {today_val:>10.1f} {ref_val:>10.1f} {dev:>+7.1f}%{flag}")

        if lt_class["anomalijos"]:
            L.append("")
            L.append("  ⚠ Anomalijos:")
            for a in lt_class["anomalijos"]:
                kryptis = "virš" if a["nuokrypis"] > 0 else "žemiau"
                L.append(f"    • {a['intervalas']} ({a['valandos']}): €{a['šiandien']:.1f} — "
                         f"{abs(a['nuokrypis']):.0f}% {kryptis} nuorodos (€{a['nuoroda']})")
    L.append("")

    # Tarpsisteminiai srautai
    L.append("─" * 65)
    L.append("  Tarpsisteminiai srautai (LT perspektyva)")
    L.append("─" * 65)
    L.append(f"  {'Jungtis':<25} {'Vid. MW':>10} {'Kryptis':>20} {'Max imp.':>10} {'Max eksp.':>10}")
    L.append(f"  {'─'*25} {'─'*10} {'─'*20} {'─'*10} {'─'*10}")
    for name, data in net_flows.items():
        if data.get("net_avg_mw") is not None:
            L.append(f"  {name:<25} {data['net_avg_mw']:>+10.1f} {data['kryptis']:>20} "
                     f"{data.get('max_importas', 0):>+10.1f} {data.get('max_eksportas', 0):>+10.1f}")
        else:
            L.append(f"  {name:<25} {'duomenų nėra':>30}")
    L.append("")

    # LT gamyba ir vartojimas
    L.append("─" * 65)
    L.append("  LT vietinė gamyba ir vartojimas (MW)")
    L.append("─" * 65)
    gl = gen_load
    has_data = gl["vartojimas_avg_mw"] is not None or gl["gamyba_avg_mw"] is not None
    if has_data:
        # Vartojimas
        if gl["vartojimas_avg_mw"] is not None:
            L.append(f"  Vidutinis vartojimas:      {gl['vartojimas_avg_mw']:>8.1f} MW")
        else:
            L.append(f"  Vidutinis vartojimas:      duomenų nėra")

        # Gamyba
        gen_t = gl["gamyba_avg_mw"]
        if gen_t is not None and gen_t > 0:
            L.append(f"  Vidutinė gamyba (viso):    {gen_t:>8.1f} MW")
            for label, key, tree in [("Saulė", "saulė_avg_mw", "├─"),
                                     ("Vėjas", "vėjas_avg_mw", "├─"),
                                     ("Kita",  "kita_avg_mw",  "└─")]:
                val = gl[key] or 0
                pct = val / gen_t * 100 if gen_t else 0
                L.append(f"    {tree} {label + ':':<20} {val:>8.1f} MW  ({pct:>5.1f}%)")
        else:
            L.append(f"  Vidutinė gamyba:           duomenų nėra")

        # Apsirūpinimas
        if gl["apsirūpinimas_%"] is not None:
            L.append(f"  Apsirūpinimas:             {gl['apsirūpinimas_%']:>7.1f}%")
    else:
        L.append("  Duomenų nėra")
    L.append("")

    # Valandinės kainos
    L.append("─" * 65)
    L.append("  Valandinės kainos (€/MWh, Lietuvos laikas)")
    L.append("─" * 65)
    L.append(f"  {'Val.':>5}  {'LT':>8}  {'LV':>8}  {'PL':>8}  {'SE4':>8}")
    L.append(f"  {'─'*5}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}")
    for h in range(24):
        vals = []
        for zone in ZONES:
            p = zone_prices[zone].get(h)
            vals.append(f"{p:>8.2f}" if p is not None else f"{'—':>8}")
        L.append(f"  {h:02d}:00  {'  '.join(vals)}")

    L.append("")
    L.append("═" * 65)
    L.append(f"  Šaltinis: ENTSO-E Transparency Platform")
    L.append(f"  Sugeneruota: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    L.append("═" * 65)

    print("\n".join(L))


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    target_date = None
    output_json = False

    for arg in sys.argv[1:]:
        if arg == "--json":
            output_json = True
        elif arg in ("--help", "-h"):
            print(__doc__)
            sys.exit(0)
        else:
            try:
                target_date = datetime.strptime(arg, "%Y-%m-%d")
            except ValueError:
                print(f"Klaida: netinkamas datos formatas '{arg}'. Naudok YYYY-MM-DD.")
                sys.exit(1)

    if target_date is None:
        target_date = datetime.now()

    generate_briefing(target_date, output_json)


if __name__ == "__main__":
    main()
