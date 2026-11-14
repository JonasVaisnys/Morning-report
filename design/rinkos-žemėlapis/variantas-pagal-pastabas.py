"""Trečias variantas pagal kolegės pastabas: šviesus fonas, pirmojo varianto pavadinimas,
„Kas lemia kainą" viršuje, grynasis importas – apskritimas žemėlapyje, išvada apačioje."""
import json, re, sys
H = int(sys.argv[1]) if len(sys.argv) > 1 else 1120
base_svg = open("gen/zemelapis.svg.txt", encoding="utf-8").read()
base_svg = base_svg[: base_svg.index("</svg>") + 6]
assert base_svg.count("<svg") == 1 and '<g fill="#F2F2F2" stroke="#A6A6A6"' in base_svg and base_svg.count("#16264C") == 2

INK, MUTED = "#16264C", "#44546A"
UP, DOWN, GREY = "#C55A11", "#2B5797", "#808080"
SANS = "'Open Sans', 'Segoe UI', Arial, sans-serif"

G = json.load(open("../geo/routes.json")); ring = G["ltRing"]; P = G["pts"]
def hit(p, q):
    best = None
    for a, b in zip(ring, ring[1:] + ring[:1]):
        d = (q[0] - p[0]) * (b[1] - a[1]) - (q[1] - p[1]) * (b[0] - a[0])
        if abs(d) < 1e-9: continue
        t = ((a[0] - p[0]) * (b[1] - a[1]) - (a[1] - p[1]) * (b[0] - a[0])) / d
        u = ((a[0] - p[0]) * (q[1] - p[1]) - (a[1] - p[1]) * (q[0] - p[0])) / d
        if 0 <= t <= 1 and 0 <= u <= 1 and (best is None or t < best): best = t
    assert best is not None, (p, q)
    return [p[0] + best * (q[0] - p[0]), p[1] + best * (q[1] - p[1])]
def back(p, q, px):
    L = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5; return [p[0] + (q[0] - p[0]) * px / L, p[1] + (q[1] - p[1]) * px / L]
def smooth(pts):
    f = lambda v: f"{v:.1f}"
    d = f"M{f(pts[0][0])} {f(pts[0][1])}"
    for i in range(len(pts) - 1):
        p0 = pts[max(i - 1, 0)]; p1 = pts[i]; p2 = pts[i + 1]; p3 = pts[min(i + 2, len(pts) - 1)]
        c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6]; c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6]
        d += f"C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(p2[0])} {f(p2[1])}"
    return d
nb = G["routes"]["nordbalt"]; coast = hit(nb[0], nb[1]); nb_pts = [back(coast, nb[1], 2)] + nb[1:]
lp = G["routes"]["litpol"]
k = next(i for i in range(2, len(lp) - 1) if lp[i + 1][0] < 495)
cross = hit(lp[k], lp[k + 1]); lp_in = lp[: k + 1] + [cross]; lp_out = [back(cross, lp[k + 1], 1.5)] + lp[k + 1:]
hub = [548.0, 190.0]
fan = []
for lv, lt in (("grobina", "darbenai"), ("viskali", "musa"), ("aizkraukle", "panevezys"), ("liksna", "ignalina")):
    x = hit(P[lv], P[lt]); end = back(x, P[lv], 3.5)
    fan.append(f"M{hub[0]:.1f} {hub[1]:.1f}Q{P[lv][0]:.1f} {P[lv][1]:.1f} {end[0]:.1f} {end[1]:.1f}")
