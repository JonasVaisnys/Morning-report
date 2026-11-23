#!/usr/bin/env python3
"""
Vertimai angliškai Morning Report versijai — MB Enerconsult
===========================================================
Eigoje paleidžiama po naujienų ir pasaulio rinkų, prieš ataskaitą:

  python scripts/vertimai.py [MMMM-MM-DD]

  data/naujienos.json        – kiekvienai naujienai prideda pavadinimas_en ir santrauka_en
  data/pasaulio-rinkos.json  – prideda analizė_md_en (ta pati analizė angliškai, angliškos antraštės)
  data/vertimai.json         – talpykla {lietuviškas tekstas: angliškas}: paramų kvietimų ir teisės aktų
                               pavadinimai, institucijos, savaitės apžvalgos naujienos. Verčiami tik nauji.

Be ANTHROPIC_API_KEY ar nepavykus – nieko nesugadina: angliška ataskaita be vertimų praleidžia naujienas
ir pasaulio rinkų tekstą, o pavadinimus rodo lietuviškai.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
CACHE = os.path.join(DATA, "vertimai.json")
VLN = ZoneInfo("Europe/Vilnius")
MODELS = ("claude-sonnet-5-5", "claude-opus-5-5")
BATCH = 40

STYLE = """Rules:
- British English, concise and factual. Translate faithfully: add nothing, omit nothing, no explanations.
- Numbers in English format: decimal point, comma as the thousands separator (1,250.5), percent without a space (23–27%),
  ranges with an en dash. Keep units (€/MWh, MW, GW, ha); "Eur" → "EUR"; "mln." → "million"; "mlrd." → "billion".
- No comma before a single "and"; no text in capital letters; quotation marks “ ”.
- Company names without quotation marks: „Ignitis grupė“ → Ignitis Group; Litgrid, ESO, EPSO-G, Amber Grid, KN Energies,
  Orlen Lietuva and other company names as written in the original.
- Institutions: Valstybinė energetikos reguliavimo taryba (VERT) → the National Energy Regulatory Council (VERT);
  Energetikos ministerija → the Ministry of Energy; Aplinkos ministerija → the Ministry of Environment;
  Finansų ministerija → the Ministry of Finance; Ekonomikos ir inovacijų ministerija → the Ministry of Economy and Innovation;
  Seimas → the Seimas; Vyriausybė → the Government; Aplinkos projektų valdymo agentūra → the Environmental Projects
  Management Agency; Lietuvos energetikos agentūra → the Lithuanian Energy Agency; Inovacijų agentūra → Innovation Agency
  Lithuania. An abbreviation on its own (APVA, LEA, VERT, ESO, CPVA) stays as it is.
- Terms: SGD → LNG; AEI → renewable energy; kaupikliai → energy storage (batteries); balansavimo rinka → balancing market;
  kitos paros rinka → day-ahead market; prekybos zona → bidding zone; pralaidumai → interconnector capacity;
  visuomeninė elektros kaina → regulated public electricity price; skirstomasis tinklas → distribution grid;
  perdavimo tinklas → transmission grid; jungtis → interconnector; daugiabučių namų modernizavimas → apartment building renovation.
- Titles of legal acts in the usual English style: „Dėl … patvirtinimo“ → “On the approval of …”; įstatymas → Law;
  nutarimas → Resolution; įsakymas → Order; „Lietuvos Respublikos … įstatymas“ → “Law of the Republic of Lithuania on …”.
- Grant call names: translate the description, keep codes and numbers.
- Place names, people's names and the names of publications stay as they are (Vilnius, Skiemonys, Verslo žinios)."""

