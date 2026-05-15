from __future__ import annotations

from pathlib import Path
from typing import Any

from src.collectors.fnguide import enrich_with_fnguide
from src.collectors.naver import enrich_with_naver
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
    return enriched


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
    return cleaned

