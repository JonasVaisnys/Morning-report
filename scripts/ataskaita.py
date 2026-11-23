#!/usr/bin/env python3
"""
Morning Report ataskaitos surinkimas — MB Enerconsult
======================================================
Iš eigos surinktų duomenų (aplankas data/) pagamina failus svetainei ir laiškui
(aplankas public/) pagal „Morning Report fragmento specifikaciją“:

  public/skiltis.html               šiandienos fragmentas svetainei
  public/archyvas/MMMM-MM-DD.html   tas pats fragmentas archyvui
  public/archyvas/index.json        archyvo sąrašas [{"data", "akcentas"}]
  public/laiskas.html               el. laiškas (be paveikslėlių; nuorodos – tik į Enerconsult svetainę)
  public/tema.txt                   laiško tema
  public/data.txt                   ataskaitos data MMMM-MM-DD – rašoma paskutinė

Blokai: Apžvalga, Šiandien (paramų terminai, įsigaliojantys teisės aktai), Elektros rinka, Naujienos,
Pasaulio rinkos. Teisės aktų ir paramų sąrašai – pirmadienio savaitės apžvalgoje („savaitės ataskaita.py“;
kasdieniame laiške juos grąžina DAILY_SLOW = True). Bloko, kurio duomenų tą dieną nėra, nėra ir ataskaitoje.

Be to, data/archyvas/MMMM-MM-DD.json – glaustas dienos duomenų įrašas savaitės apžvalgai.

Duomenys (visi nebūtini):
  data/elektros-rinka.json      „rinkos situacija.py“ (kainos, srautai, normos, veiksniai)
  data/energijos-brifingas.json „kainų brifingas.py“ (valandinės kainos; atsarginis variantas)
  data/naujienos.json           „naujienų brifingas.py“
  data/pasaulio-rinkos.json     „pasaulio rinkos.py“
  data/teisės-aktai.json        „teisės aktai.py“
  data/paramos.json             paramų rinkimas (kai bus)

Naudojimas:
  python scripts/ataskaita.py                 # šiandien (Vilniaus laiku)
  python scripts/ataskaita.py 2026-11-16
  python scripts/ataskaita.py 2026-11-12 --data kitas/data --out kitas/public
"""

import html
import json
import math
import os
import re
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
RES = os.path.join(HERE, "ištekliai")
VLN = ZoneInfo("Europe/Vilnius")

NB = "\u00a0"
MINUS = "−"
MONTHS = ["sausio", "vasario", "kovo", "balandžio", "gegužės", "birželio", "liepos", "rugpjūčio",
          "rugsėjo", "spalio", "lapkričio", "gruodžio"]
WEEKDAYS = ["pirmadienis", "antradienis", "trečiadienis", "ketvirtadienis", "penktadienis", "šeštadienis", "sekmadienis"]
REGIONS = ["Lietuva", "Kaimynai", "Europa", "Pasaulis"]
REGIONS_ACC = {"Lietuva": "Lietuvą", "Kaimynai": "kaimynus", "Europa": "Europą", "Pasaulis": "pasaulį"}
SEV = {"kritinė": "critical", "didelė": "high", "vidutinė": "medium", "maža": "low"}

# tamsios figūros (kaip galutiniame energijos dizaine)
D_BG, D_INK, D_MUTED = "#0B1B34", "#EAF0F8", "#A9B8CF"
# laiškas (brandbook, šviesus)
INK, INK2, INK3, LINE = "#16264C", "#3B4A68", "#56627A", "#D5D9DE"
RAISE_BAR, LOWER_BAR, OTHER_BAR = "#C55A11", "#2B5797", "#808080"
RAISE_TXT, LOWER_TXT = "#A84A0C", "#2B5797"
SEV_COLOR = {"kritinė": "#C00000", "didelė": "#C55A11", "vidutinė": "#BF8F00", "maža": "#808080"}
FONT = "'Segoe UI', Arial, Helvetica, sans-serif"

# Laiško nuorodos (2026-11-19 sprendimas: laiške – nuorodos į svetainę, turinys lieka laiške)
SITE_URL = "https://enerconsult.lt/"
MR_URL = "https://enerconsult.lt/morning-report/"
MR_MAILBOX = "morningreport@enerconsult.lt"
PRIVACY_URL = "https://enerconsult.lt/privatumo-politika/"


# ─── pagalbinės ──────────────────────────────────────────────────────────────

def num(x: float, d: int = 1) -> str:
    """Lietuviškas skaičius: tarpas tūkstančiams, dešimtainis kablelis, minuso ženklas."""
    r = round(float(x), d)
    s = f"{abs(r):,.{d}f}".replace(",", " ").replace(".", ",").replace(" ", NB)
    return (MINUS if r < 0 else "") + s


def sgn(x: float, d: int = 0) -> str:
    r = round(float(x), d)
    if r == 0:
        return "0"
    return ("+" if r > 0 else MINUS) + num(abs(r), d)


def pct(x: float, d: int = 1) -> str:
    return sgn(x, d) + NB + "%"


def e(s) -> str:
    return html.escape(str(s), quote=False)


def ea(s) -> str:
    return html.escape(str(s), quote=True)


def day_short(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day} d."


def day_long(d: date) -> str:
    return f"{d.year} m. {MONTHS[d.month - 1]} {d.day} d., {WEEKDAYS[d.weekday()]}"


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def join_lt(items: list[str]) -> str:
    """„a, b ir c“ – prieš vienintelį „ir“ kablelio nėra."""
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " ir " + items[-1]


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 9 and not 12 <= n % 100 <= 19:
        return few
    return many


def hours_label(h: int) -> str:
    return f"{h}–{h + 1}{NB}val."


