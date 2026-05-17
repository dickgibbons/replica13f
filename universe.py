"""Persistent fund universe (name + CIK) stored as JSON."""
from __future__ import annotations
import json
import os
import re

ROOT = os.path.dirname(__file__)
DATA_DIR = os.path.join(ROOT, "data")
UNIVERSE_PATH = os.path.join(DATA_DIR, "universe.json")

SEED = [
    {"name": "Pershing Square Capital Mgmt", "cik": "0001336528", "ww_ref_5yr": 26.1},
    {"name": "Maverick Capital Ltd", "cik": "0000934639", "ww_ref_5yr": 24.3},
    {"name": "Lone Pine Capital LLC", "cik": "0001061165", "ww_ref_5yr": 23.5},
    {"name": "Coatue Management LLC", "cik": "0001135730", "ww_ref_5yr": 22.9},
    {"name": "Viking Global Investors LP", "cik": "0001103804", "ww_ref_5yr": 22.6},
    {"name": "Whale Rock Capital Mgmt", "cik": "0001387322", "ww_ref_5yr": 21.9},
    {"name": "Millstreet Capital Mgmt", "cik": "0001590729", "ww_ref_5yr": 20.6},
]


def _norm_cik(cik: str) -> str:
    digits = re.sub(r"\D", "", str(cik))
    if not digits:
        raise ValueError("CIK must contain digits")
    return digits.zfill(10)


def _validate(fund: dict) -> dict:
    name = (fund.get("name") or "").strip()
    if not name:
        raise ValueError("Fund name is required")
    cik = _norm_cik(fund["cik"])
    out = {"name": name, "cik": cik}
    ref = fund.get("ww_ref_5yr")
    if ref is not None and ref != "":
        out["ww_ref_5yr"] = float(ref)
    return out


def load() -> list[dict]:
    if not os.path.exists(UNIVERSE_PATH):
        bootstrap_from_seed()
    with open(UNIVERSE_PATH) as f:
        return json.load(f)


def save(funds: list[dict]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    validated = [_validate(f) for f in funds]
    with open(UNIVERSE_PATH, "w") as f:
        json.dump(validated, f, indent=2)
        f.write("\n")


def bootstrap_from_seed() -> list[dict]:
    save(SEED)
    return list(SEED)


def add(fund: dict) -> list[dict]:
    row = _validate(fund)
    funds = load()
    funds = [f for f in funds if f["cik"] != row["cik"]]
    funds.append(row)
    save(funds)
    return funds


def update(cik: str, fund: dict) -> list[dict]:
    cik = _norm_cik(cik)
    row = _validate({**fund, "cik": cik})
    funds = load()
    found = False
    out = []
    for f in funds:
        if f["cik"] == cik:
            out.append(row)
            found = True
        else:
            out.append(f)
    if not found:
        out.append(row)
    save(out)
    return out


def remove(cik: str) -> list[dict]:
    cik = _norm_cik(cik)
    funds = [f for f in load() if f["cik"] != cik]
    save(funds)
    return funds
