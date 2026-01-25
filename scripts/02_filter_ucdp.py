#!/usr/bin/env python3
"""
Filter UCDP GED events into a smaller, map-friendly JSON.

Reads:
  - data/raw/ucdp_events_ukr_ru.json

Writes:
  - data/processed/ucdp_events_filtered.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def normalize_text_fields(ev: Dict[str, Any]) -> str:
    text_keys = [
        "conflict_name",
        "dyad_name",
        "side_a",
        "side_b",
        "where_coordinates",
        "adm_1",
        "adm_2",
        "location",
        "source_headline",
        "source_original",
        "notes",
        "summary",
    ]
    parts: List[str] = []
    for k in text_keys:
        v = ev.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    return " | ".join(parts).lower()


def event_in_date_range(ev: Dict[str, Any], start: Optional[date], end: Optional[date]) -> bool:
    ds = parse_date(ev.get("date_start") or ev.get("date") or "")
    de = parse_date(ev.get("date_end") or ev.get("date") or "") or ds

    # If date is missing, keep it by default (avoid accidental full drop)
    if ds is None and de is None:
        return True

    if ds is None:
        ds = de
    if de is None:
        de = ds

    if start and de and de < start:
        return False
    if end and ds and ds > end:
        return False
    return True


def matches_conflict(ev: Dict[str, Any], conflict_regex: Optional[re.Pattern]) -> bool:
    if not conflict_regex:
        return True
    name = str(ev.get("conflict_name") or "")
    return bool(conflict_regex.search(name))


def matches_types(ev: Dict[str, Any], allowed_types: Optional[set[int]]) -> bool:
    if not allowed_types:
        return True
    tov = safe_int(ev.get("type_of_violence"), default=-1)
    return tov in allowed_types


def matches_min_best(ev: Dict[str, Any], min_best: Optional[int]) -> bool:
    if min_best is None:
        return True
    return safe_int(ev.get("best"), default=0) >= min_best


def matches_exclude_keywords(ev: Dict[str, Any], exclude_keywords: List[str]) -> bool:
    if not exclude_keywords:
        return True
    blob = normalize_text_fields(ev)
    for kw in exclude_keywords:
        if kw and kw.lower() in blob:
            return False
    return True


def filter_events(
    events: List[Dict[str, Any]],
    conflict_pattern: Optional[str],
    allowed_types: Optional[List[int]],
    start: Optional[date],
    end: Optional[date],
    min_best: Optional[int],
    exclude_keywords: List[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    conflict_regex = re.compile(conflict_pattern, re.IGNORECASE) if conflict_pattern else None
    allowed_set = set(allowed_types) if allowed_types else None

    kept: List[Dict[str, Any]] = []
    dropped_reasons: Counter[str] = Counter()

    for ev in events:
        if not matches_conflict(ev, conflict_regex):
            dropped_reasons["conflict_mismatch"] += 1
            continue
        if not matches_types(ev, allowed_set):
            dropped_reasons["type_filtered_out"] += 1
            continue
        if not event_in_date_range(ev, start, end):
            dropped_reasons["date_out_of_range"] += 1
            continue
        if not matches_min_best(ev, min_best):
            dropped_reasons["below_min_best"] += 1
            continue
        if not matches_exclude_keywords(ev, exclude_keywords):
            dropped_reasons["excluded_by_keyword"] += 1
            continue
        kept.append(ev)

    type_counts = Counter(safe_int(e.get("type_of_violence"), -1) for e in kept)
    totals = {
        "best_total": sum(safe_int(e.get("best"), 0) for e in kept),
        "deaths_civilians_total": sum(safe_int(e.get("deaths_civilians"), 0) for e in kept),
        "deaths_a_total": sum(safe_int(e.get("deaths_a"), 0) for e in kept),
        "deaths_b_total": sum(safe_int(e.get("deaths_b"), 0) for e in kept),
    }

    summary = {
        "input_events": len(events),
        "kept_events": len(kept),
        "dropped_events": len(events) - len(kept),
        "dropped_by_reason": dict(dropped_reasons),
        "kept_type_of_violence_counts": dict(type_counts),
        "kept_totals": totals,
        "filters": {
            "conflict_pattern": conflict_pattern,
            "allowed_types": allowed_types,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "min_best": min_best,
            "exclude_keywords": exclude_keywords,
        },
    }
    return kept, summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Filter UCDP events into a smaller JSON for mapping.")
    ap.add_argument("-i", "--input", default="data/raw/ucdp_events_ukr_ru.json", help="Input JSON path")
    ap.add_argument("-o", "--output", default="data/processed/ucdp_events_filtered.json", help="Output JSON path")

    ap.add_argument("--conflict", default=r"Russia\s*-\s*Ukraine", help="Regex applied to conflict_name")
    ap.add_argument("--types", default="1,3", help="Allowed type_of_violence comma list. Empty = no filter.")
    ap.add_argument("--start", default="2022-02-24", help="Start date YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", default="", help="End date YYYY-MM-DD (inclusive). Empty = no limit.")
    ap.add_argument("--min-best", type=int, default=0, help="Minimum 'best' casualties to keep")
    ap.add_argument("--exclude", default="", help="Comma-separated keywords to exclude if found in text fields")

    args = ap.parse_args()

    start_d = parse_date(args.start) if args.start else None
    end_d = parse_date(args.end) if args.end else None

    types_list: List[int] = []
    if args.types.strip():
        for p in args.types.split(","):
            p = p.strip()
            if p:
                types_list.append(int(p))

    exclude_keywords = [x.strip() for x in args.exclude.split(",") if x.strip()]

    in_path = Path(args.input)
    out_path = Path(args.output)
    ensure_dir(out_path.parent)

    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    events = data.get("events")
    if not isinstance(events, list):
        print("ERROR: input JSON must contain a top-level 'events' list")
        return 2

    kept, summary = filter_events(
        events=events,
        conflict_pattern=args.conflict if args.conflict else None,
        allowed_types=types_list if types_list else None,
        start=start_d,
        end=end_d,
        min_best=args.min_best if args.min_best is not None else None,
        exclude_keywords=exclude_keywords,
    )

    out = {
        "metadata": data.get("metadata", {}),
        "generated_at_utc": utc_now_str(),
        "summary": summary,
        "events": kept,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(
        f"done input={summary['input_events']} kept={summary['kept_events']} "
        f"dropped={summary['dropped_events']} output={out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
