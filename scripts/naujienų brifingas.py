#!/usr/bin/env python3
"""
Rytinis naujienų brifingas — MB Enerconsult
Nuskaito RSS srautus ir klasifikuoja su Claude API.
Rezultatą išsaugo į data/naujienų-brifingas.md
"""

import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request

import anthropic

# ── Laiko zona ───────────────────────────────────────────────────────────────
LT_TZ = timezone(timedelta(hours=3))   # EEST vasarą; žiemą 2

# ── RSS šaltiniai ────────────────────────────────────────────────────────────
RSS_FEEDS = [
    ("Energetika.lt",    "https://www.energetika.lt/rss/"),
    ("Litgrid",          "https://www.litgrid.eu/rss/"),
    ("VERT",             "https://www.regula.lt/lt/rss/naujienos"),
    ("ESO",              "https://www.eso.lt/lt/rss/naujienos"),
    ("LRT verslas",      "https://www.lrt.lt/rss/verslas"),
    ("Delfi verslas",    "https://www.delfi.lt/rss/feeds/verslas.xml"),
    ("15min verslas",    "https://www.15min.lt/rss/verslas"),
    ("Verslo žinios",    "https://www.vz.lt/rss/"),
    ("Vakarų ekspresas", "https://www.vakaru.lt/rss/"),
]

KEYWORDS = [
    "energetika", "elektra", "elektros", "saulės energija", "saulės park",
    "vėjo elektrin", "vėjo park", "baterijų kaupikl", "bess", "litgrid",
    "eso", "vert", "atsinaujinanti energija", "elektros kaina",
    "nordpool", "nord pool", "energijos kainos", "gamyba", "tinklai",
    "akumuliatoriai", "foto", "pv park", "jūrinė energetika",
]

# ── RSS nuskaitymas ───────────────────────────────────────────────────────────

def fetch_rss(name: str, url: str) -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; EnerconsultBot/1.0)",
        "Accept":     "application/rss+xml, application/xml, text/xml",
    }
    try:
        req  = Request(url, headers=headers)
        resp = urlopen(req, timeout=12)
        root = ET.fromstring(resp.read())
    except Exception as exc:
        print(f"  ⚠  {name}: {exc}", file=sys.stderr)
        return []

    ns    = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)

    out = []
    for item in items:
        def t(tag):
            el = item.find(tag) or item.find(f"atom:{tag}", ns)
            return (el.text or "").strip() if el is not None else ""

        title = t("title")
        link  = t("link") or t("guid")
        desc  = t("description") or t("summary") or t("content")
        pub   = t("pubDate") or t("published") or t("updated")

        if title:
            out.append({"source": name, "title": title,
                        "link": link, "desc": desc[:400], "date": pub[:30]})

    print(f"  ✓ {name}: {len(out)}", file=sys.stderr)
    return out


def filter_energy(items: list[dict]) -> list[dict]:
    kw = [k.lower() for k in KEYWORDS]
    return [it for it in items
            if any(k in (it["title"] + " " + it["desc"]).lower() for k in kw)]


def collect() -> list[dict]:
    all_items = []
    for name, url in RSS_FEEDS:
        all_items.extend(fetch_rss(name, url))
    energy = filter_energy(all_items)
    print(f"\n  Iš viso: {len(all_items)}, energetikos: {len(energy)}", file=sys.stderr)
    return energy


# ── Claude API ────────────────────────────────────────────────────────────────

def generate(news: list[dict], date_lt: str) -> str:
    if not news:
        return f"# Naujienų brifingas — {date_lt}\n\nŠiandien energetikos naujienų nerasta."

    block = "\n".join(
        f"[{i+1}] [{it['source']}] {it['title']}\n    {it['desc'][:250]}\n    {it['link']}"
        for i, it in enumerate(news[:60])
    )

    prompt = f"""Tu esi Lietuvos atsinaujinančios energetikos rinkos analitikas.
Šiandien yra {date_lt}. Pateikiu naujienų sąrašą iš RSS srautų.

Atrink TIKTAI aktualias energetikos naujienas (saulė, vėjas, BESS, elektros tinklas,
reguliavimas, NordPool kainos, projektai LT/Baltijos regione). Ignoruok nesusijusias naujienas.

Suformatuok kaip rytinį briefingą MARKDOWN formatu:

# Naujienų brifingas — {date_lt}

## 🔴 Svarbiausia
(1–3 svarbiausios naujienos, 1–2 sakiniai kiekvienai, šaltinis ir nuoroda)

## ⚡ Elektros rinka
(kainos, NordPool, balansavimas — jei yra)

## 🌬️ Vėjas ir saulė
(projektai, leidimai, statyba)

## 🔋 Kaupikliai ir inovacijos
(BESS, naujovės — jei yra)

## 🏛️ Reguliavimas
(VERT, ESO, Litgrid, teisės aktai)

## 📰 Kita
(kita aktualu)

---
_Generuota automatiškai {date_lt} · MB Enerconsult_

Rašyk glaustai. Jei kategorijoje nėra naujienų — praleisk ją.

NAUJIENOS:
{block}"""

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model      = "claude-haiku-4-5",
        max_tokens = 2000,
        messages   = [{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


# ── Pagrindinis srautas ───────────────────────────────────────────────────────

def main():
    dt_lt    = datetime.now(LT_TZ)
    date_str = dt_lt.strftime("%Y-%m-%d")

    print(f"📡 Naujienų brifingas {date_str}", file=sys.stderr)

    news    = collect()
    result  = generate(news, date_str)

    # Išsaugome į data/
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "naujienų-brifingas.md")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result)

    print(result)
    print(f"\n✅ Išsaugota → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
