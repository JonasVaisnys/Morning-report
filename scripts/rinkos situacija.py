#!/usr/bin/env python3
"""
Rinkos situacijos aprašas — MB Enerconsult (prototipas)
========================================================
Atsako į klausimą „kokia šiandien situacija elektros rinkoje": kaina ir jos
norma šiam laikotarpiui, kaimynų kainos, vartojimas, vėjo ir saulės gamyba,
dujos, tarpsisteminiai pralaidumai, importas/eksportas ir išvada, kas kainą
lėmė.

Normos („įprasta šiam laikotarpiui")
    • vėjas ir saulė – to paties metų laiko (±15 d.), to paties tipo dienos
      2022 m.–praėjusiais metais, perskaičiuotos į dabartinę įrengtąją galią
      (ENTSO-E A68); būsena – pagal vietą tame pasiskirstyme
      (<P10, P10–P33, P33–P67, P67–P90, >P90);
    • vartojimas – pastarųjų 6 savaičių to paties tipo dienos, perskaičiuotos
      pagal daugiametę sezono formą į šią datą (prognozės eilutės apibrėžimas
      per metus keitėsi, todėl lygis imamas iš naujausių duomenų);
    • dujos – TTF palyginti su pastarųjų 12 mėn. vidurkiu;
    • pralaidumai – palyginti su įprastu (365 d. mediana) lygiu;
    • kaina – modelio kaina esant įprastam vėjui, saulei ir vartojimui,
      neribotiems pralaidumams ir įprastoms dujoms („kainos veiksniai.py").

Naudojimas
    python "scripts/rinkos situacija.py" 2026-11-12 [--json]

Reikia: pandas, numpy, scikit-learn; duomenys – data/istorija/.
"""

import importlib.util
import itertools
import json
import math
import os
import sys
import warnings
from datetime import date, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "..", "data", "istorija") + os.sep
_spec = importlib.util.spec_from_file_location("kv", os.path.join(HERE, "kainos veiksniai.py"))
kv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kv)


def rd(name):
    df = pd.read_csv(D + name, index_col=0, parse_dates=True); df.index = df.index.tz_localize("UTC"); return df
