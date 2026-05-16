from __future__ import annotations

import json
import os
import re
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
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"


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
        manager = str(institution.get("manager") or name)
        guru_weight = to_float(institution.get("guru_weight")) or 1.0
        changes = _collect_institution(name, cik, raw_dir, quarters, manager, guru_weight)
        all_changes.extend(changes)
        time.sleep(0.5)
    ticker_map = _load_sec_ticker_map(raw_dir)
    aggregate = _aggregate_changes(all_changes, run_date, ticker_map)
    write_json(raw_dir / "sec13f_changes_by_institution.json", all_changes)
    write_json(raw_dir / "sec13f_aggregate.json", aggregate)
    return aggregate


def _collect_institution(
    name: str,
    cik: str,
    raw_dir: Path,
    quarters: int,
    manager: str,
    guru_weight: float,
) -> list[dict[str, Any]]:
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
                "manager": manager,
                "guru_weight": guru_weight,
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
    current_total_value = _portfolio_total_value(current_holdings)
    previous_total_value = _portfolio_total_value(previous_holdings)
    keys = sorted(set(current_holdings) | set(previous_holdings))
    for key in keys:
        cur = current_holdings.get(key)
        prev = previous_holdings.get(key)
        cur_shares = to_int(cur.get("sshPrnamt")) if cur else 0
        prev_shares = to_int(prev.get("sshPrnamt")) if prev else 0
        cur_value = to_float(cur.get("value")) if cur else 0
        prev_value = to_float(prev.get("value")) if prev else 0
        current_weight = _position_weight_pct(cur_value, current_total_value)
        previous_weight = _position_weight_pct(prev_value, previous_total_value)
        weight_change = round(current_weight - previous_weight, 4)
        change = cur_shares - prev_shares
        if cur and not prev:
            status = "신규"
        elif prev and not cur:
            status = "청산"
        elif change > 0:
            status = "증가"
        elif change < 0:
            status = "감소"
        elif weight_change > 0:
            status = "비중증가"
        else:
            status = "변동없음"
        if status == "변동없음":
            continue
        pct = None if prev_shares == 0 else round(change / prev_shares * 100, 2)
        base = cur or prev or {}
        changes.append(
            {
                "institution": current["institution"],
                "manager": current.get("manager"),
                "guru_weight": current.get("guru_weight") or 1.0,
                "cik": current["cik"],
                "current_period": current["period"],
                "previous_period": previous["period"],
                "nameOfIssuer": base.get("nameOfIssuer"),
                "titleOfClass": base.get("titleOfClass"),
                "cusip": base.get("cusip"),
                "value": cur_value,
                "previous_value": prev_value,
                "current_shares": cur_shares,
                "previous_shares": prev_shares,
                "share_change": change,
                "change_pct": pct,
                "current_position_weight_pct": current_weight,
                "previous_position_weight_pct": previous_weight,
                "position_weight_change_pct": weight_change,
                "change_type": status,
            }
        )
    return changes


def _portfolio_total_value(holdings: dict[str, dict[str, Any]]) -> float:
    return float(sum(to_float(item.get("value")) or 0 for item in holdings.values()))


def _position_weight_pct(value: float | None, total_value: float | None) -> float:
    if not value or not total_value:
        return 0.0
    return round(float(value) / float(total_value) * 100, 4)


