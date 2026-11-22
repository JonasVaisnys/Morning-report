#!/usr/bin/env python3
"""
Savaitės apžvalga — MB Enerconsult
===================================
Pirmadienio laiškas apie praėjusią savaitę (pirmadienis–sekmadienis): elektros rinka,
teisės aktai, paramos, savaitės naujienos ir kas laukia. Siunčiamas atskirai nuo
kasdienio Morning Report (Brevo sąrašas #11 „MR savaitės apžvalga“).

Duomenys:
  data/istorija/…                  valandinės kainos, prognozės, prekybos grafikai, pralaidumai, TTF ir EUA
  data/archyvas/MMMM-MM-DD.json    kasdienių ataskaitų duomenys (veiksnių skaidymas, naujienos, pasaulio rinkos)
  data/šaltiniai/tar.json          Teisės aktų registro surinkimas (Chrome)
  data/šaltiniai/paramos-būsena.json  paramų kvietimų būsena (Chrome)
  data/savaitė/papildomos-naujienos.json  rankiniai papildymai (naujienos, kurias kasdienė atranka praleido)

Rezultatas (aplankas public/savaitė/):
  laiskas.html, laiskas-brevo.html, tema.txt, data.txt (pirmadienio data – rašoma paskutinė)

Naudojimas:
  python "scripts/savaitės ataskaita.py"                     # šios savaitės pirmadienis (Vilniaus laiku)
  python "scripts/savaitės ataskaita.py" 2026-11-23 [--iki 2026-11-21] [--out kitas/aplankas]
"""

import importlib.util
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
VLN = ZoneInfo("Europe/Vilnius")


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = _load("ataskaita", "ataskaita.py")          # laiško elementai ir tekstų taisyklės – tie patys kaip kasdieniame
kv = _load("kv", "kainos veiksniai.py")         # istorinių failų skaitymas su kasdieniais papildymais

num, sgn, e, cap, join_lt, plural = A.num, A.sgn, A.e, A.cap, A.join_lt, A.plural
m_tr, m_table, m_p, m_a, m_section, m_sub, m_th, m_td, m_bar = A.m_tr, A.m_table, A.m_p, A.m_a, A.m_section, A.m_sub, A.m_th, A.m_td, A.m_bar
INK, INK2, INK3, LINE, FONT, NB = A.INK, A.INK2, A.INK3, A.LINE, A.FONT, A.NB
MONTHS, WEEKDAYS = A.MONTHS, A.WEEKDAYS
WD_SHORT = ["pirm.", "antr.", "treč.", "ketv.", "penkt.", "šešt.", "sekm."]
WD_LOC = ["pirmadienį", "antradienį", "trečiadienį", "ketvirtadienį", "penktadienį", "šeštadienį", "sekmadienį"]
FULL = 0.95


# ─── pagalbinės ──────────────────────────────────────────────────────────────

def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def d_iso(x) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10])
    except Exception:
        return None


def range_lt(a: date, b: date) -> str:
    """„lapkričio 16–22 d.“ arba „lapkričio 30 – gruodžio 6 d.“."""
    if a == b:
        return f"{MONTHS[a.month - 1]} {a.day} d."
    if a.month == b.month:
        return f"{MONTHS[a.month - 1]} {a.day}–{b.day} d."
    return f"{MONTHS[a.month - 1]} {a.day} d. – {MONTHS[b.month - 1]} {b.day} d."


def dot(t: str) -> str:
    t = t.rstrip()
    return t if t.endswith(".") else t + "."


def sum_mln(x) -> float:
    m = re.match(r"\s*([\d\s]+)\s*Eur", x or "")
    return float(m.group(1).replace(" ", "")) / 1e6 if m else 0.0


def fmt_sum(x) -> str:
    """„286 930 000 Eur (likutis 8 114 600 Eur)“ → „286,9 mln. Eur (likutis 8,1 mln. Eur)“."""
    if not x:
        return "–"
    def one(m):
        v = float(m.group(1).replace(" ", "").replace("\u00a0", ""))
        return f"{num(v / 1e6, 1)}{NB}mln. Eur" if v >= 1e6 else f"{num(v / 1e3, 0)}{NB}tūkst. Eur" if v >= 1e4 else m.group(0)
    return re.sub(r"(\d[\d\s\u00a0]*\d|\d)\s*Eur", one, x)


def d_iso_lt(t: str) -> date:
    """„lapkričio 27 d. – …“ → data (rikiavimui)."""
    m = re.match(r"(\w+) (\d+) d\.", t)
    if not m or m.group(1) not in MONTHS:
        return date.max
    mon, day = MONTHS.index(m.group(1)) + 1, int(m.group(2))
    y = datetime.now(VLN).year
    return date(y, mon, day)


def pct_change(x, y) -> float | None:
    return (x / y - 1) * 100 if y else None


# ─── duomenys ────────────────────────────────────────────────────────────────

