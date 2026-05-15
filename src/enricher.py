from __future__ import annotations

from pathlib import Path
from typing import Any

from src.collectors.fnguide import enrich_with_fnguide
from src.collectors.naver import enrich_with_naver
from src.collectors.yahoo import enrich_us_with_yahoo
from src.utils.text import clean_phrase


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
        if item.get("country_code") == "KR" and kr_count < max_kr:
            item = enrich_with_fnguide(item, raw_dir)
            item = enrich_with_naver(item, raw_dir)
            kr_count += 1
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
    return cleaned
