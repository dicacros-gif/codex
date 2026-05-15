from __future__ import annotations

import re
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.utils.io import ensure_dir
from src.utils.text import to_float


SUPPLY_URL = "https://finance.naver.com/sise/sise_deal_rank_iframe.naver?sosok={market}&investor_gubun={investor}&type=buy"
INVESTORS = {
    "foreign": {"code": "9000", "label": "외국인", "section": "kr_foreign_inflows"},
    "institution": {"code": "1000", "label": "기관", "section": "kr_institution_inflows"},
}
MARKETS = {"01": "코스피", "02": "코스닥"}


def collect_kr_supply_candidates(raw_dir: Path, run_date: date, limit_per_group: int = 30) -> dict[str, list[dict[str, Any]]]:
    sections: dict[str, list[dict[str, Any]]] = {"kr_foreign_inflows": [], "kr_institution_inflows": []}
    for investor_key, investor in INVESTORS.items():
        for market_code, market_name in MARKETS.items():
            url = SUPPLY_URL.format(market=market_code, investor=investor["code"])
            body = _get_text(url)
            if not body:
                continue
            _write_raw(raw_dir / f"naver_supply_{investor_key}_{market_code}_buy.html", body)
            rows = _parse_supply_rows(
                body=body,
                run_date=run_date,
                url=url,
                investor_key=investor_key,
                investor_label=str(investor["label"]),
                market_name=market_name,
            )
            sections[str(investor["section"])].extend(rows[:limit_per_group])
            time.sleep(0.2)
    return {key: _dedupe_supply_rows(value) for key, value in sections.items()}


def _get_text(url: str) -> str | None:
    headers = {
        "User-Agent": "Mozilla/5.0 daily-stock-trend/1.0",
        "Accept": "text/html,*/*;q=0.8",
    }
    try:
        response = requests.get(url, headers=headers, timeout=8)
        response.raise_for_status()
        response.encoding = "euc-kr"
        return response.text
    except requests.RequestException:
        return None


def _parse_supply_rows(
    body: str,
    run_date: date,
    url: str,
    investor_key: str,
    investor_label: str,
    market_name: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(body, "lxml")
    rows: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        caption = table.find("caption")
        if not caption or "순매수" not in caption.get_text(" ", strip=True):
            continue
        for tr in table.find_all("tr"):
            link = tr.find("a", href=re.compile(r"/item/main\.naver\?code="))
            if not link:
                continue
            ticker = _code_from_href(link.get("href"))
            if not ticker:
                continue
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all("td")]
            if len(cells) < 4:
                continue
            quantity_thousand = to_float(cells[1])
            amount_million = to_float(cells[2])
            daily_volume = to_float(cells[3])
            if amount_million is None or amount_million <= 0:
                continue
            row = {
                "date": run_date.isoformat(),
                "country": "한국",
                "country_code": "KR",
                "ticker": ticker,
                "company_name": link.get_text(" ", strip=True),
                "exchange": market_name,
                "volume": daily_volume,
                "supply_source": f"Naver {investor_label} 순매수 상위",
                "source": "Naver Finance Supply Rank",
                "source_url": url,
                "signals": [f"{investor_label} 순매수 상위"],
                "supply_pattern": f"{investor_label} 순매수 유입",
            }
            quantity_shares = quantity_thousand * 1_000 if quantity_thousand is not None else None
            if investor_key == "foreign":
                row["foreign_net_buy"] = quantity_shares
                row["foreign_net_buy_amount_mil_krw"] = amount_million
                row["foreign_rank_volume"] = daily_volume
            else:
                row["institution_net_buy"] = quantity_shares
                row["institution_net_buy_amount_mil_krw"] = amount_million
                row["institution_rank_volume"] = daily_volume
            rows.append(row)
    rows.sort(key=lambda item: item.get(f"{investor_key}_net_buy_amount_mil_krw") or 0, reverse=True)
    return rows


def _code_from_href(href: str | None) -> str | None:
    parsed = urlparse(urljoin("https://finance.naver.com", href or ""))
    code = (parse_qs(parsed.query).get("code") or [""])[0]
    if re.fullmatch(r"\d{6}", code):
        return code
    return None


def _dedupe_supply_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        amount = to_float(row.get("foreign_net_buy_amount_mil_krw") or row.get("institution_net_buy_amount_mil_krw")) or 0
        old = best.get(ticker)
        old_amount = to_float((old or {}).get("foreign_net_buy_amount_mil_krw") or (old or {}).get("institution_net_buy_amount_mil_krw")) or 0
        if old is None or amount > old_amount:
            best[ticker] = row
    return sorted(
        best.values(),
        key=lambda item: to_float(item.get("foreign_net_buy_amount_mil_krw") or item.get("institution_net_buy_amount_mil_krw")) or 0,
        reverse=True,
    )


def _write_raw(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8", errors="ignore")
