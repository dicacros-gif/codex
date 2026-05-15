from __future__ import annotations

import re
import time
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, quote

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.utils.io import ensure_dir
from src.utils.text import to_float


FRGN_URL = "https://finance.naver.com/item/frgn.naver?code={code}&page=1&trader_day=20"
RESEARCH_URL = "https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode={code}"
HANKYUNG_URL = "https://markets.hankyung.com/consensus?searchWord={company}"


def enrich_with_naver(row: dict[str, Any], raw_dir: Path) -> dict[str, Any]:
    code = _kr_code(row.get("ticker"))
    if not code:
        return row

    frgn_html = _get_text(FRGN_URL.format(code=code), encoding="euc-kr")
    if frgn_html:
        _write_raw(raw_dir / f"naver_frgn_{code}.html", frgn_html)
        row.update({key: value for key, value in _parse_frgn(frgn_html).items() if value is not None})

    research_html = _get_text(RESEARCH_URL.format(code=code), encoding="euc-kr")
    if research_html:
        _write_raw(raw_dir / f"naver_research_{code}.html", research_html)
        report = _parse_research(research_html)
        if report:
            row.update(report)

    if not row.get("report_link"):
        company = row.get("company_name") or code
        row["report_link"] = HANKYUNG_URL.format(company=quote(str(company)))
        row["report_source"] = "한국경제 컨센서스 검색"

    time.sleep(0.25)
    return row


def _get_text(url: str, encoding: str) -> str | None:
    headers = {
        "User-Agent": "Mozilla/5.0 daily-stock-trend/1.0",
        "Accept": "text/html,*/*;q=0.8",
    }
    try:
        response = requests.get(url, headers=headers, timeout=12)
        response.raise_for_status()
        response.encoding = encoding
        return response.text
    except requests.RequestException:
        return None


def _parse_frgn(html: str) -> dict[str, Any]:
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return {}

    parsed: dict[str, Any] = {}
    for table in tables:
        if table.empty:
            continue
        columns = [str(col) for col in table.columns]
        if not any("외국인" in col for col in columns):
            continue

        numeric = table.copy()
        for column in numeric.columns:
            numeric[column] = numeric[column].map(to_float)

        foreign_cols = [col for col in numeric.columns if "외국인" in str(col) and "비율" not in str(col) and "지분" not in str(col)]
        institution_cols = [col for col in numeric.columns if "기관" in str(col)]
        rate_cols = [col for col in numeric.columns if "비율" in str(col) or "지분" in str(col)]

        if foreign_cols:
            parsed["foreign_net_buy"] = _sum_tail(numeric[foreign_cols[0]])
        if institution_cols:
            parsed["institution_net_buy"] = _sum_tail(numeric[institution_cols[0]])
        if rate_cols:
            parsed["foreign_ownership_rate"] = _last_number(numeric[rate_cols[0]])
        if parsed:
            parsed["supply_pattern"] = _supply_pattern(parsed.get("foreign_net_buy"), parsed.get("institution_net_buy"))
            break
    return parsed


def _parse_research(html: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "lxml")
    link = soup.find("a", href=re.compile(r"company_read\.naver"))
    if not link:
        return None
    href = urljoin("https://finance.naver.com", link.get("href", ""))
    row = link.find_parent("tr")
    cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")] if row else []
    title = link.get_text(" ", strip=True) or None
    broker = None
    for cell in cells:
        if cell and cell != title and not re.search(r"\d{2}\.\d{2}\.\d{2}", cell):
            broker = cell
            break
    return {
        "recent_report_broker": broker,
        "recent_report_title": title,
        "report_link": href,
        "report_source": "Naver Finance Research",
    }


def _sum_tail(series: pd.Series, periods: int = 20) -> float | None:
    values = [value for value in series.tail(periods).tolist() if value is not None and pd.notna(value)]
    if not values:
        return None
    return float(sum(values))


def _last_number(series: pd.Series) -> float | None:
    values = [value for value in series.tolist() if value is not None and pd.notna(value)]
    if not values:
        return None
    return float(values[-1])


def _supply_pattern(foreign: float | None, institution: float | None) -> str | None:
    if foreign is None and institution is None:
        return None
    f_pos = foreign is not None and foreign > 0
    i_pos = institution is not None and institution > 0
    if f_pos and i_pos:
        return "외국인+기관 동반 순매수"
    if f_pos:
        return "외국인 순매수 우위"
    if i_pos:
        return "기관 순매수 우위"
    return "외국인+기관 순매도"


def _kr_code(value: Any) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(6) if digits else None


def _write_raw(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8", errors="ignore")
