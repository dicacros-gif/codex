from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import requests

from src.utils.io import write_json
from src.utils.text import to_float


QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
SUMMARY_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
ANALYSIS_URL = "https://finance.yahoo.com/quote/{ticker}/analysis/"
NEWS_URL = "https://finance.yahoo.com/quote/{ticker}/news/"


QUOTE_FIELDS = {
    "regularMarketPrice": "close",
    "marketCap": "market_cap",
    "regularMarketVolume": "volume",
    "averageDailyVolume3Month": "average_volume_30d",
    "fiftyTwoWeekHigh": "high_52w",
    "fiftyTwoWeekLow": "low_52w",
    "trailingPE": "trailing_per",
    "forwardPE": "forward_per",
    "priceToBook": "pbr",
    "dividendYield": "dividend_yield",
    "epsTrailingTwelveMonths": "eps_ttm",
    "epsForward": "forward_eps",
    "targetMeanPrice": "target_price",
    "averageAnalystRating": "analyst_rating",
    "shortName": "company_name",
    "longName": "company_name",
    "exchange": "exchange",
    "fullExchangeName": "exchange_name",
    "sector": "sector",
    "industry": "industry",
}


def enrich_us_with_yahoo(records: list[dict[str, Any]], raw_dir: Path, detail_limit: int = 50) -> list[dict[str, Any]]:
    us_records = [row for row in records if row.get("country_code") == "US" and row.get("ticker")]
    quote_map = _quote_batch([str(row["ticker"]) for row in us_records], raw_dir)
    detail_count = 0
    for row in records:
        if row.get("country_code") != "US" or not row.get("ticker"):
            continue
        ticker = str(row["ticker"])
        quote = quote_map.get(ticker.upper()) or {}
        _merge_quote(row, quote)
        if detail_count < detail_limit:
            detail = _quote_summary(ticker, raw_dir)
            _merge_summary(row, detail)
            detail_count += 1
            time.sleep(0.12)
        _fill_report_fallback(row)
        _derive(row)
    return records


