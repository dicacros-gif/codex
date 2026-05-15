from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.collectors.sec13f import collect_13f
from src.collectors.tradingview import collect_tradingview
from src.enricher import enrich_records, merge_signal_rows
from src.report_builder import write_outputs
from src.scorer import build_sections, score_records
from src.utils.io import ensure_dir, write_json


KST = ZoneInfo("Asia/Seoul")


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    now_kst = datetime.now(timezone.utc).astimezone(KST)
    run_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else now_kst.date()
    generated_at_kst = now_kst.strftime("%Y-%m-%d %H:%M:%S")

    raw_dir = ensure_dir(root / "data" / "raw" / run_date.isoformat())
    processed_dir = ensure_dir(root / "data" / "processed")
    ensure_dir(root / "reports")

    tradingview = collect_tradingview(raw_dir=raw_dir, run_date=run_date, limit=args.limit)
    write_json(raw_dir / "tradingview_sections.json", tradingview)

    merged = merge_signal_rows(tradingview)
    enriched = enrich_records(merged, raw_dir=raw_dir, max_kr=args.max_kr_enrich)
    scored = score_records(enriched)
    write_json(processed_dir / "scored_records.json", scored)

    sec13f = collect_13f(
        institutions_path=root / "config" / "institutions_13f.json",
        raw_dir=raw_dir,
        run_date=run_date,
        quarters=args.sec_quarters,
    )
    sections = build_sections(scored, sec13f)
    write_outputs(
        root=root,
        run_date=run_date,
        sections=sections,
        records=scored,
        generated_at_kst=generated_at_kst,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect daily stock trend data and build static reports.")
    parser.add_argument("--root", default=os.getenv("PROJECT_ROOT", "."))
    parser.add_argument("--date", default=os.getenv("RUN_DATE"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("TRADINGVIEW_LIMIT", "250")))
    parser.add_argument("--max-kr-enrich", type=int, default=int(os.getenv("MAX_KR_ENRICH", "80")))
    parser.add_argument("--sec-quarters", type=int, default=int(os.getenv("SEC_13F_QUARTERS", "5")))
    return parser.parse_args()


if __name__ == "__main__":
    main()

