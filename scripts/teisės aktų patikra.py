#!/usr/bin/env python3
"""
Teisės aktų API diagnostika (2 etapas)
======================================
Pirmas etapas parodė, kad get.data.gov.lt į bet kokią užklausą iš GitHub
serverio grąžina HTTP 500 su HTML klaidos puslapiu. Čia tikrinama, kas
blokuojama: naršyklės antraštė, kitas adresas ar visas serveris, ir ar
pasiekiami kiti teisės aktų šaltiniai (e-tar.lt, e-seimas.lrs.lt).

Laikinas įrankis šakai „teises-aktai"; į pagrindinę šaką nekeliamas.
"""

import re
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path("data/teisės-aktai-patikra")
OUT.mkdir(parents=True, exist_ok=True)
LOG = open(OUT / "zondai-2.txt", "w", encoding="utf-8")

UA = {
    "naršyklė": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "curl": "curl/8.5.0",
    "python": "python-requests/2.32.3",
    "be antraštės": None,
}


def visible_text(html: str) -> str:
    html = re.sub(r"data:[^)\"'\s]+", "[duomenys]", html)
    html = re.sub(r"<(style|script)\b.*?</\1>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", txt).strip()


def get(url: str, ua: str | None, accept: str = "application/json", save: str | None = None):
    headers = {"Accept": accept, "Accept-Language": "lt,en;q=0.8"}
    if ua:
        headers["User-Agent"] = ua
    req = urllib.request.Request(url, headers=headers)
    if ua is None:
        req.remove_header("User-agent")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, body, hdrs = r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        status, body, hdrs = e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
    except Exception as e:
        status, body, hdrs = None, f"{type(e).__name__}: {e}", {}
    dt = time.time() - t0
    keep = {k: v for k, v in hdrs.items() if k.lower() in ("server", "content-type", "x-cache", "via", "set-cookie", "x-request-id", "cf-ray", "x-iinfo")}
    LOG.write(f"\n=== HTTP {status} · {dt:.1f} s · {url}\n    UA: {ua!r}\n    antraštės: {keep}\n")
    shown = body if body.lstrip().startswith(("{", "[")) else visible_text(body)
    LOG.write("    " + shown[:1200] + "\n")
    LOG.flush()
    if save:
        (OUT / save).write_text(body if body.lstrip().startswith(("{", "[")) else visible_text(body), encoding="utf-8")
    time.sleep(1.5)
    return status, body


DOK = "https://get.data.gov.lt/datasets/gov/lrsk/teises_aktai/Dokumentas?limit(1)"

# 1. Ta pati paprasta užklausa su skirtingomis antraštėmis
for name, ua in UA.items():
    get(DOK, ua, save="klaidos-puslapis.txt" if name == "python" else None)

# 2. Kiti to paties serverio ir portalo adresai
get("https://get.data.gov.lt/", UA["naršyklė"], accept="text/html")
get("https://get.data.gov.lt/datasets/gov/lrsk/:ns", UA["naršyklė"])
get("https://get-test.data.gov.lt/datasets/gov/lrsk/:ns", UA["naršyklė"])
get("https://data.gov.lt/", UA["naršyklė"], accept="text/html")

# 3. Kiti teisės aktų šaltiniai (tik ar pasiekiami)
get("https://www.e-tar.lt/portal/lt/index", UA["naršyklė"], accept="text/html")
get("https://e-seimas.lrs.lt/portal/legalActSearch/lt", UA["naršyklė"], accept="text/html")
get("https://www.lrs.lt/", UA["naršyklė"], accept="text/html")

# 4. Iš kokio adreso jungiamasi (GitHub serverio IP)
get("https://api.ipify.org?format=json", UA["curl"])

LOG.write("\nbaigta " + time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()) + "\n")
LOG.close()
