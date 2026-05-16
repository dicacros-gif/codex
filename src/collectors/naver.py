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
NAVER_NEWS_URL = "https://finance.naver.com/item/news.naver?code={code}"
NAVER_MAIN_ENCODING = "utf-8"
NAVER_ENCODING = "euc-kr"
NON_ANALYST_REPORT_SOURCES = (
    "NICE평가정보",
    "나이스디앤비",
    "한국기술신용평가",
    "한국기업데이터",
    "KISVALUE",
    "에프앤가이드",
    "FnGuide",
)


def enrich_with_naver(row: dict[str, Any], raw_dir: Path) -> dict[str, Any]:
    code = _kr_code(row.get("ticker"))
    if not code:
        return row

    main_html = _get_text(MAIN_URL.format(code=code), encoding=NAVER_MAIN_ENCODING)
    if main_html:
        _write_raw(raw_dir / f"naver_main_{code}.html", main_html)
        main_data = _parse_main(main_html)
        if main_data.get("naver_title"):
            main_data["company_name"] = main_data["naver_title"]
        for key, value in main_data.items():
            if value is None:
                continue
            if key in {"company_name", "naver_title"}:
                row[key] = value
            elif row.get(key) in (None, "", []):
                row[key] = value

    frgn_html = _get_text(FRGN_URL.format(code=code), encoding=NAVER_ENCODING)
    if frgn_html:
        _write_raw(raw_dir / f"naver_frgn_{code}.html", frgn_html)
        row.update({key: value for key, value in _parse_frgn(frgn_html).items() if value is not None})

    research_html = _get_text(RESEARCH_URL.format(code=code), encoding=NAVER_ENCODING)
    if research_html:
        _write_raw(raw_dir / f"naver_research_{code}.html", research_html)
        report = _parse_research(research_html, code)
        if report:
            row.update(report)

    if not row.get("report_link"):
        _apply_report_or_news_fallback(row, code, raw_dir)

    time.sleep(0.25)
    return row


def enrich_name_with_naver(row: dict[str, Any], raw_dir: Path) -> dict[str, Any]:
    code = _kr_code(row.get("ticker"))
    if not code or has_hangul(row.get("company_name")):
        return row

    main_html = _get_text(MAIN_URL.format(code=code), encoding=NAVER_MAIN_ENCODING)
    if not main_html:
        return row
    _write_raw(raw_dir / f"naver_main_{code}.html", main_html)
    title = _parse_main(main_html).get("naver_title")
    if title:
        row["naver_title"] = title
        row["company_name"] = title
    time.sleep(0.08)
    return row


def has_hangul(value: Any) -> bool:
    return any("\uac00" <= char <= "\ud7a3" for char in str(value or ""))


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
        rate_cols = [col for col in numeric.columns if "비율" in str(col) or "지분" in str(col) or "보유율" in str(col) or "소진율" in str(col)]

        if foreign_cols:
            parsed["foreign_net_buy_5d"] = _sum_recent(numeric[foreign_cols[0]], 5)
            parsed["foreign_net_buy_20d"] = _sum_recent(numeric[foreign_cols[0]], 20)
            parsed["foreign_net_buy"] = parsed["foreign_net_buy_20d"]
        if institution_cols:
            parsed["institution_net_buy_5d"] = _sum_recent(numeric[institution_cols[0]], 5)
            parsed["institution_net_buy_20d"] = _sum_recent(numeric[institution_cols[0]], 20)
            parsed["institution_net_buy"] = parsed["institution_net_buy_20d"]
        if parsed.get("foreign_net_buy_5d") is not None or parsed.get("institution_net_buy_5d") is not None:
            parsed["net_supply_5d"] = (parsed.get("foreign_net_buy_5d") or 0) + (parsed.get("institution_net_buy_5d") or 0)
        if parsed.get("foreign_net_buy_20d") is not None or parsed.get("institution_net_buy_20d") is not None:
            parsed["net_supply_20d"] = (parsed.get("foreign_net_buy_20d") or 0) + (parsed.get("institution_net_buy_20d") or 0)
        if rate_cols:
            parsed["foreign_ownership_rate"] = _first_number(numeric[rate_cols[0]])
            parsed["foreign_ownership_change_20d"] = _change_recent(numeric[rate_cols[0]], 20)
        if parsed:
            parsed["supply_pattern"] = _supply_pattern(parsed.get("foreign_net_buy"), parsed.get("institution_net_buy"))
            break
    return parsed


