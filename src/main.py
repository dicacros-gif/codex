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
from src.scorer import build_sections, normalize_score_scales, score_records
from src.utils.io import ensure_dir, read_json, strip_empty, write_json


KST = ZoneInfo("Asia/Seoul")
HISTORY_META_FIELDS = {"first_seen_date", "last_seen_date", "seen_count", "seen_dates"}


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
    scored_history = normalize_score_scales(_merge_record_history(
        _as_list(read_json(processed_dir / "scored_records_history.json", [])),
        _as_list(daily_scored),
    ))
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
    sec13f_history = normalize_score_scales(_merge_record_history(
        _as_list(read_json(processed_dir / "sec13f_history.json", [])),
        _as_list(daily_sec13f),
    ))
    write_json(processed_dir / "sec13f_history.json", strip_empty(sec13f_history))
    write_json(processed_dir / "run_status.json", strip_empty({
        "run_date": run_date.isoformat(),
        "generated_at_kst": generated_at_kst,
        "run_context": "github_actions" if os.getenv("GITHUB_ACTIONS") == "true" else "local",
        "history_merge_mode": "update_existing_security_and_append_new_security",
        "daily_stock_rows": len(daily_scored),
        "cumulative_stock_rows": len(scored_history),
        "daily_13f_rows": len(daily_sec13f),
        "cumulative_13f_rows": len(sec13f_history),
    }))

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
    by_security: dict[str, dict[str, Any]] = {}
    for row in existing + current:
        key = _history_key(row)
        if not key:
            continue
        by_security[key] = _merge_security_row(by_security.get(key), row)
    merged = list(by_security.values())
    merged.sort(
        key=lambda row: (
            str(row.get("last_seen_date") or row.get("date") or ""),
            _sort_number(row.get("investment_priority_score") or row.get("famous_13f_score")),
            str(row.get("ticker") or row.get("cusip") or row.get("company_name") or ""),
        ),
        reverse=True,
    )
    return merged


def _history_key(row: dict[str, Any]) -> str | None:
    country = str(row.get("country_code") or row.get("country") or "").strip().upper()
    ticker = str(row.get("ticker") or "").strip().upper()
    cusip = str(row.get("cusip") or "").strip().upper()
    company = str(row.get("company_name") or "").strip().upper()
    if ticker:
        return f"{country}|{ticker}"
    if cusip:
        return f"CUSIP|{cusip}"
    if company:
        return f"COMPANY|{company}"
    return None


def _merge_security_row(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        merged = _non_empty_row(incoming)
    else:
        merged = dict(existing)
        prefer_incoming = _latest_seen_date(incoming) >= _latest_seen_date(existing)
        for key, value in _non_empty_row(incoming).items():
            if key in HISTORY_META_FIELDS:
                continue
            if prefer_incoming or merged.get(key) in (None, "", []):
                merged[key] = value

    seen_dates = _collect_seen_dates(existing, incoming)
    if seen_dates:
        merged["first_seen_date"] = seen_dates[0]
        merged["last_seen_date"] = seen_dates[-1]
        merged["seen_dates"] = seen_dates
        merged["seen_count"] = len(seen_dates)
        merged["date"] = seen_dates[-1]
    return merged


def _non_empty_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [])}


def _collect_seen_dates(*rows: dict[str, Any] | None) -> list[str]:
    dates: set[str] = set()
    for row in rows:
        if not row:
            continue
        for key in ("first_seen_date", "last_seen_date", "date"):
            value = row.get(key)
            if value not in (None, ""):
                dates.add(str(value)[:10])
        for value in row.get("seen_dates") or []:
            if value not in (None, ""):
                dates.add(str(value)[:10])
    return sorted(dates)


def _latest_seen_date(row: dict[str, Any]) -> str:
    dates = _collect_seen_dates(row)
    return dates[-1] if dates else ""


def _sort_number(value: object) -> float:
    try:
        return float(value) if value not in (None, "") else -999.0
    except (TypeError, ValueError):
        return -999.0


if __name__ == "__main__":
    main()
