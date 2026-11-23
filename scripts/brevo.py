#!/usr/bin/env python3
"""Brevo naujienlaiškio įrankiai Morning Report siuntimui.

Raktas – tik aplinkos kintamasis BREVO_API_KEY (GitHub Secrets). Į išvestį ir žurnalus jis nepatenka.

  python scripts/brevo.py patikra                         – ar raktas veikia, kokie sąrašai
  python scripts/brevo.py kampanija DATA --sarasas 3      – sukuria dienos kampaniją iš public/laiskas-brevo.html
                                                            ir suplanuoja 7:00 Vilniaus laiku (jei vėliau – siunčia iš karto)
  python scripts/brevo.py kalbos                          – KALBA atributas ir angliški sąrašai „… EN“ (žr. kalbos())

Kalbos (2026-11-23): prenumeratorius formoje pasirenka kalbą (atributas KALBA: Lietuvių / English).
Sąrašai #4–#10 (dienos) ir #11 (savaitės apžvalga) bendri abiem kalboms; kalbos() kas rytą prieš
siuntimą perkelia English kontaktus į veidrodinius sąrašus „<sąrašas> EN“. Lietuviška kampanija tų
sąrašų kontaktų negauna, angliška siunčiama tik jiems (--kalba en). Kas KALBA nepasirinko – lietuviškai.
El. pašto adresai į išvestį nerašomi (eigos žurnalai vieši) – tik skaičiai.
"""
import json
import os
import sys
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import requests

API = "https://api.brevo.com/v3"


def call(method: str, path: str, **kw):
    key = os.environ.get("BREVO_API_KEY", "").strip()
    if not key:
        return 0, {"klaida": "BREVO_API_KEY nenustatytas"}
    r = requests.request(method, API + path, headers={"api-key": key, "accept": "application/json"}, timeout=30, **kw)
    try:
        body = r.json() if r.content else {}
    except ValueError:
        body = {"tekstas": r.text[:300]}
    return r.status_code, body


def patikra() -> dict:
    out = {}
    st, b = call("GET", "/contacts/lists", params={"limit": 50})
    out["sąrašai"] = {"http": st, "duomenys": [{"id": l.get("id"), "pavadinimas": l.get("name"), "kontaktų": l.get("uniqueSubscribers", l.get("totalSubscribers"))} for l in b.get("lists", [])] if st == 200 else b}
    st, b = call("GET", "/contacts/folders", params={"limit": 50})
    out["aplankai"] = {"http": st, "duomenys": [{"id": f.get("id"), "pavadinimas": f.get("name")} for f in b.get("folders", [])] if st == 200 else b}
    st, b = call("GET", "/senders")
    out["siuntėjai"] = {"http": st, "duomenys": [{"id": s.get("id"), "el_paštas": s.get("email"), "vardas": s.get("name"), "aktyvus": s.get("active")} for s in b.get("senders", [])] if st == 200 else b}
    st, b = call("GET", "/senders/domains")
    out["domenai"] = {"http": st, "duomenys": [{"domenas": d.get("domain_name"), "patvirtintas": d.get("verified"), "autentifikuotas": d.get("authenticated")} for d in b.get("domains", [])] if st == 200 else b}
    st, b = call("GET", "/emailCampaigns", params={"limit": 5})
    out["kampanijos"] = {"http": st, "kiekis": b.get("count") if st == 200 else b}
    return out


SENDER = {"name": "Morning Report", "email": "morningreport@enerconsult.lt"}
LT = ZoneInfo("Europe/Vilnius")
LANG_ATTR = "KALBA"
LANG_ENUM = [{"value": 1, "label": "Lietuvių"}, {"value": 2, "label": "English"}]
SOURCE_LISTS = range(4, 12)          # #4–#10 – savaitės dienos, #11 – savaitės apžvalga


def lists_all() -> list[dict]:
    out, off = [], 0
    while True:
        st, b = call("GET", "/contacts/lists", params={"limit": 50, "offset": off})
        if st != 200:
            raise RuntimeError(f"sąrašų gauti nepavyko: HTTP {st}")
        part = b.get("lists", [])
        out += part
        if len(part) < 50:
            return out
        off += 50


def mirror_name(name: str) -> str:
    return f"{name} EN"


def mirrors(ls: list[dict]) -> dict[int, int]:
    """{sąrašo id: jo angliško veidrodinio sąrašo id}."""
    by_name = {l.get("name"): l.get("id") for l in ls}
    return {l["id"]: by_name[mirror_name(l["name"])] for l in ls if mirror_name(l.get("name") or "") in by_name}


