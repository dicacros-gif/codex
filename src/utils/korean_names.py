from __future__ import annotations

import re
from typing import Any


PHRASE_REPLACEMENTS = (
    (r"\bMIRAE\s+ASSET\b", "미래에셋"),
    (r"\bNH[-\s]?AMUNDI\b", "엔에이치아문디"),
    (r"\bS\s*&\s*P\s*500\b", "에스앤피500"),
    (r"\bS\s*&\s*P\b", "에스앤피"),
    (r"\bSP\s*500\b", "에스앤피500"),
    (r"\bUS\s+S\s*&\s*P\s*500\b", "미국 에스앤피500"),
    (r"\bUS\s+TREASURY\b", "미국 국채"),
    (r"\bUS\s+LARGECAP\s*500\b", "미국 대형주500"),
    (r"\bKOREA\s+HUMANOID\s+ROBOT\s+INDUSTRY\b", "코리아 휴머노이드 로봇 산업"),
    (r"\bMID[-\s]?SMALL\b", "중소형"),
    (r"\bTOP\s*5\s*PLUS\s*TR\b", "톱5플러스티알"),
    (r"\bTOP\s*5\s*PLUS\s+TOTAL\s+RETURN\b", "톱5플러스총수익"),
    (r"\bF[-\s]?KTB\b", "국채선물"),
    (r"\bT[-\s]?BILL\b", "단기국채"),
)

TOKEN_REPLACEMENTS = {
    "ACE": "에이스",
    "ACTIVE": "액티브",
    "AI": "에이아이",
    "ALLOCATION": "분산",
    "ARIRANG": "아리랑",
    "BIG": "빅",
    "BLEND": "혼합",
    "BLENDED": "혼합",
    "BOND": "채권",
    "CD": "양도성예금증서",
    "DAILY": "데일리",
    "DEFENSE": "방산",
    "EQUITY": "주식",
    "ETF": "상장지수펀드",
    "EUROPE": "유럽",
    "FIXED": "고정",
    "FOCUS": "포커스",
    "GLOBAL": "글로벌",
    "GOLD": "금",
    "HANARO": "하나로",
    "HANWHA": "한화",
    "HEDGED": "헤지",
    "HUMANOID": "휴머노이드",
    "IBK": "아이비케이",
    "INVERSE": "인버스",
    "K": "케이",
    "KB": "케이비",
    "KIWOOM": "키움",
    "KODEX": "코덱스",
    "KOREA": "코리아",
    "KOSEF": "코세프",
    "KRX": "케이알엑스",
    "KTOP": "케이탑",
    "KS": "케이에스",
    "KTB": "국채",
    "LARGECAP": "대형주",
    "MID": "중형",
    "MONTHLY": "월배당",
    "NASDAQ": "나스닥",
    "NVIDIA": "엔비디아",
    "PLUS": "플러스",
    "PROTECTIVE": "프로텍티브",
    "RATE": "금리",
    "RISE": "라이즈",
    "ROBOT": "로봇",
    "SAMSUNG": "삼성",
    "SHINHAN": "신한",
    "SMALL": "소형",
    "SOL": "쏠",
    "SPACE": "우주",
    "SPDR": "에스피디알",
    "SYNTH": "합성",
    "TARGET": "타겟",
    "TECH": "테크",
    "THEJ": "더제이",
    "TIGER": "타이거",
    "TIMEFOLIO": "타임폴리오",
    "TOP": "톱",
    "TR": "티알",
    "TREASURY": "국채",
    "TREX": "트렉스",
    "TRF": "티알에프",
    "TOTAL": "총수익",
    "RETURN": "수익",
    "US": "미국",
    "USA": "미국",
    "VI": "브이아이",
    "YEN": "엔화",
}

LETTER_NAMES = {
    "A": "에이",
    "B": "비",
    "C": "씨",
    "D": "디",
    "E": "이",
    "F": "에프",
    "G": "지",
    "H": "에이치",
    "I": "아이",
    "J": "제이",
    "K": "케이",
    "L": "엘",
    "M": "엠",
    "N": "엔",
    "O": "오",
    "P": "피",
    "Q": "큐",
    "R": "알",
    "S": "에스",
    "T": "티",
    "U": "유",
    "V": "브이",
    "W": "더블유",
    "X": "엑스",
    "Y": "와이",
    "Z": "제트",
}


def koreanize_kr_company_name(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    text = re.sub(r"\bETF\s+Units\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bUnits\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d+)\s*Y\b", r"\1년", text, flags=re.IGNORECASE)
    text = text.replace("&", "앤")
    text = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", text)
    text = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", text)
    for pattern, replacement in PHRASE_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Za-z]+", _replace_ascii_token, text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\b(아이비케이|삼성|미래에셋|엔에이치아문디|케이비|신한|한화|키움)\s+\1\b", r"\1", text)
    text = re.sub(r"(티알에프|케이알엑스|케이탑|케이에스|톱|테크|에스앤피|나스닥|국채선물)\s+(\d)", r"\1\2", text)
    text = re.sub(r"\s+([,)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)
    return text or None


def _replace_ascii_token(match: re.Match[str]) -> str:
    token = match.group(0)
    replacement = TOKEN_REPLACEMENTS.get(token.upper())
    if replacement:
        return replacement
    return "".join(LETTER_NAMES.get(char.upper(), char) for char in token)
