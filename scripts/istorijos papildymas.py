#!/usr/bin/env python3
"""
Kasdienis istorinių duomenų papildymas — MB Enerconsult
========================================================
Kas rytą papildo kainos veiksnių modelio duomenis (data/istorija/) paskutinių
dienų ir šiandienos kitos paros duomenimis, kad „rinkos situacija.py“ galėtų
apskaičiuoti šios dienos normas ir veiksnius.

Ką paima (valandomis, UTC):
  • kitos paros kainos: LT, LV, EE, FI, SE4, PL (ENTSO-E) ir LT, LV, EE, FI (Elering);
  • LT vartojimas: faktas ir kitos paros prognozė;
  • LT gamyba pagal tipą (faktas) ir vėjo bei saulės kitos paros prognozė;
  • LT jungtys su SE4, PL ir LV: kitos paros prekybos grafikai ir rinkai siūlomi pralaidumai
    (ENTSO-E) bei Litgrid skelbiami kitos paros pralaidumai prekybai (litgrid-pralaidumai.csv,
    kuriuos modelis naudoja pirmiausia);
  • dujos (TTF) ir CO2 (EUA) – dienos reikšmės iki vakar.

Didieji istoriniai failai (*.csv.gz) nekeičiami: naujos valandos rašomos į
nesuspaustus failus data/istorija/papildymai/<pavadinimas>.csv, kad repozitorija
neaugtų po kelis MB per dieną. Modelis juos sujungia skaitydamas.

Naudojimas:
    python "scripts/istorijos papildymas.py"              # ataskaitos diena – šiandien (Vilniaus laiku)
    python "scripts/istorijos papildymas.py" 2026-11-16   # nurodyta ataskaitos diena

Aplinkos kintamasis: ENTSOE_TOKEN
"""

import csv
import importlib.util
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ir", os.path.join(HERE, "istorijos rinkimas.py"))
ir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ir)

D = os.path.join(HERE, "..", "data", "istorija")
PAP = os.path.join(D, "papildymai")
VLN = ZoneInfo("Europe/Vilnius")
MAX_BACK_DAYS = 30          # jei papildymų ilgai nebuvo – daugiausia tiek dienų atgal

ZONES_PRICE = ("LT", "LV", "EE", "FI", "SE4", "PL")
LT_BORDERS = [("LT", "SE4"), ("LT", "PL"), ("LT", "LV")]


def tasks() -> list[tuple[str, dict, str]]:
    """Tik tos ENTSO-E eilutės, kurių reikia modeliui ir ataskaitai."""
    E = ir.EIC
    t = []
    for z in ZONES_PRICE:
        t.append((f"kaina_{z}", {"documentType": "A44", "in_Domain": E[z], "out_Domain": E[z]}, "none"))
    t.append(("apkrova_LT", {"documentType": "A65", "processType": "A16", "outBiddingZone_Domain": E["LT"]}, "none"))
    t.append(("apkrova_progn_LT", {"documentType": "A65", "processType": "A01", "outBiddingZone_Domain": E["LT"]}, "none"))
    t.append(("gamyba_LT", {"documentType": "A75", "processType": "A16", "in_Domain": E["LT"]}, "psr"))
    t.append(("progn_LT", {"documentType": "A69", "processType": "A01", "in_Domain": E["LT"]}, "psr"))
    for a, b in LT_BORDERS:
        for frm, to in ((a, b), (b, a)):
            dom = {"in_Domain": E[to], "out_Domain": E[frm]}
            t.append((f"prekyba_{frm}_{to}", dict(dom, documentType="A09", **{"contract_MarketAgreement.Type": "A01"}), "none"))
            t.append((f"siulomas_{frm}_{to}", dict(dom, documentType="A31",
                      **{"Auction.Type": "A01", "contract_MarketAgreement.Type": "A01"}), "none"))
    return t


def read_merged(name: str) -> pd.DataFrame:
    """Istorinis failas kartu su jau esamais papildymais (be laiko juostos, UTC)."""
    base = pd.read_csv(os.path.join(D, f"{name}.csv.gz"), index_col=0, parse_dates=True)
    p = os.path.join(PAP, f"{name}.csv")
    if os.path.exists(p):
        extra = pd.read_csv(p, index_col=0, parse_dates=True)
        if len(extra):
            base = extra.combine_first(base)
    return base