def members(list_id: int) -> dict[str, dict]:
    """{el. paštas: {"attributes": …, "blacklisted": …}} – tik atmintyje, niekur nerašoma."""
    out, off = {}, 0
    while True:
        st, b = call("GET", f"/contacts/lists/{list_id}/contacts", params={"limit": 500, "offset": off})
        if st != 200:
            raise RuntimeError(f"sąrašo #{list_id} kontaktų gauti nepavyko: HTTP {st}")
        part = b.get("contacts", [])
        for c in part:
            if c.get("email"):
                out[c["email"].strip().lower()] = {"attributes": c.get("attributes") or {}, "blacklisted": bool(c.get("emailBlacklisted"))}
        if len(part) < 500:
            return out
        off += 500


def is_en(attrs: dict) -> bool:
    return str(attrs.get(LANG_ATTR, "")).strip().lower() in ("2", "english", "en", "anglų")


def kalbos() -> dict:
    """KALBA atributas (sukuria, jei nėra) ir veidrodiniai sąrašai „<sąrašas> EN“ su English kontaktais."""
    out = {}
    st, b = call("GET", "/contacts/attributes")
    if st == 200 and not any(a.get("name") == LANG_ATTR for a in b.get("attributes", [])):
        # pasirinkimo (category) atributas: formoje – radijo mygtukai „Lietuvių“ / „English“
        st2, b2 = call("POST", f"/contacts/attributes/category/{LANG_ATTR}", json={"type": "category", "enumeration": LANG_ENUM})
        out["atributas"] = f"sukurtas (HTTP {st2})" if st2 in (200, 201, 204) else {"klaida": st2, "atsakymas": b2}
    else:
        out["atributas"] = "yra" if st == 200 else f"patikrinti nepavyko: HTTP {st}"
    ls = lists_all()
    by_id = {l["id"]: l for l in ls}
    mir = mirrors(ls)
    res = []
    for sid in SOURCE_LISTS:
        src = by_id.get(sid)
        if not src:
            res.append({"sąrašas": sid, "klaida": "nėra"})
            continue
        mid = mir.get(sid)
        if not mid:
            st, b = call("POST", "/contacts/lists", json={"name": mirror_name(src["name"]), "folderId": src.get("folderId")})
            if st not in (200, 201):
                res.append({"sąrašas": sid, "klaida": f"angliško sąrašo sukurti nepavyko: HTTP {st}", "atsakymas": b})
                continue
            mid = b.get("id")
        want = {e for e, c in members(sid).items() if is_en(c["attributes"])}
        have = set(members(mid))
        add, rem = sorted(want - have), sorted(have - want)
        errors = []
        for i in range(0, len(add), 150):
            st, b = call("POST", f"/contacts/lists/{mid}/contacts/add", json={"emails": add[i:i + 150]})
            if st not in (200, 201, 204):
                errors.append(f"pridėti HTTP {st}")
        for i in range(0, len(rem), 150):
            st, b = call("POST", f"/contacts/lists/{mid}/contacts/remove", json={"emails": rem[i:i + 150]})
            if st not in (200, 201, 204):
                errors.append(f"pašalinti HTTP {st}")
        res.append({"sąrašas": sid, "pavadinimas": src["name"], "angliškas": mid, "anglų_kalba": len(want),
                    "pridėta": len(add), "pašalinta": len(rem), **({"klaidos": errors} if errors else {})})
    out["sąrašai"] = res
    return out


