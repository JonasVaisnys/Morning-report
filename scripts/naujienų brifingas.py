#!/usr/bin/env python3
"""
Rytinis naujienų brifingas — MB Enerconsult
Nuskaito RSS srautus ir klasifikuoja su Claude API.
Rezultatą išsaugo į data/naujienų-brifingas.md
"""

import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from urllib.request import urlopen, Request

import anthropic

# ── Laiko zona ───────────────────────────────────────────────────────────────
LT_TZ = timezone(timedelta(hours=3))   # EEST vasarą; žiemą 2

# ── RSS šaltiniai ────────────────────────────────────────────────────────────
# Tvarka: specializuoti energetikos pirmiausia, tada bendri verslo/ekonomikos.
# URL'ai patikrinti 2026-10-10; jei neveikia – grąžinamas tuščias sąrašas.
RSS_FEEDS = [
    # Delfi ekonomika ir verslas (dažnai skirtingi, kartais tie patys – deduplikuojama pagal URL)
    ("Delfi ekonomika",  "https://www.delfi.lt/rss/feeds/ekonomika.xml"),
    ("Delfi verslas",    "https://www.delfi.lt/rss/feeds/verslas.xml"),
    # 15min – bendras srautas (ekonomika/verslas/politika)
    ("15min",            "https://www.15min.lt/rss"),
    # Verslo žinios – geriausias energetikos padengimas
    ("Verslo žinios",    "https://www.vz.lt/rss/"),
    # Baltpool (Baltijos energijos birža) – kartais tuščias, bet vertingas
    ("Baltpool",         "https://www.baltpool.eu/lt/rss/"),
    # Specializuotas energetikos portalas – WordPress /feed/ (testuojama)
    ("Energetika.lt",    "https://energetika.lt/category/naujienos/feed/"),
    # Atsarginiai URL'ai:
    # ("LRT naujienos",  "https://www.lrt.lt/rss/naujienos"),  # Kol kas 404
    # ("Litgrid",        "https://litgrid.eu/lt/rss.xml"),     # Reikia patikrinti
]

# ── Montel News ──────────────────────────────────────────────────────────────
# Montel neturi RSS, bet paieškos puslapis generuojamas serveryje (HTML),
# todėl straipsnių sąrašą galima nuskaityti tiesiai. Tekstas už nemokamos
# registracijos sienos – imame pavadinimą, datą ir įžangą.
MONTEL_QUERIES = ["Lithuania", "Lithuanian", "Litgrid", "Ignitis", "Baltics", "Baltic"]
MONTEL_DAYS    = 3   # pirmadienį apima ir savaitgalį

KEYWORDS = [
    "energetika", "elektra", "elektros", "saulės energija", "saulės park",
    "vėjo elektrin", "vėjo park", "baterijų kaupikl", "bess", "litgrid",
    "eso ", "vert ", "atsinaujinanti energija", "elektros kaina",
    "nordpool", "nord pool", "energijos kainos", "gamyba", "tinklai",
    "akumuliatoriai", "pv park", "jūrinė energetika", "šiluma",
    "dujų", "naftos", "biokuras", "biodujos", "vėjas", "saulė",
    "renovacija", "efektyvumas", "emisijos", "co2", "žalioji",
    "investicij", "projektas", "parkas", "stotis", "tinkl",
    "baltpool", "viešnagė", "aukcion", "leidimai", "prijungim",
]

# ── RSS nuskaitymas ───────────────────────────────────────────────────────────

