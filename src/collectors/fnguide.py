from __future__ import annotations

import json
import re
import time
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.utils.io import ensure_dir
from src.utils.text import to_float


COMPANY_GUIDE_URL = "https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{code}"
CONSENSUS_URL = "https://wcomp.fnguide.com/CompanyInfo/Consensus?cmp_cd={code}"


def enrich_with_fnguide(row: dict[str, Any], raw_dir: Path) -> dict[str, Any]:
    code = _kr_code(row.get("ticker"))
    if not code:
        return row

    guide_html = _get_text(COMPANY_GUIDE_URL.format(code=code), encoding="utf-8")
    if guide_html:
        _write_raw(raw_dir / f"fnguide_{code}_main.html", guide_html)
        parsed = _parse_company_guide(guide_html)
        for key, value in parsed.items():
            if value is not None:
                if key == "company_name":
                    row[key] = value
                else:
                    row.setdefault(key, value)

    consensus_body = _get_text(CONSENSUS_URL.format(code=code), encoding="utf-8")
    if consensus_body:
        _write_raw(raw_dir / f"fnguide_{code}_consensus.txt", consensus_body)
        parsed = _parse_consensus(consensus_body)
        for key, value in parsed.items():
            if value is not None:
                row[key] = value

    if row.get("company_name"):
        row["company_name"] = _clean_company_name(row["company_name"])
    time.sleep(0.25)
    return row


def _get_text(url: str, encoding: str = "utf-8") -> str | None:
    headers = {
        "User-Agent": "Mozilla/5.0 daily-stock-trend/1.0",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    }
    try:
        response = requests.get(url, headers=headers, timeout=6)
        response.raise_for_status()
        response.encoding = encoding
        return response.text
    except requests.RequestException:
        return None


def _parse_company_guide(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    title = soup.find("title")
    name = None
    if title and title.text:
        name = _clean_company_name(title.text)
    if not name:
        h1 = soup.find(["h1", "h2"])
        name = _clean_company_name(h1.text) if h1 else None

    parsed = {
        "company_name": name,
        "forward_per": _number_near(text, ["Forward PER", "12M PER", "PER"]),
        "forward_peg": _number_near(text, ["PEG"]),
        "roe": _number_near(text, ["ROE"]),
        "roic": _number_near(text, ["ROIC"]),
        "fcf_margin": _number_near(text, ["FCF", "잉여현금흐름"]),
    }

    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        tables = []
    parsed.update(_parse_tables_for_metrics(tables))
    return parsed


def _parse_consensus(body: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    stripped = body.strip()
    if not stripped:
        return parsed
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        flat = _flatten(data)
        parsed["target_price"] = _first_number(flat, ["target", "TargetPrice", "TP", "목표"])
        parsed["forward_per"] = parsed.get("forward_per") or _first_number(flat, ["PER", "forwardPER"])
        parsed["expected_revenue_growth"] = _first_number(flat, ["salesGrowth", "revenueGrowth", "매출성장"])
        parsed["expected_eps_growth"] = _first_number(flat, ["epsGrowth", "EPS성장"])
        parsed["recent_report_broker"] = _first_text(flat, ["broker", "투자의견기관", "증권사"])
        parsed["recent_report_title"] = _first_text(flat, ["title", "제목", "report"])
        return parsed

    try:
        tables = pd.read_html(StringIO(body))
    except ValueError:
        tables = []
    parsed.update(_parse_tables_for_metrics(tables))
    return parsed


def _parse_tables_for_metrics(tables: list[pd.DataFrame]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for table in tables:
        text = " ".join(str(value) for value in table.to_numpy().ravel() if pd.notna(value))
        parsed.setdefault("target_price", _number_near(text, ["목표주가", "목표가", "Target Price"]))
        parsed.setdefault("forward_per", _number_near(text, ["12M PER", "Forward PER", "PER"]))
        parsed.setdefault("forward_peg", _number_near(text, ["PEG"]))
        parsed.setdefault("revenue_growth_yoy", _number_near(text, ["매출액증가율", "매출성장률", "Sales Growth"]))
        parsed.setdefault("eps_growth_yoy", _number_near(text, ["EPS증가율", "EPS Growth"]))
        parsed.setdefault("expected_revenue_growth", _number_near(text, ["예상매출성장률", "매출액 전망"]))
        parsed.setdefault("expected_eps_growth", _number_near(text, ["예상EPS성장률", "EPS 전망"]))
    return {key: value for key, value in parsed.items() if value is not None}


def _number_near(text: str, labels: list[str]) -> float | None:
    for label in labels:
        pattern = re.compile(rf"{re.escape(label)}\s*[:：]?\s*(-?\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            return to_float(match.group(1))
    return None


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.update(_flatten(item, f"{prefix}.{index}"))
    else:
        out[prefix] = value
    return out


def _first_number(flat: dict[str, Any], keys: list[str]) -> float | None:
    for wanted in keys:
        wanted_lower = wanted.lower()
        for key, value in flat.items():
            if wanted_lower in key.lower():
                number = to_float(value)
                if number is not None:
                    return number
    return None


def _first_text(flat: dict[str, Any], keys: list[str]) -> str | None:
    for wanted in keys:
        wanted_lower = wanted.lower()
        for key, value in flat.items():
            if wanted_lower in key.lower() and value not in (None, ""):
                return str(value).strip()
    return None


def _clean_company_name(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"\s*-\s*FnGuide.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\|.*$", "", text)
    return text or None


def _kr_code(value: Any) -> str | None:
    text = str(value or "").strip().upper().split(":", 1)[-1]
    if re.fullmatch(r"\d{1,6}", text):
        return text.zfill(6)
    return None


def _write_raw(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8", errors="ignore")
