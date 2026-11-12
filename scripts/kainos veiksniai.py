#!/usr/bin/env python3
"""
LT elektros kainos veiksnių analizė — MB Enerconsult
=====================================================
Įvertina, kiek kuris veiksnys lėmė Lietuvos day-ahead kainos nuokrypį nuo normos:

    gamyba (vėjas + saulė) · vartojimas · dujos ir CO2 · pralaidumai · kita

Modelis
    kaina = SRMC × m(likutinė apkrova, pralaidumų ribojimas, paros valanda, darbo d./savaitgalis)

    • SRMC – dujinės elektrinės ribiniai kaštai: TTF/0,55 + EUA·0,202/0,55 + 3 €/MWh
      (išvakarių TTF ir EUA); dujos nustato kainų lygį, todėl veikia kaip daugiklis.
    • likutinė apkrova = vartojimo prognozė − vėjo prognozė − saulės prognozė (LT, kitos paros);
    • pralaidumų ribojimas = kiek trūksta iki įprasto 850 MW importo pralaidumo
      (NordBalt SE4→LT + LitPol PL→LT); mažesni nei 100 MW svyravimai neskaičiuojami;
    • m – monotoniškas gradientinio stiprinimo modelis (didesnė likutinė apkrova ar
      didesnis ribojimas kainos nemažina).

Skaidymas
    Tikslus Shapley skaidymas pagal veiksnių grupes: tikslo valanda lyginama su
    normos valandomis (ta pati paros valanda, to paties tipo dienos per pastarąsias
    28 d.). Veiksnių indėlių suma = modelio kaina − modelio norma; „kita" –
    faktinės kainos nuokrypio dalis, kurios šie veiksniai nepaaiškina
    (kaimynų rinkos, avarijos, kiti veiksniai).

Naudojimas
    python "scripts/kainos veiksniai.py" 2026-11-11          # vienos dienos skaidymas
    python "scripts/kainos veiksniai.py" 2026-11-11 --json
    python "scripts/kainos veiksniai.py" --skalė             # bendra skalė (kryžminė patikra)

Reikia: pandas, numpy, scikit-learn; duomenys – data/istorija/ (žr. „istorijos rinkimas.py").
"""

import itertools
import json
import math
import os
import sys
import warnings
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

warnings.filterwarnings("ignore")

HIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "istorija")
LT_TZ = "Europe/Vilnius"

GAS_EF, ETA, VOM = 0.202, 0.55, 3.0      # tCO2/MWh šilumos, CCGT naudingumas, kint. sąnaudos €/MWh
NOMINAL_IMPORT = 850.0                   # MW: NordBalt 700 + LitPol ~150
CAP_THRESHOLD = 100.0                    # MW: mažesnis trūkumas nelaikomas ribojimu
WINDOW_DAYS = 28                         # normos langas

FEATS = ["rl", "cap_def", "hour", "wknd"]
MONO = [1, 1, 0, 0]
GROUPS = {"dujos": "srmc", "vartojimas": "load_f", "vėjas": "wind_f",
          "saulė": "sol_f", "pralaidumai": "cap_def"}
NAMES = list(GROUPS)
OUT_FACTORS = ["gamyba", "vartojimas", "dujos", "pralaidumai", "kita"]


# ─── DUOMENYS ────────────────────────────────────────────────────────────────

def _read(name: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(HIST_DIR, name), index_col=0, parse_dates=True)
    df.index = df.index.tz_localize("UTC")
    return df


