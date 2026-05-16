from __future__ import annotations

import re
from typing import Any

from src.utils.text import compact_join, to_float


def score_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for record in records:
        item = dict(record)
        item["valuation_score"] = _valuation(item)
        item["growth_consensus_score"] = _growth(item)
        item["quality_score"] = _quality(item)
        item["cashflow_score"] = _cashflow(item)
        item["foreign_flow_score"] = _flow(item.get("foreign_net_buy"))
        item["institution_flow_score"] = _flow(item.get("institution_net_buy"))
        item["leading_flow_score"] = _leading_flow(item)
        item["future_industry_score"] = 5 if item.get("future_industry_theme") else 0
        item["strategic_bonus_score"] = _strategic_bonus(item)
        item["long_term_stability_score"] = _long_term(item)
        item["momentum_score"] = _momentum(item)
        item["analyst_opinion_score"] = _analyst(item)
        item["volume_score"] = _volume(item)
        item["risk_penalty"] = _risk_penalty(item)
        item["investment_priority_score"] = round(
            item["valuation_score"] * 0.10
            + item["growth_consensus_score"] * 0.14
            + item["quality_score"] * 0.10
            + item["cashflow_score"] * 0.08
            + item["foreign_flow_score"] * 0.08
            + item["institution_flow_score"] * 0.08
            + item["leading_flow_score"] * 0.10
            + item["future_industry_score"] * 0.06
            + item["strategic_bonus_score"] * 0.05
            + item["long_term_stability_score"] * 0.08
            + item["momentum_score"] * 0.08
            + item["analyst_opinion_score"] * 0.07
            + item["volume_score"] * 0.08
            - item["risk_penalty"],
            2,
        )
        item["long_future_score"] = round(
            item["growth_consensus_score"] * 0.28
            + item["quality_score"] * 0.20
            + item["cashflow_score"] * 0.16
            + item["future_industry_score"] * 0.16
            + item["long_term_stability_score"] * 0.20
            - item["risk_penalty"] * 0.5,
            2,
        )
        item["leading_supply_score"] = round(
            item["foreign_flow_score"] * 0.22
            + item["institution_flow_score"] * 0.22
            + item["leading_flow_score"] * 0.28
            + item["momentum_score"] * 0.18
            + item["volume_score"] * 0.10,
            2,
        )
        item["foreign_flow_investment_score"] = item["foreign_flow_score"]
        item["core_basis"] = item.get("core_basis") or _core_basis(item)
        scored.append(item)
    scored.sort(key=lambda row: (row.get("date") or "", row.get("investment_priority_score") or 0), reverse=True)
    return scored


def build_sections(records: list[dict[str, Any]], sec13f: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    used: set[str] = set()
    non_high_records = [row for row in records if not _has_signal(row, "52주 신고가")]
    foreign = _take_unique(_sort_foreign_flow(non_high_records, dominant_only=True), used, 50)
    institution = _take_unique(_sort_institution_flow(non_high_records, dominant_only=True), used, 50)
    if len(foreign) < 15:
        foreign.extend(_take_unique(_sort_foreign_flow(non_high_records, dominant_only=False), used, 15 - len(foreign)))
    if len(institution) < 15:
        institution.extend(_take_unique(_sort_institution_flow(non_high_records, dominant_only=False), used, 15 - len(institution)))
    top = _take_unique(sorted(records, key=lambda row: row.get("investment_priority_score") or -999, reverse=True), used, 50)
    leading = _take_unique(sorted(records, key=lambda row: row.get("leading_supply_score") or -999, reverse=True), used, 50)
    long_term = _take_unique(sorted(records, key=lambda row: row.get("long_future_score") or -999, reverse=True), used, 50)
    us_highs = _take_unique(_sort_highs([row for row in records if row.get("country_code") == "US" and _has_signal(row, "52주 신고가")]), used, 120)
    kr_highs = _take_unique(_sort_highs([row for row in records if row.get("country_code") == "KR" and _has_signal(row, "52주 신고가")]), used, 120)
    us_volume = _take_unique(
        _sort_volume(
            [
                row
                for row in records
                if row.get("country_code") == "US"
                and "거래량 급증" in (row.get("signals") or [])
                and not _has_signal(row, "52주 신고가")
            ]
        ),
        used,
        120,
    )
    kr_volume = _take_unique(
        _sort_volume(
            [
                row
                for row in records
                if row.get("country_code") == "KR"
                and "거래량 급증" in (row.get("signals") or [])
                and not _has_signal(row, "52주 신고가")
            ]
        ),
        used,
        120,
    )
    return {
        "priority_top": top,
        "leading_candidates": leading,
        "long_term_candidates": long_term,
        "theme_summary": _theme_summary(records),
        "foreign_flow": foreign,
        "institution_flow_summary": institution,
        "us_52w_highs": us_highs,
        "kr_52w_highs": kr_highs,
        "us_volume_surges": us_volume,
        "kr_volume_surges": kr_volume,
        "famous_13f_changes": sec13f,
        "daily_tracking": [],
    }


def _take_unique(rows: list[dict[str, Any]], used: set[str], limit: int) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        key = _security_key(row)
        if not key or key in used:
            continue
        used.add(key)
        output.append(row)
        if len(output) >= limit:
            break
    return output


def _security_key(row: dict[str, Any]) -> str | None:
    country = str(row.get("country_code") or row.get("country") or "").upper()
    ticker = str(row.get("ticker") or "").upper()
    if country and ticker:
        return f"{country}:{ticker}"
    return None


def _sort_highs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("distance_to_52w_high_pct") if row.get("distance_to_52w_high_pct") is not None else -999,
            row.get("investment_priority_score") or -999,
            row.get("market_cap") or 0,
        ),
        reverse=True,
    )


