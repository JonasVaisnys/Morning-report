#!/usr/bin/env python3
"""Brevo naujienlaiškio įrankiai Morning Report siuntimui.

Raktas – tik aplinkos kintamasis BREVO_API_KEY (GitHub Secrets). Į išvestį ir žurnalus jis nepatenka.

  python scripts/brevo.py patikra                         – ar raktas veikia, kokie sąrašai
  python scripts/brevo.py kampanija DATA --sarasas 3      – sukuria dienos kampaniją iš public/laiskas-brevo.html
                                                            ir suplanuoja 7:00 Vilniaus laiku (jei vėliau – siunčia iš karto)
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


def kampanija(day: str, list_id: int, public: str = "public", test: bool = False) -> dict:
    """Sukuria ir suplanuoja dienos kampaniją. Kartotinis paleidimas tos pačios dienos kampanijos nedubliuoja."""
    name = f"Morning Report {'bandymas ' if test else ''}{day}"
    with open(os.path.join(public, "data.txt"), encoding="utf-8") as f:
        if f.read().strip() != day:
            return {"klaida": f"public/data.txt ne {day} – ataskaita nepagaminta, nesiunčiama"}
    html = open(os.path.join(public, "laiskas-brevo.html"), encoding="utf-8").read()
    subject = open(os.path.join(public, "tema.txt"), encoding="utf-8").read().strip()
    if "{{ unsubscribe }}" not in html:
        return {"klaida": "laiške nėra atsisakymo nuorodos"}
    # ar šios dienos kampanija jau yra
    for status in ("queued", "sent", "draft", "inProcess"):
        st, b = call("GET", "/emailCampaigns", params={"status": status, "limit": 50, "sort": "desc"})
        if st == 200:
            for c in b.get("campaigns", []):
                if c.get("name") == name:
                    return {"jau_yra": c.get("id"), "būsena": c.get("status")}
    now = datetime.now(LT)
    at = datetime.combine(date.fromisoformat(day), dtime(7, 0), LT)
    body = {"name": name, "subject": subject, "sender": SENDER, "htmlContent": html,
            "recipients": {"listIds": [list_id]}, "inlineImageActivation": False, "mirrorActive": False}
    send_now = (at - now).total_seconds() < 120
    if not send_now:
        body["scheduledAt"] = at.isoformat()
    st, b = call("POST", "/emailCampaigns", json=body)
    if st not in (200, 201):
        return {"klaida": f"kampanijos sukūrimas HTTP {st}", "atsakymas": b}
    cid = b.get("id")
    out = {"kampanija": cid, "pavadinimas": name, "sąrašas": list_id}
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
    elif cmd == "kampanija":
        day = sys.argv[2]
        lid = int(sys.argv[sys.argv.index("--sarasas") + 1]) if "--sarasas" in sys.argv else 3
        pub = sys.argv[sys.argv.index("--public") + 1] if "--public" in sys.argv else "public"
        res = kampanija(day, lid, pub, "--bandymas" in sys.argv)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        if "klaida" in res:
            sys.exit(1)
    else:
        sys.exit(f"Nežinoma komanda: {cmd}")