def load_dataset() -> pd.DataFrame:
    """Valandinis rinkinys Lietuvos laiku: kaina, prognozės, pralaidumai, dujų kaštai."""
    k, a, g, j = _read("kainos.csv.gz"), _read("apkrova.csv.gz"), _read("gamyba.csv.gz"), _read("jungtys.csv.gz")
    df = pd.DataFrame(index=k.index)
    df["p"] = k["kaina_LT"].fillna(k.get("elering_LT"))
    df["p_SE4"], df["p_PL"] = k["kaina_SE4"], k["kaina_PL"]
    df["load_f"] = a["apkrova_progn_LT"]
    df["wind_f"] = g["progn_LT_B19"]
    df["sol_f"] = g["progn_LT_B16"]
    # pralaidumų spragos (ENTSO-E skelbia pavėluotai) užpildomos paskutine žinoma reikšme
    df["c_nb"] = j["siulomas_SE4_LT"].ffill(limit=24 * 45)
    df["c_pl"] = j["siulomas_PL_LT"].ffill(limit=24 * 45)
    lt = df.index.tz_convert(LT_TZ)
    df["date"], df["hour"] = lt.date, lt.hour
    df["wknd"] = (lt.dayofweek >= 5).astype(int)
    df["ym"] = lt.strftime("%Y-%m")

    d = pd.read_csv(os.path.join(HIST_DIR, "dienos.csv"), index_col=0, parse_dates=True)
    full = d.reindex(pd.date_range(d.index.min(), d.index.max() + pd.Timedelta(days=5))).ffill()
    full["srmc"] = full["ttf_eur_mwh"] / ETA + full["eua_eur_t"] * GAS_EF / ETA + VOM
    prev = full.shift(1)                                   # aukciono dienos (D−1) reikšmės
    dd = pd.to_datetime(df["date"])
    df["ttf"] = prev["ttf_eur_mwh"].reindex(dd).to_numpy()
    df["srmc"] = prev["srmc"].reindex(dd).to_numpy()

    df["rl"] = df["load_f"] - df["wind_f"] - df["sol_f"]
    deficit = (NOMINAL_IMPORT - (df["c_nb"] + df["c_pl"])).clip(lower=0)
    df["cap_def"] = deficit.where(deficit >= CAP_THRESHOLD, 0.0)
    df = df.dropna(subset=["p", "srmc", "load_f", "wind_f", "sol_f", "cap_def"])
    df = df[~df.index.duplicated()].copy()
    df["pos"] = np.arange(len(df))
    return df


# ─── MODELIS ─────────────────────────────────────────────────────────────────

def fit(train: pd.DataFrame) -> HistGradientBoostingRegressor:
    """m = kaina/SRMC; svoriai SRMC² – kad būtų mažinama kainos (ne santykio) paklaida."""
    m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.06, max_leaf_nodes=15,
                                      min_samples_leaf=40, l2_regularization=1.0,
                                      monotonic_cst=MONO, random_state=0)
    return m.fit(train[FEATS], train["p"] / train["srmc"], sample_weight=train["srmc"] ** 2)


def _predict(m, A: dict, ti: np.ndarray, bi: np.ndarray, mask: tuple) -> np.ndarray:
    """Kaina, kai maske pažymėtų grupių reikšmės – tikslo valandos, kitų – normos valandos."""
    v = {g: np.where(mask[i], A[GROUPS[g]][ti], A[GROUPS[g]][bi]) for i, g in enumerate(NAMES)}
    X = np.column_stack([v["vartojimas"] - v["vėjas"] - v["saulė"], v["pralaidumai"],
                         A["hour"][ti], A["wknd"][ti]])
    return m.predict(X) * v["dujos"]


def shapley(m, A: dict, ti: np.ndarray, bi: np.ndarray) -> np.ndarray:
    n = len(NAMES)
    masks = list(itertools.product([0, 1], repeat=n))
    val = {mk: _predict(m, A, ti, bi, mk) for mk in masks}
    phi = np.zeros((len(ti), n))
    for k in range(n):
        for mk in masks:
            if mk[k] == 0:
                on = tuple(1 if i == k else b for i, b in enumerate(mk))
                s = sum(mk)
                w = math.factorial(s) * math.factorial(n - s - 1) / math.factorial(n)
                phi[:, k] += w * (val[on] - val[mk])
    return phi