def _sort_volume(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("relative_volume") or -999,
            row.get("change_pct") or -999,
            row.get("market_cap") or 0,
        ),
        reverse=True,
    )


def _sort_foreign_flow(records: list[dict[str, Any]], dominant_only: bool) -> list[dict[str, Any]]:
    rows = []
    for row in records:
        foreign = to_float(row.get("foreign_net_buy_20d") or row.get("foreign_net_buy"))
        if foreign is None or foreign <= 0:
            continue
        institution = to_float(row.get("institution_net_buy_20d") or row.get("institution_net_buy")) or 0
        if dominant_only and foreign <= institution:
            continue
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            row.get("foreign_flow_investment_score") or -999,
            row.get("foreign_net_buy_amount_mil_krw") or -999,
            row.get("foreign_net_buy_20d") or row.get("foreign_net_buy") or -999,
            row.get("foreign_net_buy_5d") or -999,
        ),
        reverse=True,
    )


def _sort_institution_flow(records: list[dict[str, Any]], dominant_only: bool) -> list[dict[str, Any]]:
    rows = []
    for row in records:
        institution = to_float(row.get("institution_net_buy_20d") or row.get("institution_net_buy"))
        if institution is None or institution <= 0:
            continue
        foreign = to_float(row.get("foreign_net_buy_20d") or row.get("foreign_net_buy")) or 0
        if dominant_only and institution <= foreign:
            continue
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            row.get("institution_flow_score") or -999,
            row.get("institution_net_buy_amount_mil_krw") or -999,
            row.get("institution_net_buy_20d") or row.get("institution_net_buy") or -999,
            row.get("institution_net_buy_5d") or -999,
        ),
        reverse=True,
    )


def _valuation(row: dict[str, Any]) -> float:
    per = to_float(row.get("forward_per")) or to_float(row.get("trailing_per"))
    peg = to_float(row.get("forward_peg"))
    psr = to_float(row.get("price_to_sales"))
    pfcf = to_float(row.get("price_to_fcf"))
    score = 0.0
    if per is not None:
        score += 10 if 0 < per <= 15 else 7 if per <= 25 else 4 if per <= 40 else 1
    if peg is not None:
        score += 10 if 0 < peg <= 1 else 7 if peg <= 1.8 else 3 if peg <= 3 else 0
    if psr is not None:
        score += 4 if 0 < psr <= 3 else 2 if psr <= 8 else 0
    if pfcf is not None:
        score += 4 if 0 < pfcf <= 20 else 2 if pfcf <= 40 else 0
    return min(score, 10)


def _growth(row: dict[str, Any]) -> float:
    values = [
        row.get("revenue_growth_yoy"),
        row.get("revenue_growth_qoq"),
        row.get("eps_growth_yoy"),
        row.get("eps_growth_qoq"),
        row.get("expected_revenue_growth"),
        row.get("expected_eps_growth"),
    ]
    nums = [to_float(value) for value in values if to_float(value) is not None]
    if not nums:
        return 0
    avg = sum(nums) / len(nums)
    return 10 if avg >= 25 else 8 if avg >= 15 else 6 if avg >= 8 else 3 if avg > 0 else 0


