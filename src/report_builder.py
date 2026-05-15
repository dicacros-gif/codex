from __future__ import annotations

import html
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.utils.io import ensure_dir, strip_empty, write_csv


SECTION_TITLES = [
    ("priority_top", "우선순위_TOP"),
    ("leading_candidates", "선행매매_후보"),
    ("long_term_candidates", "장기투자_후보"),
    ("theme_summary", "테마_요약"),
    ("foreign_flow", "외국인_수급"),
    ("institution_flow_summary", "기관수급_요약"),
    ("us_52w_highs", "신고가_미국"),
    ("kr_52w_highs", "신고가_한국"),
    ("us_volume_surges", "거래량_급증_미국"),
    ("kr_volume_surges", "거래량_급증_한국"),
    ("famous_13f_changes", "유명기관_13F증감"),
    ("daily_tracking", "일별_트래킹"),
]

STOCK_COLUMNS = [
    ("date", "날짜"),
    ("country", "국가"),
    ("ticker", "티커"),
    ("company_name", "기업명"),
    ("signals", "포착신호"),
    ("close", "종가"),
    ("market_cap", "시가총액"),
    ("volume", "거래량"),
    ("relative_volume", "상대거래량"),
    ("average_volume_30d", "평균거래량"),
    ("high_52w", "52주고가"),
    ("distance_to_52w_high_pct", "고가괴리"),
    ("investment_priority_score", "투자우선점수"),
    ("long_future_score", "장기/미래점수"),
    ("leading_supply_score", "선행수급점수"),
    ("foreign_flow_investment_score", "외국인수급점수"),
    ("future_industry_theme", "미래산업테마"),
    ("core_basis", "핵심근거"),
    ("forward_per", "Forward PER"),
    ("forward_peg", "Forward PEG"),
    ("trailing_per", "PER"),
    ("pbr", "PBR"),
    ("dividend_yield", "배당수익률"),
    ("revenue_growth_yoy", "매출성장률 YoY"),
    ("revenue_growth_qoq", "매출성장률 QoQ"),
    ("eps_growth_yoy", "EPS성장률 YoY"),
    ("eps_growth_qoq", "EPS성장률 QoQ"),
    ("expected_revenue_growth", "예상매출성장률"),
    ("expected_eps_growth", "예상EPS성장률"),
    ("fcf_margin", "FCF마진"),
    ("roic", "ROIC"),
    ("roe", "ROE"),
    ("target_upside_pct", "목표가상승여력"),
    ("recent_report_broker", "최근리포트증권사"),
    ("recent_report_title", "최근리포트제목"),
    ("supply_pattern", "수급패턴"),
    ("foreign_net_buy", "외국인순매수"),
    ("institution_net_buy", "기관순매수"),
    ("foreign_ownership_rate", "외국인지분율"),
    ("exchange", "거래소"),
    ("sector", "섹터"),
    ("industry", "산업"),
]

THEME_COLUMNS = [
    ("date", "날짜"),
    ("future_industry_theme", "테마"),
    ("investment_priority_score", "포착수"),
    ("core_basis", "포착 종목"),
]

F13_COLUMNS = [
    ("date", "날짜"),
    ("company_name", "보유종목/발행사"),
    ("cusip", "CUSIP"),
    ("title_of_class", "증권분류"),
    ("new_institution_count", "신규기관수"),
    ("increased_institution_count", "증가기관수"),
    ("decreased_institution_count", "감소기관수"),
    ("exited_institution_count", "청산기관수"),
    ("total_share_change", "총증감주식"),
    ("average_change_pct", "평균증감률"),
    ("total_current_shares", "총현재보유주식"),
    ("famous_13f_score", "13F투자후보점수"),
    ("institutions", "기관"),
    ("core_basis", "보유증감근거"),
]

TRACKING_COLUMNS = [
    ("date", "날짜"),
    ("section", "섹션"),
    ("row_count", "행수"),
    ("generated_at_kst", "생성시각"),
]

SECTION_COLUMNS = {
    "theme_summary": THEME_COLUMNS,
    "famous_13f_changes": F13_COLUMNS,
    "daily_tracking": TRACKING_COLUMNS,
}