lines = f'''<svg width="760" height="570" viewBox="0 0 760 570" role="img" aria-label="Jungtys: NordBalt iš Klaipėdos į Nybro, LitPol Link iš Alytaus į Elką, keturios 330 kV linijos tarp Latvijos ir Lietuvos" style="position: absolute; left: 0px; top: 0px; width: 760px; height: 570px; overflow: visible; fill: none; stroke-linecap: round; stroke-linejoin: round">
<defs><marker id="pp-rodykle" orient="auto" markerUnits="userSpaceOnUse" markerWidth="13" markerHeight="13" refX="4" refY="6" overflow="visible"><path d="M0 0 L12 6 L0 12 Z" fill="{INK}" stroke="none"></path></marker>
<marker id="pp-rodykle-maza" orient="auto" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" refX="3" refY="4.5" overflow="visible"><path d="M0 0 L9 4.5 L0 9 Z" fill="{INK}" stroke="none"></path></marker></defs>
<path d="{smooth(nb_pts)}" stroke="{INK}" stroke-width="3" marker-end="url(#pp-rodykle)"></path>
<path d="{smooth(lp_in)}" stroke="#FFFFFF" stroke-width="3"></path>
<path d="{smooth(lp_out)}" stroke="{INK}" stroke-width="3" marker-end="url(#pp-rodykle)"></path>
<g stroke="{INK}" stroke-width="2">{"".join(f'<path d="{d}" marker-end="url(#pp-rodykle-maza)"></path>' for d in fan)}</g>
<circle cx="{hub[0]}" cy="{hub[1]}" r="4.5" fill="{INK}" stroke="#FFFFFF" stroke-width="2"></circle>
<circle cx="{coast[0]:.1f}" cy="{coast[1]:.1f}" r="4" fill="#FFFFFF" stroke="{INK}" stroke-width="2"></circle>
<circle cx="{lp[0][0]}" cy="{lp[0][1]}" r="4" fill="#FFFFFF" stroke="{INK}" stroke-width="2"></circle>
</svg>'''

# grynasis importas – žiedas su dalimi viduje
R_, SW = 26, 8; C = 2 * 3.14159265 * R_; share = 0.26
donut = f'''<svg width="60" height="60" viewBox="0 0 60 60" role="img" aria-label="Grynasis importas sudaro 26 procentus vartojimo" style="position: absolute; left: 260px; top: 296px; width: 60px; height: 60px">
<circle cx="30" cy="30" r="{R_}" fill="none" stroke="#FFFFFF" stroke-width="{SW}"></circle>
<circle cx="30" cy="30" r="{R_}" fill="none" stroke="{INK}" stroke-width="{SW}" stroke-dasharray="{share * C:.1f} {C:.1f}" transform="rotate(-90 30 30)"></circle>
<text x="30" y="35" text-anchor="middle" style="font-family: {SANS}; font-size: 14px; font-weight: 700; fill: {INK}">26 %</text>
</svg>'''

GRID = "display: grid; grid-template-columns: 100px 88px 104px 68px; column-gap: 16px; align-items: center"
def meter(level):
    return "".join(f'<div style="width: 16px; height: 8px; background: {INK if i == level else "#E7E6E6"}"></div>' for i in range(1, 6))
def row(name, level, today, norm):
    return (f'<div style="{GRID}; padding: 6px 0; border-top: 1px solid #E7E6E6">'
            f'<div style="font-size: 13px; line-height: 18px; font-weight: 600">{name}</div>'
            f'<div style="display: flex; gap: 2px">{meter(level)}</div>'
            f'<div style="font-size: 14px; line-height: 18px; font-weight: 700; text-align: right">{today}</div>'
            f'<div style="font-size: 13px; line-height: 18px; color: {MUTED}; text-align: right">{norm}</div></div>')
rows = "\n".join([
    row("Vartojimas", 5, "1 723 MW", "1 505"), row("Vėjas", 4, "931 MW", "658"), row("Saulė", 4, "70 MW", "36"),
    row("Kita gamyba", 4, "327 MW", "237"), row("Dujos (TTF)", 5, "86,7 €/MWh", "51,2"),
    f'<div style="{GRID}; padding: 6px 0; border-top: 1px solid #E7E6E6; border-bottom: 1px solid #E7E6E6"><div style="font-size: 13px; line-height: 18px; font-weight: 600">Jungtys</div><div></div><div style="grid-column: 3 / 5; font-size: 13px; line-height: 18px; font-weight: 600; text-align: right">neribojamos</div></div>',
])

