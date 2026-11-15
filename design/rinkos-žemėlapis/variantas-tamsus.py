"""Antrasis variantas: brėžinio fonas, laikraščio antraštė, tikslios jungčių trasos."""
import json, re, sys
H = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
map_svg = open("gen/zemelapis.svg.txt", encoding="utf-8").read()
base_svg = map_svg[: map_svg.index("</svg>") + 6]            # tik šalių kontūrai, be senų tiesių rodyklių
assert base_svg.count("<svg") == 1 and "dc-arrow-head" not in base_svg

BG, INK, MUTED, SOFT = "#0B1B34", "#EAF0F8", "#A9B8CF", "#C5D1E3"
AMBER, UP, DOWN, GREY = "#F2A93B", "#C8841F", "#4C8FE6", "#7C8AA3"
LINE = "rgba(255, 255, 255, 0.14)"
SANS = "'Open Sans', 'Segoe UI', Arial, sans-serif"
HEAD = "Barlow, 'Open Sans', Arial, sans-serif"
MONO = "'IBM Plex Mono', Consolas, monospace"

ms = base_svg
ms = ms.replace('<g fill="#F2F2F2" stroke="#A6A6A6" stroke-width="0.75" stroke-linejoin="round">', '<g fill="#FFFFFF" fill-opacity="0.04" stroke="#EAF0F8" stroke-opacity="0.3" stroke-width="0.75" stroke-linejoin="round">')
ms = ms.replace('<g fill="#FFFFFF" stroke="#A6A6A6" stroke-width="0.75" stroke-linejoin="round">', '<g fill="#FFFFFF" fill-opacity="0.11" stroke="#EAF0F8" stroke-opacity="0.5" stroke-width="0.75" stroke-linejoin="round">')
assert 'fill-opacity="0.04"' in ms and 'fill-opacity="0.11"' in ms and ms.count("#16264C") == 2
ms = ms.replace("#16264C", INK)

# ---- jungčių trasos (taškai jau suprojektuoti į žemėlapio kadrą 760 × 570) ----
G = json.load(open("../geo/routes.json")); ring = G["ltRing"]; P = G["pts"]
def hit(p, q):
    """Pirmoji atkarpos p→q sankirta su Lietuvos kontūru."""
    best = None
    for a, b in zip(ring, ring[1:] + ring[:1]):
        d = (q[0] - p[0]) * (b[1] - a[1]) - (q[1] - p[1]) * (b[0] - a[0])
        if abs(d) < 1e-9: continue
        t = ((a[0] - p[0]) * (b[1] - a[1]) - (a[1] - p[1]) * (b[0] - a[0])) / d
        u = ((a[0] - p[0]) * (q[1] - p[1]) - (a[1] - p[1]) * (q[0] - p[0])) / d
        if 0 <= t <= 1 and 0 <= u <= 1 and (best is None or t < best): best = t
    assert best is not None, (p, q)
    return [p[0] + best * (q[0] - p[0]), p[1] + best * (q[1] - p[1])]
def back(p, q, px):            # taškas p, atitrauktas px link q
    L = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5; return [p[0] + (q[0] - p[0]) * px / L, p[1] + (q[1] - p[1]) * px / L]
def smooth(pts):               # Catmull–Rom → kubinės Bezjė kreivės
    f = lambda v: f"{v:.1f}"
    d = f"M{f(pts[0][0])} {f(pts[0][1])}"
    for i in range(len(pts) - 1):
        p0 = pts[max(i - 1, 0)]; p1 = pts[i]; p2 = pts[i + 1]; p3 = pts[min(i + 2, len(pts) - 1)]
        c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6]; c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6]
        d += f"C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(p2[0])} {f(p2[1])}"
    return d