def load(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read().strip()
        return json.loads(txt) if txt else None
    except Exception:
        return None


def https(url) -> bool:
    return isinstance(url, str) and url.startswith(("https://", "http://"))


# ─── duomenys ────────────────────────────────────────────────────────────────

class Inputs:
    def __init__(self, data_dir: str, day: date):
        self.day = day
        self.data_dir = data_dir
        iso = day.isoformat()
        sit = load(os.path.join(data_dir, "elektros-rinka.json"))
        self.sit = sit if isinstance(sit, dict) and sit.get("data") == iso and "kaina" in sit else None
        eb = load(os.path.join(data_dir, "energijos-brifingas.json"))
        self.hours = None
        if isinstance(eb, dict) and eb.get("data") == iso:
            zones = eb.get("zonos") or {}
            vh = (zones.get("LT") or zones.get("LT/LV") or {}).get("valandinės") or {}   # sutapus kainoms zona vadinasi „LT/LV“
            hrs = [vh.get(f"{h:02d}:00") for h in range(24)]
            if all(isinstance(v, (int, float)) for v in hrs):
                self.hours = [float(v) for v in hrs]
        self.eb = eb if self.hours else None
        nw = load(os.path.join(data_dir, "naujienos.json"))
        self.news = nw if isinstance(nw, dict) and nw.get("data") == iso and nw.get("naujienos") else None
        pr = load(os.path.join(data_dir, "pasaulio-rinkos.json"))
        ok = isinstance(pr, dict) and pr.get("data") == iso and isinstance(pr.get("rodikliai"), dict) \
            and all(isinstance(pr["rodikliai"].get(k), dict) for k in ("brent", "ttf", "eua"))
        self.world = pr if ok else None
        ta = load(os.path.join(data_dir, "teisės-aktai.json"))
        self.legal = ta if isinstance(ta, dict) and not ta.get("error") and ta.get("data") == iso and ta.get("energy_relevant") else None
        pa = load(os.path.join(data_dir, "paramos.json"))
        self.grants = pa if isinstance(pa, dict) and pa.get("data") == iso and pa.get("kvietimai") else None

    @property
    def price(self):
        if self.sit:
            return float(self.sit["kaina"]["lt"])
        if self.hours:
            return sum(self.hours) / 24
        return None


# ─── elektros rinka: tekstai ─────────────────────────────────────────────────

DRIVERS = [("dujos", "Dujos ir CO2"), ("kaimynai", "Kaimynų rinkos"), ("vartojimas", "Vartojimas"),
           ("pralaidumai", "Pralaidumai"), ("saulė", "Saulė"), ("vėjas", "Vėjas")]
REASON = {("dujos", 1): "brangios dujos", ("dujos", -1): "pigesnės dujos",
          ("vartojimas", 1): "didelis vartojimas", ("vartojimas", -1): "mažesnis vartojimas",
          ("vėjas", 1): "silpnas vėjas", ("vėjas", -1): "stiprus vėjas",
          ("saulė", 1): "maža saulės gamyba", ("saulė", -1): "didelė saulės gamyba",
          ("pralaidumai", 1): "apriboti pralaidumai", ("pralaidumai", -1): "didesni pralaidumai",
          ("kaimynai", 1): "brangesnės kaimynų rinkos", ("kaimynai", -1): "pigesnės kaimynų rinkos"}
REASON_GEN = {("dujos", 1): "brangių dujų", ("dujos", -1): "pigesnių dujų",
              ("vartojimas", 1): "didelio vartojimo", ("vartojimas", -1): "mažesnio vartojimo",
              ("vėjas", 1): "silpno vėjo", ("vėjas", -1): "stipraus vėjo",
              ("saulė", 1): "mažos saulės gamybos", ("saulė", -1): "didelės saulės gamybos",
              ("pralaidumai", 1): "apribotų pralaidumų", ("pralaidumai", -1): "didesnių pralaidumų",
              ("kaimynai", 1): "brangesnių kaimynų rinkų", ("kaimynai", -1): "pigesnių kaimynų rinkų"}
PRICE_PHRASE = {"gerokai mažesnė nei įprasta": "gerokai pigiau nei įprasta", "mažesnė nei įprasta": "pigiau nei įprasta",
                "didesnė nei įprasta": "brangiau nei įprasta", "gerokai didesnė nei įprasta": "gerokai brangiau nei įprasta"}
# būsenos žodis, kuris atitinka veiksnio kryptį (pvz. vėjas mažina kainą, kai jo „didesnė nei įprasta“)
STATE_DIR = {"vėjas": {-1: "didesn", 1: "mažesn"}, "saulė": {-1: "didesn", 1: "mažesn"},
             "vartojimas": {1: "didesn", -1: "mažesn"}, "dujos": {1: "brangesn", -1: "pigesn"}}
# zonų linksniai: vardininkas, kilmininkas, galininkas, vietininkas
ZN = {"SE4": ("Švedija", "Švedijos", "Švediją", "Švedijoje"), "PL": ("Lenkija", "Lenkijos", "Lenkiją", "Lenkijoje"),
      "LV": ("Latvija", "Latvijos", "Latviją", "Latvijoje"), "EE": ("Estija", "Estijos", "Estiją", "Estijoje"),
      "FI": ("Suomija", "Suomijos", "Suomiją", "Suomijoje")}
# jungtys: pralaidumų rakto pavadinimas ir trumpas vardas (ne LINE – tai linijų spalva!)
IC_LINES = {"SE4": ("LT–SE4 (NordBalt)", "NordBalt"), "PL": ("LT–PL (LitPol)", "LitPol"), "LV": ("LT–LV", "jungtys su Latvija")}
KITA_RIBA = 10.0          # €/MWh: didesnė „kita“ paaiškinama tekste (Jono sprendimas 2026-11-21)
FULL_HOURS = 12           # jungtis „pilnai apkrauta“ tekste, kai pilna bent tiek valandų


def energy_headline(inp: Inputs) -> str:
    p = inp.price
    if inp.sit:
        st = inp.sit["kaina"]["būsena"]
        if st in PRICE_PHRASE:
            why = headline_reason(inp)
            return f"Elektra šiandien {num(p)}{NB}€/MWh – {PRICE_PHRASE[st]}" + (f" dėl {why}." if why else ".")
        return f"Elektra šiandien {num(p)}{NB}€/MWh – įprasta kaina šiam metų laikui."
    return f"Elektra šiandien vidutiniškai {num(p)}{NB}€/MWh."


def contributions(inp: Inputs) -> dict:
    c = {k: float(v) for k, v in (inp.sit or {}).get("indėliai", {}).items()}
    if "jungtys" in c:                      # suderinamumas, jei kada nors būtų rašoma „jungtys“
        c["pralaidumai"] = c.pop("jungtys")
    return c


def caps_known(inp: Inputs) -> bool:
    """Ar gauti bent vienos jungties importo pralaidumai (be jų ribojimo vertinti negalima)."""
    pr = (inp.sit or {}).get("pralaidumai") or {}
    return any(isinstance(v, dict) and (v.get("importas") or {}).get("mw") is not None for v in pr.values())


def drivers(inp: Inputs) -> list:
    """Veiksniai be pralaidumų, kai jų duomenų nėra (tada jų indėlis 0 nieko nereiškia)."""
    return [(k, n) for k, n in DRIVERS if k != "pralaidumai" or caps_known(inp)]


def _strong(inp: Inputs, key: str, sign: int) -> bool:
    st = ((inp.sit or {}).get(key) or {}).get("būsena") or ""
    return st.startswith("gerokai") and STATE_DIR.get(key, {}).get(sign, "§") in st


def reason(inp: Inputs, key: str, sign: int, gen: bool = False, intense: bool = True) -> str:
    if key == "kaimynai":
        return neighbours_reason(inp, sign, gen)
    base = (REASON_GEN if gen else REASON)[(key, sign)]
    return ("labai " + base) if intense and _strong(inp, key, sign) else base


def lines_full(inp: Inputs) -> tuple[list, list]:
    """Jungtys, kurios eksportui ar importui pilnai apkrautos bent FULL_HOURS valandų."""
    jb = (inp.sit or {}).get("jungtys_būsena") or {}
    exp = [z for z in ("SE4", "PL") if (jb.get(z) or {}).get("eksportas_pilnas_val", 0) >= FULL_HOURS]
    imp = [z for z in ("SE4", "PL", "LV") if (jb.get(z) or {}).get("importas_pilnas_val", 0) >= FULL_HOURS]
    return exp, imp


def neighbours_reason(inp: Inputs, sign: int, gen: bool = False) -> str:
    det = (inp.sit or {}).get("kaimynų_detalės") or {}
    apk, kain = float(det.get("jungčių_apkrova", 0)), float(det.get("kainos", 0))
    exp, imp = lines_full(inp)
    if abs(apk) >= abs(kain):
        if sign < 0 and exp:
            w = join_lt([ZN[z][2] for z in exp])
            return f"pilnai apkrautų jungčių į {w}" if gen else f"pilnai apkrautos jungtys į {w}"
        if sign > 0 and imp:
            w = join_lt([ZN[z][1] for z in imp])
            return f"pilnai apkrautų jungčių iš {w}" if gen else f"pilnai apkrautos jungtys iš {w}"
    return (REASON_GEN if gen else REASON)[("kaimynai", sign)]


def headline_reason(inp: Inputs) -> str | None:
    """Pagrindinė nuokrypio priežastis kilmininku – tik jei ji lemia bent pusę nuokrypio."""
    k = inp.sit["kaina"]
    diff = float(k["lt"]) - float(k["norma"])
    sign = 1 if diff >= 0 else -1
    c = {f: v for f, v in contributions(inp).items() if f in dict(drivers(inp))}
    if not c:
        return None
    f, v = max(c.items(), key=lambda t: t[1] * sign)
    if v * sign < max(10.0, 0.5 * abs(diff)):
        return None
    return reason(inp, f, sign, gen=True)


def fact(inp: Inputs, key: str, sign: int) -> str:
    """Fizinis veiksnio paaiškinimas skaičiais."""
    s = inp.sit
    if key == "vėjas":
        w, L, n = float(s["vėjas"]["mw"]), float(s["vartojimas"]["mw"]), float(s["vėjas"]["norma"])
        if sign < 0:
            q = w / L if L else 0
            rel = ("daugiau, nei Lietuva suvartoja" if q >= 1.05 else "beveik tiek, kiek Lietuva suvartoja" if q >= 0.85
                   else f"{round(100 * q)}{NB}% vartojimo")
            return (f"vėjo elektrinės gamina {num(w, 0)}{NB}MW – {rel} ({num(L, 0)}{NB}MW); "
                    f"įprastai šiuo metų laiku – apie {num(n, 0)}{NB}MW")
        return f"vėjo elektrinės gamina tik {num(w, 0)}{NB}MW, įprastai šiuo metų laiku – apie {num(n, 0)}{NB}MW"
    if key == "saulė":
        v = s["saulė"]
        return f"saulės elektrinės gamina {num(v['mw'], 0)}{NB}MW, įprastai – apie {num(v['norma'], 0)}{NB}MW"
    if key == "vartojimas":
        v = s["vartojimas"]
        return f"vartojimas – {num(v['mw'], 0)}{NB}MW, įprastai – apie {num(v['norma'], 0)}{NB}MW"
    if key == "dujos":
        g = s["dujos"]
        r = round(100 * (float(g["ttf"]) / float(g["norma_12mėn"]) - 1))
        return f"TTF – {num(g['ttf'])}{NB}€/MWh, {abs(r)}{NB}% {'brangiau' if r > 0 else 'pigiau'} nei 12 mėn. vidurkis"
    if key == "pralaidumai":
        lim = []
        for z in ("SE4", "PL"):
            x = (((s.get("pralaidumai") or {}).get(IC_LINES[z][0]) or {}).get("importas")) or {}
            if x.get("mw") is not None and x.get("nominalas") and float(x["mw"]) < 0.85 * float(x["nominalas"]):
                lim.append(f"{IC_LINES[z][1]} – {num(x['mw'], 0)} iš {num(x['nominalas'], 0)}{NB}MW")
        return ("importo pralaidumai apriboti: " + ", ".join(lim)) if lim else "importo pralaidumai mažesni nei įprasta"
    if key == "kaimynai":
        kn = s["kaimynai"]
        return f"Švedijoje (SE4) – {num(kn['SE4'])}, Lenkijoje – {num(kn['PL'])}, Suomijoje – {num(kn['FI'])}{NB}€/MWh"
    return ""


def lines_note(inp: Inputs) -> str:
    """Trumpai: kurios jungtys kiek valandų pilnai apkrautos (bent 6 val.), pvz. „LitPol eksportui 23 val., iš Latvijos 9 val.“."""
    jb = (inp.sit or {}).get("jungtys_būsena") or {}
    parts = []
    for z in ("SE4", "PL", "LV"):
        x = jb.get(z) or {}
        for side, word in (("eksportas", "eksportui"), ("importas", "importui")):
            n = int(x.get(f"{side}_pilnas_val") or 0)
            if n >= 6:
                where = ("į Latviją" if side == "eksportas" else "iš Latvijos") if z == "LV" else f"{IC_LINES[z][1]} {word}"
                parts.append(f"{where} {n}{NB}val.")
    return cap(", ".join(parts))


def prices_text(inp: Inputs, zones: list) -> str:
    """„Švedijoje elektra kainuoja 141,0 €/MWh (19–20 val. – 192,8 €/MWh), Lenkijoje – 106,0 €/MWh“."""
    kn, kh = inp.sit.get("kaimynai") or {}, inp.sit.get("kaimynų_valandos") or {}
    parts = []
    for i, z in enumerate(zones):
        t = f"{ZN[z][3]} elektra kainuoja {num(kn[z])}{NB}€/MWh" if i == 0 else f"{ZN[z][3]} – {num(kn[z])}{NB}€/MWh"
        pk = kh.get(z) if i == 0 else None
        if pk and float(pk["max"]) > float(kn[z]) + 25:
            t += f" ({hours_label(int(pk['max_val']))} – {num(pk['max'])}{NB}€/MWh)"
        parts.append(t)
    return ", ".join(parts)


def trade_lines(inp: Inputs) -> tuple[list, list]:
    """Jungtys su Švedija ir Lenkija, kurios pilnai apkrautos eksportui ar importui (Latvija – atskirai per LT ir LV kainų skirtumą)."""
    exp, imp = lines_full(inp)
    return exp, [z for z in imp if z != "LV"]


def trade_sentences(inp: Inputs, with_number: bool) -> list[str]:
    """Jungtys ir kaimynai: kur eksportuojama ar importuojama visa jungties galia, kiek tai lemia kainą."""
    s = inp.sit
    m, kn, caps = s.get("mainai") or {}, s.get("kaimynai") or {}, s.get("pralaidumai") or {}
    lt_p = float(s["kaina"]["lt"])
    v = contributions(inp).get("kaimynai", 0.0)
    exp, imp = trade_lines(inp)
    out = []

    def flows(zones, side):
        parts, ratios = [], []
        for z in zones:
            fl = abs(float((m.get(z) or {}).get("mw") or 0))
            cp = (((caps.get(IC_LINES[z][0]) or {}).get(side)) or {}).get("mw")
            parts.append(f"{IC_LINES[z][1]} – {num(fl, 0)}" + (f" iš {num(cp, 0)}" if cp else "") + f"{NB}MW")
            if cp:
                ratios.append(fl / float(cp))
        how = "visa" if ratios and min(ratios) >= 0.99 else "beveik visa" if ratios and min(ratios) >= 0.9 else "didžiąją paros dalį visa"
        return how + (" jungties" if len(zones) == 1 else " jungčių") + " galia", ", ".join(parts)

    one = lambda zones, single, plural_: single if len(zones) == 1 else plural_
    if exp:
        how, fl = flows(exp, "eksportas")
        sent = f"Į {join_lt([ZN[z][2] for z in exp])} eksportuojama {how} ({fl})"
        dear = [z for z in exp if float(kn.get(z, 0)) > lt_p + 10]
        if dear:
            sent += ": " + prices_text(inp, dear)
            sent += ". " + one(dear, "Pilnai apkrauta jungtis šios kainos", "Pilnai apkrautos jungtys šių kainų") + " į Lietuvą neperduoda"
            if with_number and abs(v) >= 5:
                sent += (f", todėl kaimynų rinkos kainą mažina ({sgn(v)}{NB}€/MWh)" if v < 0
                         else f"; kaimynų rinkos kainą kelia ({sgn(v)}{NB}€/MWh)")
        out.append(sent + ".")
    if imp:
        how, fl = flows(imp, "importas")
        sent = f"Iš {join_lt([ZN[z][1] for z in imp])} importuojama {how} ({fl})"
        cheap = [z for z in imp if z in kn and float(kn[z]) < lt_p - 10]
        if cheap:
            sent += ": " + prices_text(inp, cheap)
            sent += ". Daugiau pigesnės elektros " + one(cheap, "jungtis nepraleidžia", "jungtys nepraleidžia")
            if with_number and abs(v) >= 5:
                sent += (f", todėl kaimynų rinkos kainą kelia ({sgn(v)}{NB}€/MWh)" if v > 0
                         else f"; kaimynų rinkos kainą mažina ({sgn(v)}{NB}€/MWh)")
        out.append(sent + ".")
    ll = s.get("lt_lv") or {}
    n_hi, n_lo, dlt = int(ll.get("brangiau_val") or 0), int(ll.get("pigiau_val") or 0), float(ll.get("skirtumas") or 0)
    net_export = float(((m.get("iš_viso") or {}).get("mw")) or 0) < 0
    if n_hi >= 3 and dlt >= 1:
        hrs = f"{n_hi} {plural(n_hi, 'valandą', 'valandas', 'valandų')}"
        if exp and net_export:
            out.append(f"Eksportas kainą {'šiek tiek ' if dlt < 8 else ''}kelia: {hrs} elektra Lietuvoje buvo brangesnė nei Latvijoje, "
                       f"per parą vidutiniškai {num(dlt)}{NB}€/MWh.")
        else:
            out.append(f"{cap(hrs)} jungtys iš Latvijos buvo pilnai apkrautos, todėl elektra Lietuvoje buvo brangesnė nei Latvijoje, "
                       f"per parą vidutiniškai {num(dlt)}{NB}€/MWh.")
    elif n_lo >= 3 and dlt <= -1:
        hrs = f"{n_lo} {plural(n_lo, 'valandą', 'valandas', 'valandų')}"
        out.append(f"{cap(hrs)} elektra Lietuvoje buvo pigesnė nei Latvijoje, per parą vidutiniškai {num(abs(dlt))}{NB}€/MWh.")
    return out


def kita_sentences(inp: Inputs) -> list[str]:
    """„Kita“ ≥ KITA_RIBA: kuriomis valandomis kaina labiausiai skyrėsi nuo modelio ir ką tada rodė rinka."""
    s = inp.sit
    v = contributions(inp).get("kita", 0.0)
    if abs(v) < KITA_RIBA:
        return []
    head = f"Likusios dalies („kita“, {sgn(v)}{NB}€/MWh) šie veiksniai nepaaiškina."
    x = s.get("kitos_aiškinimas")
    if not x:
        return [head, "Jos priežasties turimuose duomenyse nematyti."]
    n = int(x["valandų"])
    when = "Beveik visą parą" if n >= 16 else f"{x['nuo']}–{x['iki']}{NB}val."
    lead = f"{when} kaina buvo {'mažesnė' if v < 0 else 'didesnė'}, nei tikėjosi modelis"
    lt_p = float(x["kaina"])
    near = lambda z: z in x and abs(float(x[z]) - lt_p) <= max(3.0, 0.03 * lt_p)
    line_free = lambda z: x.get(f"x_{z.lower()}", 0) + x.get(f"i_{z.lower()}", 0) < 0.5 * n
    expl = None
    if v < 0:
        z = next((z for z in ("SE4", "PL") if near(z) and line_free(z)), None)
        if z:
            expl = (f"Lietuvos kaina buvo artima {ZN[z][1]} ({num(x[z], 0)}{NB}€/MWh), nes jungtis su {ZN[z][0]} daugumą valandų "
                    f"nebuvo pilnai apkrauta – kaina nepakilo virš {ZN[z][1]} kainos")
        elif x.get("lt_lygu_lv_val", 0) >= 0.6 * n and "LV" in x:
            expl = f"daugumą valandų Lietuvos kaina sutapo su Latvijos (vidutiniškai {num(x['LV'], 0)}{NB}€/MWh)"
            if "FI" in x and float(x["FI"]) < float(x["LV"]) - 10:
                expl += f", o Suomijoje elektra buvo dar pigesnė ({num(x['FI'], 0)}{NB}€/MWh)"
    else:
        if x.get("i_lv", 0) >= 0.3 * n and "LV" in x and lt_p > float(x["LV"]) + 3:
            k_ = int(x["i_lv"])
            expl = (f"{k_} {plural(k_, 'valandą', 'valandas', 'valandų')} jungtys iš Latvijos buvo pilnai apkrautos, todėl "
                    f"Lietuvoje elektra buvo brangesnė nei Latvijoje ({num(lt_p, 0)} ir {num(x['LV'], 0)}{NB}€/MWh)")
        else:
            z = next((z for z in ("SE4", "PL") if x.get(f"i_{z.lower()}", 0) >= 0.5 * n and z in x and float(x[z]) < lt_p - 10), None)
            if z:
                expl = (f"jungtis iš {ZN[z][1]} buvo pilnai apkrauta, todėl pigesnė {ZN[z][1]} elektra ({num(x[z], 0)}{NB}€/MWh) "
                        f"Lietuvos kainos daugiau nemažino")
            else:
                z = next((z for z in ("SE4", "PL", "LV") if near(z)), None)
                if z:
                    expl = f"Lietuvos kaina buvo artima {ZN[z][1]} ({num(x[z], 0)}{NB}€/MWh) – regione elektra brangesnė, nei rodo modelio veiksniai"
    return [head, f"{lead}: {expl or 'jungtys nebuvo pilnai apkrautos, kaimynų kainos neišsiskyrė, todėl aiškios priežasties duomenyse nematyti'}."]


def energy_paragraphs(inp: Inputs) -> list[str]:
    """Kas kainą lemia: pagrindinis veiksnys (nuokrypio kryptimi) su skaičiais, priešingi veiksniai, jungtys, „kita“."""
    if not inp.sit:
        h = inp.hours
        i_max, i_min = h.index(max(h)), h.index(min(h))
        return [f"Brangiausia {hours_label(i_max)} ({num(max(h))}{NB}€/MWh), pigiausia {hours_label(i_min)} ({num(min(h))}{NB}€/MWh)."]
    c = contributions(inp)
    k = inp.sit["kaina"]
    diff = float(k["lt"]) - float(k["norma"])
    sign = 1 if diff >= 0 else -1
    drv = {f: c.get(f, 0.0) for f, _ in drivers(inp)}
    thr = max(5.0, 0.06 * sum(abs(v) for v in c.values()))
    ranked = sorted(drv.items(), key=lambda t: -abs(t[1]))
    same = [(f, v) for f, v in ranked if v * sign >= thr]
    opp = [(f, v) for f, v in ranked if v * sign <= -thr]
    trade_known = bool(sum(trade_lines(inp), []))
    P, mentioned = [], set()
    st = k.get("būsena") or ""
    up = max(drv.items(), key=lambda t: t[1], default=None)
    dn = min(drv.items(), key=lambda t: t[1], default=None)
    if st not in PRICE_PHRASE and up and dn and up[1] >= 15 and dn[1] <= -15 and min(up[1], -dn[1]) >= 0.6 * max(up[1], -dn[1]):
        # įprasta kaina, bet stiprūs priešingi veiksniai beveik atsveria vienas kitą
        P.append(f"{cap(reason(inp, up[0], 1, intense=False))} ir {reason(inp, dn[0], -1, intense=False)} šiandien beveik atsveria vienas kitą.")
        P.append(f"Kainą kelia {reason(inp, up[0], 1)} ({sgn(up[1])}{NB}€/MWh): {fact(inp, up[0], 1)}.")
        P.append(f"Kainą mažina {reason(inp, dn[0], -1)} ({sgn(dn[1])}{NB}€/MWh): {fact(inp, dn[0], -1)}.")
        mentioned |= {up[0], dn[0]}
    elif not same:
        P.append("Kainą lemiantys veiksniai šiandien artimi įprastiems.")
    else:
        f0, v0 = same[0]
        verb = "kelia" if sign > 0 else "mažina"
        P.append(f"Kainą labiausiai {verb} {reason(inp, f0, sign)} ({sgn(v0)}{NB}€/MWh): {fact(inp, f0, sign)}.")
        mentioned.add(f0)
        for f, v in same[1:3]:
            if abs(v) >= 0.35 * abs(v0) and not (f == "kaimynai" and trade_known):
                P.append(f"Taip pat {verb} {reason(inp, f, sign, intense=False)} ({sgn(v)}{NB}€/MWh): {fact(inp, f, sign)}.")
                mentioned.add(f)
        for f, v in opp[:2]:
            if f == "kaimynai" and trade_known:
                continue
            bit = "šiek tiek " if abs(v) < 0.4 * abs(v0) else ""
            P.append(f"Kainą {bit}{'mažina' if sign > 0 else 'kelia'} {reason(inp, f, -sign, intense=False)} ({sgn(v)}{NB}€/MWh): {fact(inp, f, -sign)}.")
            mentioned.add(f)
    out = [" ".join(P)]
    t = trade_sentences(inp, with_number="kaimynai" not in mentioned)
    if t:
        out.append(" ".join(t))
    kx = kita_sentences(inp)
    if kx:
        out.append(" ".join(kx))
    return out


def drivers_note(inp: Inputs) -> str:
    t = "„Kita“ – kainos dalis, kurios šie veiksniai nepaaiškina."
    if (inp.sit or {}).get("dujų_metodas") == "šiandienos sąlygomis":
        t = ("Dujų įtaka – kiek elektra brangesnė dėl dujų ir CO2 kainos šiandienos sąlygomis; kiti veiksniai įvertinti esant įprastai dujų kainai. "
             "Kaimynų rinkos – kaimynų kainos ir tai, ar jungtys jas perduoda. " + t)
    return t


def energy_story(inp: Inputs) -> str:
    return " ".join(energy_paragraphs(inp))


def trade_highlight(inp: Inputs) -> str:
    """Vienas sakinys apžvalgai: jungtys, pilnai išnaudotos prekybai su gerokai brangesne ar pigesne kaimyne."""
    kn = inp.sit.get("kaimynai") or {}
    lt_p = float(inp.sit["kaina"]["lt"])
    exp, imp = trade_lines(inp)
    dear = [z for z in exp if float(kn.get(z, 0)) >= 1.3 * lt_p + 10]
    cheap = [z for z in imp if float(kn.get(z, 1e9)) <= 0.77 * lt_p - 10]
    names = lambda zs: " ir ".join(IC_LINES[z][1] for z in zs)
    if dear:
        one = len(dear) == 1
        return (f"{names(dear)} pilnai {'išnaudota' if one else 'išnaudotos'} eksportui į {'brangesnę' if one else 'brangesnes'} "
                + join_lt([f"{ZN[z][2]} ({num(kn[z], 0)}{NB}€/MWh)" for z in dear]) + ".")
    if cheap:
        one = len(cheap) == 1
        return (f"{names(cheap)} pilnai {'išnaudota' if one else 'išnaudotos'} importui iš {'pigesnės' if one else 'pigesnių'} "
                + join_lt([f"{ZN[z][1]} ({num(kn[z], 0)}{NB}€/MWh)" for z in cheap]) + ".")
    return ""


def energy_overview(inp: Inputs) -> str:
    p = inp.price
    if inp.sit:
        st = inp.sit["kaina"]["būsena"]
        head = f"<b>{num(p)}{NB}€/MWh</b> – " + (PRICE_PHRASE.get(st) or "įprasta kaina šiam metų laikui") + "."
        k = inp.sit["kaina"]
        sign = 1 if float(k["lt"]) - float(k["norma"]) >= 0 else -1
        c = {f: contributions(inp).get(f, 0.0) for f, _ in drivers(inp)}
        up = max(c.items(), key=lambda t: t[1], default=None)
        dn = min(c.items(), key=lambda t: t[1], default=None)
        if st not in PRICE_PHRASE and up and dn and up[1] >= 15 and dn[1] <= -15 and min(up[1], -dn[1]) >= 0.6 * max(up[1], -dn[1]):
            txt = (f" {cap(e(reason(inp, up[0], 1)))} ({sgn(up[1])}{NB}€/MWh) ir {e(reason(inp, dn[0], -1))} ({sgn(dn[1])}{NB}€/MWh) "
                   f"beveik atsveria vienas kitą.")
        else:
            main = up if sign > 0 else dn
            cnt = dn if sign > 0 else up
            parts = []
            if main and main[1] * sign >= 5:
                parts.append(f"labiausiai {'kelia' if sign > 0 else 'mažina'} {e(reason(inp, main[0], sign))} ({sgn(main[1])}{NB}€/MWh)")
            if cnt and -cnt[1] * sign >= 5:
                bit = "šiek tiek " if main and abs(cnt[1]) < 0.4 * abs(main[1]) else ""
                parts.append(f"{bit}{'mažina' if sign > 0 else 'kelia'} {e(reason(inp, cnt[0], -sign, intense=False))} ({sgn(cnt[1])}{NB}€/MWh)")
            txt = (" Kainą " + ", ".join(parts) + ".") if parts else ""
        th = trade_highlight(inp)
        return head + txt + (" " + e(th) if th else "")
    h = inp.hours
    return f"<b>{num(p)}{NB}€/MWh</b> paros vidurkis; brangiausia {hours_label(h.index(max(h)))} ({num(max(h))}{NB}€/MWh)."


# ─── kainos kreivė (tamsus fonas) ────────────────────────────────────────────

def nice_scale(lo: float, hi: float):
    top = max(hi, 1.0)
    step = 100 if top > 260 else 50 if top > 110 else 25 if top > 50 else 10
    vmax = math.ceil(top * 1.06 / step) * step
    vmin = 0 if lo >= 0 else math.floor(lo * 1.06 / step) * step
    return vmin, vmax, step


def curve_svg(hours: list[float], band, avg: float, wide: bool, aria: str) -> str:
    if wide:
        W, H, x0, x1, yb, yt, fs, fl, ticks = 1000, 330, 62, 880, 292, 34, 13, 14, range(0, 25, 3)
    else:
        W, H, x0, x1, yb, yt, fs, fl, ticks = 360, 250, 36, 284, 214, 24, 11, 12, range(0, 25, 6)
    lo = min(hours + ([band[0]] if band else []))
    hi = max(hours + ([band[1]] if band else []))
    vmin, vmax, step = nice_scale(lo, hi)
    X = lambda h: x0 + h * (x1 - x0) / 24
    Y = lambda v: yb - (v - vmin) * (yb - yt) / (vmax - vmin)
    out = [f'<svg{" class=\"mr-wide\"" if wide else " class=\"mr-narrow\""} viewBox="0 0 {W} {H}" role="img" aria-label="{ea(aria)}">']
    out.append(f'<rect x="{x0}" y="{yt - 8}" width="{x1 - x0}" height="{yb - yt + 8}" fill="{D_BG}" fill-opacity="0.6"/>')
    if band:
        out.append(f'<rect x="{x0}" y="{Y(band[1]):.1f}" width="{x1 - x0}" height="{Y(band[0]) - Y(band[1]):.1f}" fill="#FFFFFF" fill-opacity="0.12"/>')
    v = vmin
    grid = []
    while v <= vmax + 1e-9:
        if v != 0:
            grid.append(f'<line x1="{x0}" y1="{Y(v):.1f}" x2="{x1}" y2="{Y(v):.1f}"/>')
        v += step
    out.append(f'<g stroke="#FFFFFF" stroke-opacity="0.12" stroke-width="1">{"".join(grid)}</g>')
    out.append(f'<line x1="{x0}" y1="{Y(0):.1f}" x2="{x1}" y2="{Y(0):.1f}" stroke="{D_MUTED}" stroke-opacity="0.7" stroke-width="1"/>')
    out.append(f'<line x1="{x0}" y1="{Y(avg):.1f}" x2="{x1}" y2="{Y(avg):.1f}" stroke="{D_MUTED}" stroke-width="1" stroke-dasharray="4 4"/>')
    d = f"M{X(0):.1f} {Y(hours[0]):.1f}"
    for h in range(24):
        d += f" H{X(h + 1):.1f}"
        if h < 23:
            d += f" V{Y(hours[h + 1]):.1f}"
    out.append(f'<path d="{d}" fill="none" stroke="{D_INK}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    i_max, i_min = hours.index(max(hours)), hours.index(min(hours))
    labs = []
    for i, above in ((i_max, True), (i_min, False)):
        cx, cy = X(i + 0.5), Y(hours[i])
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4.5" fill="{D_INK}" stroke="{D_BG}" stroke-width="2"/>')
        ty = cy - 10 if above else (cy + fl + 8 if cy + fl + 8 < yb - 2 else cy - 10)
        anchor = "middle"
        if cx > x1 - 40:
            anchor, cx = "end", cx + 4
        elif cx < x0 + 40:
            anchor, cx = "start", cx - 4
        labs.append(f'<text x="{cx:.1f}" y="{ty:.1f}" text-anchor="{anchor}">{num(hours[i])}</text>')
    lx = x1 + 8
    y_avg = Y(avg) + 4
    labs.append(f'<text x="{lx}" y="{y_avg:.1f}">vid. {num(avg)}</text>')
    out.append(f'<g font-family="IBM Plex Mono, monospace" font-size="{fl}" font-weight="500" fill="{D_INK}">{"".join(labs)}</g>')
    side = []
    if band:
        yb1 = (Y(band[0]) + Y(band[1])) / 2 - 2
        if abs(yb1 - y_avg) < fs * 2.4:
            yb1 = y_avg + fs * 2.4 if yb1 >= y_avg else y_avg - fs * 3.4
        side.append(f'<text x="{lx}" y="{yb1:.1f}">įprasta</text><text x="{lx}" y="{yb1 + fs + 3:.1f}">{num(band[0], 0)}–{num(band[1], 0)}</text>')
    v = vmin
    while v <= vmax + 1e-9:
        side.append(f'<text x="{x0 - 8}" y="{Y(v) + 4:.1f}" text-anchor="end">{num(v, 0)}</text>')
        v += step
    for t in ticks:
        anchor = "start" if t == 0 else "end" if t == 24 else "middle"
        side.append(f'<text x="{X(t):.1f}" y="{yb + fs + 9}" text-anchor="{anchor}">{"0 val." if t == 0 else t}</text>')
    out.append(f'<g font-family="IBM Plex Mono, monospace" font-size="{fs}" fill="{D_MUTED}">{"".join(side)}</g>')
    out.append("</svg>")
    return "\n".join(out)


# ─── rinkos žemėlapis ────────────────────────────────────────────────────────

def _hit(ring, p, q):
    best = None
    for a, b in zip(ring, ring[1:] + ring[:1]):
        d = (q[0] - p[0]) * (b[1] - a[1]) - (q[1] - p[1]) * (b[0] - a[0])
        if abs(d) < 1e-9:
            continue
        t = ((a[0] - p[0]) * (b[1] - a[1]) - (a[1] - p[1]) * (b[0] - a[0])) / d
        u = ((a[0] - p[0]) * (q[1] - p[1]) - (a[1] - p[1]) * (q[0] - p[0])) / d
        if 0 <= t <= 1 and 0 <= u <= 1 and (best is None or t < best):
            best = t
    return [p[0] + best * (q[0] - p[0]), p[1] + best * (q[1] - p[1])]


def _back(p, q, px):
    L = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5
    return [p[0] + (q[0] - p[0]) * px / L, p[1] + (q[1] - p[1]) * px / L]


def _smooth(pts):
    f = lambda v: f"{v:.1f}"
    d = f"M{f(pts[0][0])} {f(pts[0][1])}"
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, len(pts) - 1)]
        c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6]
        c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6]
        d += f"C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(p2[0])} {f(p2[1])}"
    return d


