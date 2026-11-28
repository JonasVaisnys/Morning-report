#!/usr/bin/env python3
"""
Morning Report angliški tekstai — MB Enerconsult
================================================
Sakiniai, kurie lietuviškai sudaromi pagal linksnius ir todėl negali būti tik išversti
žodynu: elektros rinkos pasakojimas, „Šiandien“ eilutės, naujienų apžvalga.
Logika ir ribos – tos pačios kaip lietuviškoje versijoje (ataskaita.py), čia tik angliškos frazės.

Modulį įkelia ataskaita.py (funkcija en()), kai ataskaita gaminama anglų kalba (--kalba en).
A – ataskaita.py globalai (num, sgn, contributions, drivers ir kt.); num() ir hours_label()
anglų kalba jau rašo angliškai (dešimtainis taškas, 18:00–19:00).
Rašyba: britų anglų kalba, prieš vienintelį „and“ kablelio nėra, procentai be tarpo.
"""

import os
import re
from datetime import date

A = None    # nustato ataskaita.en()

NB = " "

DRIVER_NAMES = {"dujos": "Gas and CO2", "kaimynai": "Neighbouring markets", "vartojimas": "Consumption",
                "pralaidumai": "Interconnector capacity", "saulė": "Solar", "vėjas": "Wind", "kita": "Other"}
REASON = {("dujos", 1): "expensive gas", ("dujos", -1): "cheaper gas",
          ("vartojimas", 1): "high consumption", ("vartojimas", -1): "lower consumption",
          ("vėjas", 1): "weak wind", ("vėjas", -1): "strong wind",
          ("saulė", 1): "low solar output", ("saulė", -1): "high solar output",
          ("pralaidumai", 1): "restricted interconnector capacity", ("pralaidumai", -1): "higher interconnector capacity",
          ("kaimynai", 1): "more expensive neighbouring markets", ("kaimynai", -1): "cheaper neighbouring markets"}
# kai veiksnys gerokai nuo įprasto („labai …“ lietuviškai)
REASON_STRONG = {("dujos", 1): "very expensive gas", ("dujos", -1): "very cheap gas",
                 ("vartojimas", 1): "very high consumption", ("vartojimas", -1): "very low consumption",
                 ("vėjas", 1): "very weak wind", ("vėjas", -1): "very strong wind",
                 ("saulė", 1): "very low solar output", ("saulė", -1): "very high solar output"}
PRICE_PHRASE = {"gerokai mažesnė nei įprasta": "much cheaper than usual", "mažesnė nei įprasta": "cheaper than usual",
                "didesnė nei įprasta": "more expensive than usual", "gerokai didesnė nei įprasta": "much more expensive than usual"}
NORMAL_PRICE = "a normal price for the time of year"
ZONE = {"SE4": "Sweden", "PL": "Poland", "LV": "Latvia", "EE": "Estonia", "FI": "Finland"}
ADJ = {"SE4": "Swedish", "PL": "Polish", "LV": "Latvian", "EE": "Estonian", "FI": "Finnish"}
SHORT = {"SE4": "NordBalt", "PL": "LitPol", "LV": "the interconnectors with Latvia"}
REGION = {"Lietuva": "Lithuania", "Kaimynai": "Neighbours", "Europa": "Europe", "Pasaulis": "World"}
REGION_ON = {"Lietuva": "Lithuania", "Kaimynai": "the neighbours", "Europa": "Europe", "Pasaulis": "the world"}


def join(items: list[str]) -> str:
    """„a, b and c“ – prieš vienintelį „and“ kablelio nėra."""
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def hours(n: int) -> str:
    return f"{n} {'hour' if n == 1 else 'hours'}"


def items_word(n: int) -> str:
    return "item" if n == 1 else "items"


