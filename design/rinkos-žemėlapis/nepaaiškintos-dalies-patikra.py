import importlib.util, warnings, numpy as np, pandas as pd
warnings.filterwarnings("ignore")
from sklearn.ensemble import HistGradientBoostingRegressor
spec = importlib.util.spec_from_file_location("rs", "scripts/rinkos situacija.py"); rs = importlib.util.module_from_spec(spec); spec.loader.exec_module(rs)
S = rs.Situation(); df = S.df.copy()
a = rs.kv._read("apkrova.csv.gz"); g = rs.kv._read("gamyba.csv.gz"); j = S.j
print("progn stulpeliai:", [c for c in g.columns if c.startswith("progn_")])
ix = df.index
def col(src, c): return src[c].reindex(ix) if c in src.columns else pd.Series(np.nan, index=ix)
for z in ("LV", "EE"):
    df[f"load_{z}"] = col(a, f"apkrova_progn_{z}"); df[f"wind_{z}"] = col(g, f"progn_{z}_B19"); df[f"sol_{z}"] = col(g, f"progn_{z}_B16")
print("užpildymas %:", {c: round(100 * df[c].notna().mean()) for c in ["load_LV", "load_EE", "wind_LV", "wind_EE", "sol_LV", "sol_EE"]})
df["rl_b"] = df.load_LV + df.load_EE - df.wind_EE.fillna(0) - df.sol_EE.fillna(0) - df.wind_LV.fillna(0) - df.sol_LV.fillna(0)
# Latvijos hidroelektrinės – vakar dienos vidurkis (šios dienos faktas 6 val. dar nežinomas)
hy = g["gamyba_LV_B11"]; hyd = hy.groupby(hy.index.tz_convert("Europe/Vilnius").date).mean()
prev = {d: hyd.get(d - pd.Timedelta(days=1).to_pytimedelta()) for d in df.date.unique()}
df["hy_lv_d1"] = df.date.map(prev).astype(float)
df["fi_def"] = (1016 - col(j, "siulomas_FI_EE")).clip(lower=0)
lvcap = col(j, "siulomas_LV_LT"); df["lv_def"] = (lvcap.rolling(24 * 365, min_periods=24 * 30).median() - lvcap).clip(lower=0)
df["wind_fi"] = col(g, "progn_FI_B19")
print("užpildymas %:", {c: round(100 * df[c].notna().mean()) for c in ["rl_b", "hy_lv_d1", "fi_def", "lv_def", "wind_fi"]})
df["ym"] = pd.to_datetime(df.date).dt.to_period("M")
def run(extra, mono, label):
    feats = rs.FEATS_N + extra; res = []
    for mth in sorted(df.ym.unique()):
        tr = df[df.ym < mth]
        if tr.date.nunique() < 150: continue
        te = df[df.ym == mth]
        m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.06, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0, monotonic_cst=rs.MONO_N + mono, random_state=0)
        m.fit(tr[feats], tr.p / tr.srmc, sample_weight=tr.srmc ** 2)
        res.append(pd.DataFrame({"date": te.date, "p": te.p, "pred": m.predict(te[feats]) * te.srmc}))
    R = pd.concat(res); d = R.groupby("date").agg(p=("p", "mean"), pred=("pred", "mean"), n=("p", "size")); d = d[d.n >= 20]; e = d.p - d.pred; ae = e.abs()
    r2 = 1 - (e ** 2).sum() / ((d.p - d.p.mean()) ** 2).sum()
    print(f"{label:<46} dienų {len(d)} | |kita| mediana {ae.median():5.1f} vid. {ae.mean():5.1f} | >10: {100*(ae>10).mean():3.0f} % >20: {100*(ae>20).mean():3.0f} % | R² {r2:.2f} | autokor. {pd.Series(e.values).autocorr(1):.2f} | 11-12: {e.get(pd.Timestamp('2026-11-12').date(), float('nan')):+.1f}")
    return e
e0 = run([], [], "bazinis (dabartinis)")
run(["rl_b"], [1], "+ LV ir EE likutinė apkrova")
run(["hy_lv_d1"], [-1], "+ Latvijos hidro (vakar)")
run(["fi_def"], [1], "+ EstLink ribojimas")
run(["rl_b", "hy_lv_d1"], [1, -1], "+ LV/EE apkrova + LV hidro")
run(["rl_b", "hy_lv_d1", "fi_def", "lv_def"], [1, -1, 1, 1], "+ LV/EE apkrova + LV hidro + EstLink + LV–LT")
run(["rl_b", "hy_lv_d1", "fi_def", "lv_def", "wind_fi"], [1, -1, 1, 1, -1], "+ visa + Suomijos vėjas")