def _quote_batch(tickers: list[str], raw_dir: Path) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    unique = sorted({ticker.upper() for ticker in tickers if ticker})
    for index in range(0, len(unique), 50):
        chunk = unique[index : index + 50]
        try:
            response = requests.get(
                QUOTE_URL,
                params={"symbols": ",".join(chunk)},
                headers={"User-Agent": "Mozilla/5.0 daily-stock-trend/1.0"},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            data = {"quoteResponse": {"result": []}}
        write_json(raw_dir / f"yahoo_quote_{index // 50 + 1}.json", data)
        for item in data.get("quoteResponse", {}).get("result", []):
            symbol = str(item.get("symbol") or "").upper()
            if symbol:
                output[symbol] = item
    return output


def _quote_summary(ticker: str, raw_dir: Path) -> dict[str, Any]:
    modules = ",".join(
        [
            "financialData",
            "defaultKeyStatistics",
            "summaryDetail",
            "recommendationTrend",
            "earningsTrend",
        ]
    )
    try:
        response = requests.get(
            SUMMARY_URL.format(ticker=ticker),
            params={"modules": modules},
            headers={"User-Agent": "Mozilla/5.0 daily-stock-trend/1.0"},
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        data = {"quoteSummary": {"result": []}}
    write_json(raw_dir / f"yahoo_summary_{ticker.replace('/', '_')}.json", data)
    result = data.get("quoteSummary", {}).get("result") or []
    return result[0] if result else {}


def _merge_quote(row: dict[str, Any], quote: dict[str, Any]) -> None:
    for source, target in QUOTE_FIELDS.items():
        value = quote.get(source)
        if value in (None, "", []):
            continue
        if target in {"company_name", "exchange", "exchange_name", "sector", "industry", "analyst_rating"}:
            row.setdefault(target, value)
        elif row.get(target) in (None, ""):
            row[target] = to_float(value)


def _merge_summary(row: dict[str, Any], detail: dict[str, Any]) -> None:
    financial = detail.get("financialData") or {}
    stats = detail.get("defaultKeyStatistics") or {}
    summary = detail.get("summaryDetail") or {}
    earnings = detail.get("earningsTrend", {}).get("trend") or []

    mapping = {
        "targetMeanPrice": "target_price",
        "targetMedianPrice": "target_median_price",
        "currentPrice": "close",
        "revenueGrowth": "revenue_growth_yoy",
        "earningsGrowth": "eps_growth_yoy",
        "grossMargins": "gross_margin",
        "profitMargins": "profit_margin",
        "ebitdaMargins": "ebitda_margin",
        "freeCashflow": "free_cash_flow",
        "operatingCashflow": "operating_cash_flow",
        "totalCash": "total_cash",
        "totalDebt": "total_debt",
        "totalRevenue": "total_revenue",
        "revenuePerShare": "revenue_per_share",
        "returnOnEquity": "roe",
        "returnOnAssets": "roa",
        "debtToEquity": "debt_to_equity",
        "currentRatio": "current_ratio",
        "quickRatio": "quick_ratio",
        "recommendationMean": "recommendation_mean",
    }
    for source, target in mapping.items():
        value = _raw(financial.get(source))
        if value is not None and row.get(target) in (None, ""):
            row[target] = _percent(value) if source in {"revenueGrowth", "earningsGrowth", "grossMargins", "profitMargins", "ebitdaMargins", "returnOnEquity", "returnOnAssets"} else value

    for source, target in {
        "beta": "beta",
        "enterpriseValue": "enterprise_value",
        "forwardPE": "forward_per",
        "pegRatio": "forward_peg",
        "priceToBook": "pbr",
        "priceToSalesTrailing12Months": "price_to_sales",
        "enterpriseToRevenue": "enterprise_to_revenue",
        "enterpriseToEbitda": "enterprise_to_ebitda",
        "trailingEps": "eps_ttm",
        "forwardEps": "forward_eps",
        "floatShares": "float_shares",
        "sharesOutstanding": "shares_outstanding",
        "heldPercentInsiders": "insider_ownership_pct",
        "heldPercentInstitutions": "institutional_ownership_pct",
        "shortRatio": "short_ratio",
        "shortPercentOfFloat": "short_percent_float",
        "sharesShort": "shares_short",
        "sharesShortPriorMonth": "shares_short_prior_month",
    }.items():
        value = _raw(stats.get(source))
        if value is not None and row.get(target) in (None, ""):
            row[target] = _percent(value) if source in {"heldPercentInsiders", "heldPercentInstitutions", "shortPercentOfFloat"} else value

    for source, target in {
        "dividendYield": "dividend_yield",
        "payoutRatio": "payout_ratio",
        "fiveYearAvgDividendYield": "five_year_avg_dividend_yield",
        "trailingAnnualDividendYield": "trailing_annual_dividend_yield",
    }.items():
        value = _raw(summary.get(source))
        if value is not None and row.get(target) in (None, ""):
            row[target] = _percent(value)

    for trend in earnings:
        period = trend.get("period")
        if period in {"0q", "+1q"}:
            growth = _raw(trend.get("growth"))
            if growth is not None and row.get("expected_eps_growth") in (None, ""):
                row["expected_eps_growth"] = _percent(growth)
            revenue_growth = _raw((trend.get("revenueEstimate") or {}).get("growth"))
            if revenue_growth is not None and row.get("expected_revenue_growth") in (None, ""):
                row["expected_revenue_growth"] = _percent(revenue_growth)
            break


def _fill_report_fallback(row: dict[str, Any]) -> None:
    ticker = row.get("ticker")
    if not ticker:
        return
    row.setdefault("recent_report_broker", "Yahoo Finance News")
    row.setdefault("recent_report_title", "Yahoo News")
    row.setdefault("report_link", NEWS_URL.format(ticker=ticker))
    row.setdefault("report_source", "Yahoo Finance News")


def _derive(row: dict[str, Any]) -> None:
    close = to_float(row.get("close"))
    high = to_float(row.get("high_52w"))
    if close and high and row.get("distance_to_52w_high_pct") in (None, ""):
        row["distance_to_52w_high_pct"] = round((close / high - 1) * 100, 2)
    target = to_float(row.get("target_price"))
    if close and target and row.get("target_upside_pct") in (None, ""):
        row["target_upside_pct"] = round((target / close - 1) * 100, 2)
    fcf = to_float(row.get("free_cash_flow"))
    revenue = to_float(row.get("total_revenue"))
    if fcf is not None and revenue and row.get("fcf_margin") in (None, ""):
        row["fcf_margin"] = round(fcf / revenue * 100, 2)
    eps_growth = to_float(row.get("expected_eps_growth")) or to_float(row.get("eps_growth_yoy"))
    per = to_float(row.get("forward_per"))
    if per and eps_growth and eps_growth > 0 and row.get("forward_peg") in (None, ""):
        row["forward_peg"] = round(per / eps_growth, 2)
    volume = to_float(row.get("volume"))
    avg_volume = to_float(row.get("average_volume_30d"))
    if volume and avg_volume and row.get("relative_volume") in (None, ""):
        row["relative_volume"] = round(volume / avg_volume, 2)


def _raw(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw")
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return None
    return number


def _percent(value: float) -> float:
    return round(value * 100, 2) if abs(value) <= 1 else round(value, 2)
