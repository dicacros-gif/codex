from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import requests

from src.utils.io import write_json
from src.utils.text import to_float, to_int


SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/index.json"
SEC_ARCHIVES_FILE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{filename}"


def collect_13f(
    institutions_path: Path,
    raw_dir: Path,
    run_date: date,
    quarters: int = 5,
) -> list[dict[str, Any]]:
    if not institutions_path.exists():
        return []
    institutions = json.loads(institutions_path.read_text(encoding="utf-8"))
    all_changes: list[dict[str, Any]] = []
    for institution in institutions:
        name = institution.get("name")
        cik = _normalize_cik(institution.get("cik"))
        if not name or not cik:
            continue
        changes = _collect_institution(name, cik, raw_dir, quarters)
        all_changes.extend(changes)
        time.sleep(0.5)
    aggregate = _aggregate_changes(all_changes, run_date)
    write_json(raw_dir / "sec13f_changes_by_institution.json", all_changes)
    write_json(raw_dir / "sec13f_aggregate.json", aggregate)
    return aggregate


def _collect_institution(name: str, cik: str, raw_dir: Path, quarters: int) -> list[dict[str, Any]]:
    submissions = _get_json(SEC_SUBMISSIONS_URL.format(cik=cik))
    write_json(raw_dir / f"sec_submissions_{cik}.json", submissions or {})
    filings = _recent_13f_filings(submissions or {}, quarters)
    holdings_by_period: list[dict[str, Any]] = []
    cik_int = str(int(cik))
    for filing in filings:
        accession = filing["accession"].replace("-", "")
        index = _get_json(SEC_ARCHIVES_INDEX_URL.format(cik_int=cik_int, accession=accession))
        write_json(raw_dir / f"sec_index_{cik}_{accession}.json", index or {})
        filename = _find_info_table(index or {})
        if not filename:
            continue
        xml_text = _get_text(SEC_ARCHIVES_FILE_URL.format(cik_int=cik_int, accession=accession, filename=filename))
        if not xml_text:
            continue
        (raw_dir / f"sec_13f_{cik}_{filing['period']}.xml").write_text(xml_text, encoding="utf-8", errors="ignore")
        holdings_by_period.append(
            {
                "institution": name,
                "cik": cik,
                "period": filing["period"],
                "holdings": _parse_info_table(xml_text),
            }
        )
        time.sleep(0.25)

    if len(holdings_by_period) < 2:
        return []
    holdings_by_period.sort(key=lambda item: item["period"], reverse=True)
    return _compare_periods(holdings_by_period[0], holdings_by_period[1])


def _recent_13f_filings(submissions: dict[str, Any], quarters: int) -> list[dict[str, str]]:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    report_dates = recent.get("reportDate") or recent.get("filingDate") or []
    filings = []
    for form, accession, report_date in zip(forms, accessions, report_dates):
        if str(form).upper().startswith("13F-HR") and accession and report_date:
            filings.append({"accession": accession, "period": report_date})
        if len(filings) >= quarters:
            break
    return filings


def _find_info_table(index: dict[str, Any]) -> str | None:
    items = index.get("directory", {}).get("item") or []
    xml_files = [item.get("name") for item in items if str(item.get("name", "")).lower().endswith(".xml")]
    preferred = [
        name
        for name in xml_files
        if "info" in name.lower() or "form13f" in name.lower() or "infotable" in name.lower()
    ]
    candidates = preferred or xml_files
    for name in candidates:
        if name and not name.lower().startswith("primary"):
            return name
    return candidates[0] if candidates else None


def _parse_info_table(xml_text: str) -> dict[str, dict[str, Any]]:
    holdings: dict[str, dict[str, Any]] = {}
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return holdings
    for info in root.iter():
        if _local_name(info.tag) != "infoTable":
            continue
        item = {
            "nameOfIssuer": _child_text(info, "nameOfIssuer"),
            "titleOfClass": _child_text(info, "titleOfClass"),
            "cusip": _child_text(info, "cusip"),
            "value": to_float(_child_text(info, "value")),
            "sshPrnamt": to_int(_child_text(info, "sshPrnamt")),
        }
        key = item["cusip"] or item["nameOfIssuer"]
        if key:
            holdings[str(key)] = item
    return holdings