class Week:
    def __init__(self, data_dir: str, monday: date, iki: date | None):
        self.monday = monday
        self.start = monday - timedelta(days=7)
        self.end_full = monday - timedelta(days=1)
        self.data_dir = data_dir
        H = self._hourly()
        have = sorted(d for d, n in H.groupby("date").LT.count().items() if n >= 20)
        last = max([d for d in have if d <= self.end_full], default=None)
        if iki:
            last = min(last, iki) if last else None
        self.end = last or self.end_full
        self.complete = self.end >= self.end_full
        self.days = [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]
        self.prev_days = [d - timedelta(days=7) for d in self.days]
        self.H = H
        self.W = H[H.date.isin(self.days)]
        self.P = H[H.date.isin(self.prev_days)]
        self.arch = {d: load(os.path.join(data_dir, "archyvas", f"{d.isoformat()}.json")) for d in self.days}
        self.gas = pd.read_csv(os.path.join(data_dir, "istorija", "dienos.csv"), index_col=0, parse_dates=True)
        self.tar = load(os.path.join(data_dir, "šaltiniai", "tar.json"))
        self.grants = load(os.path.join(data_dir, "šaltiniai", "paramos-būsena.json"))
        self.extra_news = load(os.path.join(data_dir, "savaitė", "papildomos-naujienos.json")) or {}

    def _hourly(self) -> pd.DataFrame:
        k, a, g, j = kv._read("kainos.csv.gz"), kv._read("apkrova.csv.gz"), kv._read("gamyba.csv.gz"), kv._read("jungtys.csv.gz")
        H = pd.DataFrame(index=k.index)
        H["LT"] = k["kaina_LT"].fillna(k.get("elering_LT"))
        for z in ("LV", "EE", "FI", "SE4", "PL"):
            H[z] = k.get(f"kaina_{z}")
        H["load"] = a["apkrova_progn_LT"].reindex(H.index)
        H["wind"] = g["progn_LT_B19"].reindex(H.index)
        H["sol"] = g["progn_LT_B16"].reindex(H.index)
        for c in ("prekyba_LT_SE4", "prekyba_SE4_LT", "prekyba_LT_PL", "prekyba_PL_LT", "prekyba_LT_LV", "prekyba_LV_LT"):
            H[c] = j[c].reindex(H.index)
        for name, (fc, cc) in {"x_se4": ("prekyba_LT_SE4", "siulomas_LT_SE4"), "i_se4": ("prekyba_SE4_LT", "siulomas_SE4_LT"),
                               "x_pl": ("prekyba_LT_PL", "siulomas_LT_PL"), "i_pl": ("prekyba_PL_LT", "siulomas_PL_LT"),
                               "x_lv": ("prekyba_LT_LV", "siulomas_LT_LV"), "i_lv": ("prekyba_LV_LT", "siulomas_LV_LT")}.items():
            cap_ = j[cc].reindex(H.index).fillna(float(j[cc].median()))
            H[name] = ((H[fc] >= FULL * cap_) & (cap_ > 20)).astype(float).where(H[fc].notna())
        lt = H.index.tz_convert(VLN)
        H["date"], H["hour"] = lt.date, lt.hour
        return H

    # elektros rinka
    def daily(self) -> pd.DataFrame:
        d = self.W.groupby("date").agg(LT=("LT", "mean"), wind=("wind", "mean"), load=("load", "mean"),
                                       mn=("LT", "min"), mx=("LT", "max"))
        return d

    def sit(self, d: date) -> dict | None:
        r = self.arch.get(d) or {}
        s = r.get("elektros_rinka")
        return s if isinstance(s, dict) and s.get("data") == d.isoformat() else None

    def contributions(self) -> tuple[dict, float, float, int]:
        """Savaitės vidutiniai veiksnių indėliai (iš kasdienių skaidymų), vidutinė norma ir kaina, dienų skaičius."""
        rows = [self.sit(d) for d in self.days]
        rows = [s for s in rows if s and s.get("indėliai")]
        if not rows:
            return {}, None, None, 0
        keys = ["dujos", "vartojimas", "vėjas", "saulė", "pralaidumai", "kaimynai", "kita"]
        c = {k: float(np.mean([float(s["indėliai"].get(k, s["indėliai"].get("jungtys", 0) if k == "pralaidumai" else 0)) for s in rows])) for k in keys}
        norm = float(np.mean([float(s["kaina"]["norma"]) for s in rows]))
        price = float(np.mean([float(s["kaina"]["lt"]) for s in rows]))
        return c, norm, price, len(rows)

    def gas_week(self):
        g = self.gas
        cur = g.loc[pd.Timestamp(self.start - timedelta(days=1)):pd.Timestamp(self.end - timedelta(days=1))]
        prv = g.loc[pd.Timestamp(self.start - timedelta(days=8)):pd.Timestamp(self.end - timedelta(days=8))]
        return cur, prv


# ─── tekstai ─────────────────────────────────────────────────────────────────

REASON_PAST = {("dujos", 1): "brangios dujos", ("dujos", -1): "pigesnės dujos",
               ("vartojimas", 1): "didelis vartojimas", ("vartojimas", -1): "mažesnis vartojimas",
               ("vėjas", 1): "silpnas vėjas", ("vėjas", -1): "stiprus vėjas",
               ("saulė", 1): "maža saulės gamyba", ("saulė", -1): "didelė saulės gamyba",
               ("pralaidumai", 1): "apriboti pralaidumai", ("pralaidumai", -1): "didesni pralaidumai",
               ("kaimynai", 1): "brangesnės kaimynų rinkos", ("kaimynai", -1): "pigesnės kaimynų rinkos ir pilnos eksporto jungtys"}


def fake_inputs(d: date, s: dict):
    inp = A.Inputs.__new__(A.Inputs)
    inp.day, inp.sit, inp.hours, inp.news, inp.world, inp.legal, inp.grants, inp.eb = d, s, None, None, None, None, None, None
    return inp


