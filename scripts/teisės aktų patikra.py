#!/usr/bin/env python3
"""
Teisės aktų API diagnostika
===========================
Tikrina, kaip data.gov.lt (Spinta) atsako į teisės aktų užklausas: kokie yra
duomenų rinkiniai, laukų pavadinimai, kurie filtrai veikia ir kaip greitai.
Rezultatas – data/teisės-aktai-patikra/zondai.txt ir pavyzdiniai JSON failai.

Laikinas įrankis šakai „teises-aktai"; į pagrindinę šaką nekeliamas.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://get.data.gov.lt"
DOK = "/datasets/gov/lrsk/teises_aktai/Dokumentas"
OUT = Path("data/teisės-aktai-patikra")
OUT.mkdir(parents=True, exist_ok=True)
LOG = open(OUT / "zondai.txt", "w", encoding="utf-8")


def ascii_url(path: str) -> str:
    """Koduoja tik ne ASCII simbolius ir tarpus – operatoriai (>=, ", |) lieka tokie, kokie parašyti."""
    return "".join(c if 33 <= ord(c) < 127 else urllib.parse.quote(c) for c in path)


def trim(obj, limit=300):
    if isinstance(obj, str):
        return obj if len(obj) <= limit else obj[:limit] + f"…({len(obj)} simb.)"
    if isinstance(obj, list):
        return [trim(x, limit) for x in obj]
    if isinstance(obj, dict):
        return {k: trim(v, limit) for k, v in obj.items()}
    return obj


def get(path: str, save: str | None = None, maxchars: int = 2500, timeout: int = 90):
    url = BASE + ascii_url(path)
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "morning-report/1.0 (+https://enerconsult.lt)",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # tinklo klaidos, laiko riba
        status, body = None, f"{type(e).__name__}: {e}"
    dt = time.time() - t0
    try:
        data = json.loads(body)
        txt = json.dumps(trim(data), ensure_ascii=False, indent=1)
    except Exception:
        data, txt = None, body
    LOG.write(f"\n=== HTTP {status} · {dt:.1f} s · {path}\n")
    LOG.write(txt[:maxchars] + ("\n…" if len(txt) > maxchars else "") + "\n")
    LOG.flush()
    if save and data is not None:
        (OUT / save).write_text(json.dumps(trim(data, 6000), ensure_ascii=False, indent=1), encoding="utf-8")
    time.sleep(1.5)  # neapkrauti API
    return status, data


def list_ns(path: str, depth: int = 0, max_depth: int = 2):
    status, data = get(f"{path}/:ns", maxchars=4000)
    if not isinstance(data, dict):
        return
    for item in data.get("_data", []):
        name = item.get("name", "")
        LOG.write(f"{'  ' * depth}- {name} | {item.get('title', '')}\n")
        if name.endswith("/:ns") and depth < max_depth:
            list_ns("/" + name[:-4].lstrip("/"), depth + 1, max_depth)


# 1. Kokie rinkiniai yra Seimo kanceliarijos vardų erdvėje (ieškom ir projektų)
list_ns("/datasets/gov/lrsk")

# 2. Įrašo pavyzdys – laukų pavadinimai
get(f"{DOK}?limit(2)", save="dokumentas-pavyzdys.json", maxchars=6000)

# 3. Pakeitimų srautas – paskutiniai įrašai
get(f"{DOK}/:changes/-3", save="pakeitimai-paskutiniai.json", maxchars=6000)
get(f"{DOK}/:changes/-3?limit(3)")

# 4. Filtrų variantai
get(f'{DOK}?paskelbta_tar="2026-11-13"&limit(3)')
get(f'{DOK}?paskelbta_tar>="2026-11-12"&limit(3)')
get(f'{DOK}?paskelbta_tar%3E=%222026-11-12%22&limit(3)')
get(f'{DOK}?sort(-paskelbta_tar)&limit(3)', timeout=120)
get(f'{DOK}?select(_id,pavadinimas,rusis,paskelbta_tar,priimtas)&paskelbta_tar>="2026-11-12"&limit(3)')
get(f'{DOK}?priimtas>="2026-11-10"&limit(3)')
get(f'{DOK}?paskelbta_tar>="2026-11-12"&count()')
get(f'{DOK}?rusis="Įstatymas"&limit(2)')

# 5. Suvestinės redakcijos
get("/datasets/gov/lrsk/teises_aktai/Suvestine?limit(1)", save="suvestine-pavyzdys.json")

LOG.write("\nbaigta " + time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()) + "\n")
LOG.close()