def map_svg(inp: Inputs, wide: bool) -> str:
    s = inp.sit
    geo = open(os.path.join(RES, "zemelapio-pagrindas.svg.txt"), encoding="utf-8").read().strip()
    G = json.load(open(os.path.join(RES, "trasos.json"), encoding="utf-8"))
    ring, P = G["ltRing"], G["pts"]
    suf = "w" if wide else "n"
    m = s["mainai"]
    se, pl, lv = m["SE4"]["mw"], m["PL"]["mw"], m["LV"]["mw"]
    tot, share = m["iš_viso"]["mw"], m["iš_viso"]["proc_vartojimo"]
    ar_big, ar_small = f"url(#mr-rod-{suf})", f"url(#mr-rodm-{suf})"
    mk = lambda v, small=False: f' marker-end="{ar_small if small else ar_big}"' if abs(v) >= 5 else ""

    nb = G["routes"]["nordbalt"]
    coast = _hit(ring, nb[0], nb[1])
    nb_pts = [_back(coast, nb[1], 2)] + nb[1:]
    if se > 0:
        nb_pts = nb_pts[::-1]
    lp = G["routes"]["litpol"]
    k = next(i for i in range(2, len(lp) - 1) if lp[i + 1][0] < 495)
    cross = _hit(ring, lp[k], lp[k + 1])
    lp_in = lp[: k + 1] + [cross]
    lp_out = [_back(cross, lp[k + 1], 1.5)] + lp[k + 1:]
    if pl > 0:
        lp_out = lp_out[::-1]
    hub = [548.0, 190.0]
    fan = []
    for lvp, ltp in (("grobina", "darbenai"), ("viskali", "musa"), ("aizkraukle", "panevezys"), ("liksna", "ignalina")):
        x = _hit(ring, P[lvp], P[ltp])
        end = _back(x, P[lvp], 3.5)
        if lv >= 0:
            fan.append(f'<path d="M{hub[0]:.1f} {hub[1]:.1f}Q{P[lvp][0]:.1f} {P[lvp][1]:.1f} {end[0]:.1f} {end[1]:.1f}"{mk(lv, True)}/>')
        else:
            tip = _back(P[lvp], end, 6)
            fan.append(f'<path d="M{end[0]:.1f} {end[1]:.1f}L{tip[0]:.1f} {tip[1]:.1f}"{mk(lv, True)}/>')
    vb = "0 0 760 570" if wide else "262 96 448 448"
    names = {"SE4": "Švedija (SE4)", "LV": "Latvija", "PL": "Lenkija"}
    kn = s["kaimynai"]
    lt_price = float(s["kaina"]["lt"])

    def flow_words(code, v):
        if abs(v) < 5:
            return f"{names[code]} – prekybos srauto beveik nėra"
        frm, to = ("iš " + {"SE4": "Švedijos", "LV": "Latvijos", "PL": "Lenkijos"}[code], "į Lietuvą") if v > 0 else \
            ("iš Lietuvos", "į " + {"SE4": "Švediją", "LV": "Latviją", "PL": "Lenkiją"}[code])
        return f"{frm} {to} {num(abs(v), 0)} MW"
    aria = (f"Rinkos žemėlapis. Paros vidurkiai: Lietuva {num(lt_price)} €/MWh, Švedija (SE4) {num(kn['SE4'])}, "
            f"Latvija {num(kn['LV'])}, Lenkija {num(kn['PL'])}. Srautai: " +
            ", ".join(flow_words(c, v) for c, v in (("LV", lv), ("SE4", se), ("PL", pl))) + ".")
    o = [f'<svg class="{"mr-wide" if wide else "mr-narrow"}" viewBox="{vb}" role="img" aria-label="{ea(aria)}">', geo]
    o.append('<g fill="none" stroke-linecap="round" stroke-linejoin="round">')
    o.append(f'<defs><marker id="mr-rod-{suf}" orient="auto" markerUnits="userSpaceOnUse" markerWidth="13" markerHeight="13" refX="4" refY="6" overflow="visible"><path d="M0 0 L12 6 L0 12 Z" fill="{D_INK}" stroke="none"/></marker>'
             f'<marker id="mr-rodm-{suf}" orient="auto" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" refX="3" refY="4.5" overflow="visible"><path d="M0 0 L9 4.5 L0 9 Z" fill="{D_INK}" stroke="none"/></marker></defs>')
    o.append(f'<path d="{_smooth(nb_pts)}" stroke="{D_INK}" stroke-width="3"{mk(se)}/>')
    o.append(f'<path d="{_smooth(lp_in)}" stroke="{D_BG}" stroke-width="3"/>')
    o.append(f'<path d="{_smooth(lp_out)}" stroke="{D_INK}" stroke-width="3"{mk(pl)}/>')
    o.append(f'<g stroke="{D_INK}" stroke-width="2">{"".join(fan)}</g>')
    if lv >= 0:
        o.append(f'<circle cx="{hub[0]}" cy="{hub[1]}" r="4.5" fill="{D_INK}" stroke="{D_BG}" stroke-width="2"/>')
    o.append(f'<circle cx="{coast[0]:.1f}" cy="{coast[1]:.1f}" r="4" fill="{D_BG}" stroke="{D_INK}" stroke-width="2"/>')
    o.append(f'<circle cx="{lp[0][0]}" cy="{lp[0][1]}" r="4" fill="{D_INK}" stroke="{D_BG}" stroke-width="2"/>')
    o.append("</g>")
    if wide:
        pos = {"SE4": (16, 120), "LV": (612, 134), "PL": (250, 496)}
        o.append(f'<g font-family="IBM Plex Mono, monospace" font-size="13" fill="{D_MUTED}">' + "".join(
            f'<text x="{x}" y="{y}">{names[c]}</text><text x="{x}" y="{y + 25}"><tspan font-family="Barlow, sans-serif" font-size="21" font-weight="600" fill="{D_INK}">{num(kn[c])}</tspan><tspan dx="5">€/MWh</tspan></text>'
            for c, (x, y) in pos.items()) + "</g>")
        o.append(f'<g font-family="Barlow, sans-serif" font-size="17" font-weight="600" fill="{D_INK}">'
                 f'<text x="256" y="267" text-anchor="middle">{num(abs(se), 0)} MW</text>'
                 f'<text x="538" y="175" text-anchor="end">{num(abs(lv), 0)} MW</text>'
                 f'<text x="436" y="453" text-anchor="end">{num(abs(pl), 0)} MW</text></g>')
        lines = [f"vėjas {num(s['vėjas']['mw'], 0)} MW · saulė {num(s['saulė']['mw'], 0)} MW"]
        kg = s.get("kita_gamyba")
        if kg and _hours_span(kg.get("valandos")) >= 12:
            lines.append(f"kita gamyba {num(kg['mw'], 0)} MW")
        lines.append(f"{'importas' if tot >= 0 else 'eksportas'} {num(abs(tot), 0)} MW · {abs(share)}{NB}%")
        o.append(f'<g text-anchor="middle" fill="{D_BG}"><text x="549" y="264" font-family="IBM Plex Mono, monospace" font-size="13" fill="#3B4A68">Lietuva</text>'
                 f'<text x="549" y="300"><tspan font-family="Barlow, sans-serif" font-size="36" font-weight="600">{num(lt_price)}</tspan><tspan font-family="IBM Plex Mono, monospace" font-size="13" dx="6">€/MWh</tspan></text>'
                 f'<g font-family="Open Sans, sans-serif" font-size="13" font-weight="600">' +
                 "".join(f'<text x="549" y="{323 + 19 * i}">{t}</text>' for i, t in enumerate(lines)) + "</g></g>")
    else:
        pos = {"SE4": (272, 214), "LV": (590, 124), "PL": (272, 500)}
        o.append(f'<g font-family="IBM Plex Mono, monospace" font-size="17" fill="{D_MUTED}">' + "".join(
            f'<text x="{x}" y="{y}">{names[c]}</text><text x="{x}" y="{y + 32}"><tspan font-family="Barlow, sans-serif" font-size="28" font-weight="600" fill="{D_INK}">{num(kn[c])}</tspan></text>'
            for c, (x, y) in pos.items()) + "</g>")
        o.append(f'<g font-family="Barlow, sans-serif" font-size="22" font-weight="600" fill="{D_INK}">'
                 f'<text x="272" y="322" text-anchor="start">{num(abs(se), 0)} MW</text>'
                 f'<text x="538" y="170" text-anchor="end">{num(abs(lv), 0)} MW</text>'
                 f'<text x="436" y="450" text-anchor="end">{num(abs(pl), 0)} MW</text></g>')
        o.append(f'<g text-anchor="middle" fill="{D_BG}"><text x="549" y="264" font-family="IBM Plex Mono, monospace" font-size="17" fill="#3B4A68">Lietuva</text>'
                 f'<text x="549" y="310"><tspan font-family="Barlow, sans-serif" font-size="46" font-weight="600">{num(lt_price)}</tspan><tspan font-family="IBM Plex Mono, monospace" font-size="17" dx="6">€/MWh</tspan></text>'
                 f'<g font-family="Open Sans, sans-serif" font-size="15" font-weight="600">'
                 f'<text x="549" y="335">vėjas {num(s["vėjas"]["mw"], 0)} MW · saulė {num(s["saulė"]["mw"], 0)} MW</text>'
                 f'<text x="549" y="357">{"importas" if tot >= 0 else "eksportas"} {num(abs(tot), 0)} MW · {abs(share)}{NB}%</text></g></g>')
    o.append("</svg>")
    return "\n".join(o)