def day_reason(w: Week, d: date) -> str:
    """Dienos kaina ir pagrindinė jos priežastis – pagal tą pačią taisyklę kaip kasdienio laiško antraštė."""
    s = w.sit(d)
    if not s:
        return "–"
    inp = fake_inputs(d, s)
    st = s["kaina"].get("būsena") or ""
    if st not in A.PRICE_PHRASE:
        return "įprasta kaina"
    word = A.PRICE_PHRASE[st].replace(" nei įprasta", "")
    diff = float(s["kaina"]["lt"]) - float(s["kaina"]["norma"])
    sign = 1 if diff >= 0 else -1
    c = {f: v for f, v in A.contributions(inp).items() if f in dict(A.drivers(inp))}
    if not c:
        return word
    f, v = max(c.items(), key=lambda t: t[1] * sign)
    if v * sign < max(10.0, 0.5 * abs(diff)):
        return word
    return f"{word}: {A.reason(inp, f, sign, intense=False)}"


def electricity_summary(w: Week) -> tuple[str, list[str]]:
    """Antraštinis sakinys ir pastraipos apie savaitės elektros rinką."""
    W, P = w.W, w.P
    avg, prev = float(W.LT.mean()), float(P.LT.mean()) if len(P) else None
    ch = pct_change(avg, prev)
    dd = w.daily()
    hi_d, lo_d = dd.LT.idxmax(), dd.LT.idxmin()
    head = f"Elektra per savaitę vidutiniškai kainavo {num(avg)}{NB}€/MWh"
    if ch is not None:
        head += f" – {abs(round(ch))}{NB}% {'brangiau' if ch > 0 else 'pigiau'} nei ankstesnę savaitę ({num(prev)}{NB}€/MWh)"
    head += "."
    paras = [f"Brangiausia diena – {WD_LOC[hi_d.weekday()]} ({num(dd.LT[hi_d])}{NB}€/MWh), pigiausia – {WD_LOC[lo_d.weekday()]} ({num(dd.LT[lo_d])}{NB}€/MWh). "
             f"Brangiausia valanda – {num(W.LT.max())}{NB}€/MWh ({WD_LOC[W.loc[W.LT.idxmax(), 'date'].weekday()]} {A.hours_label(int(W.loc[W.LT.idxmax(), 'hour']))}), "
             f"pigiausia – {num(W.LT.min())}{NB}€/MWh ({WD_LOC[W.loc[W.LT.idxmin(), 'date'].weekday()]} {A.hours_label(int(W.loc[W.LT.idxmin(), 'hour']))})."]
    c, norm, price, n = w.contributions()
    if c:
        drv = {k: v for k, v in c.items() if k != "kita"}
        sign = 1 if price >= norm else -1
        up = max(drv.items(), key=lambda t: t[1])
        dn = min(drv.items(), key=lambda t: t[1])
        t = []
        if up[1] >= 5:
            t.append(f"Kainą labiausiai kėlė {REASON_PAST[(up[0], 1)]} (vidutiniškai {sgn(up[1])}{NB}€/MWh per dieną)" + _fact_week(w, up[0], 1))
        if dn[1] <= -5:
            t.append(f"{'Labiausiai mažino' if not t else 'Mažino'} {REASON_PAST[(dn[0], -1)]} ({sgn(dn[1])}{NB}€/MWh)" + _fact_week(w, dn[0], -1))
        if t:
            paras.append(". ".join(t) + ".")
    trade = trade_week(w)
    if trade:
        paras.append(trade)
    return head, paras


def _fact_week(w: Week, key: str, sign: int) -> str:
    W, P = w.W, w.P
    if key == "vėjas":
        return (f": vėjo elektrinės vidutiniškai gamino {num(W.wind.mean(), 0)}{NB}MW"
                + (f", ankstesnę savaitę – {num(P.wind.mean(), 0)}{NB}MW" if len(P) else ""))
    if key == "dujos":
        cur, prv = w.gas_week()
        if len(cur):
            t = f": TTF per savaitę – {num(cur.ttf_eur_mwh.min())}–{num(cur.ttf_eur_mwh.max())}{NB}€/MWh"
            return t
    if key == "vartojimas":
        return f": vidutinis vartojimas – {num(W.load.mean(), 0)}{NB}MW" + (f", ankstesnę savaitę – {num(P.load.mean(), 0)}{NB}MW" if len(P) else "")
    if key == "kaimynai":
        return (f": Švedijoje vidutiniškai {num(W.SE4.mean())}, Lenkijoje – {num(W.PL.mean())}, Suomijoje – {num(W.FI.mean())}{NB}€/MWh")
    return ""


def trade_week(w: Week) -> str:
    W = w.W
    parts = []
    for col, txt in (("x_se4", "NordBalt eksportui į Švediją"), ("i_se4", "NordBalt importui iš Švedijos"),
                     ("x_pl", "LitPol eksportui į Lenkiją"), ("i_pl", "LitPol importui iš Lenkijos"), ("i_lv", "jungtys importui iš Latvijos")):
        n = int(np.nansum(W[col]))
        if n >= 12:
            parts.append(f"{txt} – {n}{NB}val.")
    hi = int(((W.LT - W.LV) > 0.5).sum())
    t = ""
    if parts:
        t = "Jungtys buvo pilnai apkrautos: " + ", ".join(parts) + f" (iš {len(W)}{NB}val.)."
    if hi >= 6:
        t += (" " if t else "") + f"Lietuvoje elektra brangesnė nei Latvijoje buvo {hi}{NB}val., vidutiniškai {num(float((W.LT - W.LV).mean()))}{NB}€/MWh per savaitę."
    return t