SUMMARY_FIELDS = ["date", "section", "row_count", "generated_at_kst"]
NUMERIC_FIELDS = {
    "close",
    "market_cap",
    "volume",
    "average_volume_30d",
    "high_52w",
    "investment_priority_score",
    "long_future_score",
    "leading_supply_score",
    "foreign_flow_investment_score",
    "forward_per",
    "forward_peg",
    "trailing_per",
    "pbr",
    "foreign_net_buy",
    "institution_net_buy",
    "new_institution_count",
    "increased_institution_count",
    "decreased_institution_count",
    "exited_institution_count",
    "total_share_change",
    "total_current_shares",
    "famous_13f_score",
    "row_count",
}
PERCENT_FIELDS = {
    "distance_to_52w_high_pct",
    "dividend_yield",
    "revenue_growth_yoy",
    "revenue_growth_qoq",
    "eps_growth_yoy",
    "eps_growth_qoq",
    "expected_revenue_growth",
    "expected_eps_growth",
    "fcf_margin",
    "roic",
    "roe",
    "target_upside_pct",
    "foreign_ownership_rate",
    "average_change_pct",
}


def write_outputs(
    root: Path,
    run_date: date,
    sections: dict[str, list[dict[str, Any]]],
    records: list[dict[str, Any]],
    generated_at_kst: str,
) -> None:
    processed_dir = ensure_dir(root / "data" / "processed")
    reports_dir = ensure_dir(root / "reports")
    dated_report_dir = ensure_dir(reports_dir / run_date.isoformat())

    sections = {key: _dedupe_rows(value) for key, value in sections.items()}
    summary_rows = [
        {
            "date": run_date.isoformat(),
            "section": title,
            "row_count": len(sections.get(key, [])),
            "generated_at_kst": generated_at_kst,
        }
        for key, title in SECTION_TITLES
    ]
    sections["daily_tracking"] = _merge_daily_tracking(processed_dir / "daily_summary.csv", summary_rows)

    payload = strip_empty(
        {
            "generated_at_kst": generated_at_kst,
            "date": run_date.isoformat(),
            "sections": sections,
        }
    )

    write_csv(processed_dir / "daily_summary.csv", sections["daily_tracking"], SUMMARY_FIELDS)
    write_csv(reports_dir / "daily_summary.csv", sections["daily_tracking"], SUMMARY_FIELDS)
    _write_xlsx(reports_dir / "latest.xlsx", sections)

    html_text = render_html(payload)
    (root / "index.html").write_text(html_text, encoding="utf-8")
    (reports_dir / "report.html").write_text(html_text, encoding="utf-8")
    (dated_report_dir / "report.html").write_text(html_text, encoding="utf-8")


