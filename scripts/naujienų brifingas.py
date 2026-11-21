#!/usr/bin/env python3
"""
Rytinis naujienų brifingas — MB Enerconsult
Nuskaito RSS srautus ir Montel News, atrenka ir apibendrina su Claude API.
Rezultatas: data/naujienos.json (Morning Report ataskaitai: regionas, pavadinimas,
santrauka, šaltinis, nuoroda) ir data/naujienų-brifingas.md (skaityti žmogui).
"""

import html
import json
import os
import re
import sys
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from urllib.request import urlopen, Request

import anthropic

# ── Laiko zona ───────────────────────────────────────────────────────────────
LT_TZ = ZoneInfo("Europe/Vilnius")
FRESH_HOURS = 36                         # RSS įrašai ne senesni nei tiek valandų iki paleidimo
MODEL = "claude-sonnet-5-5"
MODEL_FALLBACK = "claude-haiku-4-5"
REGIONS = ("Lietuva", "Kaimynai", "Europa", "Pasaulis")

# ── RSS šaltiniai ────────────────────────────────────────────────────────────
# Tvarka: specializuoti energetikos pirmiausia, tada bendri verslo/ekonomikos.
# URL'ai patikrinti 2026-10-10; jei neveikia – grąžinamas tuščias sąrašas.
RSS_FEEDS = [
    # Delfi ekonomika ir verslas (dažnai skirtingi, kartais tie patys – deduplikuojama pagal URL)
    ("Delfi ekonomika",  "https://www.delfi.lt/rss/feeds/ekonomika.xml"),
    ("Delfi verslas",    "https://www.delfi.lt/rss/feeds/verslas.xml"),
    # 15min – bendras srautas turi tik 20 naujausių įrašų (apima kelias valandas), todėl
    # papildomai imamas verslo srautas (2026-11-21: be jo praleista žinia apie „Ignitis grupės“ vadovą)
    ("15min",            "https://www.15min.lt/rss"),
    ("15min verslas",    "https://www.15min.lt/rss/verslas"),
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

# Atranka pagal žodžių kamienus (tinka visiems linksniams: „energetika“, „energetikos“…)
# ir energetikos bendrovių bei institucijų pavadinimus. Galutinai atrenka Claude.
KEYWORDS = [
    "energet", "energij", "elektr", "šilum", "dujų", "dujos", "dujom", "dujotiek", "suskystint",
    "naft", "degal", "biokur", "biodujų", "biometan", "vandenil", "branduolin", "atomin",
    "vėjo", "saulės", "kaupikl", "baterij", "akumuliator", "tinkl", "prijungim", "sinchroniz",
    "aukcion", "tarif", "emisij", "taršos leidim", "atsinaujinanči", "renovacij", "efektyvum",
    # bendrovės, operatoriai, rinkos
    "ignit", "ignič", "litgrid", "epso", "amber grid", "kn energies", "klaipėdos naft", "orlen",
    "achema", "lietuvos energ", "baltpool", "getbaltic", "conexus", "elering", "augstsprieguma",
    "latvenergo", "eesti energia", "enefit", "fortum", "vattenfall", "nord pool", "nordpool",
    "nordbalt", "litpol", "estlink", "harmony link", "kruon", "elektrėn", "visagin", "ignalin",
    "apva", "energetikos ministr", "reguliavimo taryb",
]
KEYWORD_WORDS = re.compile(r"\b(eso|vert|lea|ast|bess|sgd|skgd|co2|ppa|aei)\b", re.I)

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
    def hit(it):
        t = (it["title"] + " " + it["desc"]).lower()
        return any(k in t for k in kw) or bool(KEYWORD_WORDS.search(t))
    return [it for it in items if hit(it)]


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

    # tik šviežios naujienos (jei data neatpažinta – paliekama)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=FRESH_HOURS)
    fresh = []
    for it in all_items:
        try:
            pub = parsedate_to_datetime(it["date"]) if it.get("date") else None
            if pub is not None and pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pub = None
        if pub is None or pub >= cutoff:
            if pub is not None:
                it["date"] = pub.astimezone(LT_TZ).strftime("%Y-%m-%d %H:%M")
            fresh.append(it)
    print(f"  Šviežių (≤ {FRESH_HOURS} val.): {len(fresh)} iš {len(all_items)}", file=sys.stderr)
    all_items = fresh

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

# Atsakymas imamas per įrankį: API grąžina jau išnagrinėtą JSON, todėl kabutės tekste
# jo nesugadina. Jei modelis įrankio nepanaudoja, bandomas JSON iš teksto.
NEWS_TOOL = {
    "name": "pateikti_naujienas",
    "description": "Pateikti rytinei ataskaitai atrinktas energetikos naujienas.",
    "input_schema": {
        "type": "object",
        "properties": {
            "svarbiausia": {"type": "integer", "description": "Svarbiausios naujienos numeris iš sąrašo."},
            "naujienos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "nr": {"type": "integer", "description": "Numeris iš sąrašo."},
                        "regionas": {"type": "string", "enum": list(REGIONS)},
                        "pavadinimas": {"type": "string"},
                        "santrauka": {"type": "string"},
                        "svarba": {"type": "string", "enum": ["didelė", "vidutinė", "maža"]},
                    },
                    "required": ["nr", "regionas", "pavadinimas", "santrauka", "svarba"],
                },
            },
        },
        "required": ["svarbiausia", "naujienos"],
    },
}