def hourly_all():
    L = rd("ilgoji.csv.gz"); a, g, k = rd("apkrova.csv.gz"), rd("gamyba.csv.gz"), rd("kainos.csv.gz")
    def other(df):
        cols = [c for c in df.columns if c.startswith("gamyba_LT_") and not c.endswith("_vart") and c not in ("gamyba_LT_B16", "gamyba_LT_B19")]
        return df[cols].sum(axis=1, min_count=3)
    def part(src_load, src_gen, src_p):
        o = pd.DataFrame(index=src_p.index)
        o["p"] = src_p["kaina_LT"]
        o["load_a"], o["load_f"] = src_load["apkrova_LT"], src_load["apkrova_progn_LT"]
        o["wind_a"], o["wind_f"] = src_gen["gamyba_LT_B19"], src_gen["progn_LT_B19"]
        o["sol_a"], o["sol_f"] = src_gen["gamyba_LT_B16"], src_gen["progn_LT_B16"]
        o["other_a"] = other(src_gen)
        return o
    old = part(L, L, L); new = part(a, g, k)
    df = pd.concat([old[old.index < new.index.min()], new]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    lt = df.index.tz_convert("Europe/Vilnius")
    df["date"] = lt.date; df["hour"] = lt.hour; df["wknd"] = (lt.dayofweek >= 5).astype(int)
    return df


WINDOW = 15            # ± dienų aplink tą pačią kalendorinę datą ankstesniais metais
GROUPS = {"dujos": "srmc", "vartojimas": "load_f", "vėjas": "wind_f", "saulė": "sol_f", "pralaidumai": "cap_def"}
NAMES = list(GROUPS)

def capacity_series():
    cap = pd.read_csv(D + "galios.csv")
    out = {}
    for psr, name in (("B19", "wind"), ("B16", "sol")):
        c = cap[cap.psr == psr].sort_values("metai")
        xs = [pd.Timestamp(f"{int(y)}-01-01").toordinal() for y in c.metai]
        out[name] = (np.array(xs, float), c.mw.to_numpy(float))
    return out
CAPS = capacity_series()
def cap_at(name, d):            # tiesinė interpoliacija tarp metų pradžių; po paskutinės – pastovi
    xs, ys = CAPS[name]; return float(np.interp(pd.Timestamp(d).toordinal(), xs, ys))

def ref_days(d, all_days):
    """To paties metų laiko (±WINDOW d.) ir to paties tipo dienos ankstesniais metais."""
    out = []
    for y in range(2022, d.year):
        try: c = date(y, d.month, d.day)
        except ValueError: c = date(y, d.month, 28)
        for k in range(-WINDOW, WINDOW + 1):
            r = c + timedelta(days=k)
            if r in all_days and (r.weekday() >= 5) == (d.weekday() >= 5): out.append(r)
    return out

def status(x, ref, words):
    p10, p33, p67, p90 = np.percentile(ref, [10, 33.3, 66.7, 90])
    i = 0 if x < p10 else 1 if x < p33 else 2 if x <= p67 else 3 if x <= p90 else 4
    return words[i], round(float((np.asarray(ref) < x).mean() * 100))
W_F = ["gerokai mažesnė nei įprasta", "mažesnė nei įprasta", "įprasta", "didesnė nei įprasta", "gerokai didesnė nei įprasta"]
W_M = ["gerokai mažesnis nei įprasta", "mažesnis nei įprasta", "įprastas", "didesnis nei įprasta", "gerokai didesnis nei įprasta"]

class Situation:
    def __init__(self):
        self.H = hourly_all()                                   # 2022+
        self.df = kv.load_dataset()                             # 2025+ modelio rinkinys
        j = kv._read("jungtys.csv.gz"); k = kv._read("kainos.csv.gz")
        self.j, self.k = j, k
        lt = j.index.tz_convert("Europe/Vilnius"); self.jdate = pd.Series(lt.date, index=j.index)
        self.kdate = pd.Series(k.index.tz_convert("Europe/Vilnius").date, index=k.index)
        H = self.H
        self.days = set(H.date.unique())
        dl = H.groupby("date").load_f.agg(["mean", "count"]); dl = dl[dl["count"] >= 22]["mean"]
        dl.index = pd.to_datetime(dl.index); self.dload = dl
        sm = dl.rolling(7, center=True, min_periods=5).mean()            # be savaitės dienos efekto
        rel = sm / sm.groupby(sm.index.year).transform("mean")
        rel = rel[rel.index.year <= 2025]
        S = rel.groupby(rel.index.dayofyear).mean().reindex(range(1, 367)).interpolate(limit_direction="both")
        ext = pd.concat([S.iloc[-20:], S, S.iloc[:20]]).rolling(21, center=True, min_periods=10).mean().iloc[20:-20]
        self.S = ext                                                       # sezono indeksas pagal metų dieną
        self._thr = None
        d = pd.read_csv(D + "dienos.csv", index_col=0, parse_dates=True)
        full = d.reindex(pd.date_range(d.index.min(), d.index.max() + pd.Timedelta(days=5))).ffill()
        full["srmc"] = full.ttf_eur_mwh / kv.ETA + full.eua_eur_t * kv.GAS_EF / kv.ETA + kv.VOM
        self.gas = full
        try:
            np_ = pd.read_csv(D + "nordpool-pralaidumai.csv", parse_dates=["utc_pradžia"])
            np_["date"] = np_["utc_pradžia"].dt.tz_convert("Europe/Vilnius").dt.date
            self.np = np_
        except Exception:
            self.np = None

    def recent_days(self, d, n=42):
        dl = self.dload; t = pd.Timestamp(d)
        r = dl[(dl.index < t) & (dl.index >= t - pd.Timedelta(days=n))]
        return r[(r.index.dayofweek >= 5) == (t.dayofweek >= 5)]
    def load_ref(self, d):
        """Vartojimo norma: pastarųjų 6 savaičių to paties tipo dienos, perskaičiuotos pagal sezono formą į šią datą."""
        r = self.recent_days(d); f = self.S[pd.Timestamp(d).dayofyear] / self.S.reindex(r.index.dayofyear).to_numpy()
        return r * f, pd.Series(f, index=r.index)
    def load_thresholds(self):
        if self._thr is None:
            dev = []
            for t in self.dload.index[self.dload.index >= "2025-03-01"]:
                ref, _ = self.load_ref(t.date())
                if len(ref) >= 5: dev.append(self.dload[t] / ref.mean() - 1)
            self._thr = np.percentile(dev, [10, 33.3, 66.7, 90])
        return self._thr

    def scaled_refs(self, d):
        """Normos valandos: ankstesnių metų to paties laikotarpio valandos, perskaičiuotos į dabartinę galią."""
        H = self.H; rd = ref_days(d, self.days)
        R = H[H.date.isin(rd)].copy()
        cw, cs = cap_at("wind", d), cap_at("sol", d)
        R["wind_f"] = R.wind_f * (cw / R.date.map(lambda r: cap_at("wind", r)))
        R["sol_f"] = R.sol_f * (cs / R.date.map(lambda r: cap_at("sol", r)))
        # vartojimas normos valandoms – iš pastarųjų 6 savaičių (ta pati valanda, to paties tipo diena), perskaičiuotas pagal sezoną
        ref, fac = self.load_ref(d)
        rec = H[H.date.isin([t.date() for t in ref.index])][["date", "hour", "load_f"]].copy()
        rec["load_f"] = rec.load_f * rec.date.map({t.date(): fac[t] for t in fac.index})
        pool = {h: g.load_f.dropna().to_numpy() for h, g in rec.groupby("hour")}
        rng = np.random.default_rng(0)
        R = R.dropna(subset=["wind_f", "sol_f"])
        R["load_f"] = [pool[h][rng.integers(len(pool[h]))] if h in pool and len(pool[h]) else np.nan for h in R.hour]
        return R.dropna(subset=["load_f", "wind_f", "sol_f"])

    def explain(self, d, hours_actual=None):
        H, df = self.H, self.df
        T = df[df.date == d]
        if len(T) < 20: return None
        R = self.scaled_refs(d)
        Rd = R.groupby("date")[["load_f", "wind_f", "sol_f", "other_a"]].mean()
        x = T[["load_f", "wind_f", "sol_f"]].mean()
        out = {"data": d.isoformat(), "ref_dienų": int(len(Rd)), "galia": {"vėjas": round(cap_at("wind", d)), "saulė": round(cap_at("sol", d))}}
        lref, _ = self.load_ref(d); ln = float(lref.mean()); th = self.load_thresholds(); dv = float(x["load_f"]) / ln - 1
        i = 0 if dv < th[0] else 1 if dv < th[1] else 2 if dv <= th[2] else 3 if dv <= th[3] else 4
        out["vartojimas"] = {"mw": round(float(x["load_f"])), "norma": round(ln), "intervalas": [round(ln * (1 + th[1])), round(ln * (1 + th[2]))], "būsena": W_M[i], "nuokrypis_proc": round(100 * dv, 1), "procentilis": None}
        for key, col, words in (("vėjas", "wind_f", W_F), ("saulė", "sol_f", W_F)):
            st, pct = status(x[col], Rd[col], words)
            p33, p50, p67 = np.percentile(Rd[col], [33.3, 50, 66.7])
            out[key] = {"mw": round(float(x[col])), "norma": round(float(p50)), "intervalas": [round(float(p33)), round(float(p67))], "būsena": st, "procentilis": pct}
        # kita gamyba – faktas (tik turimos valandos), norma toms pačioms valandoms
        Ha = H[(H.date == d) & H.other_a.notna()]
        if hours_actual is not None: Ha = Ha[Ha.hour < hours_actual]
        if len(Ha):
            hrs = sorted(Ha.hour.unique()); Ro = R[R.hour.isin(hrs)].groupby("date").other_a.mean().dropna()
            st, pct = status(Ha.other_a.mean(), Ro, W_F)
            out["kita_gamyba"] = {"mw": round(float(Ha.other_a.mean())), "norma": round(float(Ro.median())), "būsena": st, "valandos": f"{hrs[0]:02d}–{hrs[-1] + 1:02d}",
                                  "vėjas_faktas": round(float(Ha.wind_a.mean())), "saulė_faktas": round(float(Ha.sol_a.mean())), "vartojimas_faktas": round(float(Ha.load_a.mean()))}
        # dujos
        g = self.gas; prev = pd.Timestamp(d) - pd.Timedelta(days=1)
        ttf, srmc = float(g.ttf_eur_mwh.asof(prev)), float(g.srmc.asof(prev))
        yr = g.loc[prev - pd.Timedelta(days=365):prev]
        ttf_n, srmc_n = float(yr.ttf_eur_mwh.mean()), float(yr.srmc.mean())
        r = ttf / ttf_n
        gs = "gerokai pigesnės nei įprasta" if r < 0.75 else "pigesnės nei įprasta" if r < 0.9 else "įprastos" if r <= 1.1 else "brangesnės nei įprasta" if r <= 1.33 else "gerokai brangesnės nei įprasta"
        out["dujos"] = {"ttf": round(ttf, 1), "norma_12mėn": round(ttf_n, 1), "santykis": round(r, 2), "būsena": gs, "prieš_metus": round(float(g.ttf_eur_mwh.asof(prev - pd.Timedelta(days=365))), 1)}
        # modelis (mato tik praeitį) ir Shapley prieš sezoninę normą
        m = kv.fit(df[df.date < d])
        tt = T.reset_index(drop=True); B = R.reset_index(drop=True)
        ti, bi = [], []
        byh = {h: np.flatnonzero(B.hour.to_numpy() == h) for h in range(24)}
        for i, h in enumerate(tt.hour):
            for q in byh.get(h, []): ti.append(i); bi.append(q)
        ti, bi = np.array(ti), np.array(bi)
        tv = {"srmc": tt.srmc.to_numpy(float), "load_f": tt.load_f.to_numpy(float), "wind_f": tt.wind_f.to_numpy(float), "sol_f": tt.sol_f.to_numpy(float), "cap_def": tt.cap_def.to_numpy(float)}
        bv = {"srmc": np.full(len(B), srmc_n), "load_f": B.load_f.to_numpy(float), "wind_f": B.wind_f.to_numpy(float), "sol_f": B.sol_f.to_numpy(float), "cap_def": np.zeros(len(B))}
        hour, wk = tt.hour.to_numpy(float)[ti], tt.wknd.to_numpy(float)[ti]
        def f(mask):
            v = {gname: np.where(mask[i], tv[c][ti], bv[c][bi]) for i, (gname, c) in enumerate(GROUPS.items())}
            X = np.column_stack([v["vartojimas"] - v["vėjas"] - v["saulė"], v["pralaidumai"], hour, wk])
            return m.predict(X) * v["dujos"]
        n = len(NAMES); masks = list(itertools.product([0, 1], repeat=n)); val = {mk: f(mk) for mk in masks}
        phi = np.zeros((len(ti), n))
        for kk in range(n):
            for mk in masks:
                if mk[kk] == 0:
                    on = tuple(1 if i == kk else b for i, b in enumerate(mk)); s = sum(mk)
                    phi[:, kk] += math.factorial(s) * math.factorial(n - s - 1) / math.factorial(n) * (val[on] - val[mk])
        def per_target(a):                      # vidurkis per normos valandas kiekvienai tikslo valandai, tada per parą
            return float(pd.Series(a).groupby(ti).mean().mean())
        contrib = {NAMES[i]: per_target(phi[:, i]) for i in range(n)}
        p_norm = per_target(val[tuple([0] * n)]); p_model = per_target(val[tuple([1] * n)])
        gas_only = tuple(1 if nm == "dujos" else 0 for nm in NAMES)
        p_norm_gas_today = per_target(val[gas_only])
        price = float(T.p.mean())
        contrib["kita"] = price - p_model
        # įprastas kainų intervalas: normos dienų kainos pagal modelį (normalios dujos, neriboti pralaidumai)
        base = pd.DataFrame({"t": ti, "b": bi, "v": val[tuple([0] * n)]})
        base["rdate"] = B.date.to_numpy()[bi]
        sim = base.groupby("rdate").v.mean()
        tr = df[df.date < d]
        e = ((tr.p - m.predict(tr[kv.FEATS]) * tr.srmc) / tr.srmc).groupby(tr.date).mean().to_numpy()
        e = e[np.random.default_rng(0).integers(len(e), size=min(300, len(e)))]
        sim = pd.Series((sim.to_numpy()[:, None] + e[None, :] * srmc_n).ravel())      # modelio kaina + įprasta likučių sklaida
        st, pct = status(price, sim, W_F)
        out["kaina"] = {"lt": round(price, 1), "norma": round(p_norm, 1), "intervalas": [round(float(np.percentile(sim, 33.3))), round(float(np.percentile(sim, 66.7)))],
                        "norma_su_šiandienos_dujomis": round(p_norm_gas_today, 1), "būsena": st, "min": round(float(T.p.min()), 1), "max": round(float(T.p.max()), 1),
                        "min_val": int(T.hour[T.p.idxmin()]), "max_val": int(T.hour[T.p.idxmax()]), "modelis": round(p_model, 1)}
        out["indėliai"] = {k_: round(v_, 1) for k_, v_ in contrib.items()}
        # kaimynai
        kk_ = self.k[self.kdate == d]
        out["kaimynai"] = {z: round(float(kk_[f"kaina_{z}"].mean()), 1) for z in ("LV", "SE4", "PL", "EE", "FI")}
        out["lt_lv_skiriasi_val"] = int(((kk_.kaina_LT - kk_.kaina_LV).abs() > 0.01).sum())
        # prekybos grafikai (kitos paros rinka): grynasis importas pagal sieną
        jj = self.j[self.jdate == d]; load = float(x["load_f"])
        imp = {"SE4": float((jj.prekyba_SE4_LT - jj.prekyba_LT_SE4).mean()), "PL": float((jj.prekyba_PL_LT - jj.prekyba_LT_PL).mean()), "LV": float((jj.prekyba_LV_LT - jj.prekyba_LT_LV).mean())}
        out["mainai"] = {z: {"mw": round(v), "proc_vartojimo": round(100 * v / load)} for z, v in imp.items()}
        out["mainai"]["iš_viso"] = {"mw": round(sum(imp.values())), "proc_vartojimo": round(100 * sum(imp.values()) / load)}
        out["mainai"]["maks"] = {"SE4_imp": round(float(jj.prekyba_SE4_LT.max())), "SE4_eksp": round(float(jj.prekyba_LT_SE4.max())), "PL_imp": round(float(jj.prekyba_PL_LT.max())), "PL_eksp": round(float(jj.prekyba_LT_PL.max())), "LV_imp": round(float(jj.prekyba_LV_LT.max())), "LV_eksp": round(float(jj.prekyba_LT_LV.max()))}
        # pralaidumai: ENTSO-E (siūlomi rinkai) + Nord Pool, jei yra; nominalas – 365 d. mediana
        caps = {}
        hist = self.j[(self.jdate < d) & (self.jdate >= d - timedelta(days=365))]
        for name, a, b in (("LT–SE4 (NordBalt)", "SE4", "LT"), ("LT–PL (LitPol)", "PL", "LT"), ("LT–LV", "LV", "LT")):
            e = {}
            for lab, col in (("importas", f"siulomas_{a}_{b}"), ("eksportas", f"siulomas_{b}_{a}")):
                today = jj[col].dropna()
                e[lab] = {"mw": round(float(today.mean())) if len(today) >= 12 else None, "min": round(float(today.min())) if len(today) >= 12 else None,
                          "nominalas": round(float(hist[col].median())), "pask_data": str(self.jdate[self.j[col].notna()].max())}
            caps[name] = e
        if self.np is not None:
            t = self.np[self.np.date == d]
            out["nordpool"] = {f"{r.kryptis} {r.jungtis}": [round(r.mw_mean), round(r.mw_min)] for r in t.groupby(["kryptis", "jungtis"]).mw.agg(mw_mean="mean", mw_min="min").reset_index().itertuples()}
        out["pralaidumai"] = caps
        out["sąlygos"] = {"srmc": round(srmc, 1), "srmc_norma": round(srmc_n, 1), "cap_def": round(float(T.cap_def.mean()))}
        return out


DOW = ["pirmadienis", "antradienis", "trečiadienis", "ketvirtadienis", "penktadienis", "šeštadienis", "sekmadienis"]
def lt(x, nd=0): return f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")
REASON = {("dujos", 1): "brangios dujos", ("dujos", -1): "pigesnės dujos", ("vartojimas", 1): "didesnis vartojimas", ("vartojimas", -1): "mažesnis vartojimas",
          ("vėjas", 1): "maža vėjo gamyba", ("vėjas", -1): "didelė vėjo gamyba", ("saulė", 1): "maža saulės gamyba", ("saulė", -1): "didelė saulės gamyba",
          ("pralaidumai", 1): "apriboti tarpsisteminiai pralaidumai", ("pralaidumai", -1): "pralaidumai", ("kita", 1): "kiti veiksniai (kaimynų rinkos ir kt.)", ("kita", -1): "kiti veiksniai (kaimynų rinkos ir kt.)"}
def cap_status(mw, nominal):
    if mw is None: return "nėra duomenų"
    r = mw / nominal if nominal else 1
    return "atjungta" if r <= 0.05 else "stipriai apribota" if r < 0.5 else "apribota" if r < 0.85 else "neribojama"
def render(S, r):
    d = date.fromisoformat(r["data"]); k = r["kaina"]; c = r["indėliai"]; L = []
    L.append(f"{r['data']}, {DOW[d.weekday()]}")
    L.append(f"KAINA: LT vidutinė {lt(k['lt'],1)} €/MWh (min {lt(k['min'],1)} {k['min_val']} val., max {lt(k['max'],1)} {k['max_val']} val.) – {k['būsena']} šiam laikotarpiui; norma {lt(k['norma'])} €/MWh (įprastai {k['intervalas'][0]}–{k['intervalas'][1]}); su šiandienos dujomis norma būtų {lt(k['norma_su_šiandienos_dujomis'])}")
    n = r["kaimynai"]; L.append(f"KAIMYNAI: LV {lt(n['LV'],1)}, SE4 {lt(n['SE4'],1)}, PL {lt(n['PL'],1)}, EE {lt(n['EE'],1)}, FI {lt(n['FI'],1)}; LT≠LV {r['lt_lv_skiriasi_val']} val.")
    for key, lab in (("vartojimas", "VARTOJIMAS"), ("vėjas", "VĖJAS"), ("saulė", "SAULĖ")):
        v = r[key]; extra = f"{v['procentilis']} procentilis" if v.get("procentilis") is not None else f"{v['nuokrypis_proc']:+.1f} % nuo normos"
        L.append(f"{lab}: {lt(v['mw'])} MW – {v['būsena']} (norma {lt(v['norma'])} MW, įprastai {v['intervalas'][0]}–{v['intervalas'][1]}; {extra})")
    if "kita_gamyba" in r:
        o = r["kita_gamyba"]; L.append(f"KITA GAMYBA (faktas {o['valandos']} val.): {lt(o['mw'])} MW – {o['būsena']} (norma {lt(o['norma'])})")
    g = r["dujos"]; L.append(f"DUJOS: TTF {lt(g['ttf'],1)} €/MWh – {g['būsena']} (12 mėn. vidurkis {lt(g['norma_12mėn'],1)}, prieš metus {lt(g['prieš_metus'],1)})")
    # pralaidumai
    np_ = r.get("nordpool") or {}
    parts = []
    for name, key, nom in (("LT–SE4", "SE4_NB", (700, 700)), ("LT–PL", "PL_LP", (r["pralaidumai"]["LT–PL (LitPol)"]["importas"]["nominalas"], r["pralaidumai"]["LT–PL (LitPol)"]["eksportas"]["nominalas"])), ("LT–LV", "LV", (r["pralaidumai"]["LT–LV"]["importas"]["nominalas"], r["pralaidumai"]["LT–LV"]["eksportas"]["nominalas"]))):
        full = {"LT–SE4": "LT–SE4 (NordBalt)", "LT–PL": "LT–PL (LitPol)", "LT–LV": "LT–LV"}[name]
        imp = np_.get(f"importas {key}", [r["pralaidumai"][full]["importas"]["mw"], r["pralaidumai"][full]["importas"]["min"]])
        exp = np_.get(f"eksportas {key}", [r["pralaidumai"][full]["eksportas"]["mw"], r["pralaidumai"][full]["eksportas"]["min"]])
        st_i, st_e = cap_status(imp[0], nom[0]), cap_status(exp[0], nom[1])
        st = st_i if st_i == st_e else f"importas – {st_i}, eksportas – {st_e}"
        parts.append(f"{name} {imp[0]}/{exp[0]} MW (min {imp[1]}/{exp[1]}; įprastai {nom[0]}/{nom[1]}) – {st}")
    L.append("PRALAIDUMAI (importas/eksportas): " + "; ".join(parts))
    m = r["mainai"]
    def flow(z, name): 
        v = m[z]; return f"{name} {'importas' if v['mw'] >= 0 else 'eksportas'} {lt(abs(v['mw']))} MW ({abs(v['proc_vartojimo'])} % vartojimo)"
    t = m["iš_viso"]; L.append(f"MAINAI (prekybos grafikas): grynasis {'importas' if t['mw'] >= 0 else 'eksportas'} {lt(abs(t['mw']))} MW = {abs(t['proc_vartojimo'])} % vartojimo; " + "; ".join(flow(z, nm) for z, nm in (("LV", "Latvija"), ("SE4", "Švedija"), ("PL", "Lenkija"))))
    # išvada
    tot = sum(abs(v) for v in c.values()); thr = max(8, 0.12 * tot)
    up = sorted([(v, f) for f, v in c.items() if v >= thr], reverse=True); dn = sorted([(v, f) for f, v in c.items() if v <= -thr])
    def ph(lst): return ", ".join(f"{REASON[(f, 1 if v > 0 else -1)]} ({v:+.0f} €/MWh)" for v, f in lst)
    diff = k["lt"] - k["norma"]
    s = f"IŠVADA: kaina {lt(k['lt'])} €/MWh, {k['būsena']} ({diff:+.0f} €/MWh nuo normos {lt(k['norma'])})."
    if up: s += f" Kainą kelia: {ph(up)}."
    if dn: s += f" Kainą mažina: {ph(dn)}."
    L.append(s)
    L.append("   indėliai: " + ", ".join(f"{f} {v:+.1f}" for f, v in c.items()) + f" | modelis {k['modelis']}, ref. dienų {r['ref_dienų']}")
    return "\n".join(L)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    S = Situation()
    for ds in args or [max(S.df["date"]).isoformat()]:
        r = S.explain(date.fromisoformat(ds))
        if r is None:
            print(f"{ds}: nepakanka duomenų", file=sys.stderr)
            continue
        print(json.dumps(r, ensure_ascii=False, indent=1) if "--json" in sys.argv else render(S, r))
        print()


if __name__ == "__main__":
    main()