# ─── teisės aktai, paramos, naujienos ───────────────────────────────────────

def legal_acts(w: Week) -> tuple[list, str]:
    t = w.tar
    if not isinstance(t, dict):
        return [], "Teisės aktų registro duomenų šiai savaitei negauta."
    nuo, iki = d_iso(t.get("nuo")), d_iso(t.get("iki"))
    acts = [a for a in t.get("aktai") or [] if (d := d_iso(a.get("paskelbta_tar"))) and w.start <= d <= w.end]
    note = ""
    if not nuo or not iki or nuo > w.start or iki <= w.end:
        span = f"{range_lt(nuo, iki - timedelta(days=1))}" if nuo and iki and iki > nuo else "–"
        note = f"Teisės aktų registras šiai apžvalgai patikrintas tik už {span.rstrip('.')}"
    return acts, note


GRANT_ORDER = {"didelė": 0, "vidutinė": 1, "maža": 2}


def grants(w: Week) -> dict:
    g = w.grants
    out = {"nauji": [], "baigiasi": [], "planuojami": [], "atviri": 0, "atnaujinta": None}
    if not isinstance(g, dict):
        return out
    out["atnaujinta"] = g.get("atnaujinta")
    first_state = d_iso(g.get("atnaujinta"))
    today = w.monday
    for x in g.get("kvietimai") or []:
        st, start, end, seen = x.get("būklė"), d_iso(x.get("pradžia")), d_iso(x.get("pabaiga")), d_iso(x.get("pirmą_kartą"))
        if st == "planuojamas":
            out["planuojami"].append(x)
            continue
        if end and end < today:
            continue
        out["atviri"] += 1
        new = (start and w.start <= start <= today + timedelta(days=7))
        if new:
            out["nauji"].append(x)
        if end and today <= end <= today + timedelta(days=14):
            out["baigiasi"].append(x)
    out["baigiasi"].sort(key=lambda x: x.get("pabaiga") or "")
    return out