def kampanija(day: str, list_id: int, public: str = "public", test: bool = False, weekly: bool = False, at_time: dtime = dtime(7, 0),
              exclude: list | None = None, lang: str = "lt") -> dict:
    """Sukuria ir suplanuoja dienos (arba savaitės apžvalgos) kampaniją. Kartotinis paleidimas tos pačios dienos kampanijos nedubliuoja.
    exclude – sąrašai, kurių kontaktai šios kampanijos negauna (pvz. pirmadienį – savaitės apžvalgos gavėjai);
    lang – „en“ prideda „ EN“ prie kampanijos pavadinimo (angliška versija)."""
    name = f"Morning Report {'savaitės apžvalga ' if weekly else ''}{'bandymas ' if test else ''}{day}{' EN' if lang == 'en' else ''}"
    with open(os.path.join(public, "data.txt"), encoding="utf-8") as f:
        if f.read().strip() != day:
            return {"klaida": f"public/data.txt ne {day} – ataskaita nepagaminta, nesiunčiama"}
    html = open(os.path.join(public, "laiskas-brevo.html"), encoding="utf-8").read()
    subject = open(os.path.join(public, "tema.txt"), encoding="utf-8").read().strip()
    if "{{ unsubscribe }}" not in html:
        return {"klaida": "laiške nėra atsisakymo nuorodos"}
    # gavėjai: angliškai – veidrodiniai „… EN“ sąrašai; lietuviškai – be jų
    try:
        mir = mirrors(lists_all())
    except RuntimeError as exc:
        if lang == "en":
            return {"klaida": str(exc)}
        mir = {}
    if test:                         # bandymas – tiesiai nurodytam sąrašui (pvz. #2 – tik Jonas), bet kuria kalba
        recipients = {"listIds": [list_id]}
        excl = list(exclude or [])
    elif lang == "en":
        if list_id not in mir:
            return {"praleista": f"sąrašas #{list_id} neturi angliško sąrašo"}
        inc = mir[list_id]
        if not any(not c["blacklisted"] for c in members(inc).values()):
            return {"praleista": f"angliškame sąraše #{inc} gavėjų nėra"}
        recipients = {"listIds": [inc]}
        excl = [mir[x] for x in (exclude or []) if x in mir]
    else:
        recipients = {"listIds": [list_id]}
        excl = list(exclude or []) + ([mir[list_id]] if list_id in mir else [])
    if excl:
        recipients["exclusionListIds"] = excl
    # ar šios dienos kampanija jau yra
    for status in ("queued", "sent", "draft", "inProcess"):
        st, b = call("GET", "/emailCampaigns", params={"status": status, "limit": 50, "sort": "desc"})
        if st == 200:
            for c in b.get("campaigns", []):
                if c.get("name") == name:
                    return {"jau_yra": c.get("id"), "būsena": c.get("status")}
    now = datetime.now(LT)
    at = datetime.combine(date.fromisoformat(day), at_time, LT)
    body = {"name": name, "subject": subject, "sender": SENDER, "htmlContent": html,
            "recipients": recipients,
            "inlineImageActivation": False, "mirrorActive": False}
    send_now = (at - now).total_seconds() < 120
    if not send_now:
        body["scheduledAt"] = at.isoformat()
    st, b = call("POST", "/emailCampaigns", json=body)
    if st not in (200, 201):
        return {"klaida": f"kampanijos sukūrimas HTTP {st}", "atsakymas": b}
    cid = b.get("id")
    out = {"kampanija": cid, "pavadinimas": name, "sąrašai": recipients["listIds"], "be_sąrašų": recipients.get("exclusionListIds", [])}
    if send_now:
        st, b = call("POST", f"/emailCampaigns/{cid}/sendNow")
        out["siųsta_iškart"] = st
        if st not in (200, 204):
            out["klaida"] = b
    else:
        out["suplanuota"] = at.isoformat()
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "patikra"
    if cmd == "patikra":
        print(json.dumps(patikra(), ensure_ascii=False, indent=1))
    elif cmd == "kalbos":
        res = kalbos()
        print(json.dumps(res, ensure_ascii=False, indent=1))
        if any("klaida" in r or "klaidos" in r for r in res.get("sąrašai", [])):
            sys.exit(1)
    elif cmd == "kampanija":
        day = sys.argv[2]
        lid = int(sys.argv[sys.argv.index("--sarasas") + 1]) if "--sarasas" in sys.argv else 3
        weekly = "--savaitė" in sys.argv
        lang = sys.argv[sys.argv.index("--kalba") + 1] if "--kalba" in sys.argv else "lt"
        base = "public/en" if lang == "en" else "public"
        pub = sys.argv[sys.argv.index("--public") + 1] if "--public" in sys.argv else (f"{base}/savaitė" if weekly else base)
        hh, mm = (sys.argv[sys.argv.index("--laikas") + 1] if "--laikas" in sys.argv else "07:00").split(":")
        exclude = [int(x) for x in sys.argv[sys.argv.index("--be") + 1].split(",") if x.strip()] if "--be" in sys.argv else []
        res = kampanija(day, lid, pub, "--bandymas" in sys.argv, weekly, dtime(int(hh), int(mm)), exclude, lang)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        if "klaida" in res:
            sys.exit(1)
    else:
        sys.exit(f"Nežinoma komanda: {cmd}")