def _compare_periods(current: dict[str, Any], previous: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    current_holdings = current["holdings"]
    previous_holdings = previous["holdings"]
    keys = sorted(set(current_holdings) | set(previous_holdings))
    for key in keys:
        cur = current_holdings.get(key)
        prev = previous_holdings.get(key)
        cur_shares = to_int(cur.get("sshPrnamt")) if cur else 0
        prev_shares = to_int(prev.get("sshPrnamt")) if prev else 0
        change = cur_shares - prev_shares
        if cur and not prev:
            status = "신규"
        elif prev and not cur:
            status = "청산"
        elif change > 0:
            status = "증가"
        elif change < 0:
            status = "감소"
        else:
            status = "변동없음"
        if status == "변동없음":
            continue
        pct = None if prev_shares == 0 else round(change / prev_shares * 100, 2)
        base = cur or prev or {}
        changes.append(
            {
                "institution": current["institution"],
                "cik": current["cik"],
                "current_period": current["period"],
                "previous_period": previous["period"],
                "nameOfIssuer": base.get("nameOfIssuer"),
                "titleOfClass": base.get("titleOfClass"),
                "cusip": base.get("cusip"),
                "value": base.get("value"),
                "current_shares": cur_shares,
                "previous_shares": prev_shares,
                "share_change": change,
                "change_pct": pct,
                "change_type": status,
            }
        )
    return changes


def _aggregate_changes(changes: list[dict[str, Any]], run_date: date) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for change in changes:
        key = change.get("cusip") or change.get("nameOfIssuer")
        if key:
            grouped[str(key)].append(change)

    records = []
    for key, items in grouped.items():
        new_count = sum(1 for item in items if item.get("change_type") == "신규")
        increased_count = sum(1 for item in items if item.get("change_type") == "증가")
        decreased_count = sum(1 for item in items if item.get("change_type") == "감소")
        exited_count = sum(1 for item in items if item.get("change_type") == "청산")
        total_change = sum(item.get("share_change") or 0 for item in items)
        total_current = sum(item.get("current_shares") or 0 for item in items)
        pct_values = [item.get("change_pct") for item in items if item.get("change_pct") is not None]
        score = new_count * 15 + increased_count * 10 - decreased_count * 6 - exited_count * 18
        if new_count + increased_count >= 2:
            score += (new_count + increased_count) * 4
        score += min(max(total_change, 0) / 1_000_000, 15)
        base = items[0]
        records.append(
            {
                "date": run_date.isoformat(),
                "country": "미국",
                "ticker": None,
                "company_name": base.get("nameOfIssuer"),
                "cusip": base.get("cusip"),
                "title_of_class": base.get("titleOfClass"),
                "new_institution_count": new_count,
                "increased_institution_count": increased_count,
                "decreased_institution_count": decreased_count,
                "exited_institution_count": exited_count,
                "total_share_change": total_change,
                "average_change_pct": round(sum(pct_values) / len(pct_values), 2) if pct_values else None,
                "total_current_shares": total_current,
                "famous_13f_score": round(score, 2),
                "investment_priority_score": round(score, 2),
                "institutions": sorted({str(item.get("institution")) for item in items if item.get("institution")}),
                "core_basis": _basis(new_count, increased_count, decreased_count, exited_count),
                "signals": ["13F 보유 증감"],
                "source": "SEC 13F",
                "source_url": "https://www.sec.gov/edgar/search/",
            }
        )
    records.sort(key=lambda item: item.get("famous_13f_score") or 0, reverse=True)
    return records


def _basis(new_count: int, increased_count: int, decreased_count: int, exited_count: int) -> str:
    parts = []
    if new_count:
        parts.append(f"신규 {new_count}개 기관")
    if increased_count:
        parts.append(f"증가 {increased_count}개 기관")
    if decreased_count:
        parts.append(f"감소 {decreased_count}개 기관")
    if exited_count:
        parts.append(f"청산 {exited_count}개 기관")
    return " / ".join(parts)


def _get_json(url: str) -> dict[str, Any] | None:
    text = _get_text(url)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _get_text(url: str) -> str | None:
    headers = {
        "User-Agent": os.getenv("SEC_USER_AGENT", "daily-stock-trend contact@example.com"),
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov" if "data.sec.gov" in url else "www.sec.gov",
    }
    try:
        response = requests.get(url, headers=headers, timeout=35)
        response.raise_for_status()
        return response.text
    except requests.RequestException:
        return None


def _child_text(node: ET.Element, wanted: str) -> str | None:
    for child in node.iter():
        if _local_name(child.tag) == wanted:
            return child.text.strip() if child.text else None
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _normalize_cik(value: Any) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(10) if digits else None
