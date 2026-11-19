#!/usr/bin/env python3
"""
Teisės aktų ryto brifingas
==========================
Paima naujausius priimtus Lietuvos teisės aktus iš data.gov.lt (Spinta API)
ir sugeneruoja brifingą — tekstinį arba JSON.

Duomenų šaltinis:
  https://get.data.gov.lt/datasets/gov/lrsk/teises_aktai/Dokumentas
  Licencija: CC BY 4.0 (LR Seimo kanceliarija)

Naudojimas:
  python "scripts/teisės aktai.py"              # tekstinis brifingas, 7 dienos
  python "scripts/teisės aktai.py" --days 1     # tik vakar
  python "scripts/teisės aktai.py" --json       # JSON formatas
  python "scripts/teisės aktai.py" --type Įstatymas  # tik įstatymai
  python "scripts/teisės aktai.py" --json --data 2026-11-19 --is-failo data/šaltiniai/tar.json
      # rytinė ataskaita: imami per Chrome surinkti aktai (data.gov.lt užkarda
      # neprileidžia GitHub serverių); jei failo šiai datai nėra – bandoma API
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

BASE_URL = "https://get.data.gov.lt"
DATASET = "datasets/gov/lrsk/teises_aktai/Dokumentas"

# Energetikos atrankos taisyklės – bendros su rytiniu surinkimu per Chrome
RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ištekliai", "teisės-aktų-atranka.json")
with open(RULES_PATH, encoding="utf-8") as _f:
    RULES = json.load(_f)


def fetch_legislation(days: int, type_filter: str | None = None) -> list[dict]:
    """Gauna naujausius teisės aktus iš data.gov.lt API."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Spinta query syntax
    filters = [f'paskelbta_tar>="{since}"']
    if type_filter:
        filters.append(f'rusis="{type_filter}"')

    query = "&".join(filters + ["sort(-paskelbta_tar)", "limit(100)"])
    url = f"{BASE_URL}/{DATASET}?{query}"

    print(f"[teisės aktai] Užklausa: {url}", file=sys.stderr)

    resp = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    # Spinta grąžina { "_data": [...] } arba tiesiog sąrašą
    if isinstance(data, dict) and "_data" in data:
        return data["_data"]
    if isinstance(data, list):
        return data
    return []


def energy_area(doc: dict) -> str | None:
    """Energetikos sritis pagal pavadinimą ir instituciją arba None (žr. teisės-aktų-atranka.json)."""
    t = str(doc.get("pavadinimas") or "").lower()
    pi = (str(doc.get("priemusi_inst") or "") + " " + str(doc.get("parengusi_inst") or "")).lower()
    if any(k in t for k in RULES["neįtraukti_pavadinime"]):
        return None
    if any(k in pi for k in RULES["neįtraukti_institucijos"]):
        return None
    if "valstybinė energetikos reguliavimo taryba" in pi and any(k in t for k in RULES["vert_ne_energetika"]):
        return None
    for area, kws in RULES["sritys"]:
        if any(k in t for k in kws):
            return area
    return "Kita energetika" if any(k in pi for k in RULES["visada_institucijos"]) else None


def is_energy_relevant(doc: dict) -> bool:
    """Patikrina ar dokumentas susijęs su energetika."""
    return energy_area(doc) is not None


def etar_url(doc) -> str:
    """e-TAR nuoroda: API laukas „nuoroda“ (…/legalAct/<dokumento_id>); TAR kodu e-TAR akto neranda."""
    if isinstance(doc, dict):
        if str(doc.get("nuoroda") or "").startswith("https://"):
            return doc["nuoroda"]
        if doc.get("dokumento_id"):
            return f"https://e-tar.lt/portal/lt/legalAct/{doc['dokumento_id']}"
    return ""