def pairs(df: pd.DataFrame, targets: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Kiekvienai tikslo valandai – normos valandos: ta pati paros valanda,
    to paties tipo (darbo d./savaitgalis) dienos per pastarąsias WINDOW_DAYS d."""
    key = {(d, h): p for d, h, p in zip(df["date"], df["hour"], df["pos"])}
    wk = {d: w for d, w in zip(df["date"], df["wknd"])}
    days = sorted(wk)
    ti, bi = [], []
    for d, h, p in zip(targets["date"], targets["hour"], targets["pos"]):
        for x in days:
            if 0 < (d - x).days <= WINDOW_DAYS and wk[x] == wk[d]:
                q = key.get((x, h))
                if q is not None:
                    ti.append(p)
                    bi.append(q)
    return np.array(ti, dtype=int), np.array(bi, dtype=int)


def attribute(df: pd.DataFrame, m, targets: pd.DataFrame) -> pd.DataFrame:
    """Valandiniai veiksnių indėliai (€/MWh) tikslo valandoms."""
    A = {c: df[c].to_numpy(float) for c in ["srmc", "load_f", "wind_f", "sol_f", "cap_def", "hour", "wknd", "p"]}
    ti, bi = pairs(df, targets)
    if len(ti) == 0:
        return pd.DataFrame()
    phi = shapley(m, A, ti, bi)
    n = len(df)
    acc, cnt, p_norm = np.zeros((n, len(NAMES))), np.zeros(n), np.zeros(n)
    np.add.at(acc, ti, phi)
    np.add.at(cnt, ti, 1)
    np.add.at(p_norm, ti, A["p"][bi])
    norm_in = {c: np.zeros(n) for c in ("load_f", "wind_f", "sol_f", "srmc", "cap_def")}
    for c in norm_in:
        np.add.at(norm_in[c], ti, A[c][bi])
    ok = cnt > 0
    out = pd.DataFrame(acc[ok] / cnt[ok, None], columns=NAMES, index=df.index[ok])
    out["kaina"] = df["p"][ok]
    out["norma"] = p_norm[ok] / cnt[ok]
    out["kita"] = (out["kaina"] - out["norma"]) - out[NAMES].sum(axis=1)
    out["date"] = df["date"][ok]
    for c in norm_in:
        out[c] = df[c][ok]
        out[c + "_norma"] = norm_in[c][ok] / cnt[ok]
    for c in ("c_nb", "c_pl", "ttf"):
        out[c] = df[c][ok]
    return out


def daily(hourly: pd.DataFrame) -> pd.DataFrame:
    d = hourly.drop(columns=["date"]).groupby(hourly["date"]).mean()
    d["val"] = hourly.groupby("date").size()
    d["gamyba"] = d["vėjas"] + d["saulė"]
    d["skirtumas"] = d["kaina"] - d["norma"]
    return d


# ─── VIENOS DIENOS SKAIDYMAS ─────────────────────────────────────────────────

def explain_day(df: pd.DataFrame, day: date) -> dict | None:
    targets = df[df["date"] == day]
    train = df[df["date"] < day]                      # modelis mato tik praeitį
    if len(targets) < 20 or len(train) < 24 * 180:
        return None
    h = attribute(df, fit(train), targets)
    if h.empty:
        return None
    x = daily(h).iloc[0]
    total = sum(abs(x[f]) for f in OUT_FACTORS) or 1.0

    def item(f):
        return {"eur_mwh": round(float(x[f]), 1), "dalis_proc": round(100 * abs(float(x[f])) / total)}

    factors = {f: item(f) for f in OUT_FACTORS}
    factors["gamyba"]["vėjas_eur_mwh"] = round(float(x["vėjas"]), 1)
    factors["gamyba"]["saulė_eur_mwh"] = round(float(x["saulė"]), 1)
    return {
        "data": day.isoformat(),
        "kaina_eur_mwh": round(float(x["kaina"]), 1),
        "norma_eur_mwh": round(float(x["norma"]), 1),
        "norma": f"pastarųjų {WINDOW_DAYS} d. to paties tipo dienų vidurkis",
        "skirtumas_eur_mwh": round(float(x["skirtumas"]), 1),
        "veiksniai": factors,
        "sąlygos": {
            "vėjas_mw": [round(float(x["wind_f"])), round(float(x["wind_f_norma"]))],
            "saulė_mw": [round(float(x["sol_f"])), round(float(x["sol_f_norma"]))],
            "vartojimas_mw": [round(float(x["load_f"])), round(float(x["load_f_norma"]))],
            "dujų_kaštai_srmc": [round(float(x["srmc"]), 1), round(float(x["srmc_norma"]), 1)],
            "ttf_eur_mwh": round(float(x["ttf"]), 1),
            "nordbalt_mw": round(float(x["c_nb"])), "litpol_mw": round(float(x["c_pl"])),
        },
        "valandų": int(x["val"]),
    }


def print_day(r: dict) -> None:
    c = r["sąlygos"]
    print(f"{r['data']}: kaina {r['kaina_eur_mwh']:.1f} €/MWh, norma {r['norma_eur_mwh']:.1f} "
          f"({r['norma']}), skirtumas {r['skirtumas_eur_mwh']:+.1f} €/MWh")
    for f in OUT_FACTORS:
        v = r["veiksniai"][f]
        extra = (f"  (vėjas {v['vėjas_eur_mwh']:+.1f}, saulė {v['saulė_eur_mwh']:+.1f})"
                 if f == "gamyba" else "")
        print(f"  {f:<12} {v['eur_mwh']:>+7.1f} €/MWh  {v['dalis_proc']:>3} %{extra}")
    print(f"  sąlygos (šiandien / norma): vėjas {c['vėjas_mw'][0]}/{c['vėjas_mw'][1]} MW, "
          f"saulė {c['saulė_mw'][0]}/{c['saulė_mw'][1]} MW, vartojimas {c['vartojimas_mw'][0]}/"
          f"{c['vartojimas_mw'][1]} MW, dujų kaštai {c['dujų_kaštai_srmc'][0]}/{c['dujų_kaštai_srmc'][1]} €/MWh, "
          f"NordBalt {c['nordbalt_mw']} MW, LitPol {c['litpol_mw']} MW")


# ─── BENDRA SKALĖ ────────────────────────────────────────────────────────────

def scale(df: pd.DataFrame, folds: int = 6) -> pd.DataFrame:
    """Visų dienų skaidymas kryžmine patikra: kiekvieno mėnesio indėliai skaičiuojami
    modeliu, kuris to mėnesio nematė."""
    months = sorted(df["ym"].unique())
    parts = []
    for grp in [months[i::folds] for i in range(folds)]:
        m = fit(df[~df["ym"].isin(grp)])
        parts.append(attribute(df, m, df[df["ym"].isin(grp)]))
    d = daily(pd.concat(parts).sort_index())
    d = d[d["val"] >= 22]
    mabs = d[OUT_FACTORS].abs().mean()
    cov = pd.Series({f: np.cov(d[f], d["skirtumas"])[0, 1] / d["skirtumas"].var() for f in OUT_FACTORS})
    t = pd.DataFrame({"vid_poveikis_eur_mwh": mabs.round(1),
                      "dalis_proc": (100 * mabs / mabs.sum()).round(1),
                      "dispersijos_dalis_proc": (100 * cov).round(1)})
    t.attrs["dienų"] = len(d)
    t.attrs["vid_nuokrypis"] = round(float(d["skirtumas"].abs().mean()), 1)
    return t


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    df = load_dataset()
    if "--skalė" in flags or "--skale" in flags:
        t = scale(df)
        print(f"Dienų: {t.attrs['dienų']}; vidutinis kainos nuokrypis nuo normos: "
              f"{t.attrs['vid_nuokrypis']} €/MWh")
        print(t.to_string())
        return
    day = datetime.strptime(args[0], "%Y-%m-%d").date() if args else max(df["date"])
    r = explain_day(df, day)
    if r is None:
        print(f"Nepakanka duomenų dienai {day}.", file=sys.stderr)
        sys.exit(1)
    if "--json" in flags:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print_day(r)


if __name__ == "__main__":
    main()
