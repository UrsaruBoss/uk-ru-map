#!/usr/bin/env python3
"""
Fetch UCDP GED events for a selected set of countries since a start date.

Default:
  - Countries: Ukraine (369), Russia (365)
  - StartDate: 2022-02-24
Outputs:
  - data/raw/ucdp_events_ukr_ru.json
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


BASE_URL = "https://ucdpapi.pcr.uu.se/api/gedevents/25.1"


def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 30, retries: int = 3) -> Dict[str, Any]:
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0 * attempt)
            else:
                raise
    raise last_err  # for type checkers


def fetch_ucdp_events(
    countries: str,
    start_date: str,
    pagesize: int = 1000,
    sleep_s: float = 0.5,
    timeout: int = 30,
) -> Dict[str, Any]:
    params = {
        "pagesize": pagesize,
        "Country": countries,
        "StartDate": start_date,
    }

    all_events: List[Dict[str, Any]] = []
    next_url: Optional[str] = BASE_URL
    page_num = 1
    total_pages: Any = None

    while next_url:
        if page_num == 1:
            data = request_json(next_url, params=params, timeout=timeout)
        else:
            data = request_json(next_url, params=None, timeout=timeout)

        events = data.get("Result", []) or []
        all_events.extend(events)

        total_pages = data.get("TotalPages", total_pages)
        next_url = data.get("NextPageUrl")

        print(f"page={page_num} events={len(events)} total_collected={len(all_events)} total_pages={total_pages}")
        page_num += 1

        if next_url:
            time.sleep(sleep_s)

    return {
        "metadata": {
            "source": "UCDP GED API v25.1",
            "downloaded_at_utc": utc_now_str(),
            "query": params,
            "count": len(all_events),
            "total_pages": total_pages,
        },
        "events": all_events,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--countries", default="369,365", help="Comma-separated UCDP country codes (e.g., 369,365)")
    parser.add_argument("--start-date", default="2022-02-24", help="Start date YYYY-MM-DD")
    parser.add_argument("--pagesize", type=int, default=1000, help="API pagesize (max 1000)")
    parser.add_argument("--sleep", type=float, default=0.5, help="Sleep between pages (seconds)")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout (seconds)")
    parser.add_argument("--out", default="data/raw/ucdp_events_ukr_ru.json", help="Output JSON path")
    args = parser.parse_args()

    out_path = Path(args.out)
    ensure_dir(out_path.parent)

    try:
        dataset = fetch_ucdp_events(
            countries=args.countries,
            start_date=args.start_date,
            pagesize=args.pagesize,
            sleep_s=args.sleep,
            timeout=args.timeout,
        )
    except Exception as e:
        print(f"ERROR: fetch failed: {e}")
        return 2

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"saved: {out_path} (events={dataset['metadata']['count']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