def _hours_span(v) -> int:
    try:
        a, b = str(v).split("–")
        return int(b) - int(a)
    except Exception:
        return 0


# ─── fragmentas svetainei ────────────────────────────────────────────────────

def block_overview(inp: Inputs) -> str:
    rows = []
    if inp.price is not None:
        rows.append(("Elektra", energy_overview(inp)))
    if today_items(inp):
        rows.append(("Šiandien", e(today_overview(inp))))
    if inp.grants and DAILY_SLOW:
        rows.append(("Paramos", e(grants_overview(inp))))
    if inp.legal and DAILY_SLOW:
        rows.append(("Teisės aktai", e(legal_overview(inp))))
    if inp.news:
        rows.append(("Naujienos", e(news_overview(inp))))
    if not rows:
        return ""
    li = "\n".join(f'<li class="mr-sip-row"><span class="mr-sip-topic">{t}</span><span class="mr-sip-text">{x}</span></li>' for t, x in rows)
    return f'<section class="mr-block mr-lead">\n<h2 class="mr-h">Apžvalga</h2>\n<ul class="mr-sip">\n{li}\n</ul>\n</section>'


def block_energy(inp: Inputs) -> str:
    d = inp.day
    o = ['<section class="mr-block">', '<h2 class="mr-h">Elektros rinka</h2>',
         f'<p class="mr-sub">{cap(WEEKDAYS[d.weekday()])}, {day_short(d)} · Lietuvos prekybos zona · kitos paros rinka</p>',
         f'<p class="mr-headline">{e(energy_headline(inp))}</p>'] + [f'<p class="mr-text">{e(t)}</p>' for t in energy_paragraphs(inp)]
    s = inp.sit
    if s:
        o.append('<div class="mr-pair">')
        o.append('<figure class="mr-fig mr-fig-map mr-fig-dark">\n<figcaption class="mr-cap">Rinkos žemėlapis: paros vidurkiai, €/MWh, ir prekybos srautai, MW</figcaption>')
        o.append(map_svg(inp, True))
        o.append(map_svg(inp, False))
        o.append("</figure>")
        c = contributions(inp)
        items = sorted([(c.get(k, 0.0), k, name) for k, name in drivers(inp)], key=lambda t: -t[0]) + [(c.get("kita", 0.0), "kita", "Kita")]
        li = []
        for v, key, name in items:
            r = round(v)
            cls = "mr-other" if key == "kita" or r == 0 else ("mr-raise" if r > 0 else "mr-lower")
            li.append(f'<li class="mr-driver {cls}" data-value="{r}"><span class="mr-driver-name">{name}</span><span class="mr-driver-val">{sgn(r)}</span></li>')
        k = s["kaina"]
        o.append('<div class="mr-drivers">\n<h3 class="mr-h3">Kas lemia kainą</h3>\n<p class="mr-sub">nuokrypis nuo kainos įprastomis sąlygomis, €/MWh</p>')
        o.append('<ul class="mr-driver-list">\n' + "\n".join(li) + "\n</ul>")
        o.append(f'<p class="mr-driver-total">įprastomis sąlygomis <b>{num(k["norma"], 0)}</b> → šiandien <b>{num(k["lt"])}</b> '
                 f'<span class="mr-delta">{sgn(float(k["lt"]) - float(k["norma"]))}</span></p>')
        o.append('<p class="mr-keys"><span class="mr-key mr-raise">kelia kainą</span><span class="mr-key mr-lower">mažina kainą</span><span class="mr-key mr-other">kita</span></p>')
        o.append(f'<p class="mr-note">{e(drivers_note(inp))}</p>\n</div>\n</div>')
        o.append('<h3 class="mr-h3">Sąlygos šiandien</h3>')
        kp = []
        for lbl, val, unit, note in conditions(inp):
            if lbl == "Jungtys":
                cd = float((s.get("sąlygos") or {}).get("cap_def") or 0)
                if caps_known(inp):
                    kp.append(_kpi("Pralaidumų ribojimas", num(cd, 0), "MW", lines_note(inp) or ("importo pralaidumai neriboti" if cd < 1 else "trūksta iki įprasto importo pralaidumo")))
                else:
                    kp.append(_kpi("Pralaidumų ribojimas", "–", "", "pralaidumų duomenys negauti"))
            else:
                kp.append(_kpi(lbl, val, unit, note))
        o.append('<div class="mr-kpis">\n' + "\n".join(kp) + "\n</div>")
    if inp.hours:
        h = inp.hours
        band = s["kaina"]["intervalas"] if s else None
        avg = sum(h) / 24
        aria = (f"Valandinės elektros kainos Lietuvoje {day_short(d)}: nuo {num(min(h))} iki {num(max(h))} €/MWh, paros vidurkis {num(avg)} €/MWh"
                + (f", įprasta {num(band[0], 0)}–{num(band[1], 0)} €/MWh" if band else ""))
        o.append('<figure class="mr-fig mr-fig-curve mr-fig-dark">\n<figcaption class="mr-cap">Kaina per parą: valandinės kainos, €/MWh, Lietuvos laiku'
                 + (", pilka juosta – įprastas lygis" if band else "") + "</figcaption>")
        o.append(curve_svg(h, band, avg, True, aria))
        o.append(curve_svg(h, band, avg, False, aria))
        o.append("</figure>")
    note = ("Vėjas, saulė ir vartojimas – kitos paros prognozės; srautai – kitos paros prekybos grafikai; importo dalis – nuo vartojimo. "
            if s else "Veiksnių šiandien apskaičiuoti nepavyko, todėl rodomos tik kainos. ")
    o.append(f'<p class="mr-note">{note}Šaltiniai: ENTSO-E, Elering, EEX, ICE (TTF). Skaičiavimai – MB Enerconsult.</p>')
    o.append("</section>")
    return "\n".join(o)


