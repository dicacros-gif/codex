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
    "price_52_week_high",
    "price_52_week_low",
    "change",
    "change_from_open",
    "gap",
    "RSI",
    "ADX",
    "ATR",
    "Volatility.D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.YTD",
    "Perf.Y",
    "beta_1_year",
    "SMA50",
    "SMA200",
    "Recommend.All",
    "price_earnings_ttm",
    "price_book_fq",
    "price_sales_current",
    "price_free_cash_flow_ttm",
    "earnings_per_share_basic_ttm",
    "earnings_per_share_diluted_ttm",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "total_revenue",
    "total_revenue_yoy_growth_ttm",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "return_on_equity",
    "return_on_assets",
    "debt_to_equity",
    "current_ratio",
    "quick_ratio",
    "free_cash_flow",
    "dividends_yield_current",
    "float_shares_outstanding",
    "total_shares_outstanding",
    "number_of_employees",
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
        sort_fields=["change", "market_cap_basic", "price_52_week_high", "volume"],
        limit=limit,
        filters=[{"left": "close", "operation": "greater", "right": MIN_CLOSE[country]}],
    )
    candidates = []
    near_candidates = []
    for row in rows:
        close = to_float(row.get("close"))
        high_52w = to_float(row.get("price_52_week_high"))
        if close is None or high_52w is None or high_52w <= 0:
            continue
        if close < MIN_CLOSE[country]:
            continue
        distance = round((close / high_52w - 1) * 100, 3)
        if close >= high_52w * 0.995:
            item = _standardize(row, country, run_date, "52주 신고가")
            item["high_52w"] = high_52w
            item["distance_to_52w_high_pct"] = distance
            candidates.append(item)
        elif close >= high_52w * 0.97:
            item = _standardize(row, country, run_date, "52주 신고가 근접")
            item["high_52w"] = high_52w
            item["distance_to_52w_high_pct"] = distance
            near_candidates.append(item)
    if len(candidates) < 30:
        near_candidates.sort(key=lambda item: item.get("distance_to_52w_high_pct") or -999, reverse=True)
        candidates.extend(near_candidates[: 80 - len(candidates)])
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
    close = to_float(row.get("close"))
    high_52w = to_float(row.get("price_52_week_high"))
    low_52w = to_float(row.get("price_52_week_low"))
    sma50 = to_float(row.get("SMA50"))
    sma200 = to_float(row.get("SMA200"))
    total_revenue = to_float(row.get("total_revenue"))
    free_cash_flow = to_float(row.get("free_cash_flow"))
    item = {
        "date": run_date.isoformat(),
        "country": COUNTRY_LABELS[country],
        "country_code": country,
        "ticker": ticker,
        "company_name": row.get("description") or row.get("name"),
        "close": close,
        "currency": row.get("currency"),
        "volume": to_float(row.get("volume")),
        "average_volume_30d": to_float(row.get("average_volume_30d_calc")),
        "relative_volume": to_float(row.get("relative_volume_10d_calc")),
        "market_cap": market_cap,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "sma_50": sma50,
        "sma_200": sma200,
        "beta": to_float(row.get("beta_1_year")),
        "sector": row.get("sector"),
        "industry": row.get("industry"),
        "exchange": row.get("exchange"),
        "change_pct": to_float(row.get("change")),
        "change_from_open_pct": to_float(row.get("change_from_open")),
        "gap_pct": to_float(row.get("gap")),
        "rsi_14": to_float(row.get("RSI")),
        "adx_14": to_float(row.get("ADX")),
        "atr_14": to_float(row.get("ATR")),
        "volatility_d": to_float(row.get("Volatility.D")),
        "performance_1w": to_float(row.get("Perf.W")),
        "performance_1m": to_float(row.get("Perf.1M")),
        "performance_3m": to_float(row.get("Perf.3M")),
        "performance_6m": to_float(row.get("Perf.6M")),
        "performance_ytd": to_float(row.get("Perf.YTD")),
        "performance_1y": to_float(row.get("Perf.Y")),
        "recommendation_score": to_float(row.get("Recommend.All")),
        "trailing_per": to_float(row.get("price_earnings_ttm")),
        "pbr": to_float(row.get("price_book_fq")),
        "price_to_sales": to_float(row.get("price_sales_current")),
        "price_to_fcf": to_float(row.get("price_free_cash_flow_ttm")),
        "eps_ttm": to_float(row.get("earnings_per_share_diluted_ttm")) or to_float(row.get("earnings_per_share_basic_ttm")),
        "eps_growth_yoy": to_float(row.get("earnings_per_share_diluted_yoy_growth_ttm")),
        "total_revenue": total_revenue,
        "revenue_growth_yoy": to_float(row.get("total_revenue_yoy_growth_ttm")),
        "gross_margin": to_float(row.get("gross_margin")),
        "operating_margin": to_float(row.get("operating_margin")),
        "profit_margin": to_float(row.get("net_margin")),
        "roe": to_float(row.get("return_on_equity")),
        "roa": to_float(row.get("return_on_assets")),
        "debt_to_equity": to_float(row.get("debt_to_equity")),
        "current_ratio": to_float(row.get("current_ratio")),
        "quick_ratio": to_float(row.get("quick_ratio")),
        "free_cash_flow": free_cash_flow,
        "dividend_yield": to_float(row.get("dividends_yield_current")),
        "float_shares": to_float(row.get("float_shares_outstanding")),
        "shares_outstanding": to_float(row.get("total_shares_outstanding")),
        "employees": to_float(row.get("number_of_employees")),
        "source": "TradingView Scanner",
        "source_url": SCANNER_URLS[country],
        "signals": [signal],
    }
    if close and high_52w and low_52w and high_52w > low_52w:
        item["position_52w_pct"] = round((close - low_52w) / (high_52w - low_52w) * 100, 2)
    if close and sma50:
        item["sma50_gap_pct"] = round((close / sma50 - 1) * 100, 2)
    if close and sma200:
        item["sma200_gap_pct"] = round((close / sma200 - 1) * 100, 2)
    if free_cash_flow is not None and total_revenue:
        item["fcf_margin"] = round(free_cash_flow / total_revenue * 100, 2)
    return item


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