def state(st: str | None) -> str:
    """Lietuviška būsena („gerokai didesnis nei įprasta“, „pigesnės nei įprasta“) angliškai."""
    s = (st or "").strip()
    strong = s.startswith("gerokai")
    if "mažesn" in s:
        w = "well below normal" if strong else "below normal"
    elif "didesn" in s:
        w = "well above normal" if strong else "above normal"
    elif "pigesn" in s:
        w = "much cheaper than usual" if strong else "cheaper than usual"
    elif "brangesn" in s:
        w = "much more expensive than usual" if strong else "more expensive than usual"
    elif s.startswith("įprast"):
        w = "normal"
    else:
        w = s
    return w


# ─── elektros rinka ──────────────────────────────────────────────────────────

def reason(inp, key: str, sign: int, intense: bool = True) -> str:
    if key == "kaimynai":
        return neighbours_reason(inp, sign)
    if intense and A._strong(inp, key, sign) and (key, sign) in REASON_STRONG:
        return REASON_STRONG[(key, sign)]
    return REASON[(key, sign)]


def neighbours_reason(inp, sign: int) -> str:
    det = (inp.sit or {}).get("kaimynų_detalės") or {}
    apk, kain = float(det.get("jungčių_apkrova", 0)), float(det.get("kainos", 0))
    exp, imp = A.lines_full(inp)
    if abs(apk) >= abs(kain):
        if sign < 0 and exp:
            return f"fully loaded interconnectors to {join([ZONE[z] for z in exp])}"
        if sign > 0 and imp:
            return f"fully loaded interconnectors from {join([ZONE[z] for z in imp])}"
    return REASON[("kaimynai", sign)]


def headline_reason(inp) -> str | None:
    k = inp.sit["kaina"]
    diff = float(k["lt"]) - float(k["norma"])
    sign = 1 if diff >= 0 else -1
    c = {f: v for f, v in A.contributions(inp).items() if f in dict(A.drivers(inp))}
    if not c:
        return None
    f, v = max(c.items(), key=lambda t: t[1] * sign)
    if v * sign < max(10.0, 0.5 * abs(diff)):
        return None
    return reason(inp, f, sign)


def energy_headline(inp) -> str:
    p = inp.price
    if inp.sit:
        st = inp.sit["kaina"]["būsena"]
        if st in PRICE_PHRASE:
            why = headline_reason(inp)
            return f"Electricity today: {A.num(p)}{NB}€/MWh – {PRICE_PHRASE[st]}" + (f" due to {why}." if why else ".")
        return f"Electricity today: {A.num(p)}{NB}€/MWh – {NORMAL_PRICE}."
    return f"Electricity today: {A.num(p)}{NB}€/MWh on average."


def fact(inp, key: str, sign: int) -> str:
    """Fizinis veiksnio paaiškinimas skaičiais."""
    s, num = inp.sit, A.num
    if key == "vėjas":
        w, L, n = float(s["vėjas"]["mw"]), float(s["vartojimas"]["mw"]), float(s["vėjas"]["norma"])
        if sign < 0:
            q = w / L if L else 0
            rel = ("more than Lithuania consumes" if q >= 1.05 else "almost as much as Lithuania consumes" if q >= 0.85
                   else f"{round(100 * q)}% of consumption")
            return (f"wind farms are generating {num(w, 0)}{NB}MW – {rel} ({num(L, 0)}{NB}MW); "
                    f"normal for the time of year is about {num(n, 0)}{NB}MW")
        return f"wind farms are generating only {num(w, 0)}{NB}MW, against about {num(n, 0)}{NB}MW normally at this time of year"
    if key == "saulė":
        v = s["saulė"]
        return f"solar plants are generating {num(v['mw'], 0)}{NB}MW, normally about {num(v['norma'], 0)}{NB}MW"
    if key == "vartojimas":
        v = s["vartojimas"]
        return f"consumption is {num(v['mw'], 0)}{NB}MW, normally about {num(v['norma'], 0)}{NB}MW"
    if key == "dujos":
        g = s["dujos"]
        r = round(100 * (float(g["ttf"]) / float(g["norma_12mėn"]) - 1))
        return f"TTF is at {num(g['ttf'])}{NB}€/MWh, {abs(r)}% {'above' if r > 0 else 'below'} its 12-month average"
    if key == "pralaidumai":
        lim = []
        for z in ("SE4", "PL"):
            x = (((s.get("pralaidumai") or {}).get(A.IC_LINES[z][0]) or {}).get("importas")) or {}
            if x.get("mw") is not None and x.get("nominalas") and float(x["mw"]) < 0.85 * float(x["nominalas"]):
                lim.append(f"{SHORT[z]} – {num(x['mw'], 0)} of {num(x['nominalas'], 0)}{NB}MW")
        return ("import capacity is restricted: " + ", ".join(lim)) if lim else "import capacity is lower than usual"
    if key == "kaimynai":
        kn = s["kaimynai"]
        return f"Sweden (SE4) – {num(kn['SE4'])}, Poland – {num(kn['PL'])}, Finland – {num(kn['FI'])}{NB}€/MWh"
    return ""