def _kpi(label, value, unit, note) -> str:
    v = f'<b class="mr-num">{value}</b>' + (f'<span class="mr-unit">{unit}</span>' if unit else "")
    return (f'<div class="mr-kpi"><span class="mr-kpi-label">{label}</span><span class="mr-kpi-value">{v}</span>'
            f'<span class="mr-kpi-note">{note}</span></div>')


def conditions(inp: Inputs) -> list[tuple[str, str, str, str]]:
    s = inp.sit
    out = [("Vartojimas", num(s["vartojimas"]["mw"], 0), "MW", f"įprasta {num(s['vartojimas']['norma'], 0)} · {s['vartojimas']['būsena']}"),
           ("Vėjas", num(s["vėjas"]["mw"], 0), "MW", f"įprasta {num(s['vėjas']['norma'], 0)} · {s['vėjas']['būsena']}"),
           ("Saulė", num(s["saulė"]["mw"], 0), "MW", f"įprasta {num(s['saulė']['norma'], 0)} · {s['saulė']['būsena']}")]
    kg = s.get("kita_gamyba")
    if kg and _hours_span(kg.get("valandos")) >= 12:
        out.append(("Kita gamyba", num(kg["mw"], 0), "MW", f"įprasta {num(kg['norma'], 0)} · {kg['būsena']}"))
    g = s["dujos"]
    out.append(("Dujos (TTF)", num(g["ttf"]), "€/MWh", f"12 mėn. vidurkis {num(g['norma_12mėn'])} · {g['būsena']}"))
    cd = float((s.get("sąlygos") or {}).get("cap_def") or 0)
    if not caps_known(inp):
        out.append(("Jungtys", "nėra duomenų", "", "pralaidumų duomenys negauti"))
    else:
        full = bool(sum(lines_full(inp), []))
        out.append(("Jungtys", f"apribotos {num(cd, 0)}{NB}MW" if cd >= 1 else "pilnai apkrautos" if full else "neribojamos", "",
                    lines_note(inp) or ("importo pralaidumai į Lietuvą" if cd < 1 else "trūksta iki įprasto importo pralaidumo")))
    return out


def news_sorted(inp: Inputs) -> list[dict]:
    items = list(inp.news["naujienos"])
    top = inp.news.get("svarbiausia")
    first = [items[top]] if isinstance(top, int) and 0 <= top < len(items) else []
    rest = [n for i, n in enumerate(items) if not first or i != top]
    rest.sort(key=lambda n: REGIONS.index(n["regionas"]) if n.get("regionas") in REGIONS else 9)
    return first + rest


def news_overview(inp: Inputs) -> str:
    items = news_sorted(inp)
    top, rest = items[0], items[1:]
    s = top["pavadinimas"].rstrip(".") + "."
    if rest:
        regs = []
        for n in rest:
            r = REGIONS_ACC.get(n.get("regionas"))
            if r and r not in regs:
                regs.append(r)
        s += f" Dar {len(rest)} {plural(len(rest), 'naujiena', 'naujienos', 'naujienų')}" + (f" apie {join_lt(regs)}." if regs else ".")
    return s


def block_news(inp: Inputs) -> str:
    items = news_sorted(inp)
    srcs = []
    for n in items:
        if n.get("šaltinis") and n["šaltinis"] not in srcs:
            srcs.append(n["šaltinis"])
    cnt = len(items)
    o = ['<section class="mr-block">', '<h2 class="mr-h">Naujienos</h2>',
         f'<p class="mr-sub">{cnt} {plural(cnt, "naujiena", "naujienos", "naujienų")} per parą · {e(", ".join(srcs))}</p>',
         '<div class="mr-news-list">']
    for n in items:
        sev = SEV.get(n.get("svarba"))
        cls = "mr-news" + (f" mr-sev-{sev}" if sev else "")
        title = e(n["pavadinimas"])
        if https(n.get("nuoroda")):
            title = f'<a href="{ea(n["nuoroda"])}">{title}</a>'
        o.append(f'<article class="{cls}"><p class="mr-news-meta"><span class="mr-region">{e(n["regionas"])}</span>'
                 f'<span class="mr-source">{e(n.get("šaltinis") or "")}</span></p>'
                 f'<h3 class="mr-news-topic">{title}</h3><p class="mr-news-text">{e(n["santrauka"])}</p></article>')
    o += ["</div>", "</section>"]
    return "\n".join(o)


def world_sections(pr: dict) -> dict:
    """Analizės tekstas pagal antraštes (be jaustukų)."""
    out, cur = {}, None
    for line in (pr.get("analizė_md") or "").splitlines():
        if line.startswith("### "):
            cur = re.sub(r"[^\w\s,.–-]", "", line[4:]).strip()
            out[cur] = []
        elif cur and line.strip():
            out[cur].append(line.strip())
    return out


def _md_inline(t: str) -> str:
    t = e(t)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)


def world_chain_svg(pr: dict) -> str | None:
    c = ((pr.get("grandinė") or {}).get("srmc") or {})
    ccgt, old = c.get("ccgt"), c.get("senas")
    lt_el = ((pr.get("rodikliai") or {}).get("lt_elektra") or {}).get("šiandien") or {}
    avg, eve = lt_el.get("vidurkis"), lt_el.get("vakaro_pikas_18_21")
    if not ccgt or not old or avg is None:
        return None
    top = max(old["iš_viso"], avg, eve or 0)
    vmax = math.ceil(top * 1.1 / 50) * 50
    X0, X1 = 250.0, 900.0
    sx = lambda v: X0 + v / vmax * (X1 - X0)
    o = [f'<svg viewBox="0 0 1000 190" role="img" aria-label="{ea(f"Dujinių elektrinių kintamieji kaštai: nauja {num(ccgt["iš_viso"])}, senesnė {num(old["iš_viso"])} €/MWh; Lietuvos paros vidurkis {num(avg)} €/MWh")}">']
    grid, labs = [], []
    v = 0
    while v <= vmax + 1e-9:
        grid.append(f'<line x1="{sx(v):.1f}" y1="34" x2="{sx(v):.1f}" y2="148"/>')
        labs.append(f'<text x="{sx(v):.1f}" y="170" text-anchor="middle">{v}</text>')
        v += 50
    o.append(f'<g stroke="#FFFFFF" stroke-opacity="0.1" stroke-width="1">{"".join(grid)}</g>')
    o.append(f'<line x1="{X0}" y1="148" x2="{X1}" y2="148" stroke="{D_MUTED}" stroke-opacity="0.7" stroke-width="1"/>')
    for (name, sub, dd, y) in (("Nauja dujinė elektrinė", "naudingumas 55 %", ccgt, 46), ("Senesnė dujinė elektrinė", "naudingumas 40 %", old, 100)):
        a, b, cc, dd2 = sx(0), sx(dd["kuras"]), sx(dd["kuras"] + dd["co2"]), sx(dd["kuras"] + dd["co2"] + dd["vom"])
        o.append(f'<rect x="{a:.1f}" y="{y}" width="{b - a:.1f}" height="30" fill="#C5D1E3"/>'
                 f'<rect x="{b:.1f}" y="{y}" width="{cc - b:.1f}" height="30" fill="#7C8AA3"/>'
                 f'<rect x="{cc:.1f}" y="{y}" width="{dd2 - cc:.1f}" height="30" fill="#3B4A68"/>')
        o.append(f'<g font-family="IBM Plex Mono, monospace" font-size="13" font-weight="500" fill="{D_BG}">'
                 f'<text x="{a + 10:.1f}" y="{y + 20}">kuras {num(dd["kuras"])}</text>'
                 + (f'<text x="{b + 8:.1f}" y="{y + 20}">CO2 {num(dd["co2"])}</text>' if cc - b > 80 else "") + "</g>")
        o.append(f'<text x="0" y="{y + 13}" font-family="Open Sans, sans-serif" font-size="15" font-weight="600" fill="{D_INK}">{name}</text>'
                 f'<text x="0" y="{y + 30}" font-family="IBM Plex Mono, monospace" font-size="13" fill="{D_MUTED}">{sub}</text>'
                 f'<text x="1000" y="{y + 21}" text-anchor="end" font-family="Barlow, sans-serif" font-size="18" font-weight="600" fill="{D_INK}">{num(dd["iš_viso"])}</text>')
    xm = sx(avg)
    o.append(f'<line x1="{xm:.1f}" y1="30" x2="{xm:.1f}" y2="148" stroke="{D_INK}" stroke-width="1.5" stroke-dasharray="4 3"/>'
             f'<text x="{xm - 6:.1f}" y="22" text-anchor="end" font-family="IBM Plex Mono, monospace" font-size="13" font-weight="500" fill="{D_INK}">paros vidurkis {num(avg)}</text>')
    if eve is not None:
        xe = sx(eve)
        o.append(f'<line x1="{xe:.1f}" y1="30" x2="{xe:.1f}" y2="148" stroke="#F2A93B" stroke-width="1.5" stroke-dasharray="4 3"/>'
                 f'<text x="{xe + 6:.1f}" y="22" font-family="IBM Plex Mono, monospace" font-size="13" font-weight="500" fill="#F2A93B">vakaro pikas {num(eve)}</text>')
    o.append(f'<g font-family="IBM Plex Mono, monospace" font-size="13" fill="{D_MUTED}">{"".join(labs)}<text x="1000" y="22" text-anchor="end">iš viso</text></g>')
    o.append("</svg>")
    return "\n".join(o)


def world_kpis(pr: dict) -> list[tuple]:
    R = pr["rodikliai"]
    rows = []
    for key, label in (("brent", "Nafta (Brent)"), ("ttf", "Dujos (TTF)"), ("eua", "Taršos leidimai (ATL)")):
        r = R[key]
        rows.append((label, r["reikšmė"], r.get("vienetai", ""), r.get("pokytis_d_proc"), r.get("pokytis_7d_proc"), r.get("pokytis_30d_proc")))
    return rows