def week_news(w: Week) -> list[dict]:
    items, seen = [], set()
    for x in (w.extra_news.get("naujienos") or []):
        if (d := d_iso(x.get("data") or x.get("paskelbta"))) and w.start <= d <= w.end:
            items.append(dict(x, rankinė=True))
    for d in w.days:
        n = (w.arch.get(d) or {}).get("naujienos") or {}
        for x in n.get("naujienos") or []:
            items.append(x)
    out = []
    for x in items:
        key = (x.get("nuoroda") or "") or re.sub(r"\W+", " ", (x.get("pavadinimas") or "").lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
    out.sort(key=lambda x: (GRANT_ORDER.get(x.get("svarba"), 3), 0 if x.get("rankinė") else 1, -(datetime.fromisoformat(x["paskelbta"]).timestamp() if x.get("paskelbta") else 0)))
    return out[:6]


# ─── laiškas ─────────────────────────────────────────────────────────────────

def mail_essence(w: Week, head: str, acts, acts_note, gr, news) -> str:
    rows = []
    avg, prev = float(w.W.LT.mean()), (float(w.P.LT.mean()) if len(w.P) else None)
    ch = pct_change(avg, prev)
    el = f"<b>{num(avg)}{NB}€/MWh</b> vidutiniškai" + (f" – {abs(round(ch))}{NB}% {'brangiau' if ch > 0 else 'pigiau'} nei ankstesnę savaitę" if ch is not None else "") + "."
    dd = w.daily()
    hi_d, lo_d = dd.LT.idxmax(), dd.LT.idxmin()
    el += f" Brangiausia – {WD_LOC[hi_d.weekday()]} ({num(dd.LT[hi_d], 0)}), pigiausia – {WD_LOC[lo_d.weekday()]} ({num(dd.LT[lo_d], 0)}{NB}€/MWh)."
    rows.append(("Elektra", el))
    if acts:
        n = len(acts)
        rows.append(("Teisės aktai", e(f"{n} {plural(n, 'energetikai aktualus aktas', 'energetikai aktualūs aktai', 'energetikai aktualių aktų')}: {acts[0].get('pavadinimas', '').rstrip('.')}.")))
    elif acts_note:
        rows.append(("Teisės aktai", e(dot(acts_note))))
    gtxt = []
    if gr["nauji"]:
        gtxt.append("nauji: " + join_lt([short_grant(x) for x in gr["nauji"][:2]]))
    if gr["baigiasi"]:
        gtxt.append("baigiasi: " + join_lt([f"{short_grant(x)} ({md(x.get('pabaiga'))})" for x in gr["baigiasi"][:2]]))
    if gtxt:
        rows.append(("Paramos", e(cap("; ".join(gtxt)) + ".")))
    if news:
        rows.append(("Naujienos", e(f"Svarbiausia: {news[0]['pavadinimas'].rstrip('.')}." + (f" Dar {len(news) - 1} {plural(len(news) - 1, 'naujiena', 'naujienos', 'naujienų')}." if len(news) > 1 else ""))))
    tr = "".join(f'<tr><td width="28%" valign="top" style="padding:9px 8px 9px 0; border-top:1px solid {LINE}; font-family:{FONT}; font-size:14px; line-height:20px; font-weight:bold; color:{INK};">{t}</td>'
                 f'<td valign="top" style="padding:9px 0; border-top:1px solid {LINE}; font-family:{FONT}; font-size:14px; line-height:20px; color:{INK2};">{x}</td></tr>' for t, x in rows)
    return m_section("Savaitės esmė", None, m_table(tr), first=True)


def md(x) -> str:
    d = d_iso(x)
    return f"{MONTHS[d.month - 1]} {d.day} d." if d else "–"


def mmdd(x) -> str:
    d = d_iso(x)
    return f"{d.month:02d}-{d.day:02d}" if d else "–"


def short_grant(x) -> str:
    t = re.sub(r"\s+\d{4}-\d{2}$", "", x.get("pavadinimas") or "").strip()
    return t


def mail_electricity(w: Week, head: str, paras: list[str]) -> str:
    inner = m_p(e(head), 16, 22, INK, " font-weight:bold;") + "".join(m_p(e(t), 14, 21, INK2, " padding-top:6px;") for t in paras)
    # kaina pagal dienas
    dd = w.daily()
    vmax = max(float(dd.LT.max()), 1)
    tr = ""
    for d, r in dd.iterrows():
        reason = day_reason(w, d)
        tr += (f'<tr><td width="20%" style="padding:6px 8px 6px 0; border-bottom:1px solid {LINE}; font-family:{FONT}; font-size:13px; line-height:18px; color:{INK};">'
               f'<b>{cap(WD_SHORT[d.weekday()])}</b> <span style="color:{INK3};">{d.month:02d}-{d.day:02d}</span></td>'
               f'<td width="34%" valign="middle" style="padding:6px 0; border-bottom:1px solid {LINE};">{m_bar(float(r.LT) / vmax * 100, INK)}</td>'
               f'<td align="right" width="14%" style="padding:6px 8px; border-bottom:1px solid {LINE}; font-family:{FONT}; font-size:13px; line-height:18px; font-weight:bold; color:{INK}; text-align:right;">{num(r.LT)}</td>'
               f'<td style="padding:6px 0; border-bottom:1px solid {LINE}; font-family:{FONT}; font-size:12px; line-height:17px; color:{INK2};">{e(reason)}</td></tr>')
    inner += m_sub("Kaina pagal dienas", "paros vidurkiai, €/MWh; priežastis – pagrindinis dienos veiksnys") + m_table(tr)
    # kas lėmė kainą per savaitę
    c, norm, price, n = w.contributions()
    if c:
        names = dict(A.DRIVERS)
        items = sorted([(v, k, names.get(k, k)) for k, v in c.items() if k != "kita"], key=lambda t: -t[0]) + [(c.get("kita", 0.0), "kita", "Kita")]
        maxpos = max([v for v, _, _ in items if v > 0] + [1])
        maxneg = max([-v for v, _, _ in items if v < 0] + [1])
        unit = min(40 / maxpos, 20 / maxneg)
        tr = ""
        for v, key, name in items:
            r_ = round(v)
            col = A.OTHER_BAR if key == "kita" else (A.RAISE_BAR if v > 0 else A.LOWER_BAR)
            neg = m_bar(abs(v) * unit / 20 * 100, col, "right") if r_ < 0 else "&nbsp;"
            pos = m_bar(abs(v) * unit / 40 * 100, col, "left") if r_ > 0 else "&nbsp;"
            tr += (f'<tr><td width="34%" style="padding:4px 8px 4px 0; font-family:{FONT}; font-size:13px; line-height:18px; color:{INK};">{name}</td>'
                   f'<td width="20%" valign="middle" style="padding:4px 0;">{neg}</td>'
                   f'<td width="1" bgcolor="{INK}" style="width:1px; background:{INK}; font-size:0; line-height:0;">&nbsp;</td>'
                   f'<td width="40%" valign="middle" style="padding:4px 0;">{pos}</td>'
                   f'<td align="right" style="padding:4px 0; font-family:{FONT}; font-size:14px; line-height:18px; font-weight:bold; color:{INK}; text-align:right;">{sgn(r_)}</td></tr>')
        tr += (f'<tr><td colspan="4" style="padding:8px 0 0; border-top:1px solid {INK}; font-family:{FONT}; font-size:13px; line-height:20px; color:{INK2};">'
               f'įprastomis sąlygomis <b style="color:{INK};">{num(norm, 0)}</b> → per savaitę <b style="color:{INK};">{num(price)}</b></td>'
               f'<td align="right" style="padding:8px 0 0; border-top:1px solid {INK}; font-family:{FONT}; font-size:16px; line-height:20px; font-weight:bold; color:{INK}; text-align:right;">{sgn(price - norm)}</td></tr>')
        inner += m_sub("Kas lėmė kainą per savaitę", f"dienų nuokrypių nuo įprastos kainos vidurkis, €/MWh ({n} {plural(n, 'diena', 'dienos', 'dienų')})") + m_table(tr)
    # kaimynai
    W, P = w.W, w.P
    rows = m_th([("Zona", "", "left"), ("Kaina, €/MWh", "22%", "right"), ("Ankstesnė sav.", "22%", "right"), ("Srautas, MW", "24%", "right")])

    def flow(a, b):
        v = float((W[a] - W[b]).mean()) if a in W and b in W else None
        if v is None or np.isnan(v) or abs(v) < 5:
            return "–"
        return f"į LT {num(v, 0)}" if v > 0 else f"iš LT {num(-v, 0)}"
    for z, name, fl in (("LT", "Lietuva", None), ("LV", "Latvija", ("prekyba_LV_LT", "prekyba_LT_LV")), ("SE4", "Švedija (SE4)", ("prekyba_SE4_LT", "prekyba_LT_SE4")),
                        ("PL", "Lenkija", ("prekyba_PL_LT", "prekyba_LT_PL")), ("FI", "Suomija", None), ("EE", "Estija", None)):
        cur = float(W[z].mean())
        prv = float(P[z].mean()) if len(P) else None
        rows += ("<tr>" + m_td(name, bold=True, color=INK) + m_td(num(cur), "right", True, INK) + m_td(num(prv) if prv is not None else "–", "right")
                 + m_td(flow(*fl) if fl else "–", "right") + "</tr>")
    inner += m_sub("Kaimynai", "savaitės vidurkiai; srautas – kitos paros prekybos grafikų vidurkis") + m_table(rows)
    # sąlygos
    cur_g, prv_g = w.gas_week()
    rows = m_th([("Rodiklis", "", "left"), ("Per savaitę", "26%", "right"), ("Ankstesnė sav.", "26%", "right")])
    for lbl, a_, b_, unit_ in (("Vartojimas", W.load.mean(), P.load.mean() if len(P) else None, "MW"),
                               ("Vėjas", W.wind.mean(), P.wind.mean() if len(P) else None, "MW"),
                               ("Saulė", W.sol.mean(), P.sol.mean() if len(P) else None, "MW"),
                               ("Dujos (TTF)", cur_g.ttf_eur_mwh.mean() if len(cur_g) else None, prv_g.ttf_eur_mwh.mean() if len(prv_g) else None, "€/MWh"),
                               ("Taršos leidimai (ATL)", cur_g.eua_eur_t.mean() if len(cur_g) else None, prv_g.eua_eur_t.mean() if len(prv_g) else None, "€/t")):
        d_ = 0 if unit_ == "MW" else 1
        rows += ("<tr>" + m_td(lbl, bold=True, color=INK) + m_td(f"{num(a_, d_)}{NB}{unit_}" if a_ is not None and not pd.isna(a_) else "–", "right", True, INK)
                 + m_td(f"{num(b_, d_)}" if b_ is not None and not pd.isna(b_) else "–", "right") + "</tr>")
    inner += m_sub("Sąlygos", "savaitės vidurkiai; vėjas, saulė ir vartojimas – kitos paros prognozės") + m_table(rows)
    inner += m_p("Šaltiniai: ENTSO-E, Elering, Litgrid, EEX, ICE (TTF). Skaičiavimai – MB Enerconsult.", 11, 16, INK3, " padding-top:10px;")
    cap_ = f"{range_lt(w.start, w.end)} · kitos paros rinka" + ("" if w.complete else " · savaitė dar nesibaigė")
    return m_section("Elektros rinka per savaitę", cap_, inner)


def mail_legal(w: Week, acts, note) -> str:
    if acts:
        rows = m_th([("Aktas", "", "left"), ("Institucija, paskelbta", "30%", "left"), ("Įsigalioja", "16%", "right")])
        for a in acts:
            pub = (a.get("paskelbta_tar") or "")[:10]
            rows += ("<tr>" + m_td(e(a.get("pavadinimas") or "") + f'<br><span style="font-weight:normal; font-size:11px; color:{INK3};">{e(a.get("sritis") or "")}</span>', bold=True, color=INK)
                     + m_td(e(f"{A.inst_short(a.get('priemusi_inst'))}, {pub}".strip(", "))) + m_td(e((a.get("isigalioja") or "–")[:10]), "right") + "</tr>")
        inner = m_table(rows)
    else:
        inner = m_p(e("Šią savaitę energetikai aktualių teisės aktų Teisės aktų registre nepaskelbta." if not note else "Energetikai aktualių teisės aktų nerasta."), 14, 21, INK2)
    if note:
        inner += m_p(e(dot(note)), 12, 18, INK3, " font-style:italic; padding-top:8px;")
    return m_section("Teisės aktai", "Teisės aktų registre paskelbti energetikai aktualūs aktai", inner)


def grant_rows(xs, show_end=True) -> str:
    rows = m_th([("Kvietimas", "", "left"), ("Suma", "24%", "right"), ("Terminas", "16%", "right")])
    for x in xs:
        title = e(short_grant(x)) + f'<br><span style="font-weight:normal; font-size:11px; color:{INK3};">{e(x.get("institucija") or "–")}'
        if x.get("pareiškėjai"):
            title += f" · {e(x['pareiškėjai'])}"
        title += "</span>"
        when = mmdd(x.get("pabaiga")) if show_end and x.get("pabaiga") else ("nuo " + mmdd(x.get("pradžia")) if x.get("pradžia") else "–")
        rows += "<tr>" + m_td(title, bold=True, color=INK) + m_td(e(fmt_sum(x.get("suma"))), "right") + m_td(e(when), "right") + "</tr>"
    return m_table(rows)


def mail_grants(w: Week, gr) -> str:
    inner = ""
    if gr["nauji"]:
        inner += m_sub("Nauji ir prasideda") + grant_rows(gr["nauji"], show_end=False)
    if gr["baigiasi"]:
        inner += m_sub("Baigiasi per 14 dienų") + grant_rows(gr["baigiasi"])
    if gr["planuojami"]:
        pl = sorted(gr["planuojami"], key=lambda x: (0 if d_iso(x.get("pradžia")) else 1, x.get("pradžia") or "", -sum_mln(x.get("suma"))))
        inner += m_sub("Planuojami", "pirmiausia – su žinoma pradžios data") + grant_rows(pl[:5], show_end=False)
        if len(pl) > 5:
            n = len(pl) - 5
            inner += m_p(e(f"Dar {n} {plural(n, 'planuojamas kvietimas', 'planuojami kvietimai', 'planuojamų kvietimų')}."), 12, 18, INK3, " padding-top:6px;")
    if not inner:
        inner = m_p("Paramų pokyčių šią savaitę nėra.", 14, 21, INK2)
    note = f"Iš viso stebima atvirų kvietimų: {gr['atviri']}"
    if gr.get("atnaujinta"):
        note += f"; būsena – {md(gr['atnaujinta']).rstrip('.')}"
    inner += m_p(e(dot(note) + " Šaltiniai: APVA, ES investicijos, LEA, ILTE, Inovacijų agentūra, Energetikos ministerija."), 11, 16, INK3, " padding-top:10px;")
    return m_section("Paramos ir finansavimas", "Kvietimai energetikai: nauji, besibaigiantys ir planuojami", inner)


def mail_news(news) -> str:
    top, rest = news[0], news[1:]
    when = lambda x: (x.get("paskelbta") or "")[:10]
    lead = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;"><tr><td bgcolor="#F4F5F2" style="padding:12px 14px; background:#F4F5F2;">'
            + m_p("Svarbiausia", 11, 16, INK3, " font-weight:bold;")
            + m_p(e(top["pavadinimas"]), 15, 21, INK, " font-weight:bold; padding-top:2px;")
            + m_p(e(top["santrauka"]), 13, 19, INK2, " padding-top:4px;")
            + m_p(e(f"{top.get('šaltinis') or ''} · {md(when(top))}"), 11, 16, INK3, " font-style:italic; padding-top:4px;")
            + "</td></tr></table>")
    body = ""
    for reg in A.REGIONS:
        rows = [n for n in rest if n.get("regionas") == reg]
        if not rows:
            continue
        body += m_p(reg, 13, 18, INK, f" font-weight:bold; padding-top:16px; padding-bottom:2px; border-bottom:1px solid {INK};")
        for n in rows:
            body += (f'<div style="padding:8px 0; border-bottom:1px solid {LINE};">' + m_p(e(n["pavadinimas"]), 13, 19, INK, " font-weight:bold;")
                     + m_p(e(n["santrauka"]), 13, 19, INK2) + m_p(e(f"{n.get('šaltinis') or ''} · {md(when(n))}"), 11, 16, INK3, " font-style:italic;") + "</div>")
    return m_section("Savaitės naujienos", "Svarbiausios savaitės energetikos naujienos · šaltinis nurodytas prie kiekvienos", lead + body)


