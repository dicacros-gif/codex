# Stock Trend Report Automation

미국/한국 주식의 52주 신고가, 거래량 급증, 한국 외국인/기관 수급, 유명기관 SEC 13F 보유 증감, 컨센서스와 리포트 메타데이터를 매일 자동 수집해 정적 웹 리포트로 배포하는 시스템입니다.

## 자동 실행

- GitHub Actions workflow: `.github/workflows/daily-stock-trend.yml`
- 실행 시간: 매일 오전 7시 KST
- Cron: `0 22 * * *` (GitHub Actions는 UTC 기준이며 KST 07:00은 전날 UTC 22:00)
- 수동 실행: GitHub 저장소의 **Actions > Daily Stock Trend > Run workflow**

## 산출물

- `data/raw/YYYY-MM-DD/`: 원천 응답 저장
- `data/processed/daily_summary.csv`: 일별 섹션별 추적 요약
- `data/processed/scored_records.json`: 정제/점수 계산 결과
- `reports/YYYY-MM-DD/report.html`: 날짜별 HTML 리포트
- `reports/report.html`: 최신 HTML 리포트
- `index.html`: GitHub Pages 루트 리포트

## 데이터 출처

- TradingView Scanner
  - 미국: `https://scanner.tradingview.com/america/scan`
  - 한국: `https://scanner.tradingview.com/korea/scan`
- FnGuide
  - CompanyGuide: `https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{종목코드}`
  - Consensus: `https://wcomp.fnguide.com/CompanyInfo/Consensus?cmp_cd={종목코드}`
- Naver Finance
  - 외국인/기관 수급: `https://finance.naver.com/item/frgn.naver?code={종목코드}&page=1&trader_day=20`
  - 리서치 상세 링크: `company_read.naver`
- 한국경제 컨센서스 fallback
  - `https://markets.hankyung.com/consensus?searchWord={기업명}`
- SEC 13F
  - `https://data.sec.gov/submissions/CIK{CIK}.json`
  - XML information table

## 필터

- 미국 종목: 종가 10달러 이하 제외
- 한국 종목: 종가 10,000원 미만 제외

## 화면 구성

상단 탭을 누르면 아래 내용이 해당 섹션으로 전환됩니다.

- 우선순위_TOP
- 선행매매_후보
- 장기투자_후보
- 테마_요약
- 외국인_수급
- 기관수급_요약
- 신고가_미국
- 신고가_한국
- 거래량_급증_미국
- 거래량_급증_한국
- 유명기관_13F증감
- 일별_트래킹

## 주의사항

- SEC는 User-Agent를 요구합니다. 저장소 Settings > Secrets and variables에서 `SEC_USER_AGENT`를 실제 연락 가능한 값으로 설정하는 것을 권장합니다.
- 외부 사이트 HTML 구조가 바뀌면 해당 필드는 빈칸으로 남을 수 있습니다.
- 보고서에는 순위, 등급, 투자우선순위, 13F선호순위 같은 금지 컬럼을 표시하지 않습니다.
- 로컬 데스크톱 복사나 수동 엑셀 작업은 사용하지 않습니다. 자동 실행 결과는 저장소와 GitHub artifact에 남습니다.