def block_world(inp: Inputs) -> str:
    pr = inp.world
    R = pr["rodikliai"]
    t = R["brent"].get("kotiruotės_laikas") or R["ttf"].get("kotiruotės_laikas") or ""
    when = ""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d:\d\d)", t)
    if m:
        dq = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        when = f"kotiruotės {day_short(dq)} {m.group(4)} · "
    o = ['<section class="mr-block">', '<h2 class="mr-h">Pasaulio rinkos</h2>',
         f'<p class="mr-sub">{when}nafta, dujos ir taršos leidimai · poveikis Lietuvos elektros kainai</p>', '<div class="mr-kpis">']
    for label, val, unit, d1, d7, d30 in world_kpis(pr):
        note = []
        if d1 is not None:
            note.append(f'<span class="mr-delta">{pct(d1)}</span> per dieną')
        if d7 is not None:
            note.append(f"{pct(d7)} per 7 d.")
        o.append(_kpi(label, num(val, 2), e(unit), ", ".join(note)))
    o.append("</div>")
    sec = world_sections(pr)
    if sec.get("Santrauka"):
        o.append(f'<p class="mr-text">{_md_inline(" ".join(sec["Santrauka"]))}</p>')
    ch = world_chain_svg(pr)
    if ch:
        o.append('<figure class="mr-fig mr-fig-dark">\n<figcaption class="mr-cap">Nuo dujų iki elektros kainos: dujinių elektrinių kintamieji kaštai ir Lietuvos kaina, €/MWh</figcaption>')
        o.append(ch)
        o.append("</figure>")
        o.append('<p class="mr-note">Šviesi juostos dalis – kuras (TTF kaina, padalyta iš naudingumo), pilka – CO2 (ATL kaina pagal išmetimus), tamsi – kiti kintamieji kaštai.</p>')
    for name in ("Nafta", "Dujos ir SGD", "Poveikis Lietuvai"):
        key = next((k for k in sec if k.startswith(name)), None)
        if key and sec[key]:
            o.append(f'<h3 class="mr-h3">{e(name)}</h3>\n<p class="mr-text">{_md_inline(" ".join(sec[key]))}</p>')
    watch_key = next((k for k in sec if k.startswith("Ką stebėti")), None)
    if watch_key:
        items = [re.sub(r"^[-•]\s*", "", x) for x in sec[watch_key] if x.startswith(("-", "•"))]
        if items:
            o.append('<h3 class="mr-h3">Ką stebėti</h3>\n' + "\n".join(f'<p class="mr-text">– {_md_inline(x)}</p>' for x in items))
    o.append("</section>")
    return "\n".join(o)


def inst_short(x: str | None) -> str:
    """„Lietuvos Respublikos energetikos ministerija“ → „Energetikos ministerija“."""
    x = (x or "").strip()
    if x.startswith("Lietuvos Respublikos "):
        x = x[len("Lietuvos Respublikos "):]
        x = x[:1].upper() + x[1:]
    return x


def legal_overview(inp: Inputs) -> str:
    acts = inp.legal["energy_relevant"]
    n = len(acts)
    return f"{n} {plural(n, 'naujas energetikos teisės aktas', 'nauji energetikos teisės aktai', 'naujų energetikos teisės aktų')}: {acts[0].get('pavadinimas', '').rstrip('.')}."


def block_legal(inp: Inputs) -> str:
    acts = inp.legal["energy_relevant"]
    o = ['<section class="mr-block">', '<h2 class="mr-h">Teisės aktai</h2>',
         '<p class="mr-sub">Teisės aktų registre paskelbti energetikai aktualūs aktai · e-tar.lt</p>',
         '<table class="mr-table">', '<thead><tr><th>Sritis</th><th>Aktas</th><th>Institucija, paskelbta</th><th>Įsigalioja</th></tr></thead>', "<tbody>"]
    for a in acts:
        title = e(a.get("pavadinimas") or "")
        if https(a.get("url")):
            title = f'<a href="{ea(a["url"])}">{title}</a>'
        pub = a.get("paskelbta_tar") or a.get("priimtas") or ""
        eff = a.get("isigalioja") or ""
        o.append(f'<tr><td><span class="mr-status mr-sev-medium">{e(a.get("sritis") or "Paskelbta")}</span></td><td>{title}</td>'
                 f'<td>{e(inst_short(a.get("priemusi_inst")))}{", " if pub else ""}{f"<time datetime={chr(34)}{ea(pub[:10])}{chr(34)}>{e(pub[:10])}</time>" if pub else ""}</td>'
                 f'<td>{f"<time datetime={chr(34)}{ea(eff[:10])}{chr(34)}>{e(eff[:10])}</time>" if eff else "–"}</td></tr>')
    o += ["</tbody>", "</table>", "</section>"]
    return "\n".join(o)


def grants_overview(inp: Inputs) -> str:
    k = inp.grants["kvietimai"]
    ch = [x for x in k if x.get("būsena") in ("nauja", "pasikeitė", "baigiasi", "pasibaigė")]
    n = len(ch)
    return f"{n} {plural(n, 'kvietimo pokytis', 'kvietimų pokyčiai', 'kvietimų pokyčių')}: {ch[0].get('pavadinimas', '').rstrip('.')} ({ch[0].get('būsena')})." if n else f"Stebimi {len(k)} kvietimai, pokyčių nėra."


def block_grants(inp: Inputs) -> str:
    k = inp.grants["kvietimai"]
    o = ['<section class="mr-block">', '<h2 class="mr-h">Paramos</h2>',
         '<p class="mr-sub">Kvietimai ir jų pokyčiai · APVA, LEA, ES investicijos, Inovacijų agentūra, ILTE, EnMin</p>',
         '<table class="mr-table">', '<thead><tr><th>Būsena</th><th>Kvietimas</th><th>Institucija</th><th>Esmė</th><th>Terminas</th></tr></thead>', "<tbody>"]
    for x in k:
        sev = SEV.get(x.get("svarba"), "low")
        name = e(x.get("pavadinimas") or "")
        if https(x.get("nuoroda")):
            name = f'<a href="{ea(x["nuoroda"])}">{name}</a>'
        end = x.get("pabaiga") or ""
        o.append(f'<tr><td><span class="mr-status mr-sev-{sev}">{e(cap(x.get("būsena") or ""))}</span></td><td>{name}</td>'
                 f'<td>{e(x.get("institucija") or "–")}</td><td>{e(x.get("esmė") or x.get("pokytis") or "")}</td>'
                 f'<td>{f"<time datetime={chr(34)}{ea(end[:10])}{chr(34)}>{e(end[:10])}</time>" if end else "–"}</td></tr>')
    o += ["</tbody>", "</table>", "</section>"]
    return "\n".join(o)


# ─── šiandien: terminai ir įsigaliojimai (lėti procesai – savaitės apžvalgoje) ──

DAILY_SLOW = False      # teisės aktų ir paramų sąrašai kasdieniame laiške (2026-11-20 sprendimas: jie – savaitės apžvalgoje)


def today_items(inp: Inputs) -> list[str]:
    """Kas vyksta šiandien ar rytoj: paramų terminai ir pradžios, teisės aktų įsigaliojimas."""
    if getattr(inp, "_today", None) is not None:
        return inp._today
    d = inp.day
    iso, tom = d.isoformat(), date.fromordinal(d.toordinal() + 1).isoformat()
    base = getattr(inp, "data_dir", None) or os.path.join(ROOT, "data")
    g = load(os.path.join(base, "šaltiniai", "paramos-būsena.json")) or {}
    t = load(os.path.join(base, "šaltiniai", "tar.json")) or {}
    out = []
    for x in g.get("kvietimai") or []:
        name = re.sub(r"\s+\d{4}-\d{2}$", "", x.get("pavadinimas") or "").strip()
        inst = x.get("institucija") or ""
        end, start = (x.get("pabaiga") or "")[:10], (x.get("pradžia") or "")[:10]
        if x.get("būklė") == "paskelbtas" and end == iso:
            out.append(f"Šiandien baigiasi paraiškų priėmimas: {name} ({inst}).")
        elif x.get("būklė") == "paskelbtas" and end == tom:
            out.append(f"Rytoj baigiasi paraiškų priėmimas: {name} ({inst}).")
        if start == iso:
            out.append(f"Šiandien prasideda paraiškų priėmimas: {name} ({inst}).")
    for a in t.get("aktai") or []:
        if (a.get("isigalioja") or "")[:10] == iso:
            out.append(f"Šiandien įsigalioja: {(a.get('pavadinimas') or '').rstrip('.')}.")
    inp._today = out
    return out


def today_overview(inp: Inputs) -> str:
    it = today_items(inp)
    n = len(it) - 1
    return it[0] + (f" Dar {n} {plural(n, 'terminas', 'terminai', 'terminų')} – žemiau." if n > 0 else "")


def block_today(inp: Inputs) -> str:
    it = today_items(inp)
    if not it:
        return ""
    li = "\n".join(f'<li class="mr-sip-row"><span class="mr-sip-text">{e(x)}</span></li>' for x in it)
    return f'<section class="mr-block">\n<h2 class="mr-h">Šiandien</h2>\n<p class="mr-sub">Paramų terminai ir teisės aktų įsigaliojimas</p>\n<ul class="mr-sip">\n{li}\n</ul>\n</section>'


def mail_today(inp: Inputs) -> str:
    it = today_items(inp)
    if not it:
        return ""
    tr = "".join(f'<tr><td width="14" valign="top" style="padding:4px 0; font-family:{FONT}; font-size:13px; line-height:19px; color:{INK};">–</td>'
                 f'<td style="padding:4px 0; font-family:{FONT}; font-size:13px; line-height:19px; color:{INK2};">{e(x)}</td></tr>' for x in it)
    return m_section("Šiandien", "Paramų terminai ir teisės aktų įsigaliojimas · visa savaitės apžvalga – pirmadieniais", m_table(tr))


def fragment(inp: Inputs, alerts: list[str]) -> str:
    parts = [f'<article class="mr-report" data-date="{inp.day.isoformat()}">']
    for a in alerts:
        parts.append(f'<p class="mr-alert">{e(a)}</p>')
    for b in (block_overview(inp),
              block_today(inp),
              block_energy(inp) if inp.price is not None else "",
              block_grants(inp) if inp.grants and DAILY_SLOW else "",
              block_legal(inp) if inp.legal and DAILY_SLOW else "",
              block_news(inp) if inp.news else "",
              block_world(inp) if inp.world else ""):
        if b:
            parts.append(b)
    src = []
    if inp.price is not None:
        src += ["ENTSO-E", "Elering", "EEX", "ICE (TTF)"]
    if inp.world:
        src += ["Yahoo Finance", "ECB", "EIA"]
    now = datetime.now(VLN)
    news_src = "; naujienų šaltiniai nurodyti prie kiekvienos naujienos" if inp.news else ""
    parts.append(f'<footer class="mr-foot">\n<p class="mr-note">Šaltiniai: {e(", ".join(dict.fromkeys(src)) or "–")}{news_src}. '
                 f'Skaičiavimai – MB Enerconsult. Parengta automatiškai {now:%Y-%m-%d %H:%M}.</p>\n</footer>')
    parts.append("</article>")
    return "\n\n".join(parts) + "\n"


# ─── laiškas (B variantas: be paveikslėlių; nuorodos – tik į Enerconsult svetainę) ─

def m_tr(inner: str, pad: str = "22px 36px 0") -> str:
    # mr-px: telefone šonų paraštės 16 px vietoj 36 px (žr. <style> laiško antraštėje)
    return f'<tr><td class="mr-px" style="padding:{pad};">{inner}</td></tr>'


def m_table(rows: str, extra: str = "") -> str:
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;{extra}">{rows}</table>'


def m_p(text: str, size=14, lh=21, color=INK2, extra="") -> str:
    return f'<p style="margin:0; font-family:{FONT}; font-size:{size}px; line-height:{lh}px; color:{color};{extra}">{text}</p>'


def mr_day_url(d: date) -> str:
    """Tos dienos ataskaita svetainėje (archyvo adresas – tinka ir šiandienai)."""
    return f"{MR_URL}?data={d.isoformat()}"


def m_a(url: str, text: str, color: str = INK, underline: bool = True) -> str:
    deco = "underline" if underline else "none"
    return f'<a href="{ea(url)}" style="color:{color}; text-decoration:{deco};">{text}</a>'


def m_button(url: str, text: str) -> str:
    # spalva ir paraštės – langelyje, kad mygtukas atrodytų vienodai ir Outlook kompiuteryje
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate;"><tr>'
            f'<td bgcolor="{INK}" style="padding:9px 18px; background:{INK}; border-radius:3px;">'
            f'<a href="{ea(url)}" style="font-family:{FONT}; font-size:13px; line-height:18px; font-weight:bold; color:#FFFFFF; text-decoration:none;">{text}</a>'
            f'</td></tr></table>')


def unsub_line() -> str:
    mailto = f"mailto:{MR_MAILBOX}?subject=Atsisakyti"
    return ("Gaunate šį laišką, nes užsiprenumeravote Morning Report. Norėdami atsisakyti, atsakykite į šį laišką žodžiu „Atsisakyti“ arba "
            + m_a(mailto, "parašykite mums", INK2) + ".")