def lines_note(inp) -> str:
    """Pvz. „LitPol export 23 h, from Latvia 9 h“ – kiek valandų jungtis pilnai apkrauta (bent 6 val.)."""
    jb = (inp.sit or {}).get("jungtys_būsena") or {}
    parts = []
    for z in ("SE4", "PL", "LV"):
        x = jb.get(z) or {}
        for side, word in (("eksportas", "export"), ("importas", "import")):
            n = int(x.get(f"{side}_pilnas_val") or 0)
            if n >= 6:
                where = ("to Latvia" if side == "eksportas" else "from Latvia") if z == "LV" else f"{SHORT[z]} {word}"
                parts.append(f"{where} {n}{NB}h")
    return A.cap(", ".join(parts))


def prices_text(inp, zones: list) -> str:
    """„electricity in Sweden costs 141.0 €/MWh (18:00–19:00 – 192.8 €/MWh), in Poland – 106.0 €/MWh“."""
    kn, kh = inp.sit.get("kaimynai") or {}, inp.sit.get("kaimynų_valandos") or {}
    parts = []
    for i, z in enumerate(zones):
        t = f"electricity in {ZONE[z]} costs {A.num(kn[z])}{NB}€/MWh" if i == 0 else f"in {ZONE[z]} – {A.num(kn[z])}{NB}€/MWh"
        pk = kh.get(z) if i == 0 else None
        if pk and float(pk["max"]) > float(kn[z]) + 25:
            t += f" ({A.hours_label(int(pk['max_val']))} – {A.num(pk['max'])}{NB}€/MWh)"
        parts.append(t)
    return ", ".join(parts)