def last_complete_hour(df: pd.DataFrame, cols: list[str]) -> datetime | None:
    cols = [c for c in cols if c in df.columns]
    if not cols:
        return None
    s = df[cols].notna().any(axis=1)
    s = s[s]
    return s.index.max().to_pydatetime() if len(s) else None


def write_pap(name: str, series: dict[str, dict[datetime, float]]) -> int:
    """Naujas reikšmes įrašo į papildymų failą; esamos tų pačių valandų reikšmės perrašomos."""
    series = {k: v for k, v in series.items() if v}
    if not series:
        ir.log(f"{name}: naujų duomenų nėra")
        return 0
    new = pd.DataFrame({k: pd.Series(v) for k, v in series.items()})
    new.index = pd.to_datetime(new.index)
    os.makedirs(PAP, exist_ok=True)
    p = os.path.join(PAP, f"{name}.csv")
    if os.path.exists(p):
        old = pd.read_csv(p, index_col=0, parse_dates=True)
        new = new.combine_first(old)
    new = new.sort_index()
    new = new[sorted(new.columns)]
    new.index.name = "utc"
    new.to_csv(p, date_format="%Y-%m-%dT%H:%M", float_format="%.3f")
    ir.log(f"{name}: papildymuose {len(new)} val., {new.index.min():%Y-%m-%d %H:%M} – {new.index.max():%Y-%m-%d %H:%M} UTC")
    return len(series)


def update_litgrid(day: date, budget_s: int = 150) -> None:
    """Litgrid kitos paros pralaidumai prekybai: nuo paskutinės turimos dienos iki rytojaus.
    Skydelis riboja užklausų dažnį (HTTP 429), todėl imama ne daugiau kaip savaitė per užklausą
    ir laikomasi laiko ribos, kad nevėlintų rytinės ataskaitos."""
    path = os.path.join(D, "litgrid-pralaidumai.csv")
    data: dict[str, list[str]] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for r_ in list(csv.reader(f))[1:]:
                if r_:
                    data[r_[0]] = r_[1:7]
    last = date.fromisoformat(max(data)[:10]) if data else day - timedelta(days=35)
    cur = max(last - timedelta(days=1), day - timedelta(days=35))
    end = day + timedelta(days=1)
    t0, n_new = time.time(), 0
    while cur <= end and time.time() - t0 < budget_s:
        to = min(cur + timedelta(days=6), end)
        part = {}
        for attempt in range(2):
            try:
                code, text = ir.litgrid_export(ir.LITGRID_CAT["da_pralaidumai"], cur, to)
            except Exception as e:      # noqa: BLE001
                code, text = 0, f"{type(e).__name__}: {ir.clean(e)[:160]}"
            if code == 200:
                part = ir.litgrid_parse(text)
                break
            ir.log(f"Litgrid {cur}…{to}: {code} {text[:100]!r}, bandymas {attempt + 1}")
            if time.time() - t0 + 30 > budget_s:
                break
            time.sleep(30)
        if not part:
            break
        n_new += sum(1 for k in part if data.get(k) != part[k])
        data.update(part)
        ir.log(f"Litgrid {cur}…{to}: eilučių {len(part)}")
        cur = to + timedelta(days=1)
        if cur <= end:
            time.sleep(20)
    if n_new:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["laikas"] + ir.LITGRID_COLS)
            for k in sorted(data):
                w.writerow([k] + data[k])
    ir.log(f"Litgrid pralaidumai: naujų ar pakeistų eilučių {n_new}, iš viso {len(data)}, paskutinė {max(data, default='-')}")