def ask_claude_json(prompt: str, max_tokens: int = 8000) -> dict:
    """Pirmas modelis, kurio atsakymą pavyksta perskaityti; kitaip – atsarginis modelis.
    Naujesni modeliai prieš atsakymą „galvoja“, tie žetonai įskaičiuojami į max_tokens."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    last = None
    for model in (MODEL, MODEL_FALLBACK):
        try:
            msg = client.messages.create(model=model, max_tokens=max_tokens, tools=[NEWS_TOOL],
                                         messages=[{"role": "user", "content": prompt}])
            for b in msg.content:
                if getattr(b, "type", "") == "tool_use" and b.name == NEWS_TOOL["name"] and isinstance(b.input, dict):
                    print(f"  ✓ Claude modelis: {model} (įrankis)", file=sys.stderr)
                    return b.input
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                data = json.loads(m.group(0))
                print(f"  ✓ Claude modelis: {model} (tekstas)", file=sys.stderr)
                return data
            print(f"  ⚠ Claude ({model}): atsakyme nėra JSON, stop_reason={msg.stop_reason}, "
                  f"blokai={[getattr(b, 'type', '?') for b in msg.content]}", file=sys.stderr)
        except Exception as exc:          # pvz. modelis nepasiekiamas ar JSON sugadintas – bandomas atsarginis
            last = exc
            print(f"  ⚠ Claude ({model}): {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
    raise last or RuntimeError("Claude negrąžino naujienų sąrašo")


def generate_json(news: list[dict], date_lt: str) -> dict:
    """Atrinktos naujienos su regionu ir santrauka. Nuorodos ir šaltiniai imami iš RSS, ne iš modelio."""
    out = {"data": date_lt, "sugeneruota": datetime.now(LT_TZ).isoformat(timespec="seconds"),
           "svarbiausia": None, "naujienos": [], "kandidatų": len(news)}
    if not news:
        return out
    cand = news[:150]
    block = "\n".join(
        f"[{i+1}] [{it['source']} · {it.get('date', '')}] {it['title']}\n    {it['desc'][:300]}"
        for i, it in enumerate(cand)
    )
    prompt = f"""Tu esi MB Enerconsult energetikos rinkos analitikas. Šiandien {date_lt}.
Pateikiu naujienų sąrašą iš Lietuvos portalų RSS srautų ir Montel News (angliškai).

Atrink iki 8 svarbiausių energetikos naujienų rytinei ataskaitai Lietuvos energetikos verslui
(elektra, saulė, vėjas, kaupikliai, tinklai, reguliavimas, kainos, dujos, nafta, šiluma).
Nesusijusias naujienas praleisk. Svarbios ir energetikos bendrovių bei institucijų naujienos: vadovų
pasikeitimai, strateginiai sprendimai, finansiniai rezultatai, investicijos („Ignitis grupė“, ESO, „Litgrid“,
EPSO-G, „Amber Grid“, „KN Energies“, „Orlen Lietuva“, VERT, Energetikos ministerija ir pan.).
Montel straipsniuose „Baltic“ kartais reiškia tik Baltijos jūrą –
tokius praleisk, jei nėra ryšio su Lietuva ar Baltijos šalimis.
Pageidaujama apimtis: apie 70 % Lietuva, 15 % kaimynai, 10 % Europa, 5 % pasaulis;
energetikoje apie 70 % elektra, 30 % iškastinis kuras. Jei tinkamų naujienų mažiau – pateik mažiau.
Ta pati tema neturi kartotis.