def trade_sentences(inp, with_number: bool) -> list[str]:
    s, num, sgn = inp.sit, A.num, A.sgn
    m, kn, caps = s.get("mainai") or {}, s.get("kaimynai") or {}, s.get("pralaidumai") or {}
    lt_p = float(s["kaina"]["lt"])
    v = A.contributions(inp).get("kaimynai", 0.0)
    exp, imp = A.trade_lines(inp)
    out = []

    def flows(zones, side):
        parts, ratios = [], []
        for z in zones:
            fl = abs(float((m.get(z) or {}).get("mw") or 0))
            cp = (((caps.get(A.IC_LINES[z][0]) or {}).get(side)) or {}).get("mw")
            parts.append(f"{SHORT[z]} – {num(fl, 0)}" + (f" of {num(cp, 0)}" if cp else "") + f"{NB}MW")
            if cp:
                ratios.append(fl / float(cp))
        r = min(ratios) if ratios else 0
        how = ("use the full interconnector capacity" if r >= 0.99 else "use almost the full interconnector capacity" if r >= 0.9
               else "use the full interconnector capacity for most of the day")
        return how, ", ".join(parts)

    if exp:
        how, fl = flows(exp, "eksportas")
        sent = f"Exports to {join([ZONE[z] for z in exp])} {how} ({fl})"
        dear = [z for z in exp if float(kn.get(z, 0)) > lt_p + 10]
        if dear:
            sent += ": " + prices_text(inp, dear)
            sent += ". " + ("The fully loaded interconnector does not pass this price on to Lithuania" if len(dear) == 1
                            else "The fully loaded interconnectors do not pass these prices on to Lithuania")
            if with_number and abs(v) >= 5:
                sent += (f", so neighbouring markets lower the price ({sgn(v)}{NB}€/MWh)" if v < 0
                         else f"; neighbouring markets raise the price ({sgn(v)}{NB}€/MWh)")
        out.append(sent + ".")
    if imp:
        how, fl = flows(imp, "importas")
        sent = f"Imports from {join([ZONE[z] for z in imp])} {how} ({fl})"
        cheap = [z for z in imp if z in kn and float(kn[z]) < lt_p - 10]
        if cheap:
            sent += ": " + prices_text(inp, cheap)
            sent += ". " + ("The interconnector does" if len(cheap) == 1 else "The interconnectors do") + " not let more of the cheaper electricity through"
            if with_number and abs(v) >= 5:
                sent += (f", so neighbouring markets raise the price ({sgn(v)}{NB}€/MWh)" if v > 0
                         else f"; neighbouring markets lower the price ({sgn(v)}{NB}€/MWh)")
        out.append(sent + ".")
    ll = s.get("lt_lv") or {}
    n_hi, n_lo, dlt = int(ll.get("brangiau_val") or 0), int(ll.get("pigiau_val") or 0), float(ll.get("skirtumas") or 0)
    net_export = float(((m.get("iš_viso") or {}).get("mw")) or 0) < 0
    if n_hi >= 3 and dlt >= 1:
        if exp and net_export:
            out.append(f"Exports {'slightly ' if dlt < 8 else ''}raise the price: for {hours(n_hi)} electricity in Lithuania is more expensive "
                       f"than in Latvia, by {num(dlt)}{NB}€/MWh on average over the day.")
        else:
            out.append(f"For {hours(n_hi)} the interconnectors from Latvia are fully loaded, so electricity in Lithuania is more expensive "
                       f"than in Latvia, by {num(dlt)}{NB}€/MWh on average over the day.")
    elif n_lo >= 3 and dlt <= -1:
        out.append(f"For {hours(n_lo)} electricity in Lithuania is cheaper than in Latvia, by {num(abs(dlt))}{NB}€/MWh on average over the day.")
    return out


def kita_sentences(inp) -> list[str]:
    s, num, sgn = inp.sit, A.num, A.sgn
    v = A.contributions(inp).get("kita", 0.0)
    if abs(v) < A.KITA_RIBA:
        return []
    head = f"The remaining part (“other”, {sgn(v)}{NB}€/MWh) is not explained by these drivers."
    x = s.get("kitos_aiškinimas")
    if not x:
        return [head, "Its cause is not visible in the available data."]
    n = int(x["valandų"])
    when = "For almost the whole day" if n >= 16 else f"From {int(x['nuo']):02d}:00 to {int(x['iki']):02d}:00"
    lead = f"{when} the price is {'lower' if v < 0 else 'higher'} than the model expects"
    lt_p = float(x["kaina"])
    near = lambda z: z in x and abs(float(x[z]) - lt_p) <= max(3.0, 0.03 * lt_p)
    line_free = lambda z: x.get(f"x_{z.lower()}", 0) + x.get(f"i_{z.lower()}", 0) < 0.5 * n
    expl = None
    if v < 0:
        z = next((z for z in ("SE4", "PL") if near(z) and line_free(z)), None)
        if z:
            expl = (f"the Lithuanian price is close to the {ADJ[z]} price ({num(x[z], 0)}{NB}€/MWh) because the interconnector with "
                    f"{ZONE[z]} is not fully loaded for most hours, so the price does not rise above the {ADJ[z]} level")
        elif x.get("lt_lygu_lv_val", 0) >= 0.6 * n and "LV" in x:
            expl = f"for most hours the Lithuanian price matches the Latvian price ({num(x['LV'], 0)}{NB}€/MWh on average)"
            if "FI" in x and float(x["FI"]) < float(x["LV"]) - 10:
                expl += f", while electricity in Finland is even cheaper ({num(x['FI'], 0)}{NB}€/MWh)"
    else:
        if x.get("i_lv", 0) >= 0.3 * n and "LV" in x and lt_p > float(x["LV"]) + 3:
            k_ = int(x["i_lv"])
            expl = (f"for {hours(k_)} the interconnectors from Latvia are fully loaded, so electricity in Lithuania is more expensive "
                    f"than in Latvia ({num(lt_p, 0)} and {num(x['LV'], 0)}{NB}€/MWh)")
        else:
            z = next((z for z in ("SE4", "PL") if x.get(f"i_{z.lower()}", 0) >= 0.5 * n and z in x and float(x[z]) < lt_p - 10), None)
            if z:
                expl = (f"the interconnector from {ZONE[z]} is fully loaded, so cheaper {ADJ[z]} electricity ({num(x[z], 0)}{NB}€/MWh) "
                        f"cannot lower the Lithuanian price any further")
            else:
                z = next((z for z in ("SE4", "PL", "LV") if near(z)), None)
                if z:
                    expl = (f"the Lithuanian price is close to the {ADJ[z]} price ({num(x[z], 0)}{NB}€/MWh) – electricity in the region "
                            f"is more expensive than the model drivers suggest")
    default = ("the interconnectors are not fully loaded and neighbouring prices do not stand out, "
               "so no clear cause is visible in the data")
    return [head, f"{lead}: {expl or default}."]