def _aggregate_changes(
    changes: list[dict[str, Any]],
    run_date: date,
    ticker_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    ticker_map = ticker_map or {}
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
        weight_increased_items = [item for item in items if (item.get("position_weight_change_pct") or 0) > 0]
        weight_decreased_items = [item for item in items if (item.get("position_weight_change_pct") or 0) < 0]
        weight_delta_values = [
            item.get("position_weight_change_pct")
            for item in items
            if item.get("position_weight_change_pct") is not None
        ]
        total_change = sum(item.get("share_change") or 0 for item in items)
        total_current = sum(item.get("current_shares") or 0 for item in items)
        pct_values = [item.get("change_pct") for item in items if item.get("change_pct") is not None]
        guru_position_score = _guru_position_score(weight_increased_items)
        score = new_count * 15 + increased_count * 10 - decreased_count * 6 - exited_count * 18
        if new_count + increased_count >= 2:
            score += (new_count + increased_count) * 4
        score += min(max(total_change, 0) / 1_000_000, 15)
        score += guru_position_score
        if len(weight_increased_items) >= 2:
            score += len(weight_increased_items) * 5
        score_100 = _score_100(score)
        base = items[0]
        issuer = base.get("nameOfIssuer")
        ticker = _lookup_ticker(ticker_map, issuer, base.get("cusip"))
        records.append(
            {
                "date": run_date.isoformat(),
                "country": "미국",
                "ticker": ticker,
                "company_name": issuer,
                "cusip": base.get("cusip"),
                "title_of_class": base.get("titleOfClass"),
                "new_institution_count": new_count,
                "increased_institution_count": increased_count,
                "decreased_institution_count": decreased_count,
                "exited_institution_count": exited_count,
                "position_weight_increased_count": len(weight_increased_items),
                "position_weight_decreased_count": len(weight_decreased_items),
                "max_position_weight_change_pct": _max_positive_weight_change(weight_increased_items),
                "average_position_weight_change_pct": (
                    round(sum(weight_delta_values) / len(weight_delta_values), 2) if weight_delta_values else None
                ),
                "guru_position_score": round(guru_position_score, 2),
                "top_position_weight_changes": _top_position_weight_changes(weight_increased_items),
                "total_share_change": total_change,
                "average_change_pct": round(sum(pct_values) / len(pct_values), 2) if pct_values else None,
                "total_current_shares": total_current,
                "famous_13f_score": score_100,
                "investment_priority_score": score_100,
                "institutions": sorted({str(item.get("institution")) for item in items if item.get("institution")}),
                "core_basis": _basis(
                    new_count,
                    increased_count,
                    decreased_count,
                    exited_count,
                    len(weight_increased_items),
                    _top_position_weight_changes(weight_increased_items),
                ),
                "signals": ["13F 보유 증감"],
                "source": "SEC 13F",
                "source_url": "https://www.sec.gov/edgar/search/",
            }
        )
    records.sort(key=lambda item: item.get("famous_13f_score") or 0, reverse=True)
    return records


def _guru_position_score(items: list[dict[str, Any]]) -> float:
    score = 0.0
    for item in items:
        delta = max(to_float(item.get("position_weight_change_pct")) or 0, 0)
        guru_weight = to_float(item.get("guru_weight")) or 1.0
        score += min(delta, 8) * guru_weight * 3
        if str(item.get("manager") or "").lower() == "bill ackman":
            score += 25
    return score


def _score_100(value: Any) -> float:
    number = to_float(value)
    if number is None:
        return 0.0
    return round(max(0.0, min(100.0, number)), 2)


def _max_positive_weight_change(items: list[dict[str, Any]]) -> float | None:
    values = [to_float(item.get("position_weight_change_pct")) for item in items]
    values = [value for value in values if value is not None and value > 0]
    return round(max(values), 2) if values else None


def _top_position_weight_changes(items: list[dict[str, Any]], limit: int = 5) -> str | None:
    ranked = sorted(items, key=lambda item: item.get("position_weight_change_pct") or 0, reverse=True)
    parts = []
    for item in ranked[:limit]:
        delta = to_float(item.get("position_weight_change_pct"))
        current = to_float(item.get("current_position_weight_pct"))
        if delta is None or delta <= 0:
            continue
        manager = item.get("manager") or item.get("institution")
        institution = item.get("institution")
        label = str(manager)
        if institution and institution != manager:
            label = f"{label}/{institution}"
        current_text = f", 현재 {_pct_label(current, '%')}" if current is not None else ""
        parts.append(f"{label} +{_pct_label(delta, '%p')}{current_text}")
    return " / ".join(parts) if parts else None


def _pct_label(value: float | None, suffix: str) -> str:
    if value is None:
        return ""
    absolute = abs(value)
    if 0 < absolute < 0.5:
        return f"<1{suffix}"
    return f"{int(round(value))}{suffix}"


def _load_sec_ticker_map(raw_dir: Path) -> dict[str, str]:
    data = _get_json(SEC_COMPANY_TICKERS_URL) or {}
    write_json(raw_dir / "sec_company_tickers_exchange.json", data)
    fields = data.get("fields") or []
    rows = data.get("data") or []
    try:
        name_index = fields.index("name")
        ticker_index = fields.index("ticker")
    except ValueError:
        return {}
    output: dict[str, str] = {}
    rows = sorted(rows, key=_ticker_row_priority)
    for row in rows:
        if not isinstance(row, list) or len(row) <= max(name_index, ticker_index):
            continue
        ticker = str(row[ticker_index] or "").strip().upper()
        if not ticker:
            continue
        for name in _ticker_match_keys(row[name_index]):
            output.setdefault(name, ticker)
    for name, ticker in SEC_TICKER_ALIASES.items():
        output.setdefault(name, ticker)
    return output


def _ticker_row_priority(row: Any) -> tuple[int, int, str]:
    if not isinstance(row, list) or len(row) < 4:
        return (99, 99, "")
    ticker = str(row[2] or "").strip().upper()
    exchange = str(row[3] or "").strip().upper()
    exchange_rank = {"NYSE": 0, "NASDAQ": 1, "NYSE ARCA": 2, "NYSEAMERICAN": 3, "OTC": 8}.get(exchange, 5)
    class_rank = 3 if "-" in ticker else 0
    return (exchange_rank, class_rank, ticker)


def _lookup_ticker(ticker_map: dict[str, str], issuer: Any, cusip: Any = None) -> str | None:
    cusip_key = str(cusip or "").strip().upper()
    if cusip_key in SEC_CUSIP_TICKER_ALIASES:
        return SEC_CUSIP_TICKER_ALIASES[cusip_key]
    for key in _ticker_match_keys(issuer):
        ticker = SEC_TICKER_ALIASES.get(key) or ticker_map.get(key)
        if ticker:
            return ticker
    issuer_key = _issuer_match_key(issuer)
    if len(issuer_key) < 6:
        return None
    matches: list[tuple[int, str]] = []
    issuer_tokens = issuer_key.split()
    for name, ticker in ticker_map.items():
        if len(name) < 6:
            continue
        name_tokens = name.split()
        if not name_tokens or not issuer_tokens or name_tokens[0] != issuer_tokens[0]:
            continue
        if name.startswith(issuer_key) or issuer_key.startswith(name):
            matches.append((abs(len(name) - len(issuer_key)), ticker))
            continue
        if len(name_tokens) >= 2 and len(issuer_tokens) >= 2 and name_tokens[:2] == issuer_tokens[:2]:
            overlap = sum(1 for token in issuer_tokens if token in name_tokens)
            if overlap >= min(3, len(issuer_tokens)):
                matches.append((10 + abs(len(name) - len(issuer_key)), ticker))
    return sorted(matches)[0][1] if matches else None


def _ticker_match_keys(value: Any) -> list[str]:
    keys = []
    for key in (_normalize_issuer_name(value), _issuer_match_key(value)):
        if key and key not in keys:
            keys.append(key)
    return keys


def _normalize_issuer_name(value: Any) -> str:
    text = str(value or "").upper()
    text = text.replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    text = re.sub(r"\b(CL|CLASS|COM|COMMON|SHS|SHARES|STOCK|ORD|NEW|THE)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _issuer_match_key(value: Any) -> str:
    text = _normalize_issuer_name(value)
    replacements = {
        "HLDNGS": "HOLDINGS",
        "HLDGS": "HOLDINGS",
        "HLDG": "HOLDINGS",
        "MGMT": "MANAGEMENT",
        "MANAGMT": "MANAGEMENT",
        "GEN": "GENERAL",
        "ELEC": "ELECTRIC",
        "INDS": "INDUSTRIES",
        "INDL": "INDUSTRIAL",
        "INSTRS": "INSTRUMENTS",
        "MATLS": "MATERIALS",
        "MANUFAC": "MANUFACTURING",
        "MFG": "MANUFACTURING",
        "INTL": "INTERNATIONAL",
        "TECH": "TECHNOLOGY",
        "STR": "STREET",
        "DISC": "DISCOUNT",
        "FINL": "FINANCIAL",
        "NATL": "NATIONAL",
        "NAT": "NATURAL",
        "PPTY": "PROPERTY",
        "PPTYS": "PROPERTIES",
        "CTRS": "CENTERS",
        "SVCS": "SERVICES",
        "SVSC": "SERVICES",
        "SYS": "SYSTEMS",
        "WKS": "WORKS",
        "PRODS": "PRODUCTS",
        "APT": "APARTMENT",
        "CMNTYS": "COMMUNITIES",
        "AMER": "AMERICA",
        "AMERN": "AMERICAN",
        "WTR": "WATER",
        "UTILS": "UTILITIES",
        "SVC": "SERVICE",
        "MTR": "MOTOR",
        "MNG": "MINING",
        "BK": "BANK",
        "MTNS": "MOUNTAINS",
        "INS": "INSURANCE",
        "DEVS": "DEVELOPMENTS",
        "TRANSN": "TRANSPORTATION",
        "LABS": "LABORATORIES",
        "COS": "COMPANIES",
    }
    for source, target in replacements.items():
        text = re.sub(rf"\b{source}\b", target, text)
    text = re.sub(
        r"\b(CORP|CORPORATION|INC|INCORPORATED|PLC|LTD|LIMITED|CO|COMPANY|LP|LLC|HOLDINGS|GROUP|NV|N V|SA|S A|AG|DE|DEL|OR|PA|VA|UK|NEW|ETF|TRUST|TR|T)\b",
        " ",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text


SEC_TICKER_ALIASES = {
    "STATE STREET SPDR S P 500": "SPY",
    "STATE STREET SPDR S P 500 ETF": "SPY",
    "STATE STREET SPDR S P 500 ETF T": "SPY",
    "SPDR S P 500": "SPY",
    "D R HORTON": "DHI",
    "MACYS": "M",
    "CRH": "CRH",
    "BOBS DISCOUNT FURNITURE": "BOBS",
    "PETROLEO BRASILEIRO": "PBR",
    "BANK AMERICA": "BAC",
    "CADENCE BANK": "CADE",
    "POTLATCHDELTIC": "PCH",
    "TELEFONAKTIEBOLAGET LM ERICS": "ERIC",
    "TELEFONAKTIEBOLAGET LM ERICSSON": "ERIC",
    "MIDCAP FINANCIAL INVESTMENT": "MFIC",
    "MIDCAP FINANCIAL INVSTMNT": "MFIC",
    "MADDEN STEVEN": "SHOO",
    "FORGE GLOBAL": "FRGE",
    "AMICUS THERAPEUTICS": "FOLD",
    "SEMRUSH": "SEMR",
    "EXACT SCIENCES": "EXAS",
    "WP CAREY": "WPC",
    "ONESTREAM": "OS",
}


SEC_CUSIP_TICKER_ALIASES = {
    "12740C103": "CADE",
    "737630103": "PCH",
    "294821608": "ERIC",
    "03761U502": "MFIC",
    "81369Y506": "XLE",
    "81369Y605": "XLF",
    "78464A888": "XHB",
    "556269108": "SHOO",
    "34629L202": "FRGE",
    "03152W109": "FOLD",
    "81686C104": "SEMR",
    "30063P105": "EXAS",
    "92936U109": "WPC",
    "68278B107": "OS",
}


def _basis(
    new_count: int,
    increased_count: int,
    decreased_count: int,
    exited_count: int,
    weight_increased_count: int,
    top_position_weight_changes: str | None,
) -> str:
    parts = []
    if new_count:
        parts.append(f"신규 {new_count}개 기관")
    if increased_count:
        parts.append(f"증가 {increased_count}개 기관")
    if weight_increased_count:
        parts.append(f"포지션 비중 증가 {weight_increased_count}개 대가/기관")
    if top_position_weight_changes:
        parts.append(top_position_weight_changes)
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
        response = requests.get(url, headers=headers, timeout=8)
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