def fetch_rss(name: str, url: str) -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; EnerconsultBot/1.0; +https://enerconsult.lt)",
        "Accept":     "application/rss+xml, application/xml, application/atom+xml, text/xml;q=0.9",
    }
    try:
        req  = Request(url, headers=headers)
        resp = urlopen(req, timeout=15)
        raw  = resp.read()
    except Exception as exc:
        print(f"  ⚠  {name}: {exc}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        preview = raw[:200].decode("utf-8", errors="replace").replace("\n", " ")
        print(f"  ⚠  {name}: XML klaida ({exc}); pradžia: {preview}", file=sys.stderr)
        return []

    ns    = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//atom:entry", ns)

    if not items:
        preview = raw[:300].decode("utf-8", errors="replace").replace("\n", " ")
        print(f"  ⚠  {name}: 0 elementų – pradžia: {preview[:200]}", file=sys.stderr)

    out = []
    for item in items:
        def t(tag: str) -> str:
            el = item.find(tag)
            if el is None:
                el = item.find(f"atom:{tag}", ns)
            if el is None:
                return ""
            return (el.text or "").strip()

        title = t("title")
        link  = t("link") or t("guid")
        desc  = t("description") or t("summary") or t("content")
        pub   = t("pubDate") or t("published") or t("updated")

        if title:
            out.append({"source": name, "title": title,
                        "link": link, "desc": desc[:400], "date": pub[:30]})

    print(f"  ✓ {name}: {len(out)}", file=sys.stderr)
    return out


MONTEL_ITEM_RE = re.compile(
    r'<li class="search-results__item">.*?'
    r'href="(?P<href>/news/[^"]+)".*?'
    r'search-results__title">(?P<title>.*?)</div>.*?'
    r'datetime="(?P<dt>[^"]+)".*?'
    r'search-results__excerpt">(?P<excerpt>.*?)</span>',
    re.S,
)


def fetch_montel() -> list[dict]:
    """Montel News straipsniai apie Lietuvą/Baltijos šalis per paskutines MONTEL_DAYS d."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    cutoff = datetime.now(timezone.utc) - timedelta(days=MONTEL_DAYS)
    found: dict[str, dict] = {}

    for q in MONTEL_QUERIES:
        url = f"https://montelnews.com/search?q={quote(q)}"
        try:
            html_text = urlopen(Request(url, headers=headers), timeout=20).read().decode("utf-8", "replace")
        except Exception as exc:
            print(f"  ⚠  Montel ({q}): {exc}", file=sys.stderr)
            continue

        matches = list(MONTEL_ITEM_RE.finditer(html_text))
        if not matches:
            print(f"  ⚠  Montel ({q}): paieškos rezultatų struktūra neatpažinta", file=sys.stderr)
            continue

        fresh = 0
        for m in matches:
            try:
                published = datetime.fromisoformat(m["dt"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if published < cutoff:
                continue
            link = "https://montelnews.com" + m["href"]
            if link in found:
                continue
            clean = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
            found[link] = {
                "source": "Montel News",
                "title":  clean(m["title"]),
                "link":   link,
                "desc":   clean(m["excerpt"])[:400],
                "date":   published.astimezone(LT_TZ).strftime("%Y-%m-%d %H:%M"),
                "montel": True,
            }
            fresh += 1
        print(f"  ✓ Montel ({q}): {len(matches)} rezultatų, naujų per {MONTEL_DAYS} d.: {fresh}",
              file=sys.stderr)

    items = sorted(found.values(), key=lambda it: it["date"], reverse=True)
    print(f"  ✓ Montel iš viso: {len(items)}", file=sys.stderr)
    return items


def filter_energy(items: list[dict]) -> list[dict]:
    kw = [k.lower() for k in KEYWORDS]
    return [it for it in items
            if any(k in (it["title"] + " " + it["desc"]).lower() for k in kw)]


def collect() -> list[dict]:
    raw_items: list[dict] = []
    for name, url in RSS_FEEDS:
        raw_items.extend(fetch_rss(name, url))

    # Deduplikacija pagal nuorodą (tas pats straipsnis keliuose šaltiniuose)
    seen: set[str] = set()
    all_items: list[dict] = []
    for it in raw_items:
        key = it["link"] or it["title"]
        if key and key not in seen:
            seen.add(key)
            all_items.append(it)

    energy = filter_energy(all_items)
    total_dupes = len(raw_items) - len(all_items)
    print(
        f"\n  Iš viso straipsnių: {len(raw_items)} "
        f"(dublikatai: {total_dupes}, unikalūs: {len(all_items)}), "
        f"energetikos: {len(energy)}",
        file=sys.stderr,
    )

    # Montel – jau atrinkta pagal paiešką (angliškai), todėl lietuviškų
    # raktažodžių filtras netaikomas; dedama pirmiau, kad nebūtų nukirpta.
    montel = fetch_montel()
    return montel + energy


# ── Claude API ────────────────────────────────────────────────────────────────

def generate(news: list[dict], date_lt: str) -> str:
    if not news:
        return (
            f"# Naujienų brifingas — {date_lt}\n\n"
            "Šiandien energetikos naujienų RSS srautuose nerasta.\n\n"
            "_Generuota automatiškai · MB Enerconsult_"
        )

    block = "\n".join(
        f"[{i+1}] [{it['source']}{' · ' + it['date'] if it.get('montel') else ''}] {it['title']}\n"
        f"    {it['desc'][:250]}\n"
        f"    {it['link']}"
        for i, it in enumerate(news[:70])
    )
    n_montel = sum(1 for it in news if it.get("montel"))

    prompt = f"""Tu esi Lietuvos atsinaujinančios energetikos rinkos analitikas dirbantis MB Enerconsult.
Šiandien yra {date_lt}. Pateikiu naujienų sąrašą iš RSS srautų ir Montel News
(tarptautinė energetikos naujienų agentūra, straipsniai angliškai; jų įrašų: {n_montel}).

Atrink TIKTAI aktualias energetikos naujienas (saulė, vėjas, BESS, elektros tinklas,
reguliavimas, NordPool kainos, projektai LT/Baltijos regione, biokuras, šiluma, dujos).
Ignoruok nesusijusias naujienas (sportas, pramogos, politika be energetikos ryšio).
Montel straipsniuose „Baltic" kartais reiškia tik Baltijos jūrą (pvz. Švedijos ar Suomijos
naujienos) – tokius praleisk, jei nėra ryšio su Lietuva ar Baltijos šalimis.