def spike_sentences(inp) -> list[str]:
    """Kainos šuolis – ta pati logika kaip ataskaita.py spike_sentences(), angliškai (esamasis laikas, kaip kituose sakiniuose)."""
    num = A.num
    out = []
    for e in (inp.sit or {}).get("šuoliai") or []:
        p = float(e["kaina"])
        k = e.get("kartai")
        rel = (f"{num(k)} times the surrounding hours" if k else "far above the surrounding hours") + f" (about {num(e['aplink'], 0)}{NB}€/MWh)"
        if int(e["iki"]) - int(e["nuo"]) > 1:
            out.append(f"From {int(e['nuo']):02d}:00 to {int(e['iki']):02d}:00 the price jumps: the most expensive hour is "
                       f"{A.hours_label(int(e['val']))} at {num(p)}{NB}€/MWh, {rel}.")
        else:
            out.append(f"At {A.hours_label(int(e['val']))} the price jumps to {num(p)}{NB}€/MWh – {rel}.")
        ld, wd = e.get("vartojimas") or {}, e.get("vėjas") or {}
        cond = []
        if ld.get("mw") and ld.get("aplink") and ld["mw"] >= 1.08 * ld["aplink"]:
            cond.append(f"consumption rises ({num(ld['mw'], 0)}{NB}MW, about {num(ld['aplink'], 0)}{NB}MW in the surrounding hours)")
        if wd.get("mw") is not None and wd.get("aplink") and wd["aplink"] - wd["mw"] >= 100 and wd["mw"] <= 0.6 * wd["aplink"]:
            cond.append(f"wind output drops ({num(wd['mw'], 0)}{NB}MW, about {num(wd['aplink'], 0)}{NB}MW in the surrounding hours)")
        if cond:
            out.append(f"In that hour {join(cond)}.")
        lead = "Meanwhile" if cond else "In that hour"
        kn = e.get("kaimynai") or {}
        nb = [z for z in ("LV", "SE4", "PL") if z in kn]
        prices = join([f"{num(kn[z])}" + (f"{NB}€/MWh" if i == 0 else "") + f" in {ZONE[z]}" for i, z in enumerate(nb)])
        imp = e.get("importas") or {}
        live = [z for z in ("SE4", "PL", "LV") if z in imp and imp[z]["pralaidumas"] > 20]
        full = [z for z in live if imp[z]["pilna"]]
        regional = [z for z in (e.get("kaimynų_šuoliai") or []) if z in kn]
        same = next((z for z in ("LV", "SE4", "PL", "EE", "FI") if z in kn and abs(float(kn[z]) - p) <= 1), None)
        if regional:
            eq = [z for z in regional if abs(float(kn[z]) - p) <= 1]
            grp = {}
            for z in regional:
                if z not in eq:
                    grp.setdefault(round(float(kn[z]), 1), []).append(z)
            items = [f"the same {num(p)}{NB}€/MWh in {join([ZONE[z] for z in eq])}"] if eq else []
            items += [f"{num(v)}{NB}€/MWh in {join([ZONE[z] for z in zs])}" for v, zs in grp.items()]
            out.append("The jump is regional: " + join(items) + ".")
        elif live and full == live and nb and p > max(float(kn[z]) for z in nb) + 20:
            items = [f"{num(imp[z]['srautas'], 0)}{NB}MW from {ZONE[z]}" for z in full if not A.spike_reduced(imp[z])]
            for z in [z for z in full if A.spike_reduced(imp[z])]:
                x = imp[z]
                t = f"only {num(x['srautas'], 0)}{NB}MW from {ZONE[z]}" + (", the lowest of the day" if x.get("paros_min") else "")
                extra = [f"about {num(x['aplink'], 0)}{NB}MW in the surrounding hours"]
                nom = x.get("nominalas")
                if nom and float(nom) - x["pralaidumas"] >= 0.15 * float(nom):
                    extra.append(f"{num(nom, 0)}{NB}MW normally")
                items.append(t + f" ({', '.join(extra)})")
            out.append(f"{lead} all import interconnectors are full: {join(items)}.")
            much = "much " if max(float(kn[z]) for z in nb) <= 0.7 * p else ""
            out.append(f"So the price is set by local power plants, while electricity in the neighbouring markets costs {much}less: {prices}.")
        else:
            if full and full != live:
                t = (f"{lead} only the interconnector from {ZONE[full[0]]} is full" if len(full) == 1
                     else f"{lead} only the interconnectors from {join([ZONE[z] for z in full])} are full")
            elif full:
                t = f"{lead} all import interconnectors are full"
            elif live:
                t = f"{lead} the import interconnectors are not full"
            else:
                t = ""
            if nb:
                t = (t + ", while electricity costs " if t else f"{lead} electricity costs ") + prices
            if t:
                out.append(t + ".")
            out.append(f"The Lithuanian price matches the {ADJ[same]} price in that hour." if same
                       else "No clear cause of the jump is visible in the data.")
    return out


