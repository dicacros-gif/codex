from __future__ import annotations

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
    top = sorted(records, key=lambda row: row.get("investment_priority_score") or -999, reverse=True)[:50]
    leading = sorted(records, key=lambda row: row.get("leading_supply_score") or -999, reverse=True)[:50]
    long_term = sorted(records, key=lambda row: row.get("long_future_score") or -999, reverse=True)[:50]
    foreign = sorted(
        [row for row in records if row.get("foreign_net_buy") is not None],
        key=lambda row: row.get("foreign_flow_investment_score") or -999,
        reverse=True,
    )[:80]
    institution = sorted(
        [row for row in records if row.get("institution_net_buy") is not None],
        key=lambda row: row.get("institution_flow_score") or -999,
        reverse=True,
    )[:80]
    return {
        "priority_top": top,
        "leading_candidates": leading,
        "long_term_candidates": long_term,
        "theme_summary": _theme_summary(records),
        "foreign_flow": foreign,
        "institution_flow_summary": institution,
        "us_52w_highs": [row for row in records if row.get("country_code") == "US" and "52주 신고가" in (row.get("signals") or [])],
        "kr_52w_highs": [row for row in records if row.get("country_code") == "KR" and "52주 신고가" in (row.get("signals") or [])],
        "us_volume_surges": [row for row in records if row.get("country_code") == "US" and "거래량 급증" in (row.get("signals") or [])],
        "kr_volume_surges": [row for row in records if row.get("country_code") == "KR" and "거래량 급증" in (row.get("signals") or [])],
        "famous_13f_changes": sec13f,
        "daily_tracking": [],
    }


def _valuation(row: dict[str, Any]) -> float:
    per = to_float(row.get("forward_per")) or to_float(row.get("trailing_per"))
    peg = to_float(row.get("forward_peg"))
    score = 0.0
    if per is not None:
        score += 10 if 0 < per <= 15 else 7 if per <= 25 else 4 if per <= 40 else 1
    if peg is not None:
        score += 10 if 0 < peg <= 1 else 7 if peg <= 1.8 else 3 if peg <= 3 else 0
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
    roic = to_float(row.get("roic"))
    roe = to_float(row.get("roe"))
    value = roic if roic is not None else roe
    if value is None:
        return 0
    return 10 if value >= 20 else 8 if value >= 12 else 5 if value >= 6 else 2 if value > 0 else 0


def _cashflow(row: dict[str, Any]) -> float:
    margin = to_float(row.get("fcf_margin"))
    if margin is None:
        return 0
    return 10 if margin >= 15 else 8 if margin >= 8 else 5 if margin >= 3 else 2 if margin > 0 else 0


def _flow(value: Any) -> float:
    number = to_float(value)
    if number is None:
        return 0
    return 10 if number > 0 else 0


def _leading_flow(row: dict[str, Any]) -> float:
    score = 0
    if "52주 신고가" in (row.get("signals") or []):
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
    if change is None:
        return 0
    return 10 if change >= 8 else 8 if change >= 4 else 5 if change > 0 else 0


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
    return penalty


def _core_basis(row: dict[str, Any]) -> str | None:
    parts = []
    parts.extend(row.get("signals") or [])
    rel = to_float(row.get("relative_volume"))
    if rel is not None:
        parts.append(f"거래량 {rel:.2f}배")
    if row.get("supply_pattern"):
        parts.append(row["supply_pattern"])
    if row.get("recent_report_title"):
        parts.append(str(row["recent_report_title"]))
    return compact_join(parts)


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
                "investment_priority_score": 0,
                "long_future_score": 0,
                "leading_supply_score": 0,
            },
        )
        bucket["investment_priority_score"] += 1
        tickers = bucket.setdefault("_tickers", [])
        if row.get("ticker"):
            tickers.append(row["ticker"])
    output = []
    for bucket in counts.values():
        tickers = bucket.pop("_tickers", [])
        bucket["core_basis"] = f"포착 종목 {len(tickers)}개: {', '.join(tickers[:8])}"
        output.append(bucket)
    output.sort(key=lambda row: row.get("investment_priority_score") or 0, reverse=True)
    return output[:40]