def _parse_main(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    market_cap_100m = _table_number(soup, ["시가총액"])
    trailing_pair = _per_eps_pair(soup, "PER")
    forward_pair = _per_eps_pair(soup, "추정PER")
    parsed = {
        "market_cap": market_cap_100m * 100_000_000 if market_cap_100m is not None else _naver_market_cap(text),
        "trailing_per": trailing_pair[0] or _table_number(soup, ["PER"]) or _number_after(text, "PER"),
        "forward_per": forward_pair[0] or _number_after(text, "추정PER"),
        "pbr": _table_number(soup, ["PBR"]) or _number_after(text, "PBR"),
        "eps_ttm": trailing_pair[1] or _table_number(soup, ["EPS"]) or _number_after(text, "EPS"),
        "forward_eps": forward_pair[1] or _number_after(text, "추정EPS"),
        "dividend_yield": _table_number(soup, ["배당수익률"]) or _number_after(text, "배당수익률"),
        "foreign_ownership_rate": _table_number(soup, ["외국인소진율"]) or _number_after(text, "외국인소진율"),
    }
    title = soup.find("title")
    if title and title.text:
        naver_title = re.sub(r"\s*:\s*네이버.*$", "", title.text).strip()
        naver_title = re.sub(r"\s*:\s*Npay\s*증권.*$", "", naver_title).strip()
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
    candidates = []
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
        detail_link = row.find("a", href=re.compile(r"company_read\.naver"))
        if not detail_link or not cells:
            continue
        href = urljoin("https://finance.naver.com/research/", detail_link.get("href", ""))
        if not _is_valid_naver_report_url(href, code):
            continue
        pdf_link = row.find("a", href=re.compile(r"stock-research/company/.+\.pdf"))
        pdf_href = urljoin("https://finance.naver.com", pdf_link.get("href", "")) if pdf_link else None
        candidates.append((row, cells, detail_link, href, pdf_href))

    if not candidates:
        return None

    for row, cells, link, href, pdf_href in candidates:
        detail = _parse_report_detail(href)
        if not detail:
            continue
        title = detail.get("recent_report_title") or link.get_text(" ", strip=True) or None
        broker = detail.get("recent_report_broker")
        for cell in cells:
            if broker:
                break
            if cell and cell != title and not re.search(r"\d{2}\.\d{2}\.\d{2}", cell):
                broker = cell
                break
        if _is_company_profile_report(broker, title):
            continue
        output = {
            "recent_report_broker": broker,
            "recent_report_title": title,
            "report_link": pdf_href or href,
            "report_detail_link": href,
            "report_source": "Naver Finance Research",
        }
        for key in ("target_price", "analyst_opinion"):
            if detail.get(key) is not None:
                output[key] = detail[key]
        return output
    return None


def _is_company_profile_report(broker: Any, title: Any) -> bool:
    broker_text = str(broker or "")
    title_text = str(title or "")
    if any(source in broker_text for source in NON_ANALYST_REPORT_SOURCES):
        return True
    profile_words = ("기업개요", "기업현황", "기업분석", "사업 현황", "전문기업", "기술분석보고서")
    return any(word in title_text for word in profile_words) and not re.search(r"목표|투자의견|실적|Review|프리뷰|전망", title_text, re.IGNORECASE)


def _is_valid_naver_report_url(url: str, code: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc != "finance.naver.com" or parsed.path != "/research/company_read.naver":
        return False
    query_code = (parse_qs(parsed.query).get("itemCode") or [""])[0]
    return _kr_code(query_code) == code


def _parse_report_detail(url: str) -> dict[str, Any] | None:
    body = _get_text(url, encoding=NAVER_ENCODING)
    if not body:
        return None
    soup = BeautifulSoup(body, "lxml")
    if soup.select_one(".error_content"):
        return None
    title = soup.find("title")
    title_text = title.get_text(" ", strip=True) if title else ""
    if "네이버 :: 세상의 모든 지식" in title_text:
        return None

    parsed: dict[str, Any] = {}
    title_match = re.search(r"종목분석\s*-\s*(.*?)\s*:\s*Npay", title_text)
    if title_match:
        parsed["recent_report_title"] = title_match.group(1).strip()
    source = soup.select_one("p.source")
    if source:
        source_text = source.get_text(" ", strip=True)
        broker = source_text.split("|", 1)[0].strip()
        if broker:
            parsed["recent_report_broker"] = broker
    summary_cell = soup.find("td")
    summary_text = summary_cell.get_text(" ", strip=True) if summary_cell else ""
    target_match = re.search(r"목표가\s*([\d,]+)", summary_text)
    if target_match:
        parsed["target_price"] = to_float(target_match.group(1))
    opinion_match = re.search(r"투자의견\s*([^|]+)", summary_text)
    if opinion_match:
        opinion = opinion_match.group(1).strip()
        if opinion and "없음" not in opinion:
            parsed["analyst_opinion"] = opinion
    return parsed if parsed.get("recent_report_title") or parsed.get("recent_report_broker") else None


def _apply_report_or_news_fallback(row: dict[str, Any], code: str, raw_dir: Path) -> None:
    company = _fallback_company_name(row, code)
    hankyung_url = HANKYUNG_URL.format(company=quote(str(company)))
    hankyung_html = _get_text(hankyung_url, encoding="utf-8")
    if hankyung_html:
        _write_raw(raw_dir / f"hankyung_consensus_{code}.html", hankyung_html)
        report = _parse_hankyung_consensus(hankyung_html, code, str(company))
        if report:
            row.update(report)
            return

    row["report_link"] = NAVER_NEWS_URL.format(code=code)
    row["report_source"] = "네이버증권 뉴스"
    row["recent_report_broker"] = "네이버증권"
    row["recent_report_title"] = "종목 뉴스"


def _parse_hankyung_consensus(html_text: str, code: str, company: str) -> dict[str, Any] | None:
    normalized = html_text.replace("\\u002F", "/").replace("\\/", "/")
    scope = _nuxt_scope(normalized)
    company_key = _match_key(company)
    best: dict[str, Any] | None = None
    for match in re.finditer(r"\{[^{}]*REPORT_TITLE:[^{}]*REPORT_FILEPATH:[^{}]*\}", normalized, re.DOTALL):
        item = match.group(0)
        title = _extract_js_field(item, "REPORT_TITLE", scope)
        link = _extract_js_field(item, "REPORT_FILEPATH", scope)
        business_code = _extract_js_field(item, "BUSINESS_CODE", scope)
        business_name = _extract_js_field(item, "BUSINESS_NAME", scope)
        if not title or not link:
            continue
        title_key = _match_key(title)
        name_key = _match_key(business_name or "")
        is_match = code == _kr_code(business_code) or (company_key and (company_key == name_key or company_key in name_key or name_key in company_key))
        if not is_match:
            continue
        broker = _extract_js_field(item, "OFFICE_NAME", scope) or "한국경제"
        report = {
            "recent_report_broker": broker,
            "recent_report_title": _clean_js_text(title),
            "report_link": _absolute_hankyung_link(link),
            "report_source": "한국경제 컨센서스",
        }
        for source, target in (
            ("TARGET_STOCK_PRICES", "target_price"),
            ("GRADE_VALUE", "analyst_opinion"),
            ("STOCK_PRE_PER", "forward_per"),
            ("STOCK_PRE_PBR", "pbr"),
            ("STOCK_PRE_ROE", "roe"),
        ):
            value = _extract_js_field(item, source, scope)
            if value not in (None, "", "N/A"):
                numeric = to_float(value)
                report[target] = numeric if numeric is not None else value
        best = report
        if code == _kr_code(business_code) or f"({code})" in title:
            break
    return best


def _absolute_hankyung_link(value: str) -> str:
    link = _clean_js_text(value)
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return urljoin("https://markets.hankyung.com", link)


def _nuxt_scope(text: str) -> dict[str, str]:
    match = re.search(r"window\.__NUXT__=\(function\((?P<params>[^)]*)\)\{.*?\}\((?P<args>.*)\)\);</script>", text, re.DOTALL)
    if not match:
        return {}
    params = [part.strip() for part in match.group("params").split(",") if part.strip()]
    args = _split_js_args(match.group("args"))
    return {
        param: value
        for param, raw in zip(params, args)
        if (value := _decode_js_literal(raw)) not in (None, "")
    }


def _split_js_args(value: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    quote_char: str | None = None
    escaped = False
    for char in value:
        current.append(char)
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote_char:
            if char == quote_char:
                quote_char = None
            continue
        if char in {"'", '"'}:
            quote_char = char
            continue
        if char == ",":
            current.pop()
            args.append("".join(current).strip())
            current = []
    if current:
        args.append("".join(current).strip())
    return args


def _extract_js_field(item: str, key: str, scope: dict[str, str]) -> str | None:
    match = re.search(rf"{re.escape(key)}\s*:\s*(?P<value>\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[A-Za-z_$][\w$]*|-?\d+(?:\.\d+)?)", item)
    if not match:
        return None
    return _decode_js_literal(match.group("value"), scope)


def _decode_js_literal(value: str, scope: dict[str, str] | None = None) -> str | None:
    token = value.strip()
    if token in {"null", "undefined", "false"}:
        return None
    if token == "true":
        return "true"
    if len(token) >= 2 and token[0] in {"'", '"'} and token[-1] == token[0]:
        return _clean_js_text(token[1:-1])
    if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return token
    return (scope or {}).get(token)


def _clean_js_text(value: str) -> str:
    text = value.replace("\\u002F", "/").replace("\\/", "/")
    text = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda match: chr(int(match.group(1), 16)), text)
    text = text.replace('\\"', '"').replace("\\'", "'").replace("\\r", " ").replace("\\n", " ").replace("\\t", " ")
    return re.sub(r"\s+", " ", text).strip()


def _match_key(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()


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


def _table_number(soup: BeautifulSoup, labels: list[str]) -> float | None:
    for header in soup.find_all(["th", "dt"]):
        header_text = header.get_text(" ", strip=True)
        if not any(label in header_text for label in labels):
            continue
        parent = header.find_parent("tr") or header.find_parent("dl")
        cells = parent.find_all(["td", "dd"]) if parent else []
        for cell in cells:
            number = to_float(cell.get_text(" ", strip=True))
            if number is not None:
                return number
    return None


def _per_eps_pair(soup: BeautifulSoup, label: str) -> tuple[float | None, float | None]:
    for header in soup.find_all("th"):
        header_text = header.get_text(" ", strip=True)
        if label not in header_text or "EPS" not in header_text:
            continue
        if label == "PER" and "추정PER" in header_text:
            continue
        parent = header.find_parent("tr")
        if not parent:
            continue
        value_text = " ".join(cell.get_text(" ", strip=True) for cell in parent.find_all("td"))
        values = [to_float(match) for match in re.findall(r"-?\d[\d,]*(?:\.\d+)?", value_text)]
        values = [value for value in values if value is not None]
        if values:
            if "N/A" in value_text:
                return None, values[0]
            return values[0], values[1] if len(values) > 1 else None
    return None, None


def _number_after(text: str, label: str) -> float | None:
    pattern = re.compile(rf"{re.escape(label)}\s*[:：]?\s*(-?\d[\d,]*(?:\.\d+)?)\s*%?")
    match = pattern.search(text)
    if not match:
        return None
    return to_float(match.group(1))


def _sum_recent(series: pd.Series, periods: int = 20) -> float | None:
    values = _recent_numbers(series, periods)
    if not values:
        return None
    return float(sum(values))


def _first_number(series: pd.Series) -> float | None:
    values = _recent_numbers(series, 1)
    if not values:
        return None
    return float(values[0])


def _change_recent(series: pd.Series, periods: int = 20) -> float | None:
    values = _recent_numbers(series, periods)
    if len(values) < 2:
        return None
    return round(float(values[0] - values[-1]), 2)


def _recent_numbers(series: pd.Series, periods: int) -> list[float]:
    values = []
    for value in series.tolist():
        if value is None or pd.isna(value):
            continue
        number = to_float(value)
        if number is not None:
            values.append(float(number))
    return values[:periods]


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
    text = str(value or "").strip().upper().split(":", 1)[-1]
    if re.fullmatch(r"[A-Z0-9]{6}", text):
        return text
    if re.fullmatch(r"\d{1,6}", text):
        return text.zfill(6)
    return None


def _write_raw(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8", errors="ignore")