def _balanced(st, up, dn) -> bool:
    return (st not in PRICE_PHRASE and up and dn and up[1] >= 15 and dn[1] <= -15
            and min(up[1], -dn[1]) >= 0.6 * max(up[1], -dn[1]))


def energy_paragraphs(inp) -> list[str]:
    num, sgn, cap = A.num, A.sgn, A.cap
    if not inp.sit:
        h = inp.hours
        i_max, i_min = h.index(max(h)), h.index(min(h))
        return [f"The most expensive hour is {A.hours_label(i_max)} ({num(max(h))}{NB}€/MWh), "
                f"the cheapest {A.hours_label(i_min)} ({num(min(h))}{NB}€/MWh)."]
    c = A.contributions(inp)
    k = inp.sit["kaina"]
    diff = float(k["lt"]) - float(k["norma"])
    sign = 1 if diff >= 0 else -1
    drv = {f: c.get(f, 0.0) for f, _ in A.drivers(inp)}
    thr = max(5.0, 0.06 * sum(abs(v) for v in c.values()))
    ranked = sorted(drv.items(), key=lambda t: -abs(t[1]))
    same = [(f, v) for f, v in ranked if v * sign >= thr]
    opp = [(f, v) for f, v in ranked if v * sign <= -thr]
    trade_known = bool(sum(A.trade_lines(inp), []))
    P, mentioned = [], set()
    st = k.get("būsena") or ""
    up = max(drv.items(), key=lambda t: t[1], default=None)
    dn = min(drv.items(), key=lambda t: t[1], default=None)
    if _balanced(st, up, dn):
        P.append(f"{cap(reason(inp, up[0], 1, intense=False))} and {reason(inp, dn[0], -1, intense=False)} almost offset each other today.")
        P.append(f"The price is pushed up by {reason(inp, up[0], 1)} ({sgn(up[1])}{NB}€/MWh): {fact(inp, up[0], 1)}.")
        P.append(f"It is pushed down by {reason(inp, dn[0], -1)} ({sgn(dn[1])}{NB}€/MWh): {fact(inp, dn[0], -1)}.")
        mentioned |= {up[0], dn[0]}
    elif not same:
        P.append("The drivers of the price are close to normal today.")
    else:
        f0, v0 = same[0]
        way, back = ("up", "down") if sign > 0 else ("down", "up")
        P.append(f"The price is pushed {way} most by {reason(inp, f0, sign)} ({sgn(v0)}{NB}€/MWh): {fact(inp, f0, sign)}.")
        mentioned.add(f0)
        for f, v in same[1:3]:
            if abs(v) >= 0.35 * abs(v0) and not (f == "kaimynai" and trade_known):
                P.append(f"It is also pushed {way} by {reason(inp, f, sign, intense=False)} ({sgn(v)}{NB}€/MWh): {fact(inp, f, sign)}.")
                mentioned.add(f)
        for f, v in opp[:2]:
            if f == "kaimynai" and trade_known:
                continue
            bit = "slightly " if abs(v) < 0.4 * abs(v0) else ""
            P.append(f"It is {bit}pushed {back} by {reason(inp, f, -sign, intense=False)} ({sgn(v)}{NB}€/MWh): {fact(inp, f, -sign)}.")
            mentioned.add(f)
    out = [" ".join(P)]
    t = trade_sentences(inp, with_number="kaimynai" not in mentioned)
    if t:
        out.append(" ".join(t))
    sx = spike_sentences(inp)
    if sx:
        out.append(" ".join(sx))
    kx = kita_sentences(inp)
    if kx:
        out.append(" ".join(kx))
    return out