def brevo_footer_lines() -> str:
    """Brevo laiško atsisakymo ir kontakto eilutės (vietoj unsub_line). {{ unsubscribe }} – Brevo asmeninė nuoroda."""
    mailto = f"mailto:{MR_MAILBOX}"
    return ("Gaunate šį laišką, nes užsiprenumeravote Morning Report. "
            + m_a("{{ unsubscribe }}", "Atsisakyti prenumeratos", INK2) + ".</p>"
            + f'<p style="margin:0; padding-top:6px; font-family:{FONT}; font-size:12px; line-height:18px; color:{INK2};">'
            + "Pastebėjote klaidą, kas nors neveikia ar norite ką nors pasakyti? Parašykite "
            + m_a(mailto, MR_MAILBOX, INK2) + ".")


def m_section(title: str, caption: str | None, inner: str, first=False) -> str:
    top = "" if first else f"border-top:1px solid {LINE}; padding-top:18px;"
    cap_html = m_p(caption, 12, 18, INK3, " font-style:italic;") if caption else ""
    return m_tr(f'<div style="{top}">' + m_p(title, 18, 24, INK, " font-weight:bold;") + cap_html
                + f'<div style="height:12px; line-height:12px; font-size:0;">&nbsp;</div>{inner}</div>')


def m_sub(title: str, caption: str | None = None) -> str:
    c = m_p(caption, 11, 16, INK3) if caption else ""
    return f'<div style="height:16px; line-height:16px; font-size:0;">&nbsp;</div>' + m_p(title, 14, 20, INK, " font-weight:bold;") + c + \
        '<div style="height:6px; line-height:6px; font-size:0;">&nbsp;</div>'


def m_th(cells: list[tuple[str, str, str]]) -> str:
    return "<tr>" + "".join(
        f'<th align="{a}" style="{("width:" + w + ";") if w else ""} padding:6px 8px; border-bottom:1px solid #A6A6A6; background:#F2F2F2; '
        f'font-family:{FONT}; font-size:11px; line-height:16px; font-weight:bold; color:{INK2}; text-align:{a};">{c}</th>' for c, w, a in cells) + "</tr>"


def m_td(content: str, align="left", bold=False, color=INK2, extra="") -> str:
    return (f'<td align="{align}" valign="top" style="padding:8px; border-bottom:1px solid {LINE}; font-family:{FONT}; font-size:13px; '
            f'line-height:19px; color:{color}; text-align:{align};{" font-weight:bold;" if bold else ""}{extra}">{content}</td>')


def m_bar(width_pct: float, color: str, align="left") -> str:
    w = max(1, min(100, round(width_pct)))
    return (f'<table role="presentation" align="{align}" width="{w}%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">'
            f'<tr><td height="12" bgcolor="{color}" style="height:12px; background:{color}; font-size:0; line-height:0;">&nbsp;</td></tr></table>')


def mail_overview(inp: Inputs) -> str:
    rows = []
    if inp.price is not None:
        rows.append(("Elektra", energy_overview(inp)))
    if today_items(inp):
        rows.append(("Šiandien", e(today_overview(inp))))
    if inp.grants and DAILY_SLOW:
        rows.append(("Paramos", e(grants_overview(inp))))
    if inp.legal and DAILY_SLOW:
        rows.append(("Teisės aktai", e(legal_overview(inp))))
    if inp.news:
        rows.append(("Naujienos", e(news_overview(inp))))
    if not rows:
        return ""
    tr = "".join(f'<tr><td width="28%" valign="top" style="padding:9px 8px 9px 0; border-top:1px solid {LINE}; font-family:{FONT}; font-size:14px; line-height:20px; font-weight:bold; color:{INK};">{t}</td>'
                 f'<td valign="top" style="padding:9px 0; border-top:1px solid {LINE}; font-family:{FONT}; font-size:14px; line-height:20px; color:{INK2};">{x}</td></tr>' for t, x in rows)
    return m_section("Apžvalga", None, m_table(tr), first=True)


def mail_energy(inp: Inputs) -> str:
    s, d = inp.sit, inp.day
    inner = m_p(e(energy_headline(inp)), 16, 22, INK, " font-weight:bold;") + \
        "".join(m_p(e(t), 14, 21, INK2, " padding-top:6px;") for t in energy_paragraphs(inp))
    if s:
        m, kn = s["mainai"], s["kaimynai"]

        def flow(code, v, gen_to, gen_from):
            if abs(v) < 5:
                return "–"
            return f"į Lietuvą {num(abs(v), 0)}{NB}MW" if v > 0 else f"iš Lietuvos {num(abs(v), 0)}{NB}MW"
        rows = m_th([("Zona", "", "left"), ("Kaina, €/MWh", "26%", "right"), ("Prekybos srautas", "36%", "left")])
        rows += "<tr>" + m_td("Lietuva", bold=True, color=INK) + m_td(num(s["kaina"]["lt"]), "right", True, INK) + m_td("–") + "</tr>"
        for code, name in (("LV", "Latvija"), ("SE4", "Švedija (SE4)"), ("PL", "Lenkija")):
            rows += "<tr>" + m_td(name, bold=True, color=INK) + m_td(num(kn[code]), "right", True, INK) + m_td(flow(code, m[code]["mw"], "", "")) + "</tr>"
        tot = m["iš_viso"]
        inner += m_sub("Kainos ir srautai", "paros vidurkiai") + m_table(rows) + \
            m_p(f"Lietuvoje: vėjas {num(s['vėjas']['mw'], 0)}{NB}MW, saulė {num(s['saulė']['mw'], 0)}{NB}MW. "
                f"Grynasis {'importas' if tot['mw'] >= 0 else 'eksportas'} – {num(abs(tot['mw']), 0)}{NB}MW, {abs(tot['proc_vartojimo'])}{NB}% vartojimo.",
                12, 18, INK2, " padding-top:6px;")
        # kas lemia kainą
        c = contributions(inp)
        items = sorted([(c.get(k, 0.0), k, name) for k, name in drivers(inp)], key=lambda t: -t[0]) + [(c.get("kita", 0.0), "kita", "Kita")]
        maxpos = max([v for v, _, _ in items if v > 0] + [1])
        maxneg = max([-v for v, _, _ in items if v < 0] + [1])
        unit = min(40 / maxpos, 20 / maxneg)          # procentai lentelės pločio vienam €/MWh
        tr = ""
        for v, key, name in items:
            r = round(v)
            col = OTHER_BAR if key == "kita" else (RAISE_BAR if v > 0 else LOWER_BAR)
            neg = m_bar(abs(v) * unit / 20 * 100, col, "right") if r < 0 else "&nbsp;"
            pos = m_bar(abs(v) * unit / 40 * 100, col, "left") if r > 0 else "&nbsp;"
            tr += (f'<tr><td width="34%" style="padding:4px 8px 4px 0; font-family:{FONT}; font-size:13px; line-height:18px; color:{INK};">{name}</td>'
                   f'<td width="20%" valign="middle" style="padding:4px 0;">{neg}</td>'
                   f'<td width="1" bgcolor="{INK}" style="width:1px; background:{INK}; font-size:0; line-height:0;">&nbsp;</td>'
                   f'<td width="40%" valign="middle" style="padding:4px 0;">{pos}</td>'
                   f'<td align="right" style="padding:4px 0; font-family:{FONT}; font-size:14px; line-height:18px; font-weight:bold; color:{INK}; text-align:right;">{sgn(r)}</td></tr>')
        k = s["kaina"]
        tr += (f'<tr><td colspan="4" style="padding:8px 0 0; border-top:1px solid {INK}; font-family:{FONT}; font-size:13px; line-height:20px; color:{INK2};">'
               f'įprastomis sąlygomis <b style="color:{INK};">{num(k["norma"], 0)}</b> → šiandien <b style="color:{INK};">{num(k["lt"])}</b></td>'
               f'<td align="right" style="padding:8px 0 0; border-top:1px solid {INK}; font-family:{FONT}; font-size:16px; line-height:20px; font-weight:bold; color:{INK}; text-align:right;">{sgn(float(k["lt"]) - float(k["norma"]))}</td></tr>')
        legend = "".join(f'<td width="10" height="10" bgcolor="{col}" style="width:10px; height:10px; background:{col}; font-size:0; line-height:0;">&nbsp;</td>'
                         f'<td style="padding:0 14px 0 6px; font-family:{FONT}; font-size:11px; line-height:16px; color:{INK3};">{t}</td>'
                         for col, t in ((RAISE_BAR, "kelia kainą"), (LOWER_BAR, "mažina kainą"), (OTHER_BAR, "kita – veiksniai jos nepaaiškina")))
        inner += m_sub("Kas lemia kainą", "nuokrypis nuo kainos įprastomis sąlygomis, €/MWh") + m_table(tr) + \
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse; margin-top:8px;"><tr>{legend}</tr></table>' + \
            m_p(e(drivers_note(inp)), 11, 16, INK3, " padding-top:6px;")
        # sąlygos
        rows = m_th([("Rodiklis", "", "left"), ("Šiandien", "24%", "right"), ("Įprasta", "20%", "right")])
        for lbl, val, unit_, note in conditions(inp):
            norm = note.split(" · ")[0].replace("įprasta ", "").replace("12 mėn. vidurkis ", "")
            state = note.split(" · ")[1] if " · " in note else ""
            if lbl == "Jungtys":
                ln = lines_note(inp)
                lab = f'{lbl}<br><span style="font-weight:normal; font-size:12px; color:{INK3};">{e(ln)}</span>' if ln else lbl
                rows += "<tr>" + m_td(lab, bold=True, color=INK) + m_td(val, "right", True, INK) + m_td("–", "right") + "</tr>"
                continue
            rows += ("<tr>" + m_td(f'{lbl}<br><span style="font-weight:normal; font-size:12px; color:{INK3};">{e(state)}</span>', bold=True, color=INK)
                     + m_td(f"{val}{NB}{unit_}", "right", True, INK) + m_td(norm, "right") + "</tr>")
        inner += m_sub("Sąlygos šiandien", "paros vidurkiai; vėjas, saulė ir vartojimas – kitos paros prognozės; dujos – 12 mėn. vidurkis") + m_table(rows)
    if inp.hours:
        h = inp.hours
        ints = [("Naktis", 0, 6), ("Ryto kilimas", 6, 7), ("Ryto pikas", 7, 10), ("Vidurdienis", 10, 16), ("Popietė", 16, 18), ("Vakaro pikas", 18, 22), ("Vėlus vakaras", 22, 24)]
        vals = [(n, a, b, sum(h[a:b]) / (b - a)) for n, a, b in ints]
        vmax = max(max(v for *_, v in vals), 1)
        tr = ""
        for n, a, b, v in vals:
            bar = m_bar(max(v, 0) / vmax * 100, INK) if v > 0 else "&nbsp;"
            tr += (f'<tr><td width="40%" style="padding:5px 8px 5px 0; border-bottom:1px solid {LINE}; font-family:{FONT}; font-size:13px; line-height:18px; color:{INK};">'
                   f'{n} <span style="color:{INK3};">{a}–{b}{NB}val.</span></td>'
                   f'<td width="44%" valign="middle" style="padding:5px 0; border-bottom:1px solid {LINE};">{bar}</td>'
                   f'<td align="right" style="padding:5px 0; border-bottom:1px solid {LINE}; font-family:{FONT}; font-size:13px; line-height:18px; font-weight:bold; color:{INK}; text-align:right;">{num(v)}</td></tr>')
        txt = f"Brangiausia {hours_label(h.index(max(h)))} – {num(max(h))}{NB}€/MWh, pigiausia {hours_label(h.index(min(h)))} – {num(min(h))}{NB}€/MWh."
        if s:
            b = s["kaina"]["intervalas"]
            txt += f" Įprasta paros kaina – {num(b[0], 0)}–{num(b[1], 0)}{NB}€/MWh."
        inner += m_sub("Kaina per parą", "vidutinė kaina paros dalimis, €/MWh, Lietuvos laiku") + m_table(tr) + m_p(txt, 12, 18, INK2, " padding-top:6px;")
    visuals = [name for name, ok in (("rinkos žemėlapis", bool(s)), ("valandinių kainų grafikas", bool(inp.hours))) if ok]
    if visuals:
        inner += m_p(f'{cap(" ir ".join(visuals))} – {m_a(mr_day_url(d), "svetainėje")}.', 13, 19, INK2, " padding-top:12px;")
    inner += m_p("Šaltiniai: ENTSO-E, Elering, EEX, ICE (TTF). Skaičiavimai – MB Enerconsult.", 11, 16, INK3, " padding-top:10px;")
    return m_section("Elektros rinka", f"{cap(WEEKDAYS[d.weekday()])}, {day_short(d)} · kitos paros rinka", inner)


