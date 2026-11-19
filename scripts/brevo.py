#!/usr/bin/env python3
"""Brevo naujienlaiškio įrankiai Morning Report siuntimui.

Raktas – tik aplinkos kintamasis BREVO_API_KEY (GitHub Secrets). Į išvestį ir žurnalus jis nepatenka.

  python scripts/brevo.py patikra      – ar raktas veikia, kokie sąrašai, siuntėjai ir domenai
"""
import json
import os
import sys

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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "patikra"
    if cmd == "patikra":
        print(json.dumps(patikra(), ensure_ascii=False, indent=1))
    else:
        sys.exit(f"Nežinoma komanda: {cmd}")