def generate_text(docs: list[dict], days: int, type_filter: str | None) -> str:
    """Tekstinis brifingas."""
    today = datetime.now().strftime("%Y-%m-%d")
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    lines = []
    lines.append("═" * 60)
    lines.append("  Teisės aktų brifingas")
    lines.append(f"  {today} (laikotarpis: {since} — {today})")
    lines.append("═" * 60)
    lines.append("")

    if not docs:
        lines.append("Naujų teisės aktų nerasta nurodytu laikotarpiu.")
        return "\n".join(lines)

    # Grupuojam pagal rūšį
    by_type: dict[str, list[dict]] = {}
    energy_docs = []

    for doc in docs:
        rūšis = doc.get("rusis", "Kita") or "Kita"
        by_type.setdefault(rūšis, []).append(doc)
        if is_energy_relevant(doc):
            energy_docs.append(doc)

    # Energetikai aktualūs — pirmi
    if energy_docs:
        lines.append(f"⚡ Energetikai aktualūs ({len(energy_docs)})")
        lines.append("─" * 50)
        for doc in energy_docs:
            lines.append(f"  • {doc.get('pavadinimas', '?')}")
            lines.append(f"    Rūšis: {doc.get('rusis', '?')} | "
                         f"Priimtas: {doc.get('priimtas', '?')} | "
                         f"Paskelbta: {doc.get('paskelbta_tar', '?')}")
            inst = doc.get("priemusi_inst", "")
            if inst:
                lines.append(f"    Priėmė: {inst}")
            tar = doc.get("tar_kodas", "")
            if tar:
                lines.append(f"    {etar_url(doc)}")
            lines.append("")
        lines.append("")

    # Visos rūšys — suvestinė
    lines.append(f"Visi teisės aktai: {len(docs)}")
    lines.append("─" * 50)
    for rūšis, rūšies_docs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        lines.append(f"  {rūšis}: {len(rūšies_docs)}")

    lines.append("")

    # Visas sąrašas (ne energetiniai)
    non_energy = [d for d in docs if d not in energy_docs]
    if non_energy:
        lines.append("Kiti teisės aktai")
        lines.append("─" * 50)
        for doc in non_energy[:30]:  # limitas kad nebūtų per ilgas
            lines.append(f"  • {doc.get('pavadinimas', '?')}")
            lines.append(f"    {doc.get('rusis', '?')} | "
                         f"{doc.get('priimtas', '?')} | "
                         f"{doc.get('priemusi_inst', '?')}")
            tar = doc.get("tar_kodas", "")
            if tar:
                lines.append(f"    {etar_url(doc)}")
            lines.append("")

        if len(non_energy) > 30:
            lines.append(f"  ... ir dar {len(non_energy) - 30} aktų")

    return "\n".join(lines)


def generate_json(docs: list[dict], days: int, type_filter: str | None) -> dict:
    """JSON struktūra integracijai su Morning Report."""
    today = datetime.now().strftime("%Y-%m-%d")
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    energy_docs = [d for d in docs if is_energy_relevant(d)]

    return {
        "section": "teises_aktai",
        "generated": datetime.now().isoformat(),
        "period": {"from": since, "to": today, "days": days},
        "type_filter": type_filter,
        "total_count": len(docs),
        "energy_relevant_count": len(energy_docs),
        "energy_relevant": [
            {
                "pavadinimas": d.get("pavadinimas"),
                "sritis": d.get("sritis") or energy_area(d),
                "rusis": d.get("rusis"),
                "priimtas": d.get("priimtas"),
                "paskelbta_tar": d.get("paskelbta_tar"),
                "isigalioja": d.get("isigalioja"),
                "galioj_busena": d.get("galioj_busena"),
                "priemusi_inst": d.get("priemusi_inst"),
                "tar_kodas": d.get("tar_kodas"),
                "atv_dok_nr": d.get("atv_dok_nr"),
                "url": etar_url(d),
            }
            for d in energy_docs
        ],
        "by_type": {
            rūšis: len([d for d in docs if (d.get("rusis") or "Kita") == rūšis])
            for rūšis in sorted(set((d.get("rusis") or "Kita") for d in docs))
        },
        "all_acts": [
            {
                "pavadinimas": d.get("pavadinimas"),
                "rusis": d.get("rusis"),
                "priimtas": d.get("priimtas"),
                "paskelbta_tar": d.get("paskelbta_tar"),
                "isigalioja": d.get("isigalioja"),
                "priemusi_inst": d.get("priemusi_inst"),
                "tar_kodas": d.get("tar_kodas"),
                "url": etar_url(d),
            }
            for d in docs
        ],
    }


