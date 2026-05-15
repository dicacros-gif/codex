from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from src.collectors.fnguide import enrich_with_fnguide
from src.collectors.naver import enrich_name_with_naver, enrich_with_naver, has_hangul
from src.collectors.yahoo import enrich_us_with_yahoo
from src.utils.korean_names import koreanize_kr_company_name
from src.utils.text import clean_phrase, compact_join, to_float


def merge_signal_rows(sections: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for section_name, rows in sections.items():
        for row in rows:
            country = str(row.get("country_code") or row.get("country") or "")
            ticker = str(row.get("ticker") or "")
            if not country or not ticker:
                continue
            key = (country, ticker)
            if key not in merged:
                merged[key] = dict(row)
                merged[key]["section_sources"] = [section_name]
            else:
                current = merged[key]
                current["section_sources"].append(section_name)
                signals = set(current.get("signals") or [])
                signals.update(row.get("signals") or [])
                current["signals"] = sorted(signals)
                for field, value in row.items():
                    if field == "supply_source" and value not in (None, "", []):
                        existing = str(current.get(field) or "")
                        current[field] = existing if str(value) in existing.split(" / ") else compact_join([existing, value])
                    elif current.get(field) in (None, "", []):
                        current[field] = value
    return [clean_record(record) for record in merged.values()]


def enrich_records(records: list[dict[str, Any]], raw_dir: Path, max_kr: int = 80) -> list[dict[str, Any]]:
    enriched = []
    full_kr_enrich_keys = _select_full_kr_enrichment_keys(records, max_kr)
    for record in records:
        item = dict(record)
        if item.get("country_code") == "KR":
            if _record_key(item) in full_kr_enrich_keys:
                item = enrich_with_fnguide(item, raw_dir)
                item = enrich_with_naver(item, raw_dir)
            elif not has_hangul(item.get("company_name")):
                item = enrich_name_with_naver(item, raw_dir)
        enriched.append(clean_record(item))
    enriched = enrich_us_with_yahoo(enriched, raw_dir=raw_dir, detail_limit=50)
    return [clean_record(item) for item in enriched]


def _select_full_kr_enrichment_keys(records: list[dict[str, Any]], max_kr: int) -> set[str]:
    if max_kr <= 0:
        return set()
    kr_records = [record for record in records if record.get("country_code") == "KR" and _record_key(record)]
    supply = [record for record in kr_records if not _has_52w_high_signal(record) and _has_supply_signal(record)]
    supply_keys = {_record_key(record) for record in supply}
    non_high = [record for record in kr_records if not _has_52w_high_signal(record) and _record_key(record) not in supply_keys]
    highs = [record for record in kr_records if _has_52w_high_signal(record)]
    supply_quota = min(len(supply), max_kr)
    selected = supply[:supply_quota]
    non_high_quota = min(len(non_high), max_kr - len(selected), max(10, max_kr // 3))
    selected.extend(non_high[:non_high_quota])
    selected.extend(highs[: max_kr - len(selected)])
    if len(selected) < max_kr:
        selected.extend(non_high[non_high_quota : max_kr - len(selected) + non_high_quota])
    return {_record_key(record) for record in selected if _record_key(record)}


def _record_key(record: dict[str, Any]) -> str | None:
    country = str(record.get("country_code") or record.get("country") or "").upper()
    ticker = str(record.get("ticker") or "").upper()
    if country and ticker:
        return f"{country}:{ticker}"
    return None


def _has_52w_high_signal(record: dict[str, Any]) -> bool:
    return any(str(signal).startswith("52주 신고가") for signal in (record.get("signals") or []))


def _has_supply_signal(record: dict[str, Any]) -> bool:
    return any("순매수" in str(signal) for signal in (record.get("signals") or []))


def clean_record(record: dict[str, Any]) -> dict[str, Any]:
    cleaned = {}
    for key, value in record.items():
        if isinstance(value, str):
            cleaned[key] = clean_phrase(value)
        elif isinstance(value, list):
            cleaned[key] = [clean_phrase(item) if isinstance(item, str) else item for item in value]
        else:
            cleaned[key] = value
    if not cleaned.get("future_industry_theme"):
        cleaned["future_industry_theme"] = clean_phrase(cleaned.get("industry") or cleaned.get("sector"))
    if cleaned.get("country_code") == "KR" and cleaned.get("company_name"):
        original_name = str(cleaned.get("company_name") or "")
        localized_name = koreanize_kr_company_name(original_name)
        if localized_name and localized_name != original_name:
            cleaned.setdefault("official_company_name", original_name)
            cleaned["company_name"] = localized_name
        report_link = str(cleaned.get("report_link") or "")
        if "markets.hankyung.com/consensus?searchWord=" in report_link:
            cleaned["report_link"] = f"https://markets.hankyung.com/consensus?searchWord={quote(str(cleaned['company_name']))}"
    if cleaned.get("target_price") is not None and cleaned.get("close"):
        try:
            cleaned["target_upside_pct"] = round((float(cleaned["target_price"]) / float(cleaned["close"]) - 1) * 100, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            cleaned["target_upside_pct"] = None
    close = to_float(cleaned.get("close"))
    eps_ttm = to_float(cleaned.get("eps_ttm"))
    forward_eps = to_float(cleaned.get("forward_eps"))
    if forward_eps and eps_ttm and eps_ttm > 0 and cleaned.get("expected_eps_growth") in (None, ""):
        cleaned["expected_eps_growth"] = round((forward_eps / eps_ttm - 1) * 100, 2)
    total_revenue = to_float(cleaned.get("total_revenue"))
    expected_revenue = to_float(cleaned.get("expected_revenue"))
    if expected_revenue and total_revenue and total_revenue > 0 and cleaned.get("expected_revenue_growth") in (None, ""):
        cleaned["expected_revenue_growth"] = round((expected_revenue / total_revenue - 1) * 100, 2)
    if close and forward_eps and forward_eps > 0 and cleaned.get("forward_per") in (None, ""):
        cleaned["forward_per"] = round(close / forward_eps, 2)
    if cleaned.get("forward_peg") in (None, ""):
        try:
            growth = float(cleaned.get("expected_eps_growth") or cleaned.get("eps_growth_yoy") or cleaned.get("eps_growth_qoq") or 0)
            per = float(cleaned.get("forward_per") or cleaned.get("trailing_per") or 0)
            if growth > 0 and per > 0:
                cleaned["forward_peg"] = round(per / growth, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if cleaned.get("relative_volume") in (None, ""):
        try:
            volume = float(cleaned.get("volume") or 0)
            average = float(cleaned.get("average_volume_30d") or 0)
            if volume > 0 and average > 0:
                cleaned["relative_volume"] = round(volume / average, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    high_52w = to_float(cleaned.get("high_52w"))
    low_52w = to_float(cleaned.get("low_52w"))
    if close and high_52w and low_52w and high_52w > low_52w and cleaned.get("position_52w_pct") in (None, ""):
        cleaned["position_52w_pct"] = round((close - low_52w) / (high_52w - low_52w) * 100, 2)
    for source, target in (("sma_50", "sma50_gap_pct"), ("sma_200", "sma200_gap_pct")):
        average = to_float(cleaned.get(source))
        if close and average and cleaned.get(target) in (None, ""):
            cleaned[target] = round((close / average - 1) * 100, 2)
    free_cash_flow = to_float(cleaned.get("free_cash_flow"))
    if free_cash_flow is not None and total_revenue and cleaned.get("fcf_margin") in (None, ""):
        cleaned["fcf_margin"] = round(free_cash_flow / total_revenue * 100, 2)
    market_cap = to_float(cleaned.get("market_cap"))
    if market_cap and total_revenue and total_revenue > 0 and cleaned.get("price_to_sales") in (None, ""):
        cleaned["price_to_sales"] = round(market_cap / total_revenue, 2)
    if market_cap and free_cash_flow and free_cash_flow > 0 and cleaned.get("price_to_fcf") in (None, ""):
        cleaned["price_to_fcf"] = round(market_cap / free_cash_flow, 2)
    total_cash = to_float(cleaned.get("total_cash"))
    total_debt = to_float(cleaned.get("total_debt"))
    if total_cash is not None and total_debt is not None and cleaned.get("net_cash") in (None, ""):
        cleaned["net_cash"] = round(total_cash - total_debt, 2)
    return cleaned