TOOL = {
    "name": "pateikti_vertimus",
    "description": "Pateikti tekstų vertimus į anglų kalbą.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vertimai": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"nr": {"type": "integer"}, "en": {"type": "string"}},
                    "required": ["nr", "en"],
                },
            },
        },
        "required": ["vertimai"],
    },
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def load(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save(path: str, obj, indent=1) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.write("\n")


_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def clean(src: str, en: str) -> str:
    """Be kabučių aplink visą vertimą, jei originale jų nebuvo (modelis kartais apgaubia pavadinimą “…”)."""
    en = (en or "").strip()
    if len(en) > 2 and en[0] in "“\"" and en[-1] in "”\"" and not src.strip().startswith(("„", "“", "\"")):
        en = en[1:-1].strip()
    return en


def translate_list(texts: list[str]) -> dict[str, str]:
    """{lietuviškas: angliškas} sąrašo tekstams; nepavykę – praleidžiami."""
    out = {}
    for i in range(0, len(texts), BATCH):
        part = texts[i:i + BATCH]
        block = "\n".join(f"[{n + 1}] {t}" for n, t in enumerate(part))
        prompt = ("Translate each numbered text from a Lithuanian energy market report (Morning Report by MB Enerconsult) "
                  "into English for international readers: investors, developers and energy companies.\n\n"
                  f"{STYLE}\n\nReturn every translation once by calling the tool pateikti_vertimus (nr – the number in brackets).\n\n"
                  f"TEXTS:\n{block}")
        data = None
        for model in MODELS:
            try:
                msg = client().messages.create(model=model, max_tokens=12000, tools=[TOOL],
                                               messages=[{"role": "user", "content": prompt}])
                for b in msg.content:
                    if getattr(b, "type", "") == "tool_use" and b.name == TOOL["name"] and isinstance(b.input, dict):
                        data = b.input
                if data:
                    log(f"  ✓ {model}: {len(part)} tekstų")
                    break
                log(f"  ⚠ {model}: atsakyme nėra vertimų (stop_reason={msg.stop_reason})")
            except Exception as exc:
                log(f"  ⚠ {model}: {type(exc).__name__}: {str(exc)[:200]}")
        for row in (data or {}).get("vertimai", []):
            try:
                src = part[int(row["nr"]) - 1]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            en = clean(src, row.get("en") or "")
            if en:
                out[src] = en
    return out


def translate_markdown(md: str) -> str | None:
    """Pasaulio rinkų analizė angliškai su ataskaitai žinomomis antraštėmis."""
    prompt = ("Translate this Markdown section of a Lithuanian energy market report (Morning Report by MB Enerconsult) into English "
              "for international readers.\n\n"
              "Keep exactly the same structure: the same sections, paragraphs and bullet points, Markdown bold and the emoji in headings. "
              "Use these headings: ### Summary, ### 🛢️ Oil, ### 🔥 Gas and LNG, ### ⚡ Impact on Lithuania, ### 👀 What to watch. "
              "Sources in brackets stay as they are, e.g. (Reuters; FT). Return only the translated Markdown.\n\n"
              f"{STYLE}\n\nMARKDOWN:\n{md}")
    for model in MODELS:
        try:
            msg = client().messages.create(model=model, max_tokens=12000, messages=[{"role": "user", "content": prompt}])
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
            if "### Summary" in text:
                log(f"  ✓ {model}: pasaulio rinkų analizė")
                return text
            log(f"  ⚠ {model}: vertime nėra antraštės „### Summary“ (stop_reason={msg.stop_reason})")
        except Exception as exc:
            log(f"  ⚠ {model}: {type(exc).__name__}: {str(exc)[:200]}")
    return None


def grant_name(x: str) -> str:
    """Kaip ataskaitoje: be pabaigos „ 2026-07“."""
    return re.sub(r"\s+\d{4}-\d{2}$", "", x or "").strip()


def cache_candidates(day: date) -> list[str]:
    """Pavadinimai, kurie gali patekti į angliškas ataskaitas (kasdienę ir savaitės)."""
    out = []
    g = load(os.path.join(DATA, "šaltiniai", "paramos-būsena.json")) or {}
    for x in g.get("kvietimai") or []:
        out += [grant_name(x.get("pavadinimas")), (x.get("pavadinimas") or "").strip(), x.get("institucija") or "", x.get("pareiškėjai") or ""]
    t = load(os.path.join(DATA, "šaltiniai", "tar.json")) or {}
    ta = load(os.path.join(DATA, "teisės-aktai.json")) or {}
    for a in (t.get("aktai") or []) + (ta.get("energy_relevant") or []):
        out += [(a.get("pavadinimas") or "").strip().rstrip("."), (a.get("pavadinimas") or "").strip(), a.get("priemusi_inst") or "",
                a.get("sritis") or ""]
    # savaitės apžvalgai: papildomos naujienos ir praėjusių dienų naujienos be angliškų laukų
    pn = load(os.path.join(DATA, "savaitė", "papildomos-naujienos.json")) or {}
    for n in pn.get("naujienos") or []:
        out += [n.get("pavadinimas") or "", n.get("santrauka") or "", n.get("šaltinis") or ""]
    for k in range(1, 9):
        a = load(os.path.join(DATA, "archyvas", f"{(day - timedelta(days=k)).isoformat()}.json")) or {}
        for n in ((a.get("naujienos") or {}).get("naujienos") or []):
            if not n.get("pavadinimas_en"):
                out += [n.get("pavadinimas") or "", n.get("santrauka") or ""]
    return [s.strip() for s in dict.fromkeys(out) if s and s.strip() and re.search(r"[A-Za-zĄ-ž]", s)]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    day = date.fromisoformat(args[0]) if args else datetime.now(VLN).date()
    iso = day.isoformat()
    log(f"🌐 Vertimai {iso}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("  ⚠ ANTHROPIC_API_KEY nenustatytas – vertimai praleidžiami")
        return

    # 1. naujienos
    path = os.path.join(DATA, "naujienos.json")
    nw = load(path)
    if isinstance(nw, dict) and nw.get("data") == iso and nw.get("naujienos"):
        todo = [n for n in nw["naujienos"] if not (n.get("pavadinimas_en") and n.get("santrauka_en"))]
        if todo:
            tr = translate_list([s for n in todo for s in (n["pavadinimas"], n["santrauka"])])
            for n in todo:
                if tr.get(n["pavadinimas"]) and tr.get(n["santrauka"]):
                    n["pavadinimas_en"], n["santrauka_en"] = tr[n["pavadinimas"]], tr[n["santrauka"]]
            save(path, nw)
        log(f"  naujienos: {sum(1 for n in nw['naujienos'] if n.get('pavadinimas_en'))} iš {len(nw['naujienos'])} angliškai")

    # 2. pasaulio rinkos
    path = os.path.join(DATA, "pasaulio-rinkos.json")
    pr = load(path)
    if isinstance(pr, dict) and pr.get("data") == iso and pr.get("analizė_md") and not pr.get("analizė_md_en"):
        en = translate_markdown(pr["analizė_md"])
        if en:
            pr["analizė_md_en"] = en
            save(path, pr, indent=2)
    log(f"  pasaulio rinkos: {'yra' if (pr or {}).get('analizė_md_en') else 'nėra'} angliškos analizės")

    # 3. pavadinimų talpykla
    cache = {k: clean(k, v) for k, v in (load(CACHE) or {}).items()}
    new = [s for s in cache_candidates(day) if s not in cache]
    if new:
        cache.update(translate_list(new))
        save(CACHE, dict(sorted(cache.items())), indent=1)
    log(f"  talpykla: {len(cache)} įrašų, nauji – {len(new)}")


if __name__ == "__main__":
    main()
