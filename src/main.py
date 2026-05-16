from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.collectors.naver_supply import collect_kr_supply_candidates
from src.collectors.sec13f import collect_13f
from src.collectors.tradingview import collect_tradingview
from src.enricher import enrich_records, merge_signal_rows
from src.report_builder import write_outputs
from src.scorer import build_sections, score_records
from src.utils.io import ensure_dir, read_json, strip_empty, write_json


KST = ZoneInfo("Asia/Seoul")


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    now_kst = datetime.now(timezone.utc).astimezone(KST)
    run_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else now_kst.date()
    generated_at_kst = now_kst.strftime("%Y-%m-%d %H:%M:%S")

    raw_root = ensure_dir(root / "data" / "raw")
    raw_dir = ensure_dir(raw_root / run_date.isoformat())
    processed_dir = ensure_dir(root / "data" / "processed")
    dated_processed_dir = ensure_dir(processed_dir / run_date.isoformat())
    ensure_dir(root / "reports")

    tradingview = collect_tradingview(raw_dir=raw_dir, run_date=run_date, limit=args.limit)
    tradingview.update(collect_kr_supply_candidates(raw_dir=raw_dir, run_date=run_date, limit_per_group=args.supply_limit))
    write_json(raw_dir / "tradingview_sections.json", tradingview)

    merged = merge_signal_rows(tradingview)
    enriched = enrich_records(merged, raw_dir=raw_dir, max_kr=args.max_kr_enrich)
    scored = score_records(enriched)
    daily_scored = strip_empty(scored)
    write_json(dated_processed_dir / "scored_records.json", daily_scored)
    scored_history = _merge_record_history(
        _as_list(read_json(processed_dir / "scored_records_history.json", [])),
        _as_list(daily_scored),
    )
    write_json(processed_dir / "scored_records_history.json", strip_empty(scored_history))
    write_json(processed_dir / "scored_records.json", strip_empty(scored_history))

    sec13f = collect_13f(
        institutions_path=root / "config" / "institutions_13f.json",
        raw_dir=raw_dir,
        run_date=run_date,
        quarters=args.sec_quarters,
    )
    daily_sec13f = strip_empty(sec13f)
    write_json(dated_processed_dir / "sec13f_aggregate.json", daily_sec13f)
    sec13f_history = _merge_record_history(
        _as_list(read_json(processed_dir / "sec13f_history.json", [])),
        _as_list(daily_sec13f),
    )
    write_json(processed_dir / "sec13f_history.json", strip_empty(sec13f_history))

    sections = build_sections(scored_history, sec13f_history)
    write_outputs(
        root=root,
        run_date=run_date,
        sections=sections,
        records=scored_history,
        generated_at_kst=generated_at_kst,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect daily stock trend data and build static reports.")
    parser.add_argument("--root", default=os.getenv("PROJECT_ROOT", "."))
    parser.add_argument("--date", default=os.getenv("RUN_DATE"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("TRADINGVIEW_LIMIT", "250")))
    parser.add_argument("--max-kr-enrich", type=int, default=int(os.getenv("MAX_KR_ENRICH", "80")))
    parser.add_argument("--sec-quarters", type=int, default=int(os.getenv("SEC_13F_QUARTERS", "5")))
    parser.add_argument("--supply-limit", type=int, default=int(os.getenv("NAVER_SUPPLY_LIMIT", "30")))
    return parser.parse_args()


def _as_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _merge_record_history(existing: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in current + existing:
        key = _history_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(dict(row))
    merged.sort(
        key=lambda row: (
            str(row.get("date") or ""),
            _sort_number(row.get("investment_priority_score") or row.get("famous_13f_score")),
            str(row.get("ticker") or row.get("cusip") or row.get("company_name") or ""),
        ),
        reverse=True,
    )
    return merged


def _history_key(row: dict[str, Any]) -> str | None:
    row_date = str(row.get("date") or "").strip()
    country = str(row.get("country_code") or row.get("country") or "").strip().upper()
    ticker = str(row.get("ticker") or "").strip().upper()
    cusip = str(row.get("cusip") or "").strip().upper()
    company = str(row.get("company_name") or "").strip().upper()
    if ticker:
        return f"{row_date}|{country}|{ticker}"
    if cusip:
        return f"{row_date}|CUSIP|{cusip}"
    if company:
        return f"{row_date}|COMPANY|{company}"
    return None


def _sort_number(value: object) -> float:
    try:
        return float(value) if value not in (None, "") else -999.0
    except (TypeError, ValueError):
        return -999.0


if __name__ == "__main__":
    main()