Kiekvienai atrinktai naujienai:
- "nr": numeris iš sąrašo;
- "regionas": "Lietuva"; "Kaimynai" – TIK Latvija, Estija, Lenkija, Švedija, Suomija ir visas Baltijos regionas;
  "Europa" – ES ir kitos Europos šalys (pvz., Vokietija, Ukraina, Moldova); "Pasaulis" – visa kita;
- "pavadinimas": trumpas pavadinimas lietuviškai;
- "santrauka": vienas ar du sakiniai lietuviškai TIK iš pateikto pavadinimo ir aprašo – jokių
  skaičių, faktų ar vertinimų, kurių tekste nėra; nerašyk, iš kur informacija (pvz. „pasak pavadinimo“);
- "svarba": "didelė", "vidutinė" arba "maža" Lietuvos energetikos verslui.
Vieną naujieną pažymėk kaip svarbiausią laukelyje "svarbiausia" (jos nr).

Rašyba: lietuviški skaičiai (dešimtainis kablelis, tarpas prieš %), jokio teksto didžiosiomis
raidėmis, prieš vienintelį jungtuką „ir“ kablelio nedėk. Kabutėms tekste naudok tik „ ir “.

Rezultatą pateik vieną kartą iškviesdamas įrankį pateikti_naujienas. Jei įrankio panaudoti
negali, grąžink tik JSON be paaiškinimų, pvz.:
{{"svarbiausia": 4, "naujienos": [{{"nr": 4, "regionas": "Lietuva", "pavadinimas": "…", "santrauka": "…", "svarba": "didelė"}}]}}

NAUJIENOS:
{block}"""
    data = ask_claude_json(prompt)
    items, top = [], None
    for row in data.get("naujienos", []):
        try:
            src = cand[int(row["nr"]) - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        reg = row.get("regionas")
        if reg not in REGIONS or not row.get("pavadinimas") or not row.get("santrauka"):
            continue
        if int(row["nr"]) == data.get("svarbiausia"):
            top = len(items)
        items.append({
            "regionas": reg,
            "pavadinimas": row["pavadinimas"].strip(),
            "santrauka": row["santrauka"].strip(),
            "svarba": row.get("svarba") if row.get("svarba") in ("didelė", "vidutinė", "maža") else None,
            "šaltinis": src["source"],
            "nuoroda": src.get("link") or None,
            "paskelbta": src.get("date") or None,
        })
    out["naujienos"] = items
    out["svarbiausia"] = top
    return out


def render_md(res: dict) -> str:
    L = [f"# Naujienų brifingas — {res['data']}", ""]
    if not res["naujienos"]:
        L.append("Šiandien energetikos naujienų nerasta.")
    for reg in REGIONS:
        rows = [n for n in res["naujienos"] if n["regionas"] == reg]
        if not rows:
            continue
        L += [f"## {reg}", ""]
        for n in rows:
            title = f"[**{n['pavadinimas']}**]({n['nuoroda']})" if n.get("nuoroda") else f"**{n['pavadinimas']}**"
            L += [title, f"{n['santrauka']} _{n['šaltinis']}{', ' + n['paskelbta'] if n.get('paskelbta') else ''}_", ""]
    L += ["---", f"_Generuota automatiškai {res['data']} · MB Enerconsult_"]
    return "\n".join(L)


# ── Pagrindinis srautas ───────────────────────────────────────────────────────

def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    date_str = args[0] if args else datetime.now(LT_TZ).strftime("%Y-%m-%d")
    print(f"📡 Naujienų brifingas {date_str}", file=sys.stderr)

    news = collect()
    try:
        res = generate_json(news, date_str)
    except Exception as exc:              # ataskaita gaminama ir be naujienų bloko
        print(f"  ⚠ Naujienų atrinkti nepavyko: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        res = {"data": date_str, "sugeneruota": datetime.now(LT_TZ).isoformat(timespec="seconds"),
               "svarbiausia": None, "naujienos": [], "kandidatų": len(news), "klaida": type(exc).__name__}
    print(f"  Atrinkta naujienų: {len(res['naujienos'])}", file=sys.stderr)

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "naujienos.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    md = render_md(res)
    with open(os.path.join(out_dir, "naujienų-brifingas.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print("\n✅ Išsaugota → data/naujienos.json, data/naujienų-brifingas.md", file=sys.stderr)


if __name__ == "__main__":
    main()