def mail_ahead(w: Week, gr, acts) -> str:
    today = w.monday
    items = []
    for x in sorted(gr["baigiasi"], key=lambda x: x.get("pabaiga") or ""):
        if (d := d_iso(x.get("pabaiga"))) and d <= today + timedelta(days=7):
            items.append(f"{md(x['pabaiga'])} – baigiasi paraiškų priėmimas: {short_grant(x)} ({x.get('institucija') or '–'}).")
    for x in gr["nauji"]:
        if (d := d_iso(x.get("pradžia"))) and today <= d <= today + timedelta(days=7):
            items.append(f"{md(x['pradžia'])} – prasideda paraiškų priėmimas: {short_grant(x)} ({x.get('institucija') or '–'}).")
    for a in acts:
        if (d := d_iso(a.get("isigalioja"))) and today <= d <= today + timedelta(days=7):
            items.append(f"{md(a['isigalioja'])} – įsigalioja: {a.get('pavadinimas', '').rstrip('.')}.")
    if not items:
        return ""
    items.sort(key=lambda t: d_iso_lt(t))
    tr = "".join(f'<tr><td width="14" valign="top" style="padding:4px 0; font-family:{FONT}; font-size:13px; line-height:19px; color:{INK};">–</td>'
                 f'<td style="padding:4px 0; font-family:{FONT}; font-size:13px; line-height:19px; color:{INK2};">{e(t)}</td></tr>' for t in items)
    return m_section("Savaitė prieš akis", f"{range_lt(today, today + timedelta(days=6))} · terminai ir įsigaliojimai", m_table(tr))