def fetch_range(nuo: str, iki: str) -> list[dict]:
    """Aktai, paskelbti TAR nuo `nuo` (imtinai) iki `iki` (neimtinai)."""
    query = "&".join([f'paskelbta_tar>="{nuo}"', f'paskelbta_tar<"{iki}"', "sort(-paskelbta_tar)", "limit(2000)"])
    url = f"{BASE_URL}/{DATASET}?{query}"
    print(f"[teisės aktai] Užklausa: {url}", file=sys.stderr)
    resp = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("_data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])


def load_collected(path: str, day: str) -> dict | None:
    """Per Chrome surinktas failas (data/šaltiniai/tar.json), jei jis skirtas šios dienos ataskaitai."""
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError) as e:
        print(f"[teisės aktai] Surinkto failo nėra arba jis sugadintas ({path}): {e}", file=sys.stderr)
        return None
    if not isinstance(c, dict) or c.get("iki") != day or not isinstance(c.get("aktai"), list):
        print(f"[teisės aktai] Surinktas failas ne šiai dienai (iki={c.get('iki') if isinstance(c, dict) else None}, reikia {day})", file=sys.stderr)
        return None
    return c


def main():
    parser = argparse.ArgumentParser(description="Teisės aktų ryto brifingas")
    parser.add_argument("--days", type=int, default=7,
                        help="Kiek dienų atgal žiūrėti (numatyta: 7)")
    parser.add_argument("--type", dest="type_filter", default=None,
                        help="Filtruoti pagal rūšį (pvz. Įstatymas)")
    parser.add_argument("--json", action="store_true",
                        help="Išvesti JSON formatu")
    parser.add_argument("--data", default=None,
                        help="Ataskaitos data (YYYY-MM-DD): imami aktai, paskelbti iki šios datos nuo ankstesnio surinkimo")
    parser.add_argument("--is-failo", dest="is_failo", default=None,
                        help="Per Chrome surinktas failas (data/šaltiniai/tar.json)")
    args = parser.parse_args()

    if args.data:
        day = args.data
        collected = load_collected(args.is_failo, day) if args.is_failo else None
        if collected:
            docs = collected["aktai"]
            print(f"[teisės aktai] Iš surinkto failo: {collected.get('viso')} aktų, energetikos {len(docs)} "
                  f"({collected.get('nuo')} – {collected.get('iki')}, surinkta {collected.get('surinkta')})", file=sys.stderr)
            source, period, total = "data.gov.lt TAR (surinkta per Chrome)", {"from": collected.get("nuo"), "to": day}, collected.get("viso", len(docs))
        else:
            nuo = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                docs = fetch_range(nuo, day)
            except requests.RequestException as e:
                print(f"[teisės aktai] API klaida: {e}", file=sys.stderr)
                print(json.dumps({"section": "teises_aktai", "data": day, "error": str(e), "total_count": 0,
                                  "energy_relevant": [], "all_acts": []}, ensure_ascii=False, indent=2))
                sys.exit(1)
            source, period, total = "data.gov.lt TAR (API)", {"from": nuo, "to": day}, len(docs)
        result = generate_json(docs, 1, args.type_filter)
        result.update({"data": day, "šaltinis": source, "period": period, "total_count": total})
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(generate_text(docs, 1, args.type_filter))
        return

    try:
        docs = fetch_legislation(args.days, args.type_filter)
        print(f"[teisės aktai] Gauta {len(docs)} dokumentų", file=sys.stderr)
    except requests.RequestException as e:
        print(f"[teisės aktai] API klaida: {e}", file=sys.stderr)
        if args.json:
            print(json.dumps({
                "section": "teises_aktai",
                "error": str(e),
                "total_count": 0,
                "energy_relevant": [],
                "all_acts": [],
            }, ensure_ascii=False, indent=2))
        else:
            print(f"❌ Nepavyko gauti teisės aktų: {e}")
        sys.exit(1)

    if args.json:
        result = generate_json(docs, args.days, args.type_filter)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(generate_text(docs, args.days, args.type_filter))


if __name__ == "__main__":
    main()