def _quality(row: dict[str, Any]) -> float:
    values = [
        to_float(row.get("roic")),
        to_float(row.get("roe")),
        to_float(row.get("roa")),
        to_float(row.get("gross_margin")),
        to_float(row.get("operating_margin")),
        to_float(row.get("profit_margin")),
    ]
    nums = [value for value in values if value is not None]
    if not nums:
        return 0
    value = sum(nums) / len(nums)
    return 10 if value >= 20 else 8 if value >= 12 else 5 if value >= 6 else 2 if value > 0 else 0


def _cashflow(row: dict[str, Any]) -> float:
    margin = to_float(row.get("fcf_margin"))
    free_cash_flow = to_float(row.get("free_cash_flow"))
    if margin is None and free_cash_flow is None:
        return 0
    if margin is None:
        return 6 if free_cash_flow and free_cash_flow > 0 else 0
    return 10 if margin >= 15 else 8 if margin >= 8 else 5 if margin >= 3 else 2 if margin > 0 else 0


def _flow(value: Any) -> float:
    number = to_float(value)
    if number is None:
        return 0
    return 10 if number > 0 else 0


def _leading_flow(row: dict[str, Any]) -> float:
    score = 0
    if _has_signal(row, "52주 신고가"):
        score += 5
    if "거래량 급증" in (row.get("signals") or []):
        score += 5
    return score


def _strategic_bonus(row: dict[str, Any]) -> float:
    source_count = len(set(row.get("section_sources") or []))
    return min(source_count * 2, 10)


def _long_term(row: dict[str, Any]) -> float:
    market_cap = to_float(row.get("market_cap"))
    if market_cap is None:
        return 0
    return 10 if market_cap >= 100_000_000_000 else 8 if market_cap >= 10_000_000_000 else 5 if market_cap >= 1_000_000_000 else 2


def _momentum(row: dict[str, Any]) -> float:
    change = to_float(row.get("change_pct"))
    performance_1w = to_float(row.get("performance_1w"))
    performance_1m = to_float(row.get("performance_1m"))
    performance_ytd = to_float(row.get("performance_ytd"))
    position = to_float(row.get("position_52w_pct"))
    sma50_gap = to_float(row.get("sma50_gap_pct"))
    sma200_gap = to_float(row.get("sma200_gap_pct"))
    rsi = to_float(row.get("rsi_14"))
    adx = to_float(row.get("adx_14"))
    short_values = [value for value in [change, performance_1w, performance_1m] if value is not None]
    best_short = max(short_values) if short_values else None
    if best_short is None and performance_ytd is None and position is None and sma50_gap is None and sma200_gap is None:
        return 0
    score = 10 if best_short is not None and best_short >= 12 else 8 if best_short is not None and best_short >= 6 else 5 if best_short is not None and best_short > 0 else 0
    if position is not None and position >= 80:
        score += 2
    if (sma50_gap or 0) > 0 and (sma200_gap or 0) > 0:
        score += 2
    if performance_ytd is not None and performance_ytd > 0:
        score += 1
    if rsi is not None and 50 <= rsi <= 70 and adx is not None and adx >= 25:
        score += 1
    return min(score, 10)


def _analyst(row: dict[str, Any]) -> float:
    upside = to_float(row.get("target_upside_pct"))
    recommendation = to_float(row.get("recommendation_score"))
    score = 0
    if upside is not None:
        score += 6 if upside >= 25 else 4 if upside >= 10 else 2 if upside > 0 else 0
    if recommendation is not None:
        score += 4 if recommendation >= 0.4 else 2 if recommendation > 0 else 0
    return min(score, 10)


def _volume(row: dict[str, Any]) -> float:
    rel = to_float(row.get("relative_volume"))
    if rel is None:
        return 0
    return 10 if rel >= 5 else 8 if rel >= 3 else 5 if rel >= 2 else 0