def render_html(payload: dict[str, Any]) -> str:
    generated = payload.get("generated_at_kst") or ""
    sections = payload.get("sections") or {}
    tab_buttons = "\n".join(
        f"<button class='tab-btn{' on' if index == 0 else ''}' type='button' data-tab='{key}'>{html.escape(title)}</button>"
        for index, (key, title) in enumerate(SECTION_TITLES)
    )
    panels = "\n".join(
        _render_panel(key, title, sections.get(key, []), active=(index == 0))
        for index, (key, title) in enumerate(SECTION_TITLES)
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Trend Report</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
@font-face{{font-family:'SOK500';src:local('SamsungOneKorean500'),local('SamsungOneKorean 500');font-weight:500;font-style:normal;font-display:swap}}
:root{{--bg:#FFFFFF;--card:#FFFFFF;--card2:#F7F9FC;--hdr:#2F2F2F;--hdrText:#FFFFFF;--row2:#F8FAFC;--t1:#111827;--t2:#475569;--t3:#64748B;--bd:#D8DFE9;--ac:#2563EB;--acL:#DBEAFE;--acT:#1D4ED8;--okB:#C6EFCE;--okT:#006100;--buyB:#E2F0D9;--buyT:#375623;--warnB:#FFC000;--warnT:#111827;--negB:#F4CCCC;--negT:#9C0006;--link:#0563C1;--tbg:rgba(255,255,255,.94);--r:12px;--shadow:0 4px 18px rgba(15,23,42,.08)}}
[data-p=ocean]{{--ac:#0D9488;--acL:#CCFBF1;--acT:#0F766E}}
[data-p=sunset]{{--ac:#EA580C;--acL:#FED7AA;--acT:#C2410C}}
[data-p=violet]{{--ac:#7C3AED;--acL:#EDE9FE;--acT:#6D28D9}}
[data-t=dark]{{--bg:#0B1120;--card:#111827;--card2:#0F172A;--row2:#0B1222;--t1:#E5E7EB;--t2:#CBD5E1;--t3:#94A3B8;--bd:#243044;--tbg:rgba(11,17,32,.94);--shadow:0 8px 26px rgba(0,0,0,.35)}}
*,*::before,*::after{{box-sizing:border-box}}body{{margin:0;font-family:'SOK500','Noto Sans KR',system-ui,sans-serif;background:var(--bg);color:var(--t1);line-height:1.65}}button,input{{font:inherit}}a{{color:var(--link);font-weight:700;text-decoration:none}}a:hover{{text-decoration:underline}}
.topbar{{position:sticky;top:0;z-index:50;background:var(--tbg);backdrop-filter:blur(16px);border-bottom:1px solid var(--bd);padding:.35rem .9rem;display:flex;align-items:center;gap:.55rem}}
.tabs{{display:flex;gap:.25rem;overflow-x:auto;flex:1;min-width:0}}.tab-btn{{border:1px solid var(--bd);background:var(--card);color:var(--t2);border-radius:999px;padding:.28rem .65rem;font-size:.68rem;font-weight:800;white-space:nowrap;cursor:pointer}}.tab-btn:hover,.tab-btn.on{{background:var(--ac);border-color:var(--ac);color:#fff}}
.tools{{display:flex;align-items:center;gap:.35rem;flex-shrink:0}}.pb{{width:18px;height:18px;border-radius:50%;border:2px solid transparent;cursor:pointer}}.pb.on{{border-color:var(--t1);box-shadow:0 0 0 2px var(--bg),0 0 0 4px var(--t3)}}.pb[data-c=default]{{background:linear-gradient(135deg,#2563eb,#60a5fa)}}.pb[data-c=ocean]{{background:linear-gradient(135deg,#0d9488,#2dd4bf)}}.pb[data-c=sunset]{{background:linear-gradient(135deg,#ea580c,#fb923c)}}.pb[data-c=violet]{{background:linear-gradient(135deg,#7c3aed,#a78bfa)}}.mode{{border:1px solid var(--bd);background:var(--card2);border-radius:999px;padding:.18rem .52rem;font-size:.66rem;font-weight:800;color:var(--t2);cursor:pointer}}
main{{width:min(1680px,calc(100% - 1.5rem));margin:0 auto;padding:.75rem 0 2rem}}.statusbar{{display:flex;flex-wrap:wrap;gap:.35rem;align-items:center;margin-bottom:.7rem}}.chip{{font-size:.66rem;font-weight:800;background:var(--card2);border:1px solid var(--bd);padding:.14rem .5rem;border-radius:999px;color:var(--t2)}}.panel{{display:none;background:var(--card);border:1px solid var(--bd);border-radius:var(--r);box-shadow:var(--shadow);overflow:hidden}}.panel.on{{display:block}}.panel-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;padding:.7rem 1rem;border-bottom:1px solid var(--bd);background:var(--card2)}}.panel-head h2{{font-size:.95rem;margin:0;font-weight:900}}.panel-head p{{margin:.1rem 0 0;font-size:.68rem;color:var(--t3)}}.downloads{{display:flex;flex-wrap:wrap;gap:.35rem}}.downloads a{{font-size:.66rem;border:1px solid var(--bd);border-radius:6px;padding:.16rem .45rem;background:var(--card)}}
.table-wrap{{overflow:auto;max-height:calc(100vh - 170px)}}table{{width:100%;border-collapse:separate;border-spacing:0;font-size:.72rem}}th{{position:sticky;top:0;z-index:2;background:var(--hdr);color:var(--hdrText);padding:.46rem .45rem;text-align:left;font-weight:900;white-space:nowrap;border-bottom:2px solid var(--bd)}}td{{padding:.42rem .45rem;border-bottom:1px solid var(--bd);vertical-align:top;background:var(--card)}}tbody tr:nth-child(even) td{{background:var(--row2)}}tbody tr:hover td{{background:var(--acL)}}.empty{{padding:1.1rem;color:var(--t3);font-size:.78rem}}.num{{font-family:'JetBrains Mono',monospace;white-space:nowrap}}.pos-strong{{background:var(--okB)!important;color:var(--okT);font-weight:900;border-radius:4px}}.pos-buy{{background:var(--buyB)!important;color:var(--buyT);font-weight:900;border-radius:4px}}.warn{{background:var(--warnB)!important;color:var(--warnT);font-weight:900;border-radius:4px}}.neg{{background:var(--negB)!important;color:var(--negT);font-weight:900;border-radius:4px}}.basis{{min-width:260px;max-width:520px}}.tag{{display:inline-block;border:1px solid var(--bd);background:var(--card2);border-radius:999px;padding:.05rem .35rem;margin:.05rem;font-size:.64rem;font-weight:800;color:var(--t2)}}footer{{font-size:.66rem;color:var(--t3);text-align:center;padding:.8rem 0}}
@media(max-width:768px){{main{{width:calc(100% - .5rem)}}.topbar{{padding:.28rem .45rem;align-items:flex-start;flex-direction:column}}.tools{{align-self:flex-end}}.table-wrap{{max-height:none}}}}
</style>
</head>
<body data-t="light" data-p="default">
<div class="topbar">
  <div class="tabs" role="tablist" aria-label="리포트 섹션">
    {tab_buttons}
  </div>
  <div class="tools">
    <button class="pb on" type="button" data-c="default" title="기본 색상"></button>
    <button class="pb" type="button" data-c="ocean" title="오션"></button>
    <button class="pb" type="button" data-c="sunset" title="선셋"></button>
    <button class="pb" type="button" data-c="violet" title="바이올렛"></button>
    <button class="mode" type="button" id="modeToggle">Dark</button>
  </div>
</div>
<main>
  <div class="statusbar"><span class="chip">{html.escape(str(generated))} KST</span><span class="chip">매일 07:00 자동 실행</span></div>
  {panels}
</main>
<script>
const tabs=[...document.querySelectorAll('.tab-btn')];
const panels=[...document.querySelectorAll('.panel')];
tabs.forEach(btn=>btn.addEventListener('click',()=>{{
  tabs.forEach(item=>item.classList.toggle('on',item===btn));
  panels.forEach(panel=>panel.classList.toggle('on',panel.id==='panel-'+btn.dataset.tab));
  window.scrollTo({{top:0,behavior:'smooth'}});
}}));
document.querySelectorAll('.pb').forEach(btn=>btn.addEventListener('click',()=>{{
  document.body.setAttribute('data-p',btn.dataset.c);
  document.querySelectorAll('.pb').forEach(item=>item.classList.toggle('on',item===btn));
}}));
document.getElementById('modeToggle').addEventListener('click',()=>{{
  const next=document.body.getAttribute('data-t')==='dark'?'light':'dark';
  document.body.setAttribute('data-t',next);
  document.getElementById('modeToggle').textContent=next==='dark'?'Light':'Dark';
}});
</script>
</body>
</html>"""


def _render_panel(key: str, title: str, rows: list[dict[str, Any]], active: bool) -> str:
    rows = _dedupe_rows(rows)
    table = _render_table(key, rows)
    return f"""<section class="panel{' on' if active else ''}" id="panel-{html.escape(key)}">
  <div class="panel-head">
    <div><h2>{html.escape(title)}</h2><p>{len(rows)}개 유니크 항목</p></div>
    <div class="downloads"><a href="reports/latest.xlsx">latest.xlsx</a></div>
  </div>
  {table}
</section>"""


def _render_table(section_key: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<div class='empty'>표시할 데이터가 없습니다.</div>"
    columns = SECTION_COLUMNS.get(section_key, STOCK_COLUMNS)
    head = "".join(f"<th>{html.escape(label)}</th>" for _, label in columns)
    body = "\n".join(_render_row(row, columns) for row in rows)
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def _render_row(row: dict[str, Any], columns: list[tuple[str, str]]) -> str:
    cells = []
    for field, _ in columns:
        value = row.get(field)
        classes = _classes(field, value)
        cells.append(f"<td class='{classes}'>{_format_cell(field, value, row)}</td>")
    return "<tr>" + "".join(cells) + "</tr>"


def _format_cell(field: str, value: Any, row: dict[str, Any]) -> str:
    if value in (None, "", []):
        if field == "recent_report_broker":
            return html.escape(_fallback_report_broker(row) or "")
        if field == "recent_report_title":
            link = _fallback_report_link(row)
            title = _fallback_report_title(row)
            return _link(link, title) if link and title else ""
        return ""
    if field == "recent_report_title":
        link = _fallback_report_link(row)
        return _link(link, str(value)) if link else html.escape(str(value))
    if field in {"source_url", "report_link"}:
        return _link(str(value), "열기")
    if isinstance(value, list):
        return "".join(f"<span class='tag'>{html.escape(str(item))}</span>" for item in value if item not in (None, ""))
    if field == "relative_volume":
        number = _number(value)
        return "" if number is None else html.escape(f"{int(round(number))}x")
    if field in PERCENT_FIELDS:
        number = _number(value)
        return "" if number is None else html.escape(f"{int(round(number))}%")
    if field in NUMERIC_FIELDS or isinstance(value, (int, float)):
        return html.escape(_compact_number(value))
    return html.escape(_clean_display_text(str(value)))


def _link(url: str, label: str) -> str:
    return f"<a href='{html.escape(url, quote=True)}' target='_blank' rel='noopener'>{html.escape(label)}</a>"


def _compact_number(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    sign = "-" if number < 0 else ""
    absolute = abs(number)
    if absolute >= 10_000:
        return f"{sign}{int(round(absolute / 1_000)):,}K"
    rounded = int(round(absolute))
    if rounded == 0:
        return "0"
    return f"{sign}{rounded:,}"


def _clean_display_text(value: str) -> str:
    def repl(match: Any) -> str:
        number = float(match.group(0).replace(",", ""))
        return f"{int(round(number)):,}"

    return re.sub(r"-?\d[\d,]*\.\d+", repl, value)


def _fallback_report_broker(row: dict[str, Any]) -> str | None:
    if row.get("country_code") == "US" or row.get("country") == "미국":
        return "Yahoo Finance"
    if row.get("country_code") == "KR" or row.get("country") == "한국":
        return "한국경제"
    return None


def _fallback_report_title(row: dict[str, Any]) -> str | None:
    if row.get("country_code") == "US" or row.get("country") == "미국":
        return "Analyst Estimates / Analysis"
    if row.get("country_code") == "KR" or row.get("country") == "한국":
        return "컨센서스 검색"
    return None


def _fallback_report_link(row: dict[str, Any]) -> str | None:
    if row.get("report_link"):
        return str(row["report_link"])
    ticker = row.get("ticker")
    if not ticker:
        return None
    if row.get("country_code") == "US" or row.get("country") == "미국":
        return f"https://finance.yahoo.com/quote/{ticker}/analysis/"
    if row.get("country_code") == "KR" or row.get("country") == "한국":
        company = row.get("company_name") or ticker
        from urllib.parse import quote

        return f"https://markets.hankyung.com/consensus?searchWord={quote(str(company))}"
    return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _classes(field: str, value: Any) -> str:
    classes = []
    if field in NUMERIC_FIELDS or field in PERCENT_FIELDS or field == "relative_volume":
        classes.append("num")
    if field == "core_basis":
        classes.append("basis")
    if field in {"investment_priority_score", "long_future_score", "leading_supply_score", "foreign_flow_investment_score", "famous_13f_score"}:
        number = _number(value)
        if number is not None:
            if number >= 8:
                classes.append("pos-strong")
            elif number >= 5:
                classes.append("pos-buy")
    if field in {"target_upside_pct", "distance_to_52w_high_pct", "average_change_pct"}:
        number = _number(value)
        if number is not None:
            if number >= 20:
                classes.append("pos-strong")
            elif number > 0:
                classes.append("pos-buy")
            elif number < 0:
                classes.append("neg")
    return " ".join(classes)


def _merge_daily_tracking(path: Path, summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing: list[dict[str, Any]] = []
    if path.exists():
        try:
            existing = pd.read_csv(path).to_dict("records")
        except Exception:
            existing = []
    combined = summary_rows + existing
    seen = set()
    unique = []
    for row in combined:
        key = (str(row.get("date")), str(row.get("section")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    unique.sort(key=lambda row: str(row.get("date") or ""), reverse=True)
    return unique[:500]


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = []
    seen = set()
    for row in rows:
        key = row.get("ticker") or row.get("cusip") or row.get("company_name") or row.get("future_industry_theme") or tuple(sorted(row.items()))
        key = str(key).upper()
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _write_xlsx(path: Path, sections: dict[str, list[dict[str, Any]]]) -> None:
    ensure_dir(path.parent)
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for key, title in SECTION_TITLES:
                columns = SECTION_COLUMNS.get(key, STOCK_COLUMNS)
                rows = [_export_row(row, columns) for row in sections.get(key, [])]
                frame = pd.DataFrame(rows, columns=[label for _, label in columns])
                frame.to_excel(writer, sheet_name=title[:31], index=False)
    except Exception:
        return


def _export_row(row: dict[str, Any], columns: list[tuple[str, str]]) -> dict[str, str]:
    return {label: _format_export_cell(field, row.get(field), row) for field, label in columns}


def _format_export_cell(field: str, value: Any, row: dict[str, Any]) -> str:
    if value in (None, "", []):
        if field == "recent_report_broker":
            return _fallback_report_broker(row) or ""
        if field == "recent_report_title":
            return _fallback_report_title(row) or ""
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    if field == "relative_volume":
        number = _number(value)
        return "" if number is None else f"{int(round(number))}x"
    if field in PERCENT_FIELDS:
        number = _number(value)
        return "" if number is None else f"{int(round(number))}%"
    if field in NUMERIC_FIELDS or isinstance(value, (int, float)):
        return _compact_number(value)
    return _clean_display_text(str(value))