nb = G["routes"]["nordbalt"]; coast = hit(nb[0], nb[1]); nb_pts = [back(coast, nb[1], 2)] + nb[1:]
lp = G["routes"]["litpol"]
k = next(i for i in range(len(lp) - 1) if True and i >= 2 and ((lp[i + 1][0] < 495)))      # atkarpa, kertanti sieną
cross = hit(lp[k], lp[k + 1]); lp_in = lp[: k + 1] + [cross]; lp_out = [back(cross, lp[k + 1], 1.5)] + lp[k + 1:]
hub = [548.0, 190.0]
fan = []
for lv, lt in (("grobina", "darbenai"), ("viskali", "musa"), ("aizkraukle", "panevezys"), ("liksna", "ignalina")):
    x = hit(P[lv], P[lt]); end = back(x, P[lv], 3.5)
    fan.append(f"M{hub[0]:.1f} {hub[1]:.1f}Q{P[lv][0]:.1f} {P[lv][1]:.1f} {end[0]:.1f} {end[1]:.1f}")
print("Klaipėdos krantas:", [round(v, 1) for v in coast], "| LitPol siena:", [round(v, 1) for v in cross], "| vėduoklė:", fan)
lines = f'''<svg width="760" height="570" viewBox="0 0 760 570" role="img" aria-label="Jungtys: NordBalt iš Klaipėdos į Nybro, LitPol Link iš Alytaus į Elką, keturios 330 kV linijos tarp Latvijos ir Lietuvos" style="position: absolute; left: 0px; top: 0px; width: 760px; height: 570px; overflow: visible; fill: none; stroke-linecap: round; stroke-linejoin: round">
<defs><marker id="mr-rodykle" orient="auto" markerUnits="userSpaceOnUse" markerWidth="13" markerHeight="13" refX="4" refY="6" overflow="visible"><path d="M0 0 L12 6 L0 12 Z" fill="{INK}" stroke="none"></path></marker>
<marker id="mr-rodykle-maza" orient="auto" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" refX="3" refY="4.5" overflow="visible"><path d="M0 0 L9 4.5 L0 9 Z" fill="{INK}" stroke="none"></path></marker></defs>
<path d="{smooth(nb_pts)}" stroke="{INK}" stroke-width="3" marker-end="url(#mr-rodykle)"></path>
<path d="{smooth(lp_in)}" stroke="{BG}" stroke-width="3"></path>
<path d="{smooth(lp_out)}" stroke="{INK}" stroke-width="3" marker-end="url(#mr-rodykle)"></path>
<g stroke="{INK}" stroke-width="2">{"".join(f'<path d="{d}" marker-end="url(#mr-rodykle-maza)"></path>' for d in fan)}</g>
<circle cx="{hub[0]}" cy="{hub[1]}" r="4.5" fill="{INK}" stroke="{BG}" stroke-width="2"></circle>
<circle cx="{coast[0]:.1f}" cy="{coast[1]:.1f}" r="4" fill="{BG}" stroke="{INK}" stroke-width="2"></circle>
<circle cx="{lp[0][0]}" cy="{lp[0][1]}" r="4" fill="{INK}" stroke="{BG}" stroke-width="2"></circle>
</svg>'''

GRID = "display: grid; grid-template-columns: 96px 98px 96px 62px; column-gap: 16px; align-items: center"
def meter(level):
    return "".join(f'<div style="width: 18px; height: 8px; background: {INK if i == level else "rgba(255, 255, 255, 0.16)"}"></div>' for i in range(1, 6))
def row(name, level, today, norm):
    return (f'<div style="{GRID}; padding: 9px 0; border-top: 1px solid {LINE}">'
            f'<div style="font-size: 14px; line-height: 18px; font-weight: 600">{name}</div>'
            f'<div style="display: flex; gap: 2px">{meter(level)}</div>'
            f'<div style="font-family: {HEAD}; font-size: 16px; line-height: 18px; font-weight: 600; text-align: right">{today}</div>'
            f'<div style="font-family: {MONO}; font-size: 12px; line-height: 18px; color: {MUTED}; text-align: right">{norm}</div></div>')