def update_daily(day: date) -> None:
    """TTF ir EUA dienos reikšmės iki vakar – į dienos.csv ir dienos-ilga.csv."""
    ttf = ir.yahoo_daily("TTF=F", "3mo")
    eua = ir.eex_eua(sorted({day.year, (day - timedelta(days=40)).year}))
    ttf = {d: v for d, v in ttf.items() if d < day}
    eua = {d: v for d, v in eua.items() if d < day}
    ir.log(f"TTF {len(ttf)} d. (paskutinė {max(ttf, default='-')}), EUA {len(eua)} d. (paskutinė {max(eua, default='-')})")
    if not ttf and not eua:
        return
    for fname in ("dienos.csv", "dienos-ilga.csv"):
        path = os.path.join(D, fname)
        rows: dict[str, list[str]] = {}
        with open(path, encoding="utf-8") as f:
            r = csv.reader(f)
            header = next(r)
            for row in r:
                if row:
                    rows[row[0]] = row[1:]
        i_ttf, i_eua = header.index("ttf_eur_mwh") - 1, header.index("eua_eur_t") - 1
        for d in sorted(set(ttf) | set(eua)):
            key = d.isoformat()
            cur = rows.get(key, [""] * (len(header) - 1))
            if d in ttf:
                cur[i_ttf] = f"{ttf[d]:.3f}"
            if d in eua:
                cur[i_eua] = f"{eua[d]:.2f}"
            rows[key] = cur
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            for key in sorted(rows):
                w.writerow([key] + rows[key])


def main() -> None:
    if not ir.TOKEN:
        print("Klaida: nenustatytas ENTSOE_TOKEN.", file=sys.stderr)
        sys.exit(1)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    day = date.fromisoformat(args[0]) if args else datetime.now(VLN).date()

    # laikotarpis UTC: nuo 3 dienų prieš ataskaitos dieną iki jos pabaigos (+ atsarga)
    start = datetime(day.year, day.month, day.day) - timedelta(days=4)
    end = datetime(day.year, day.month, day.day) + timedelta(days=1, hours=23)
    have = last_complete_hour(read_merged("kainos"), ["kaina_LT", "elering_LT"])
    if have is not None and have < start:
        start = max(have - timedelta(days=1), datetime(day.year, day.month, day.day) - timedelta(days=MAX_BACK_DAYS))
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    ir.log(f"Ataskaitos diena {day}; laikotarpis {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UTC")

    results: dict[str, dict[datetime, float]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(ir.fetch_series, lbl, prm, [(start, end)], split): lbl for lbl, prm, split in tasks()}
        for fut in as_completed(futs):
            try:
                for k, v in fut.result().items():
                    results.setdefault(k, {}).update(v)
            except Exception as exc:
                ir.log(f"  ⚠ {futs[fut]}: {type(exc).__name__}: {ir.clean(exc)[:200]}")
    results.update(ir.elering_prices(start, end))

    def pick(*prefixes: str) -> dict[str, dict[datetime, float]]:
        return {k: v for k, v in results.items() if k.startswith(prefixes)}

    write_pap("kainos", pick("kaina_", "elering_"))
    write_pap("apkrova", pick("apkrova_"))
    write_pap("gamyba", pick("gamyba_", "progn_"))
    write_pap("jungtys", pick("prekyba_", "siulomas_"))
    update_daily(day)
    try:
        update_litgrid(day)
    except Exception as exc:        # noqa: BLE001 – be pralaidumų ataskaita vis tiek gaminama
        ir.log(f"  ⚠ Litgrid: {type(exc).__name__}: {ir.clean(exc)[:200]}")

    # ar ataskaitos dienai pakanka duomenų (24 val. Lietuvos laiku)
    k = read_merged("kainos")
    a = read_merged("apkrova")
    g = read_merged("gamyba")
    lo = datetime(day.year, day.month, day.day, tzinfo=VLN).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    hi = lo + timedelta(hours=24)
    def n(df, col):
        if col not in df.columns:
            return 0
        s = df[col]
        return int(s[(s.index >= lo) & (s.index < hi)].notna().sum())
    price_h = max(n(k, "kaina_LT"), n(k, "elering_LT"))
    ir.log(f"Ataskaitos dienai: kainų {price_h} val., vartojimo prognozės {n(a, 'apkrova_progn_LT')} val., "
           f"vėjo prognozės {n(g, 'progn_LT_B19')} val., saulės prognozės {n(g, 'progn_LT_B16')} val.")


if __name__ == "__main__":
    main()