def _risk_penalty(row: dict[str, Any]) -> float:
    penalty = 0.0
    if not row.get("company_name"):
        penalty += 2
    if not row.get("source"):
        penalty += 2
    if not row.get("forward_per") and not row.get("trailing_per"):
        penalty += 1
    beta = to_float(row.get("beta"))
    debt = to_float(row.get("debt_to_equity"))
    current_ratio = to_float(row.get("current_ratio"))
    profit_margin = to_float(row.get("profit_margin"))
    short_float = to_float(row.get("short_percent_float"))
    if beta is not None and beta >= 2:
        penalty += 0.8
    if debt is not None and debt >= 200:
        penalty += 1
    if current_ratio is not None and current_ratio < 1:
        penalty += 0.8
    if profit_margin is not None and profit_margin < 0:
        penalty += 1
    if short_float is not None and short_float >= 20:
        penalty += 1
    return penalty


def _core_basis(row: dict[str, Any]) -> str | None:
    parts = []
    parts.extend(row.get("signals") or [])
    rel = to_float(row.get("relative_volume"))
    if rel is not None:
        parts.append(f"거래량 {int(round(rel))}배")
    if row.get("supply_pattern"):
        parts.append(row["supply_pattern"])
    position = to_float(row.get("position_52w_pct"))
    if position is not None:
        parts.append(f"52주 위치 {int(round(position))}%")
    performance_1m = to_float(row.get("performance_1m"))
    if performance_1m is not None:
        parts.append(f"1개월 성과 {int(round(performance_1m))}%")
    rsi = to_float(row.get("rsi_14"))
    adx = to_float(row.get("adx_14"))
    if rsi is not None and adx is not None:
        parts.append(f"RSI {int(round(rsi))} / ADX {int(round(adx))}")
    fcf_margin = to_float(row.get("fcf_margin"))
    if fcf_margin is not None:
        parts.append(f"FCF마진 {int(round(fcf_margin))}%")
    debt = to_float(row.get("debt_to_equity"))
    if debt is not None:
        parts.append(f"부채비율 {int(round(debt))}%")
    if row.get("recent_report_title") and not _is_generic_report_title(row.get("recent_report_title")):
        parts.append(str(row["recent_report_title"]))
    return compact_join(parts)


def _is_generic_report_title(value: Any) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    return text in {"종목 뉴스", "yahoo news", "컨센서스 검색", "analyst estimates / analysis", "analyst estimates/analysis"}


def _has_signal(row: dict[str, Any], prefix: str) -> bool:
    return any(str(signal).startswith(prefix) for signal in (row.get("signals") or []))


def _theme_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for row in records:
        theme = row.get("future_industry_theme")
        if not theme:
            continue
        bucket = counts.setdefault(
            str(theme),
            {
                "date": row.get("date"),
                "country": "혼합",
                "ticker": None,
                "company_name": str(theme),
                "future_industry_theme": str(theme),
                "core_basis": "",
                "stock_count": 0,
            },
        )
        bucket["stock_count"] += 1
        tickers = bucket.setdefault("_tickers", [])
        if row.get("ticker"):
            tickers.append(row["ticker"])
        for source, target in [
            ("investment_priority_score", "_investment_scores"),
            ("long_future_score", "_long_scores"),
            ("leading_supply_score", "_leading_scores"),
            ("relative_volume", "_relative_volumes"),
        ]:
            value = to_float(row.get(source))
            if value is not None:
                bucket.setdefault(target, []).append(value)
    output = []
    for bucket in counts.values():
        tickers = _unique_values(bucket.pop("_tickers", []))
        bucket["top_tickers"] = ", ".join(tickers[:12])
        bucket["core_basis"] = f"포착 종목 {len(tickers)}개: {', '.join(tickers[:8])}"
        bucket["avg_investment_priority_score"] = _avg(bucket.pop("_investment_scores", []))
        bucket["avg_long_future_score"] = _avg(bucket.pop("_long_scores", []))
        bucket["avg_leading_supply_score"] = _avg(bucket.pop("_leading_scores", []))
        bucket["avg_relative_volume"] = _avg(bucket.pop("_relative_volumes", []))
        output.append(bucket)
    output.sort(key=lambda row: (row.get("stock_count") or 0, row.get("avg_investment_priority_score") or 0), reverse=True)
    return output[:40]


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _unique_values(values: list[Any]) -> list[str]:
    output = []
    seen = set()
    for value in values:
        text = str(value)
        key = text.upper()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output