rows = "\n".join([
    row("Vartojimas", 5, "1 723 MW", "1 505"), row("Vėjas", 4, "931 MW", "658"), row("Saulė", 4, "70 MW", "36"),
    row("Kita gamyba", 4, "327 MW", "237"), row("Dujos (TTF)", 5, "86,7 €/MWh", "51,2"),
    f'<div style="{GRID}; padding: 9px 0; border-top: 1px solid {LINE}; border-bottom: 1px solid {LINE}"><div style="font-size: 14px; line-height: 18px; font-weight: 600">Jungtys</div><div></div><div style="grid-column: 3 / 5; font-size: 14px; line-height: 18px; font-weight: 600; text-align: right">neribojamos</div></div>',
])

BASE = 64
def bar(name, v, unexplained=False):
    w = round(abs(v) * 1.7)
    col = GREY if unexplained else (UP if v > 0 else DOWN)
    if v > 0:
        mark = f'<div style="position: absolute; left: {BASE + 1}px; top: 0px; width: {w}px; height: 14px; background: {col}; border-radius: 0 4px 4px 0"></div>'
    elif v < 0:
        mark = f'<div style="position: absolute; left: {BASE - w}px; top: 0px; width: {w}px; height: 14px; background: {col}; border-radius: {"4px 0 0 4px" if w >= 8 else "2px 0 0 2px"}"></div>'
    else:
        mark = ""
    val = "0" if v == 0 else (f"+{v}" if v > 0 else f"−{abs(v)}")
    return (f'<div style="display: grid; grid-template-columns: 136px 200px 48px; column-gap: 8px; align-items: center; height: 22px">'
            f'<div style="font-size: 14px; line-height: 22px; color: {MUTED if unexplained else INK}">{name}</div>'
            f'<div style="position: relative; height: 14px"><div style="position: absolute; left: {BASE}px; top: -8px; width: 1px; height: 30px; background: rgba(255, 255, 255, 0.45)"></div>{mark}</div>'
            f'<div style="font-family: {HEAD}; font-size: 16px; line-height: 22px; font-weight: 600; text-align: right; color: {MUTED if unexplained else INK}">{val}</div></div>')
bars = "\n".join(bar(n, v) for n, v in [("Dujos ir CO2", 74), ("Kaimynų rinkos", 59), ("Vartojimas", 17), ("Pralaidumai", 0), ("Saulė", -2), ("Vėjas", -35)])
bar_un = bar("Nepaaiškinta dalis", -24, unexplained=True)

ys = [49.4, 54.8, 57.1, 61.5, 68.8, 67.5, 67.6, 54.1, 36.5, 35.6, 44.6, 52.6, 61.5, 66.0, 62.8, 62.2, 61.6, 59.5, 48.5, 51.6, 49.2, 66.2, 80.5, 85.5]
X0, X1 = 40, 680
step = (X1 - X0) / 24
d = f"M{X0:.1f},{ys[0]}"
for i, y in enumerate(ys):
    if i: d += f"V{y}"
    d += f"H{X0 + (i + 1) * step:.1f}"
