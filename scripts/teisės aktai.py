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
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

BASE_URL = "https://get.data.gov.lt"
DATASET = "datasets/gov/lrsk/teises_aktai/Dokumentas"

# Energetikai aktualios rūšys ir raktažodžiai (filtruoti „relevance" žymą)
ENERGY_KEYWORDS = [
    "energi", "elektr", "atsinaujinan", "vėjo", "saulės", "kaupikl",
    "baterij", "akumuliator", "tinkl", "perdavim", "skirstom",
    "generato", "elektrinė", "gamyb", "litgrid", "eso ",
    "ignitis", "epso", "vert ", "apva ", "lea ",
    "taršos", "emisij", "anglies", "klimat", "žali",
    "subsidij", "lengvat", "parama", "aukcion",
    "bess", "pv ", "aei", "gaminan",
]


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


def is_energy_relevant(doc: dict) -> bool:
    """Patikrina ar dokumentas susijęs su energetika."""
    text = " ".join([
        str(doc.get("pavadinimas", "")),
        str(doc.get("parengusi_inst", "")),
        str(doc.get("priemusi_inst", "")),
    ]).lower()

    return any(kw in text for kw in ENERGY_KEYWORDS)


def etar_url(tar_kodas: str) -> str:
    """Sudaro e-TAR nuorodą."""
    if tar_kodas:
        return f"https://www.e-tar.lt/portal/lt/legalAct/{tar_kodas}"
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
                lines.append(f"    {etar_url(tar)}")
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
                lines.append(f"    {etar_url(tar)}")
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
                "rusis": d.get("rusis"),
                "priimtas": d.get("priimtas"),
                "paskelbta_tar": d.get("paskelbta_tar"),
                "isigalioja": d.get("isigalioja"),
                "galioj_busena": d.get("galioj_busena"),
                "priemusi_inst": d.get("priemusi_inst"),
                "tar_kodas": d.get("tar_kodas"),
                "atv_dok_nr": d.get("atv_dok_nr"),
                "url": etar_url(d.get("tar_kodas", "")),
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
                "url": etar_url(d.get("tar_kodas", "")),
            }
            for d in docs
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Teisės aktų ryto brifingas")
    parser.add_argument("--days", type=int, default=7,
                        help="Kiek dienų atgal žiūrėti (numatyta: 7)")
    parser.add_argument("--type", dest="type_filter", default=None,
                        help="Filtruoti pagal rūšį (pvz. Įstatymas)")
    parser.add_argument("--json", action="store_true",
                        help="Išvesti JSON formatu")
    args = parser.parse_args()

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
