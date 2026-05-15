from __future__ import annotations

import re
import time
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.utils.io import ensure_dir
from src.utils.text import to_float


FRGN_URL = "https://finance.naver.com/item/frgn.naver?code={code}&page=1&trader_day=20"
MAIN_URL = "https://finance.naver.com/item/main.naver?code={code}"
RESEARCH_URL = "https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode={code}"
HANKYUNG_URL = "https://markets.hankyung.com/consensus?searchWord={company}"


def enrich_with_naver(row: dict[str, Any], raw_dir: Path) -> dict[str, Any]:
    code = _kr_code(row.get("ticker"))
    if not code:
        return row

    main_html = _get_text(MAIN_URL.format(code=code), encoding="utf-8")
    if main_html:
        _write_raw(raw_dir / f"naver_main_{code}.html", main_html)
        row.update({key: value for key, value in _parse_main(main_html).items() if value is not None and row.get(key) in (None, "", [])})

    frgn_html = _get_text(FRGN_URL.format(code=code), encoding="utf-8")
    if frgn_html:
        _write_raw(raw_dir / f"naver_frgn_{code}.html", frgn_html)
        row.update({key: value for key, value in _parse_frgn(frgn_html).items() if value is not None})

    research_html = _get_text(RESEARCH_URL.format(code=code), encoding="utf-8")
    if research_html:
        _write_raw(raw_dir / f"naver_research_{code}.html", research_html)
        report = _parse_research(research_html, code)
        if report:
            row.update(report)

    if not row.get("report_link"):
        _apply_hankyung_fallback(row, code)

    time.sleep(0.25)
    return row


def _get_text(url: str, encoding: str) -> str | None:
    headers = {
        "User-Agent": "Mozilla/5.0 daily-stock-trend/1.0",
        "Accept": "text/html,*/*;q=0.8",
    }
    try:
        response = requests.get(url, headers=headers, timeout=6)
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


def _parse_main(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    parsed = {
        "market_cap": _naver_market_cap(text),
        "trailing_per": _number_after(text, "PER"),
        "forward_per": _number_after(text, "추정PER"),
        "pbr": _number_after(text, "PBR"),
        "eps_ttm": _number_after(text, "EPS"),
        "forward_eps": _number_after(text, "추정EPS"),
        "dividend_yield": _number_after(text, "배당수익률"),
        "foreign_ownership_rate": _number_after(text, "외국인소진율"),
    }
    title = soup.find("title")
    if title and title.text:
        naver_title = re.sub(r"\s*:\s*네이버.*$", "", title.text).strip()
        if "\ufffd" not in naver_title:
            parsed["naver_title"] = naver_title
    close = _number_after(text, "현재가")
    if close is not None:
        parsed["close"] = close
    high = _number_after(text, "52주최고")
    if high is not None:
        parsed["high_52w"] = high
    return parsed


def _parse_research(html: str, code: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "lxml")
    link = soup.find("a", href=re.compile(r"company_read\.naver"))
    if not link:
        return None
    href = urljoin("https://finance.naver.com", link.get("href", ""))
    if not _is_valid_naver_report_link(href, code):
        return None
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


def _is_valid_naver_report_link(url: str, code: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc != "finance.naver.com" or parsed.path != "/company_read.naver":
        return False
    query_code = (parse_qs(parsed.query).get("itemCode") or [""])[0]
    if _kr_code(query_code) != code:
        return False
    body = _get_text(url, encoding="utf-8")
    if not body:
        return False
    soup = BeautifulSoup(body, "lxml")
    if soup.select_one(".error_content"):
        return False
    title = soup.find("title")
    title_text = title.get_text(" ", strip=True) if title else ""
    if "네이버 :: 세상의 모든 지식" in title_text:
        return False
    return bool(soup.get_text(" ", strip=True))


def _apply_hankyung_fallback(row: dict[str, Any], code: str) -> None:
    company = _fallback_company_name(row, code)
    row["report_link"] = HANKYUNG_URL.format(company=quote(str(company)))
    row["report_source"] = "한국경제 컨센서스 검색"
    row["recent_report_broker"] = "한국경제"
    row["recent_report_title"] = "컨센서스 검색"


def _fallback_company_name(row: dict[str, Any], code: str) -> str:
    for key in ("naver_title", "company_name", "ticker"):
        value = row.get(key)
        if value in (None, ""):
            continue
        text = re.sub(r"\s*:\s*(?:Npay\s*)?증권.*$", "", str(value)).strip()
        text = re.sub(r"\s*:\s*네이버.*$", "", text).strip()
        if text and "\ufffd" not in text:
            return text
    return code


def _naver_market_cap(text: str) -> float | None:
    match = re.search(r"시가총액\s*([\d,]+)\s*억원", text)
    if not match:
        return None
    number = to_float(match.group(1))
    return number * 100_000_000 if number is not None else None


def _number_after(text: str, label: str) -> float | None:
    pattern = re.compile(rf"{re.escape(label)}\s*[:：]?\s*(-?\d[\d,]*(?:\.\d+)?)\s*%?")
    match = pattern.search(text)
    if not match:
        return None
    return to_float(match.group(1))


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