xmax, xmin = X0 + 9.5 * step, X0 + 23.5 * step
ticks = "".join(f'<text x="{X0 + h * step:.1f}" y="178" text-anchor="{a}">{t}</text>' for h, t, a in [(0, "0 val.", "start"), (6, "6", "middle"), (12, "12", "middle"), (18, "18", "middle"), (24, "24", "end")])
chart = f'''<svg width="760" height="184" viewBox="0 0 760 184" role="img" aria-label="Lietuvos valandinės elektros kainos lapkričio 12 d.: nuo 153 iki 256 eurų už megavatvalandę, paros vidurkis 208,5, įprastas paros vidurkis 94–148" style="width: 760px; height: 184px; font-family: {MONO}; font-size: 11px">
<rect x="{X0}" y="6" width="{X1 - X0}" height="154" fill="{BG}" fill-opacity="0.9"></rect>
<rect x="{X0}" y="88" width="{X1 - X0}" height="26.3" fill="#FFFFFF" fill-opacity="0.12"></rect>
<g stroke="#FFFFFF" stroke-opacity="0.12" stroke-width="1"><path d="M{X0} 14H{X1}"></path><path d="M{X0} 62.7H{X1}"></path></g>
<path d="M{X0} 160H{X1}" stroke="{MUTED}" stroke-opacity="0.7" stroke-width="1"></path>
<path d="M{X0} 58.5H{X1}" stroke="{MUTED}" stroke-width="1" stroke-dasharray="4 4"></path>
<path d="{d}" fill="none" stroke="{INK}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
<circle cx="{xmax:.1f}" cy="35.6" r="4" fill="{INK}" stroke="{BG}" stroke-width="2"></circle>
<circle cx="{xmin:.1f}" cy="85.5" r="4" fill="{INK}" stroke="{BG}" stroke-width="2"></circle>
<g fill="{INK}" font-weight="500"><text x="{xmax:.1f}" y="25" text-anchor="middle">255,6</text><text x="{xmin:.1f}" y="76" text-anchor="middle">153,2</text><text x="{X1 + 8}" y="62">vid. 208,5</text></g>
<g fill="{MUTED}"><text x="{X1 + 8}" y="99">įprasta</text><text x="{X1 + 8}" y="113">94–148</text><text x="32" y="18" text-anchor="end">300</text><text x="32" y="66.7" text-anchor="end">200</text><text x="32" y="115.3" text-anchor="end">100</text><text x="32" y="164" text-anchor="end">0</text>{ticks}</g>
</svg>'''

ICON = f'width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" style="fill: none; stroke: {BG}; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round"'
H2 = f"margin: 0; font-family: {HEAD}; font-size: 18px; line-height: 24px; font-weight: 600"
LBL = f"font-family: {MONO}; font-size: 11px; line-height: 16px; color: {MUTED}"
FLOW = f"font-family: {HEAD}; font-size: 17px; line-height: 22px; font-weight: 600"
PAPER = ("background-color: #0B1B34; background-image: linear-gradient(rgba(255, 255, 255, 0.09) 1px, transparent 1px), linear-gradient(90deg, rgba(255, 255, 255, 0.09) 1px, transparent 1px), "
         "linear-gradient(rgba(255, 255, 255, 0.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255, 255, 255, 0.035) 1px, transparent 1px); "
         "background-size: 200px 200px, 200px 200px, 40px 40px, 40px 40px; background-position: -1px -1px")

