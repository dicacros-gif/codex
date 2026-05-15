from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

from src.utils.io import write_json
from src.utils.text import to_float


SCANNER_URLS = {
    "US": "https://scanner.tradingview.com/america/scan",
    "KR": "https://scanner.tradingview.com/korea/scan",
}

COUNTRY_LABELS = {"US": "미국", "KR": "한국"}
MIN_CLOSE = {"US": 10.0, "KR": 10000.0}

TV_COLUMNS = [
    "name",
    "description",
    "close",
    "currency",
    "volume",
    "relative_volume_10d_calc",
    "average_volume_30d_calc",
    "market_cap_basic",
    "sector",
    "industry",
    "exchange",
    "High.52W",
    "Low.52W",
    "change",
    "Recommend.All",
    "price_earnings_ttm",
    "earnings_per_share_basic_ttm",
    "dividends_yield_current",
]


def collect_tradingview(raw_dir: Path, run_date: date, limit: int = 250) -> dict[str, list[dict[str, Any]]]:
    output = {
        "us_52w_highs": _collect_52w("US", raw_dir, run_date, limit),
        "kr_52w_highs": _collect_52w("KR", raw_dir, run_date, limit),
        "us_volume_surges": _collect_volume("US", raw_dir, run_date, limit),
        "kr_volume_surges": _collect_volume("KR", raw_dir, run_date, limit),
    }
    return output


def _collect_52w(country: str, raw_dir: Path, run_date: date, limit: int) -> list[dict[str, Any]]:
    rows = _scan_many(
        country=country,
        raw_dir=raw_dir,
        run_date=run_date,
        label="52w_high",
        sort_fields=["change", "market_cap_basic", "High.52W", "volume"],
        limit=limit,
        filters=[{"left": "close", "operation": "greater", "right": MIN_CLOSE[country]}],
    )
    candidates = []
    for row in rows:
        close = to_float(row.get("close"))
        high_52w = to_float(row.get("High.52W"))
        if close is None or high_52w is None or high_52w <= 0:
            continue
        if close < MIN_CLOSE[country]:
            continue
        if close >= high_52w * 0.995:
            item = _standardize(row, country, run_date, "52주 신고가")
            item["high_52w"] = high_52w
            item["distance_to_52w_high_pct"] = round((close / high_52w - 1) * 100, 3)
            candidates.append(item)
    return _dedupe(candidates, "ticker")


def _collect_volume(country: str, raw_dir: Path, run_date: date, limit: int) -> list[dict[str, Any]]:
    rows = _scan_many(
        country=country,
        raw_dir=raw_dir,
        run_date=run_date,
        label="volume_surge",
        sort_fields=["relative_volume_10d_calc", "volume", "change"],
        limit=limit,
        filters=[{"left": "close", "operation": "greater", "right": MIN_CLOSE[country]}],
    )
    candidates = []
    for row in rows:
        close = to_float(row.get("close"))
        relative_volume = to_float(row.get("relative_volume_10d_calc"))
        if close is None or close < MIN_CLOSE[country]:
            continue
        if relative_volume is None or relative_volume < 2:
            continue
        item = _standardize(row, country, run_date, "거래량 급증")
        item["relative_volume"] = relative_volume
        candidates.append(item)
    return _dedupe(candidates, "ticker")


def _scan_many(
    country: str,
    raw_dir: Path,
    run_date: date,
    label: str,
    sort_fields: list[str],
    limit: int,
    filters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for sort_field in sort_fields:
        data = _scan(country, sort_field, limit, filters)
        write_json(raw_dir / f"tradingview_{country.lower()}_{label}_{sort_field.replace('.', '_')}.json", data)
        for row in _rows_from_response(data, country, run_date):
            key = row.get("symbol") or row.get("ticker") or row.get("name")
            if key:
                merged[str(key)] = row
        time.sleep(0.35)
    return list(merged.values())


def _scan(
    country: str,
    sort_field: str,
    limit: int,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    url = SCANNER_URLS[country]
    payload: dict[str, Any] = {
        "columns": TV_COLUMNS,
        "ignore_unknown_fields": True,
        "options": {"lang": "en"},
        "range": [0, limit],
        "sort": {"sortBy": sort_field, "sortOrder": "desc"},
    }
    if filters:
        payload["filter"] = filters
    try:
        response = requests.post(url, json=payload, timeout=20)
        if response.status_code >= 400 and filters:
            payload.pop("filter", None)
            response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        return {"error": str(exc), "data": []}
    except ValueError as exc:
        return {"error": f"Invalid JSON: {exc}", "data": []}


def _rows_from_response(data: dict[str, Any], country: str, run_date: date) -> list[dict[str, Any]]:
    rows = []
    for item in data.get("data") or []:
        values = item.get("d") or []
        row = dict(zip(TV_COLUMNS, values))
        row["symbol"] = item.get("s")
        row["country_code"] = country
        row["date"] = run_date.isoformat()
        rows.append(row)
    return rows


def _standardize(row: dict[str, Any], country: str, run_date: date, signal: str) -> dict[str, Any]:
    ticker = _ticker(row.get("symbol"), row.get("name"), country)
    market_cap = to_float(row.get("market_cap_basic"))
    return {
        "date": run_date.isoformat(),
        "country": COUNTRY_LABELS[country],
        "country_code": country,
        "ticker": ticker,
        "company_name": row.get("description") or row.get("name"),
        "close": to_float(row.get("close")),
        "currency": row.get("currency"),
        "volume": to_float(row.get("volume")),
        "average_volume_30d": to_float(row.get("average_volume_30d_calc")),
        "relative_volume": to_float(row.get("relative_volume_10d_calc")),
        "market_cap": market_cap,
        "sector": row.get("sector"),
        "industry": row.get("industry"),
        "exchange": row.get("exchange"),
        "change_pct": to_float(row.get("change")),
        "recommendation_score": to_float(row.get("Recommend.All")),
        "trailing_per": to_float(row.get("price_earnings_ttm")),
        "source": "TradingView Scanner",
        "source_url": SCANNER_URLS[country],
        "signals": [signal],
    }


def _ticker(symbol: Any, name: Any, country: str) -> str | None:
    raw = str(symbol or name or "").strip()
    if not raw:
        return None
    value = raw.split(":", 1)[-1]
    if country == "KR":
        digits = "".join(ch for ch in value if ch.isdigit())
        return digits.zfill(6) if digits else value
    return value


def _dedupe(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if not value:
            continue
        if value not in seen:
            seen[str(value)] = row
        else:
            old = seen[str(value)]
            old_signals = set(old.get("signals") or [])
            old_signals.update(row.get("signals") or [])
            old["signals"] = sorted(old_signals)
    return list(seen.values())