Suformatuok kaip rytinį briefingą MARKDOWN formatu:

# Naujienų brifingas — {date_lt}

## 🌍 Montel News apie Lietuvą ir Baltijos šalis
(kiekvienas aktualus Montel straipsnis: pavadinimas lietuviškai kaip nuoroda, data,
1–2 sakiniai santraukos lietuviškai. Jei aktualių Montel straipsnių nėra – parašyk vieną
eilutę „Per pastarąsias {MONTEL_DAYS} d. Montel News apie Lietuvą nerašė.")

## 🔴 Svarbiausia
(1–3 svarbiausios naujienos, 1–2 sakiniai kiekvienai, šaltinis ir nuoroda)

## ⚡ Elektros rinka
(kainos, NordPool, balansavimas — jei yra)

## 🌬️ Vėjas ir saulė
(projektai, leidimai, statyba — jei yra)

## 🔋 Kaupikliai ir inovacijos
(BESS, akumuliatoriai, naujovės — jei yra)

## 🏛️ Reguliavimas ir politika
(VERT, ESO, Litgrid, teisės aktai, leidimai — jei yra)

## 📰 Kita aktualu
(kita svarbu energetikui — jei yra)

---
_Generuota automatiškai {date_lt} · MB Enerconsult_

Rašyk glaustai lietuviškai. Montel skyrių rodyk visada; kitas kategorijas be naujienų praleisk visiškai.
Ta pati naujiena neturi kartotis keliuose skyriuose.
Kiekviena naujiena: pavadinimas kaip nuoroda, 1–2 sakiniai santrauka, šaltinis.

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

def main() -> None:
    dt_lt    = datetime.now(LT_TZ)
    date_str = dt_lt.strftime("%Y-%m-%d")

    print(f"📡 Naujienų brifingas {date_str} (UTC+3)", file=sys.stderr)

    news   = collect()
    result = generate(news, date_str)

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