def email(w: Week) -> tuple[str, str]:
    head, paras = electricity_summary(w)
    acts, acts_note = legal_acts(w)
    gr = grants(w)
    news = week_news(w)
    rows = []
    rows.append(m_tr(m_table(
        f'<tr><td style="padding:0 0 12px; border-bottom:1px solid #A6A6A6; font-family:{FONT}; font-size:20px; line-height:26px; font-weight:bold; letter-spacing:0.02em; color:{INK};">{m_a(A.SITE_URL, "Enerconsult", INK, False)}</td>'
        f'<td align="right" style="padding:0 0 12px; border-bottom:1px solid #A6A6A6; font-family:{FONT}; font-size:12px; line-height:26px; font-style:italic; color:{INK3}; text-align:right;">kas pirmadienį</td></tr>'),
        "24px 36px 0"))
    rows.append(m_tr(m_p("Morning Report", 26, 32, INK, " font-weight:bold;") + m_p(f"Savaitės apžvalga · {w.start.year} m. {range_lt(w.start, w.end)}", 14, 20, INK, " padding-top:2px;")
                     + m_p("Lietuvos energetikos rinka per savaitę: elektros kainos, teisės aktai, paramos ir naujienos", 12, 18, INK3, " font-style:italic; padding-top:2px;"),
                     "18px 36px 0"))
    rows.append(m_tr(A.m_button(A.MR_URL, "Kasdienė ataskaita svetainėje →"), "14px 36px 0"))
    if not w.complete:
        rows.append(m_tr(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="#FFF4DC" style="padding:10px 12px; background:#FFF4DC; border-left:4px solid #F2A93B;">'
                         + m_p(e(dot(f"Peržiūra: duomenys iki {md(w.end.isoformat())}")), 13, 19, INK) + "</td></tr></table>", "16px 36px 0"))
    rows.append(mail_essence(w, head, acts, acts_note, gr, news))
    rows.append(mail_electricity(w, head, paras))
    rows.append(mail_legal(w, acts, acts_note))
    rows.append(mail_grants(w, gr))
    if news:
        rows.append(mail_news(news))
    ah = mail_ahead(w, gr, acts)
    if ah:
        rows.append(ah)
    rows.append(m_tr(f'<div style="border-top:1px solid {LINE}; padding-top:18px;">' + m_p("Iki kito pirmadienio.", 16, 22, INK, " font-weight:bold;")
                     + m_p(f'Kasdienė Morning Report ataskaita su rinkos žemėlapiu ir archyvu – {m_a(A.MR_URL, "enerconsult.lt/morning-report")}.', 12, 18, INK2, " padding-top:8px;")
                     + m_p(f'Gavote persiųstą laišką? {m_a(A.MR_URL + "#prenumerata", "Užsiprenumeruokite")}.', 12, 18, INK2, " padding-top:6px;")
                     + m_p(A.unsub_line(), 12, 18, INK2, " padding-top:6px;")
                     + f'<p style="margin:14px 0 0; padding-top:12px; border-top:1px solid #A6A6A6; font-family:{FONT}; font-size:11px; line-height:16px; text-align:center; color:{INK3};">Morning Report · {m_a(A.SITE_URL, "MB Enerconsult", INK3)} · {m_a(A.PRIVACY_URL, "Privatumo politika", INK3)}</p></div>',
                     "26px 36px 28px"))
    body = "\n".join(rows)
    pre = head
    html_ = f'''<!doctype html>
<html lang="lt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no, date=no, address=no">
<style>
@media only screen and (max-width: 480px) {{
  .mr-px {{ padding-left: 16px !important; padding-right: 16px !important; }}
  .mr-outer {{ padding: 8px 0 !important; }}
}}
</style>
<title>Morning Report · savaitės apžvalga · {e(range_lt(w.start, w.end))}</title>
</head>
<body style="margin:0; padding:0; background:#F2F2F2;">
<div style="display:none; max-height:0; overflow:hidden; mso-hide:all;">{e(pre)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F2F2F2" style="background:#F2F2F2;">
<tr><td class="mr-outer" align="center" style="padding:24px 10px;">
<!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF" style="max-width:600px; background:#FFFFFF; border-collapse:collapse;">
{body}
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr>
</table>
</body>
</html>
'''
    subject = f"Morning Report · savaitės apžvalga · {range_lt(w.start, w.end)} · LT {num(float(w.W.LT.mean()), 0)} €/MWh".replace(NB, " ")
    return html_, subject