def mail_news(inp: Inputs) -> str:
    items = news_sorted(inp)
    top, rest = items[0], items[1:]
    lead = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;"><tr><td bgcolor="#F4F5F2" style="padding:12px 14px; background:#F4F5F2;">'
            + m_p("Svarbiausia", 11, 16, INK3, " font-weight:bold;")
            + m_p(e(top["pavadinimas"]), 15, 21, INK, " font-weight:bold; padding-top:2px;")
            + m_p(e(top["santrauka"]), 13, 19, INK2, " padding-top:4px;")
            + m_p(e(f"{top.get('šaltinis') or ''} · {top['regionas']}"), 11, 16, INK3, " font-style:italic; padding-top:4px;")
            + "</td></tr></table>")
    body = ""
    for reg in REGIONS:
        rows = [n for n in rest if n.get("regionas") == reg]
        if not rows:
            continue
        body += m_p(reg, 13, 18, INK, f" font-weight:bold; padding-top:16px; padding-bottom:2px; border-bottom:1px solid {INK};")
        for n in rows:
            body += (f'<div style="padding:8px 0; border-bottom:1px solid {LINE};">' + m_p(e(n["pavadinimas"]), 13, 19, INK, " font-weight:bold;")
                     + m_p(e(n["santrauka"]), 13, 19, INK2) + m_p(e(n.get("šaltinis") or ""), 11, 16, INK3, " font-style:italic;") + "</div>")
    return m_section("Naujienos", "Per parą · šaltinis nurodytas prie kiekvienos naujienos", lead + body)


def mail_world(inp: Inputs) -> str:
    pr = inp.world

    def ch(v):
        if v is None:
            return "–"
        return f'<span style="color:{RAISE_TXT if v > 0 else LOWER_TXT}; font-weight:bold;">{pct(v)}</span>'
    rows = m_th([("Rodiklis", "", "left"), ("Kaina", "28%", "right"), ("Per dieną", "17%", "right"), ("Per 7 d.", "16%", "right")])
    for label, val, unit, d1, d7, d30 in world_kpis(pr):
        # siaurame ekrane vienetas nukeliamas po skaičiumi, kad lentelė tilptų telefone
        rows += ("<tr>" + m_td(label, bold=True, color=INK) + m_td(f'<b style="color:{INK}; white-space:nowrap;">{num(val, 2)}</b> '
                                                                   f'<span style="font-size:11px; white-space:nowrap;">{e(unit)}</span>', "right")
                 + m_td(ch(d1), "right") + m_td(ch(d7), "right") + "</tr>")
    sec = world_sections(pr)
    inner = m_table(rows)
    if sec.get("Santrauka"):
        inner += m_p(_md_inline(" ".join(sec["Santrauka"])), 13, 19, INK2, " padding-top:10px;")
    c = ((pr.get("grandinė") or {}).get("srmc") or {}).get("ccgt")
    lt_el = ((pr.get("rodikliai") or {}).get("lt_elektra") or {}).get("šiandien") or {}
    if c and lt_el.get("vidurkis") is not None:
        inner += m_p(f"Dujinės elektrinės kintamieji kaštai – {num(c['iš_viso'])}{NB}€/MWh (kuras {num(c['kuras'])}, CO2 {num(c['co2'])}, kiti {num(c['vom'])}). "
                     f"Lietuvos paros vidurkis – {num(lt_el['vidurkis'])}{NB}€/MWh.", 13, 19, INK2, " padding-top:8px;")
    watch_key = next((k for k in sec if k.startswith("Ką stebėti")), None)
    if watch_key:
        items = [re.sub(r"^[-•]\s*", "", x) for x in sec[watch_key] if x.startswith(("-", "•"))]
        if items:
            tr = "".join(f'<tr><td width="14" valign="top" style="padding:4px 0; font-family:{FONT}; font-size:13px; line-height:19px; color:{INK};">–</td>'
                         f'<td style="padding:4px 0; font-family:{FONT}; font-size:13px; line-height:19px; color:{INK2};">{_md_inline(x)}</td></tr>' for x in items)
            inner += m_sub("Ką stebėti") + m_table(tr)
    R = pr["rodikliai"]
    t = R["brent"].get("kotiruotės_laikas") or ""
    mm = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d:\d\d)", t)
    capt = "Nafta, dujos ir taršos leidimai"
    if mm:
        capt = f"Kotiruotės {day_short(date(int(mm.group(1)), int(mm.group(2)), int(mm.group(3))))} {mm.group(4)} · " + capt.lower()
    return m_section("Pasaulio rinkos", capt, inner)


def mail_legal(inp: Inputs) -> str:
    rows = m_th([("Aktas", "", "left"), ("Institucija, data", "34%", "left")])
    for a in inp.legal["energy_relevant"]:
        pub = (a.get("paskelbta_tar") or a.get("priimtas") or "")[:10]
        rows += "<tr>" + m_td(e(a.get("pavadinimas") or ""), bold=True, color=INK) + m_td(e(f"{inst_short(a.get('priemusi_inst'))}, {pub}".strip(", "))) + "</tr>"
    return m_section("Teisės aktai", "Teisės aktų registre paskelbti energetikai aktualūs aktai", m_table(rows))


def mail_grants(inp: Inputs) -> str:
    rows = m_th([("Būsena", "22%", "left"), ("Kvietimas", "", "left"), ("Terminas", "22%", "right")])
    for x in inp.grants["kvietimai"]:
        col = SEV_COLOR.get(x.get("svarba"), "#808080")
        st = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td width="9" height="9" bgcolor="{col}" style="width:9px; height:9px; background:{col}; font-size:0; line-height:0;">&nbsp;</td>'
              f'<td style="padding-left:6px; font-family:{FONT}; font-size:12px; line-height:16px; font-weight:bold; color:{INK};">{e(cap(x.get("būsena") or ""))}</td></tr></table>')
        rows += "<tr>" + m_td(st) + m_td(e(f"{x.get('pavadinimas') or ''} ({x.get('institucija') or '–'})"), bold=True, color=INK) + m_td(e(x.get("pabaiga") or "–"), "right") + "</tr>"
    return m_section("Paramos", "Kvietimai ir jų pokyčiai", m_table(rows))


def email(inp: Inputs, alerts: list[str]) -> str:
    d = inp.day
    pre = energy_headline(inp) if inp.price is not None else "Morning Report"
    rows = []
    rows.append(m_tr(m_table(
        f'<tr><td style="padding:0 0 12px; border-bottom:1px solid #A6A6A6; font-family:{FONT}; font-size:20px; line-height:26px; font-weight:bold; letter-spacing:0.02em; color:{INK};">{m_a(SITE_URL, "Enerconsult", INK, False)}</td>'
        f'<td align="right" style="padding:0 0 12px; border-bottom:1px solid #A6A6A6; font-family:{FONT}; font-size:12px; line-height:26px; font-style:italic; color:{INK3}; text-align:right;">kasdien 7:00</td></tr>'),
        "24px 36px 0"))
    rows.append(m_tr(m_p("Morning Report", 26, 32, INK, " font-weight:bold;") + m_p(e(day_long(d)), 14, 20, INK, " padding-top:2px;")
                     + m_p("Lietuvos energetikos rinka: kainos, šiandienos terminai, naujienos ir pasaulio rinkos", 12, 18, INK3, " font-style:italic; padding-top:2px;"),
                     "18px 36px 0"))
    rows.append(m_tr(m_button(mr_day_url(d), "Atidaryti svetainėje →"), "14px 36px 0"))
    for a in alerts:
        rows.append(m_tr(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="#FFF4DC" style="padding:10px 12px; background:#FFF4DC; border-left:4px solid #F2A93B;">'
                         + m_p(e(a), 13, 19, INK) + "</td></tr></table>", "16px 36px 0"))
    ov = mail_overview(inp)
    if ov:
        rows.append(ov)
    td = mail_today(inp)
    if td:
        rows.append(td)
    if inp.price is not None:
        rows.append(mail_energy(inp))
    if inp.grants and DAILY_SLOW:
        rows.append(mail_grants(inp))
    if inp.legal and DAILY_SLOW:
        rows.append(mail_legal(inp))
    if inp.news:
        rows.append(mail_news(inp))
    if inp.world:
        rows.append(mail_world(inp))
    rows.append(m_tr(f'<div style="border-top:1px solid {LINE}; padding-top:18px;">' + m_p("Iki rytojaus, 7:00.", 16, 22, INK, " font-weight:bold;")
                     + m_p(f'Vaizdinė versija su rinkos žemėlapiu, grafikais ir ankstesnių dienų archyvu – {m_a(MR_URL, "enerconsult.lt/morning-report")}.', 12, 18, INK2, " padding-top:8px;")
                     + m_p(f'Gavote persiųstą laišką? {m_a(MR_URL + "#prenumerata", "Užsiprenumeruokite")}.', 12, 18, INK2, " padding-top:6px;")
                     + m_p(unsub_line(), 12, 18, INK2, " padding-top:6px;")
                     + f'<p style="margin:14px 0 0; padding-top:12px; border-top:1px solid #A6A6A6; font-family:{FONT}; font-size:11px; line-height:16px; text-align:center; color:{INK3};">Morning Report · {m_a(SITE_URL, "MB Enerconsult", INK3)} · {m_a(PRIVACY_URL, "Privatumo politika", INK3)}</p></div>',
                     "26px 36px 28px"))
    body = "\n".join(rows)
    return f'''<!doctype html>
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
<title>Morning Report · {e(day_short(d))}</title>
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


# ─── dienos duomenų archyvas savaitės apžvalgai ─────────────────────────────

def archive_record(inp: Inputs) -> dict:
    """Glaustas dienos įrašas data/archyvas/MMMM-MM-DD.json: iš jų savaitės apžvalga (žr. „savaitės ataskaita.py“)."""
    pr = inp.world
    world = None
    if pr:
        world = {"kotiruotės_laikas": (pr["rodikliai"].get("brent") or {}).get("kotiruotės_laikas"),
                 "rodikliai": [{"pavadinimas": l, "reikšmė": v, "vienetai": u, "per_dieną": d1, "per_7d": d7, "per_30d": d30}
                               for l, v, u, d1, d7, d30 in world_kpis(pr)]}
    return {"data": inp.day.isoformat(), "elektros_rinka": inp.sit, "valandos": inp.hours,
            "naujienos": inp.news, "pasaulio_rinkos": world,
            "teisės_aktai": (inp.legal or {}).get("energy_relevant") if inp.legal else None,
            "paramos": (inp.grants or {}).get("kvietimai") if inp.grants else None}


# ─── pagrindinis ─────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    data_dir = os.path.join(ROOT, "data")
    out_dir = os.path.join(ROOT, "public")
    if "--data" in args:
        data_dir = args[args.index("--data") + 1]
    if "--out" in args:
        out_dir = args[args.index("--out") + 1]
    pos = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] not in ("--data", "--out"))]
    day = date.fromisoformat(pos[0]) if pos else datetime.now(VLN).date()

    inp = Inputs(data_dir, day)
    alerts = []
    if inp.price is None:
        others = any((inp.news, inp.world, inp.legal, inp.grants))
        alerts.append("Šiandienos elektros kainų duomenų gauti nepavyko." + (" Kitos ataskaitos dalys – žemiau." if others else ""))
    elif not inp.sit:
        alerts.append("Šiandien nepavyko apskaičiuoti, kas lemia elektros kainą, todėl rodomos tik kainos.")
    status = {"kainos": inp.hours is not None, "modelis": inp.sit is not None, "naujienos": inp.news is not None,
              "pasaulio_rinkos": inp.world is not None, "teisės_aktai": inp.legal is not None, "paramos": inp.grants is not None}
    print("Ataskaita", day, json.dumps(status, ensure_ascii=False), file=sys.stderr)

    frag = fragment(inp, alerts)
    mail = email(inp, alerts)
    subject = f"Morning Report · {day_short(day)}" + (f" · LT {num(inp.price)}{NB}€/MWh".replace(NB, " ") if inp.price is not None else "")
    akcentas = (energy_headline(inp) if inp.price is not None else "Morning Report").replace(NB, " ")

    os.makedirs(os.path.join(out_dir, "archyvas"), exist_ok=True)

    def write(rel, text):
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    write("skiltis.html", frag)
    write(f"archyvas/{day.isoformat()}.html", frag)
    idx_path = os.path.join(out_dir, "archyvas", "index.json")
    idx = load(idx_path) or []
    idx = [x for x in idx if isinstance(x, dict) and x.get("data") != day.isoformat()]
    idx.append({"data": day.isoformat(), "akcentas": akcentas})
    idx.sort(key=lambda x: x["data"], reverse=True)
    write("archyvas/index.json", json.dumps(idx, ensure_ascii=False, indent=2) + "\n")
    bad = re.findall(r'style="[^"]*[{}\[\]][^"]*"', mail + frag)
    if bad or not re.fullmatch(r"#[0-9A-Fa-f]{6}", LINE):
        sys.exit(f"Sugadinti stiliai laiške ar fragmente ({len(bad)}), pvz. {bad[:1]} – laiškas nesiunčiamas")
    write("laiskas.html", mail)
    # Brevo variantas: atsisakymas – Brevo nuoroda, kontaktas – tik pastaboms
    assert unsub_line() in mail
    write("laiskas-brevo.html", mail.replace(unsub_line(), brevo_footer_lines()))
    write("tema.txt", subject + "\n")
    arch = os.path.join(data_dir, "archyvas")
    os.makedirs(arch, exist_ok=True)
    with open(os.path.join(arch, f"{day.isoformat()}.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(archive_record(inp), f, ensure_ascii=False, indent=1)
        f.write("\n")
    write("data.txt", day.isoformat())          # paskutinis: pagal jį Power Automate supranta, kad ataskaita paruošta
    print(f"Įrašyta: {out_dir} (fragmentas {len(frag)} B, laiškas {len(mail)} B)", file=sys.stderr)


if __name__ == "__main__":
    main()