BASE = 72
def bar(name, v, other=False):
    w = round(abs(v) * 1.8); col = GREY if other else (UP if v > 0 else DOWN)
    if v > 0:
        mark = f'<div style="position: absolute; left: {BASE + 1}px; top: 0px; width: {w}px; height: 14px; background: {col}; border-radius: 0 4px 4px 0"></div>'
    elif v < 0:
        mark = f'<div style="position: absolute; left: {BASE - w}px; top: 0px; width: {w}px; height: 14px; background: {col}; border-radius: {"4px 0 0 4px" if w >= 8 else "2px 0 0 2px"}"></div>'
    else:
        mark = ""
    val = "0" if v == 0 else (f"+{v}" if v > 0 else f"−{abs(v)}")
    return (f'<div style="display: grid; grid-template-columns: 124px 216px 52px; column-gap: 8px; align-items: center; height: 20px">'
            f'<div style="font-size: 13px; line-height: 20px">{name}</div>'
            f'<div style="position: relative; height: 14px"><div style="position: absolute; left: {BASE}px; top: -6px; width: 1px; height: 26px; background: #A6A6A6"></div>{mark}</div>'
            f'<div style="font-size: 14px; line-height: 20px; font-weight: 700; text-align: right">{val}</div></div>')
bars = "\n".join([bar(n, v) for n, v in [("Dujos ir CO2", 74), ("Kaimynų rinkos", 59), ("Vartojimas", 17), ("Pralaidumai", 0), ("Saulė", -2), ("Vėjas", -35)]] + [bar("Kita", -24, other=True)])

ys = [49.4, 54.8, 57.1, 61.5, 68.8, 67.5, 67.6, 54.1, 36.5, 35.6, 44.6, 52.6, 61.5, 66.0, 62.8, 62.2, 61.6, 59.5, 48.5, 51.6, 49.2, 66.2, 80.5, 85.5]
X0, X1 = 40, 1184
step = (X1 - X0) / 24
d = f"M{X0:.1f},{ys[0]}"
for i, y in enumerate(ys):
    if i: d += f"V{y}"
    d += f"H{X0 + (i + 1) * step:.1f}"
xmax, xmin = X0 + 9.5 * step, X0 + 23.5 * step
ticks = "".join(f'<text x="{X0 + h * step:.1f}" y="178" text-anchor="{a}">{t}</text>' for h, t, a in [(0, "0 val.", "start"), (6, "6", "middle"), (12, "12", "middle"), (18, "18", "middle"), (24, "24", "end")])
chart = f'''<svg width="1200" height="186" viewBox="0 0 1200 186" role="img" aria-label="Lietuvos valandinės elektros kainos lapkričio 12 d.: nuo 153 iki 256 eurų už megavatvalandę, paros vidurkis 208,5, įprastas paros vidurkis 94–148" style="width: 1200px; height: 186px; font-family: {SANS}; font-size: 11px">
<rect x="{X0}" y="88" width="{X1 - X0}" height="26.3" fill="#E7E6E6"></rect>
<g stroke="#E7E6E6" stroke-width="1"><path d="M{X0} 14H{X1}"></path><path d="M{X0} 62.7H{X1}"></path></g>
<path d="M{X0} 160H{X1}" stroke="#A6A6A6" stroke-width="1"></path>
<path d="M{X0} 58.5H{X1}" stroke="#808080" stroke-width="1"></path>
<path d="{d}" fill="none" stroke="{INK}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
<circle cx="{xmax:.1f}" cy="35.6" r="4" fill="{INK}" stroke="#FFFFFF" stroke-width="2"></circle>
<circle cx="{xmin:.1f}" cy="85.5" r="4" fill="{INK}" stroke="#FFFFFF" stroke-width="2"></circle>
<g fill="{INK}" font-weight="600"><text x="{xmax:.1f}" y="25" text-anchor="middle">255,6</text><text x="{xmin:.1f}" y="76" text-anchor="middle">153,2</text><text x="{X1}" y="52" text-anchor="end">vidurkis 208,5</text></g>
<g fill="{MUTED}"><text x="{X0 + 8}" y="105" text-anchor="start">įprasta 94–148</text><text x="32" y="18" text-anchor="end">300</text><text x="32" y="66.7" text-anchor="end">200</text><text x="32" y="115.3" text-anchor="end">100</text><text x="32" y="164" text-anchor="end">0</text>{ticks}</g>
</svg>'''

