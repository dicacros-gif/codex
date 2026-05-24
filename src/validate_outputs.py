from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    run_date = args.date or datetime.now(timezone.utc).astimezone(KST).date().isoformat()

    run_status = _read_json(root / "data" / "processed" / "run_status.json")
    _require(run_status.get("run_date") == run_date, f"run_status.json run_date is not {run_date}")
    _require(
        run_status.get("history_merge_mode") == "update_existing_security_and_append_new_security",
        "history merge mode is not cumulative update-by-security",
    )
    if os.getenv("GITHUB_ACTIONS") == "true":
        _require(run_status.get("run_context") == "github_actions", "workflow did not record github_actions context")

    daily_path = root / "data" / "processed" / run_date / "scored_records.json"
    _require(daily_path.exists(), f"missing daily processed file: {daily_path}")

    scored = _read_json(root / "data" / "processed" / "scored_records.json")
    scored_history = _read_json(root / "data" / "processed" / "scored_records_history.json")
    sec13f_history = _read_json(root / "data" / "processed" / "sec13f_history.json")
    _require(isinstance(scored, list), "scored_records.json is not a list")
    _require(isinstance(scored_history, list), "scored_records_history.json is not a list")
    _require(isinstance(sec13f_history, list), "sec13f_history.json is not a list")

    _assert_unique(scored, "scored_records.json")
    _assert_unique(scored_history, "scored_records_history.json")
    _assert_unique(sec13f_history, "sec13f_history.json")
    _assert_has_latest_seen(scored, run_date, "scored_records.json")
    _assert_has_latest_seen(sec13f_history, run_date, "sec13f_history.json")

    for forbidden in (
        root / "reports" / "latest.json",
        root / "reports" / "latest.csv",
        root / "data" / "processed" / "latest.json",
        root / "data" / "processed" / "latest.csv",
    ):
        _require(not forbidden.exists(), f"forbidden output exists: {forbidden}")
    _require((root / "reports" / "latest.xlsx").exists(), "reports/latest.xlsx is missing")
    _require((root / "index.html").exists(), "index.html is missing")

    print(
        "validated cumulative server update:",
        f"run_date={run_date}",
        f"stocks={len(scored)}",
        f"sec13f={len(sec13f_history)}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate generated daily stock trend outputs.")
    parser.add_argument("--root", default=os.getenv("PROJECT_ROOT", "."))
    parser.add_argument("--date", default=os.getenv("RUN_DATE"))
    return parser.parse_args()


def _read_json(path: Path) -> Any:
    _require(path.exists(), f"missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_unique(rows: list[dict[str, Any]], label: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for row in rows:
        key = _security_key(row)
        if not key:
            continue
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    _require(not duplicates, f"{label} contains duplicate securities: {duplicates[:5]}")


def _assert_has_latest_seen(rows: list[dict[str, Any]], run_date: str, label: str) -> None:
    has_latest = any(str(row.get("last_seen_date") or row.get("date") or "")[:10] == run_date for row in rows)
    _require(has_latest, f"{label} has no row updated for {run_date}")


def _security_key(row: dict[str, Any]) -> str | None:
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


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


if __name__ == "__main__":
    main()
