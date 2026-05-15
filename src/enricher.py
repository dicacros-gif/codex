from __future__ import annotations

from pathlib import Path
from typing import Any

from src.collectors.fnguide import enrich_with_fnguide
from src.collectors.naver import enrich_name_with_naver, enrich_with_naver, has_hangul
from src.collectors.yahoo import enrich_us_with_yahoo
from src.utils.korean_names import koreanize_kr_company_name
from src.utils.text import clean_phrase, to_float


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
                    if current.get(field) in (None, "", []):
                        current[field] = value
    return [clean_record(record) for record in merged.values()]


def enrich_records(records: list[dict[str, Any]], raw_dir: Path, max_kr: int = 80) -> list[dict[str, Any]]:
    enriched = []
    kr_count = 0
    for record in records:
        item = dict(record)
        if item.get("country_code") == "KR":
            if kr_count < max_kr:
                item = enrich_with_fnguide(item, raw_dir)
                item = enrich_with_naver(item, raw_dir)
                kr_count += 1
            elif not has_hangul(item.get("company_name")):
                item = enrich_name_with_naver(item, raw_dir)
        enriched.append(clean_record(item))
    enriched = enrich_us_with_yahoo(enriched, raw_dir=raw_dir, detail_limit=50)
    return [clean_record(item) for item in enriched]


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
    if cleaned.get("target_price") is not None and cleaned.get("close"):
        try:
            cleaned["target_upside_pct"] = round((float(cleaned["target_price"]) / float(cleaned["close"]) - 1) * 100, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            cleaned["target_upside_pct"] = None
    if cleaned.get("forward_peg") in (None, ""):
        try:
            growth = float(cleaned.get("expected_eps_growth") or cleaned.get("eps_growth_yoy") or 0)
            per = float(cleaned.get("forward_per") or 0)
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
    close = to_float(cleaned.get("close"))
    high_52w = to_float(cleaned.get("high_52w"))
    low_52w = to_float(cleaned.get("low_52w"))
    if close and high_52w and low_52w and high_52w > low_52w and cleaned.get("position_52w_pct") in (None, ""):
        cleaned["position_52w_pct"] = round((close - low_52w) / (high_52w - low_52w) * 100, 2)
    for source, target in (("sma_50", "sma50_gap_pct"), ("sma_200", "sma200_gap_pct")):
        average = to_float(cleaned.get(source))
        if close and average and cleaned.get(target) in (None, ""):
            cleaned[target] = round((close / average - 1) * 100, 2)
    free_cash_flow = to_float(cleaned.get("free_cash_flow"))
    total_revenue = to_float(cleaned.get("total_revenue"))
    if free_cash_flow is not None and total_revenue and cleaned.get("fcf_margin") in (None, ""):
        cleaned["fcf_margin"] = round(free_cash_flow / total_revenue * 100, 2)
    total_cash = to_float(cleaned.get("total_cash"))
    total_debt = to_float(cleaned.get("total_debt"))
    if total_cash is not None and total_debt is not None and cleaned.get("net_cash") in (None, ""):
        cleaned["net_cash"] = round(total_cash - total_debt, 2)
    return cleaned
