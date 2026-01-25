#!/usr/bin/env python3
"""
Fetch and parse equipment loss summaries (Oryx pages) and personnel counters.

Outputs:
  - data/processed/war_stats.json

Notes:
  - Oryx is an open-source intelligence blog. HTML structure may change.
  - Estimates are based on a simple price model and status multipliers.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup


URL_ORYX_RU = "https://www.oryxspioenkop.com/2022/02/attack-on-europe-documenting-equipment.html"
URL_ORYX_UA = "https://www.oryxspioenkop.com/2022/02/attack-on-europe-documenting-ukrainian.html"
URL_RU_JSON_PERSONNEL = (
    "https://raw.githubusercontent.com/PetroIvaniuk/2022-Ukraine-Russia-War-Dataset/main/data/russia_losses_personnel.json"
)
URL_UA_LOSSES_SOLDIERS = "https://ualosses.org/en/soldiers/"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Money model
STATUS_MULTIPLIER = {
    "destroyed": 1.00,
    "damaged": 0.45,
    "abandoned": 0.75,
    "captured": 0.65,
}

PRICE_USD = {
    "Tanks": 3_000_000,
    "Armoured Fighting Vehicles": 1_200_000,
    "Infantry Fighting Vehicles": 1_800_000,
    "Armoured Personnel Carriers": 650_000,
    "Mine-Resistant Ambush Protected (MRAP) Vehicles": 450_000,
    "Infantry Mobility Vehicles": 180_000,
    "Command Posts And Communications Stations": 900_000,
    "Engineering Vehicles And Equipment": 500_000,
    "Unmanned Ground Vehicles": 150_000,
    "Self-Propelled Anti-Tank Missile Systems": 900_000,
    "Artillery Systems": 1_500_000,
    "Self-Propelled Artillery": 2_800_000,
    "Towed Artillery": 750_000,
    "Multiple Rocket Launchers": 4_000_000,
    "Anti-Aircraft Warfare Systems": 12_000_000,
    "Self-Propelled Anti-Aircraft Guns": 2_500_000,
    "Radars": 8_000_000,
    "Aircraft": 25_000_000,
    "Helicopters": 16_000_000,
    "Unmanned Aerial Vehicles": 120_000,
    "Reconnaissance Unmanned Aerial Vehicles": 80_000,
    "Combat Unmanned Aerial Vehicles": 1_500_000,
    "Naval Ships": 35_000_000,
    "Trucks, Vehicles and Jeeps": 120_000,
    "Logistics Trains": 2_000_000,
    "Cruise Missiles": 1_000_000,
    "Ballistic Missiles": 3_000_000,
}
DEFAULT_PRICE_USD = 250_000

HEADER_RE = re.compile(
    r"^(?P<cat>.+?)\s*\((?P<total>[\d,]+),\s*of which\s*(?P<rest>.+)\)\s*$",
    re.IGNORECASE,
)
PAIR_RE = re.compile(r"(destroyed|damaged|abandoned|captured)\s*:\s*([\d,]+)", re.IGNORECASE)


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_text(url: str, headers: Dict[str, str], timeout: int, retries: int) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0 * attempt)
            else:
                raise
    raise last_err  # pragma: no cover


def request_json(url: str, headers: Dict[str, str], timeout: int, retries: int) -> Any:
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0 * attempt)
            else:
                raise
    raise last_err  # pragma: no cover


def parse_oryx_categories(html: str) -> Dict[str, Dict[str, int]]:
    soup = BeautifulSoup(html, "lxml")
    out: Dict[str, Dict[str, int]] = {}

    for h in soup.find_all(["h2", "h3"]):
        t = h.get_text(" ", strip=True)
        if not t:
            continue
        if "of which" not in t.lower() or "(" not in t:
            continue

        m = HEADER_RE.match(t)
        if not m:
            continue

        cat = m.group("cat").strip()
        total = int(m.group("total").replace(",", ""))
        rest = m.group("rest")
        pairs = {k.lower(): int(v.replace(",", "")) for k, v in PAIR_RE.findall(rest)}

        out[cat] = {
            "total": total,
            "destroyed": pairs.get("destroyed", 0),
            "damaged": pairs.get("damaged", 0),
            "abandoned": pairs.get("abandoned", 0),
            "captured": pairs.get("captured", 0),
        }

    # fallback: try scanning full text if headings change
    if not out:
        text = soup.get_text("\n", strip=True)
        for line in text.splitlines():
            if "of which" not in line.lower():
                continue
            m = HEADER_RE.match(line.strip())
            if not m:
                continue
            cat = m.group("cat").strip()
            total = int(m.group("total").replace(",", ""))
            rest = m.group("rest")
            pairs = {k.lower(): int(v.replace(",", "")) for k, v in PAIR_RE.findall(rest)}
            out[cat] = {
                "total": total,
                "destroyed": pairs.get("destroyed", 0),
                "damaged": pairs.get("damaged", 0),
                "abandoned": pairs.get("abandoned", 0),
                "captured": pairs.get("captured", 0),
            }

    return out


def money_score(categories: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    total_usd = 0.0
    enriched: Dict[str, Any] = {}

    for cat, d in categories.items():
        unit_price = PRICE_USD.get(cat, DEFAULT_PRICE_USD)
        usd = 0.0
        for status in ("destroyed", "damaged", "abandoned", "captured"):
            usd += d.get(status, 0) * unit_price * STATUS_MULTIPLIER[status]

        enriched[cat] = {**d, "unit_price_usd": unit_price, "usd_estimated": int(usd)}
        total_usd += usd

    return {
        "categories": enriched,
        "total_usd_estimated": int(total_usd),
        "total_billion_usd_estimated": round(total_usd / 1_000_000_000, 3),
    }


def get_live_ru_personnel_from_dataset(headers: Dict[str, str], timeout: int, retries: int) -> Dict[str, Any]:
    data = request_json(URL_RU_JSON_PERSONNEL, headers=headers, timeout=timeout, retries=retries)
    last = data[-1] if isinstance(data, list) and data else {}
    return {
        "day": last.get("day"),
        "date": last.get("date"),
        "personnel": last.get("personnel"),
        "personnel_info": last.get("personnel*"),
    }


def get_live_ua_personnel_ualosses(headers: Dict[str, str], timeout: int, retries: int) -> Optional[int]:
    try:
        html = request_text(URL_UA_LOSSES_SOLDIERS, headers=headers, timeout=timeout, retries=retries)
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)

        m = re.search(r"(\d[\d,\. ]*)\s*people", text, re.IGNORECASE)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)))

        m = re.search(r"total\s*[:\-]\s*(\d[\d,\. ]*)", text, re.IGNORECASE)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)))

        return None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/processed/war_stats.json", help="Output JSON path")
    ap.add_argument("--timeout", type=int, default=25, help="HTTP timeout seconds")
    ap.add_argument("--retries", type=int, default=3, help="HTTP retries")
    ap.add_argument("--sleep", type=float, default=0.8, help="Sleep between Oryx requests (seconds)")
    args = ap.parse_args()

    out_path = Path(args.out)
    ensure_dir(out_path.parent)

    headers = dict(DEFAULT_HEADERS)

    # RU equipment
    print("fetch: oryx_ru")
    ru_html = request_text(URL_ORYX_RU, headers=headers, timeout=args.timeout, retries=args.retries)
    ru_cats = parse_oryx_categories(ru_html)
    ru_score = money_score(ru_cats)

    time.sleep(args.sleep)

    # UA equipment
    print("fetch: oryx_ua")
    ua_html = request_text(URL_ORYX_UA, headers=headers, timeout=args.timeout, retries=args.retries)
    ua_cats = parse_oryx_categories(ua_html)
    ua_score = money_score(ua_cats)

    # Personnel
    print("fetch: ru_personnel_dataset")
    ru_personnel = get_live_ru_personnel_from_dataset(headers=headers, timeout=args.timeout, retries=args.retries)

    print("fetch: ua_personnel_ualosses")
    ua_personnel = get_live_ua_personnel_ualosses(headers=headers, timeout=args.timeout, retries=args.retries)

    payload = {
        "timestamp_utc": utc_now_iso(),
        "sources": {
            "oryx_ru": URL_ORYX_RU,
            "oryx_ua": URL_ORYX_UA,
            "ru_personnel_dataset": URL_RU_JSON_PERSONNEL,
            "ua_personnel_ualosses": URL_UA_LOSSES_SOLDIERS,
        },
        "money_model": {
            "status_multiplier": STATUS_MULTIPLIER,
            "default_price_usd": DEFAULT_PRICE_USD,
            "prices_usd": PRICE_USD,
        },
        "russia": {"personnel": ru_personnel, "equipment_oryx": ru_score},
        "ukraine": {"personnel_dead_ualosses": ua_personnel, "equipment_oryx": ua_score},
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"saved: {out_path}")
    print(f"equipment_estimate_billion_usd ru={ru_score['total_billion_usd_estimated']} ua={ua_score['total_billion_usd_estimated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