html = f'''<!doctype html>
<html lang="lt">
<head>
<meta charset="utf-8">
<title>Elektros rinkos žemėlapis – mažiau teksto</title>
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@500;600&family=IBM+Plex+Mono:wght@400;500&family=Open+Sans:wght@400;600;700&display=swap">
<style>
body{{margin:0}}
a{{color:#EAF0F8}}a:hover{{color:#F2A93B}}
</style>
</helmet>
<div style="width: 1280px; height: {H}px; box-sizing: border-box; padding: 40px; display: flex; flex-direction: column; gap: 28px; {PAPER}; font-family: {SANS}; color: {INK}; font-variant-numeric: tabular-nums">

<div style="display: flex; flex-direction: column">
<div style="border-top: 1px solid rgba(255, 255, 255, 0.3); padding: 10px 0 12px; text-align: center"><div style="font-family: {HEAD}; font-size: 60px; line-height: 64px; font-weight: 600; letter-spacing: 0.01em">Morning Report</div></div>
<div style="border-top: 3px solid {INK}; border-bottom: 1px solid {INK}; height: 3px"></div>
<div style="display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; column-gap: 24px; padding: 8px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.3); font-family: {MONO}; font-size: 12px; line-height: 18px; color: {SOFT}"><div style="display: flex; gap: 10px; align-items: center"><div style="width: 28px; height: 2px; background: {AMBER}"></div>elektros rinkos apžvalga</div><div>2026 m. lapkričio 12 d., ketvirtadienis</div><div style="text-align: right">enerconsult.lt</div></div>
<h1 style="margin: 0; padding-top: 22px; font-family: {HEAD}; font-size: 36px; line-height: 44px; font-weight: 600">Elektra šiandien 208,5 €/MWh – gerokai brangiau nei įprasta</h1>
</div>

<div style="display: flex; gap: 40px; align-items: stretch">

<div style="display: flex; flex-direction: column; gap: 14px; width: 760px; flex: none">
<div style="position: relative; width: 760px; height: 570px; box-sizing: border-box; overflow: hidden; outline: 1px solid rgba(255, 255, 255, 0.18); outline-offset: -1px">
{ms}
{lines}

<div style="position: absolute; left: 436px; top: 264px; width: 224px; box-sizing: border-box; display: flex; flex-direction: column; align-items: center; text-align: center; color: {BG}"><span style="font-family: {MONO}; font-size: 11px; line-height: 16px; color: #3B4A68">Lietuva</span><span style="line-height: 42px"><b style="font-family: {HEAD}; font-size: 36px; font-weight: 600">208,5</b> <span style="font-family: {MONO}; font-size: 12px">€/MWh</span></span><span style="display: flex; gap: 10px; align-items: center; font-size: 12px; line-height: 16px; font-weight: 600; white-space: nowrap"><span style="display: flex; gap: 4px; align-items: center"><svg {ICON}><path d="M3 8h10a3 3 0 1 0-3-3"></path><path d="M3 12h15a3 3 0 1 1-3 3"></path><path d="M3 16h7"></path></svg>vėjas 931 MW</span><span style="display: flex; gap: 4px; align-items: center"><svg {ICON}><circle cx="12" cy="12" r="4"></circle><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1l2.1-2.1M17 7l2.1-2.1"></path></svg>saulė 70 MW</span></span><span style="display: flex; gap: 4px; align-items: center; margin-top: 3px; font-size: 12px; line-height: 16px; font-weight: 600; white-space: nowrap"><svg {ICON}><path d="M3 21V11l6 4v-4l6 4V5h5v16z"></path></svg>kita gamyba 327 MW</span></div>

<div style="position: absolute; left: 16px; top: 108px; width: 140px; {LBL}"><span>Švedija (SE4)<br><b style="font-family: {HEAD}; font-size: 20px; line-height: 26px; font-weight: 600; color: {INK}">205,3</b> €/MWh</span></div>
<div style="position: absolute; left: 612px; top: 122px; width: 140px; {LBL}"><span>Latvija<br><b style="font-family: {HEAD}; font-size: 20px; line-height: 26px; font-weight: 600; color: {INK}">191,5</b> €/MWh</span></div>
<div style="position: absolute; left: 250px; top: 484px; width: 140px; {LBL}"><span>Lenkija<br><b style="font-family: {HEAD}; font-size: 20px; line-height: 26px; font-weight: 600; color: {INK}">336,3</b> €/MWh</span></div>

<div style="position: absolute; left: 196px; top: 250px; width: 120px; text-align: center; {FLOW}">203 MW</div>
<div style="position: absolute; left: 438px; top: 158px; width: 100px; text-align: right; {FLOW}">787 MW</div>
<div style="position: absolute; left: 352px; top: 436px; width: 84px; text-align: right; {FLOW}">137 MW</div>
</div>
<div style="display: flex; align-items: center; gap: 12px; {LBL}; line-height: 20px"><span>grynasis importas</span><b style="font-family: {HEAD}; font-size: 17px; line-height: 20px; font-weight: 600; color: {INK}">448 MW</b><div style="position: relative; width: 240px; height: 8px; background: rgba(255, 255, 255, 0.16); border-radius: 4px; overflow: hidden"><div style="position: absolute; left: 0px; top: 0px; width: 62px; height: 8px; background: {INK}"></div></div><span>26 % vartojimo</span></div>

<div style="display: flex; flex-direction: column; gap: 8px; margin-top: 20px">
<div style="display: flex; align-items: baseline; gap: 10px"><h2 style="{H2}">Kaina per parą</h2><span style="{LBL}">€/MWh</span></div>
{chart}
</div>
</div>

<div style="width: 1px; flex: none; margin: 0 -20px; background: {LINE}"></div>

<div style="display: flex; flex-direction: column; justify-content: space-between; flex: 1; min-width: 0">

<div style="display: flex; flex-direction: column; gap: 10px">
<h2 style="{H2}">Išvada</h2>
<div style="font-size: 16px; line-height: 26px; text-wrap: pretty">Dujos brangios, vartojimas didelis, kaimynų rinkos brangesnės. Stiprus vėjas kainą mažina.</div>
</div>

<div style="display: flex; flex-direction: column; gap: 12px">
<h2 style="{H2}">Sąlygos šiandien</h2>
<div style="display: flex; flex-direction: column">
<div style="{GRID}; padding-bottom: 6px; {LBL}"><div></div><div style="display: flex; justify-content: space-between"><span>mažiau</span><span>daugiau</span></div><div style="text-align: right">šiandien</div><div style="text-align: right">įprasta</div></div>
{rows}
</div>
</div>

<div style="display: flex; flex-direction: column; gap: 14px">
<div style="display: flex; flex-direction: column; gap: 4px">
<h2 style="{H2}">Kas lemia kainą</h2>
<div style="{LBL}">nuokrypis nuo kainos įprastomis sąlygomis, €/MWh</div>
</div>
<div style="display: flex; flex-direction: column; gap: 8px">
{bars}
<div style="margin-top: 6px; padding-top: 10px; border-top: 1px dashed rgba(255, 255, 255, 0.2)">
{bar_un}
</div>
</div>
<div style="display: flex; justify-content: space-between; align-items: baseline; padding-top: 10px; border-top: 1px solid rgba(255, 255, 255, 0.3); font-size: 14px; line-height: 24px"><div>įprastomis sąlygomis <b>119</b> → šiandien <b>208,5</b></div><div style="font-family: {HEAD}; font-size: 18px; font-weight: 600">+89</div></div>
<div style="display: flex; gap: 14px; {LBL}">
<div style="display: flex; gap: 6px; align-items: center"><div style="width: 10px; height: 10px; background: {UP}; border-radius: 2px"></div>kelia kainą</div>
<div style="display: flex; gap: 6px; align-items: center"><div style="width: 10px; height: 10px; background: {DOWN}; border-radius: 2px"></div>mažina kainą</div>
<div style="display: flex; gap: 6px; align-items: center"><div style="width: 10px; height: 10px; background: {GREY}; border-radius: 2px"></div>modelis nepaaiškina</div>
</div>
</div>

</div>
</div>

<div style="{LBL}">Skaičiai žemėlapyje ir lentelėje – paros vidurkiai; vėjas, saulė ir vartojimas – kitos paros prognozės. Šaltiniai: ENTSO-E, Litgrid, EEX, TTF. Skaičiavimai – MB Enerconsult.</div>

</div>
</x-dc>
<script type="text/x-dc" data-dc-script data-props='{{"$preview":{{"width":1280,"height":{H}}}}}'>
class Component extends DCLogic {{
renderVals() {{
return {{}};
}}
}}
</script>
</body>
</html>
'''
assert "/>" not in html
open("gen/Maziau-teksto.dc.html", "w", encoding="utf-8").write(html)
body = html.split("</helmet>", 1)[1].split("</x-dc>", 1)[0]
base = "gen/node_modules/@fontsource"
css = "".join(f'<link rel="stylesheet" href="{base}/{f}">' for f in ["barlow/500.css", "barlow/600.css", "ibm-plex-mono/400.css", "ibm-plex-mono/500.css", "open-sans/400.css", "open-sans/600.css", "open-sans/700.css"])
open("preview-b.html", "w", encoding="utf-8").write('<!doctype html><html lang="lt"><head><meta charset="utf-8">' + css + '<style>body{margin:0}</style></head><body>' + body + '</body></html>')
print("parašyta, aukštis", H, "| simbolių", len(html))