ICON = 'width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" style="fill: none; stroke: #FFFFFF; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round"'
H2 = "margin: 0; font-size: 15px; line-height: 22px; font-weight: 700"
NB = f"font-size: 12px; line-height: 16px; color: {MUTED}"
FLOW = "font-size: 15px; line-height: 20px; font-weight: 700"

html = f'''<!doctype html>
<html lang="lt">
<head>
<meta charset="utf-8">
<title>Elektros rinkos žemėlapis – pagal pastabas</title>
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Open+Sans:ital,wght@0,400;0,600;0,700;1,400&display=swap">
<style>
body{{margin:0}}
a{{color:#16264C}}a:hover{{color:#2B5797}}
</style>
</helmet>
<div style="width: 1280px; height: {H}px; box-sizing: border-box; padding: 40px; display: flex; flex-direction: column; gap: 20px; background: #FFFFFF; font-family: {SANS}; color: {INK}; font-variant-numeric: tabular-nums">

<div style="display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; padding-bottom: 14px; border-bottom: 1px solid #A6A6A6">
<div style="display: flex; flex-direction: column; gap: 2px">
<div style="font-size: 13px; line-height: 18px; color: {MUTED}">Morning Report · elektros rinka</div>
<h1 style="margin: 0; font-size: 28px; line-height: 36px; font-weight: 700">Elektros rinka šiandien</h1>
</div>
<div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px">
<div style="font-size: 15px; line-height: 22px; font-weight: 600">2026 m. lapkričio 12 d., ketvirtadienis</div>
<div style="font-size: 12px; line-height: 16px; color: {MUTED}">kitos paros rinkos kainos, prognozės ir prekybos srautai</div>
</div>
</div>

<div style="display: flex; gap: 32px; align-items: flex-start">

<div style="position: relative; width: 760px; height: 570px; flex: none; background: #E8EEF5; border-radius: 4px; overflow: hidden; outline: 1px solid #D5DBE7; outline-offset: -1px">
{base_svg}
{lines}
{donut}
<div style="position: absolute; left: 116px; top: 300px; width: 134px; text-align: right; {NB}"><span>grynasis importas,<br>% vartojimo<br><b style="font-size: 15px; line-height: 20px; color: {INK}">448 MW</b></span></div>

<div style="position: absolute; left: 436px; top: 266px; width: 224px; box-sizing: border-box; display: flex; flex-direction: column; align-items: center; text-align: center; color: #FFFFFF"><span style="font-size: 12px; line-height: 16px; color: #D5DBE7">Lietuva</span><span style="line-height: 40px"><b style="font-size: 32px; font-weight: 700">208,5</b> <span style="font-size: 13px">€/MWh</span></span><span style="display: flex; gap: 10px; align-items: center; font-size: 12px; line-height: 16px; white-space: nowrap"><span style="display: flex; gap: 4px; align-items: center"><svg {ICON}><path d="M3 8h10a3 3 0 1 0-3-3"></path><path d="M3 12h15a3 3 0 1 1-3 3"></path><path d="M3 16h7"></path></svg>vėjas 931 MW</span><span style="display: flex; gap: 4px; align-items: center"><svg {ICON}><circle cx="12" cy="12" r="4"></circle><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1l2.1-2.1M17 7l2.1-2.1"></path></svg>saulė 70 MW</span></span><span style="display: flex; gap: 4px; align-items: center; margin-top: 3px; font-size: 12px; line-height: 16px; white-space: nowrap"><svg {ICON}><path d="M3 21V11l6 4v-4l6 4V5h5v16z"></path></svg>kita gamyba 327 MW</span></div>

<div style="position: absolute; left: 16px; top: 110px; width: 124px; {NB}"><span>Švedija (SE4)<br><b style="font-size: 17px; line-height: 24px; color: {INK}">205,3</b> €/MWh</span></div>
<div style="position: absolute; left: 618px; top: 124px; width: 124px; {NB}"><span>Latvija<br><b style="font-size: 17px; line-height: 24px; color: {INK}">191,5</b> €/MWh</span></div>
<div style="position: absolute; left: 250px; top: 486px; width: 124px; {NB}"><span>Lenkija<br><b style="font-size: 17px; line-height: 24px; color: {INK}">336,3</b> €/MWh</span></div>

<div style="position: absolute; left: 196px; top: 252px; width: 120px; text-align: center; {FLOW}">203 MW</div>
<div style="position: absolute; left: 438px; top: 160px; width: 100px; text-align: right; {FLOW}">787 MW</div>
<div style="position: absolute; left: 352px; top: 437px; width: 84px; text-align: right; {FLOW}">137 MW</div>
</div>

<div style="display: flex; flex-direction: column; gap: 32px; flex: 1; min-width: 0">

<div style="display: flex; flex-direction: column; gap: 10px">
<div style="display: flex; flex-direction: column; gap: 2px">
<h2 style="{H2}">Kas lemia kainą</h2>
<div style="{NB}">Nuokrypis nuo kainos įprastomis sąlygomis, €/MWh</div>
</div>
<div style="display: flex; flex-direction: column; gap: 6px">
{bars}
</div>
<div style="{NB}">„Kita“ – kainos dalis, kurios šie veiksniai nepaaiškina.</div>
<div style="display: flex; justify-content: space-between; align-items: baseline; padding-top: 8px; border-top: 1px solid #A6A6A6; font-size: 13px; line-height: 20px"><div>įprastomis sąlygomis <b>119</b> → šiandien <b>208,5</b></div><div style="font-weight: 700">+89</div></div>
<div style="display: flex; gap: 16px; {NB}">
<div style="display: flex; gap: 6px; align-items: center"><div style="width: 10px; height: 10px; background: {UP}; border-radius: 2px"></div>kelia kainą</div>
<div style="display: flex; gap: 6px; align-items: center"><div style="width: 10px; height: 10px; background: {DOWN}; border-radius: 2px"></div>mažina kainą</div>
</div>
</div>

<div style="display: flex; flex-direction: column; gap: 8px">
<h2 style="{H2}">Sąlygos šiandien</h2>
<div style="display: flex; flex-direction: column">
<div style="{GRID}; padding-bottom: 4px; font-size: 11px; line-height: 16px; color: {MUTED}"><div></div><div style="display: flex; justify-content: space-between"><span>mažiau</span><span>daugiau</span></div><div style="text-align: right">šiandien</div><div style="text-align: right">įprasta</div></div>
{rows}
</div>
</div>

</div>
</div>

<div style="display: flex; flex-direction: column; gap: 6px">
<h2 style="{H2}">Kaina per parą, €/MWh</h2>
{chart}
</div>

<div style="display: flex; flex-direction: column; gap: 4px; padding-top: 14px; border-top: 1px solid #E7E6E6">
<h2 style="{H2}">Išvada</h2>
<div style="font-size: 14px; line-height: 22px">Kaina gerokai didesnė nei įprastai, tai lemia brangios dujos, didelis vartojimas, brangesnės kaimyninės rinkos. Stiprus vėjas kainą mažina.</div>
</div>

<div style="font-size: 11px; line-height: 16px; color: {MUTED}">Skaičiai žemėlapyje ir lentelėje – paros vidurkiai; vėjas, saulė ir vartojimas – kitos paros prognozės. Šaltiniai: ENTSO-E, Litgrid, EEX, TTF. Skaičiavimai – MB Enerconsult.</div>

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
open("gen/Pagal-pastabas.dc.html", "w", encoding="utf-8").write(html)
body = html.split("</helmet>", 1)[1].split("</x-dc>", 1)[0]
base = "gen/node_modules/@fontsource"
css = "".join(f'<link rel="stylesheet" href="{base}/{f}">' for f in ["open-sans/400.css", "open-sans/600.css", "open-sans/700.css"])
open("preview-c.html", "w", encoding="utf-8").write('<!doctype html><html lang="lt"><head><meta charset="utf-8">' + css + '<style>body{margin:0}</style></head><body>' + body + '</body></html>')
print("parašyta, aukštis", H, "| simbolių", len(html))