def drivers_note(inp) -> str:
    t = "“Other” is the part of the price that these drivers do not explain."
    if (inp.sit or {}).get("dujų_metodas") == "šiandienos sąlygomis":
        t = ("Gas effect – how much more expensive electricity is because of gas and CO2 prices under today's conditions; "
             "the other drivers are assessed at a normal gas price. "
             "Neighbouring markets – neighbouring prices and whether the interconnectors pass them on. " + t)
    return t


def trade_highlight(inp) -> str:
    kn = inp.sit.get("kaimynai") or {}
    lt_p = float(inp.sit["kaina"]["lt"])
    exp, imp = A.trade_lines(inp)
    dear = [z for z in exp if float(kn.get(z, 0)) >= 1.3 * lt_p + 10]
    cheap = [z for z in imp if float(kn.get(z, 1e9)) <= 0.77 * lt_p - 10]
    names = lambda zs: " and ".join(SHORT[z] for z in zs)
    if dear:
        verb = "is" if len(dear) == 1 else "are"
        return (f"{names(dear)} {verb} fully used for exports to more expensive "
                + join([f"{ZONE[z]} ({A.num(kn[z], 0)}{NB}€/MWh)" for z in dear]) + ".")
    if cheap:
        verb = "is" if len(cheap) == 1 else "are"
        return (f"{names(cheap)} {verb} fully used for imports from cheaper "
                + join([f"{ZONE[z]} ({A.num(kn[z], 0)}{NB}€/MWh)" for z in cheap]) + ".")
    return ""


