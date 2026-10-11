"""Laikina kryžminė patikra: Elering prieš Nord Pool (LT), Yahoo Brent prieš EIA Brent spot."""
import importlib.util
import io
from datetime import date, datetime, timedelta

import requests
import xlrd

spec = importlib.util.spec_from_file_location("pr", "scripts/pasaulio rinkos.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

d = datetime.now(pr.LT_TZ).date()
start = datetime.combine(d, datetime.min.time(), pr.LT_TZ)
el = [(t, p) for t, p in pr.elering_lt(start, start + timedelta(days=1))
      if t.astimezone(pr.LT_TZ).date() == d]
npl = pr.nordpool_lt_day(d)
print("LT", d)
print("  Elering :", pr.lt_day_stats(el))
print("  NordPool:", pr.lt_day_stats(npl))
diff = [abs(a[1] - b[1]) for a, b in zip(el, npl) if a[0] == b[0]]
print(f"  sutampančių intervalų {len(diff)}, didžiausias skirtumas {max(diff) if diff else None}")

yb = dict(pr.yahoo_series("BZ=F", "3mo")["series"])
r = requests.get("https://www.eia.gov/dnav/pet/hist_xls/RBRTEd.xls", timeout=30,
                 headers={"User-Agent": pr.UA_BOT})
wb = xlrd.open_workbook(file_contents=r.content)
sh = wb.sheet_by_name("Data 1")
rows = []
for i in range(sh.nrows):
    v = sh.row_values(i)
    if isinstance(v[0], float) and isinstance(v[1], float):
        rows.append((xlrd.xldate_as_datetime(v[0], wb.datemode).date(), v[1]))
print("Brent: EIA spot prieš Yahoo BZ=F (paskutinės 8 EIA dienos)")
for dd, spot in rows[-8:]:
    fut = yb.get(dd)
    print(f"  {dd}  EIA {spot:7.2f}  Yahoo {fut if fut is None else round(fut, 2)}"
          + (f"  skirtumas {fut - spot:+.2f}" if fut else ""))

yt = dict(pr.yahoo_series("TTF=F", "3mo")["series"])
print("TTF 2026-09-11 (OilPriceAPI tinklaraštis: 80,77):", yt.get(date(2026, 9, 11)))