def main() -> None:
    args = sys.argv[1:]
    data_dir = os.path.join(ROOT, "data")
    out_dir = os.path.join(ROOT, "public", "savaitė")
    iki = None
    if "--data" in args:
        data_dir = args[args.index("--data") + 1]
    if "--out" in args:
        out_dir = args[args.index("--out") + 1]
    if "--iki" in args:
        iki = date.fromisoformat(args[args.index("--iki") + 1])
    pos = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] not in ("--data", "--out", "--iki"))]
    today = date.fromisoformat(pos[0]) if pos else datetime.now(VLN).date()
    monday = today - timedelta(days=today.weekday())
    w = Week(data_dir, monday, iki)
    mail, subject = email(w)
    os.makedirs(out_dir, exist_ok=True)

    def write(rel, text):
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    bad = re.findall(r'style="[^"]*[{}\[\]][^"]*"', mail)
    if bad or not re.fullmatch(r"#[0-9A-Fa-f]{6}", LINE):
        sys.exit(f"Sugadinti stiliai savaitės apžvalgoje ({len(bad)}), pvz. {bad[:1]} – nesiunčiama")
    write("laiskas.html", mail)
    assert A.unsub_line() in mail
    write("laiskas-brevo.html", mail.replace(A.unsub_line(), A.brevo_footer_lines()))
    write("tema.txt", subject + "\n")
    write("data.txt", monday.isoformat())
    print(f"Savaitės apžvalga {w.start}–{w.end} ({'pilna' if w.complete else 'dalinė'}): {len(mail)} B → {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