def energy_overview(inp) -> str:
    num, sgn, cap, e = A.num, A.sgn, A.cap, A.e
    p = inp.price
    if inp.sit:
        st = inp.sit["kaina"]["būsena"]
        head = f"<b>{num(p)}{NB}€/MWh</b> – " + (PRICE_PHRASE.get(st) or NORMAL_PRICE) + "."
        k = inp.sit["kaina"]
        sign = 1 if float(k["lt"]) - float(k["norma"]) >= 0 else -1
        c = {f: A.contributions(inp).get(f, 0.0) for f, _ in A.drivers(inp)}
        up = max(c.items(), key=lambda t: t[1], default=None)
        dn = min(c.items(), key=lambda t: t[1], default=None)
        if _balanced(st, up, dn):
            txt = (f" {cap(e(reason(inp, up[0], 1)))} ({sgn(up[1])}{NB}€/MWh) and {e(reason(inp, dn[0], -1))} "
                   f"({sgn(dn[1])}{NB}€/MWh) almost offset each other.")
        else:
            main = up if sign > 0 else dn
            cnt = dn if sign > 0 else up
            parts = []
            if main and main[1] * sign >= 5:
                parts.append(f"pushed {'up' if sign > 0 else 'down'} most by {e(reason(inp, main[0], sign))} ({sgn(main[1])}{NB}€/MWh)")
            if cnt and -cnt[1] * sign >= 5:
                bit = "slightly " if main and abs(cnt[1]) < 0.4 * abs(main[1]) else ""
                parts.append(f"{bit}pushed {'down' if sign > 0 else 'up'} by {e(reason(inp, cnt[0], -sign, intense=False))} "
                             f"({sgn(cnt[1])}{NB}€/MWh)")
            txt = (" The price is " + " and ".join(parts) + ".") if parts else ""
        th = trade_highlight(inp)
        return head + txt + (" " + e(th) if th else "")
    h = inp.hours
    return (f"<b>{num(p)}{NB}€/MWh</b> daily average; the most expensive hour is {A.hours_label(h.index(max(h)))} "
            f"({num(max(h))}{NB}€/MWh).")


# ─── šiandien, naujienos ─────────────────────────────────────────────────────

def today_items(inp) -> list[str]:
    """Kaip ataskaita.today_items, angliškai; pavadinimai – iš vertimų talpyklos (data/vertimai.json)."""
    d = inp.day
    iso, tom = d.isoformat(), date.fromordinal(d.toordinal() + 1).isoformat()
    base = getattr(inp, "data_dir", None) or os.path.join(A.ROOT, "data")
    g = A.load(os.path.join(base, "šaltiniai", "paramos-būsena.json")) or {}
    t = A.load(os.path.join(base, "šaltiniai", "tar.json")) or {}
    out = []
    for x in g.get("kvietimai") or []:
        name = A.vertimas(re.sub(r"\s+\d{4}-\d{2}$", "", x.get("pavadinimas") or "").strip())
        inst = A.vertimas(x.get("institucija") or "")
        end, start = (x.get("pabaiga") or "")[:10], (x.get("pradžia") or "")[:10]
        if x.get("būklė") == "paskelbtas" and end == iso:
            out.append(f"Applications close today: {name} ({inst}).")
        elif x.get("būklė") == "paskelbtas" and end == tom:
            out.append(f"Applications close tomorrow: {name} ({inst}).")
        if start == iso:
            out.append(f"Applications open today: {name} ({inst}).")
    for a in t.get("aktai") or []:
        if (a.get("isigalioja") or "")[:10] == iso:
            out.append(f"Taking effect today: {A.vertimas((a.get('pavadinimas') or '').rstrip('.'))}.")
    return out


def today_overview(inp) -> str:
    it = A.today_items(inp)
    n = len(it) - 1
    return it[0] + (f" {n} more {'deadline' if n == 1 else 'deadlines'} below." if n > 0 else "")


def news_overview(inp) -> str:
    items = A.news_sorted(inp)
    top, rest = items[0], items[1:]
    s = A.nf(top, "pavadinimas").rstrip(".") + "."
    if rest:
        regs = []
        for n in rest:
            r = REGION_ON.get(n.get("regionas"))
            if r and r not in regs:
                regs.append(r)
        s += f" {len(rest)} more {items_word(len(rest))}" + (f" on {join(regs)}." if regs else ".")
    return s
