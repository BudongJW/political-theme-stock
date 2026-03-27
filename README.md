# 정치 테마주 분석 시스템 (Political Theme Stock Analyzer)

선거 여론조사 데이터 + 정치 뉴스 감성 분석 → 정치 테마주 예측 시스템

## 개요

대한민국 선거(대선/총선)를 앞두고 정치인 지지율 변화에 따라 움직이는 **정치 테마주**를
자동으로 식별하고 매매 시그널을 분석합니다.

```
정치인 지지율 변화
      ↓
연관 기업 식별 (정치인-기업 관계 DB)
      ↓
뉴스 감성 분석 (LLM 기반)
      ↓
수급/주가 이상 감지 (pykrx)
      ↓
Slack 알림
```

## 핵심 기능

- **여론조사 수집**: 선관위 공개 데이터 + 주요 여론조사 기관 크롤링
- **정치인-기업 관계 DB**: 출신 지역, 지분 보유, 공약 수혜 기업 매핑
- **테마주 스크리닝**: 지지율 변동 시 관련 종목 자동 필터링
- **뉴스 감성 분석**: Claude API 기반 정치 뉴스 → 테마 영향도 분석
- **이상 수급 감지**: 거래량/거래대금 급증 종목 알림
- **Slack 알림**: 시그널 발생 시 실시간 알림

## 기술 스택

- **데이터 수집**: Python, pykrx, requests, BeautifulSoup
- **분석**: Claude API (Anthropic), pandas, numpy
- **스케줄링**: APScheduler
- **알림**: Slack Webhook
- **저장**: SQLite (로컬), CSV

## 설치

```bash
git clone https://github.com/BudongJW/political-theme-stock.git
cd political-theme-stock
pip install -r requirements.txt
cp config/settings.example.yaml config/settings.yaml
# settings.yaml 에 API 키 입력 후
python src/main.py
```

## 프로젝트 구조

```
political-theme-stock/
├── src/
│   ├── collectors/
│   │   ├── poll_collector.py       # 여론조사 데이터 수집
│   │   ├── news_collector.py       # 정치 뉴스 수집
│   │   └── stock_collector.py      # 주가/수급 데이터 수집 (pykrx)
│   ├── analyzers/
│   │   ├── sentiment_analyzer.py   # LLM 기반 뉴스 감성 분석
│   │   ├── theme_mapper.py         # 정치인-테마주 매핑
│   │   └── signal_detector.py      # 매매 시그널 감지
│   ├── notifiers/
│   │   └── slack_notifier.py       # Slack 알림
│   ├── database.py                 # SQLite DB 관리
│   └── main.py                     # 메인 스케줄러
├── data/
│   ├── raw/                        # 원본 수집 데이터
│   └── processed/                  # 가공 데이터
├── config/
│   ├── settings.example.yaml
│   └── politician_stock_map.yaml   # 정치인-테마주 매핑 DB
├── notebooks/                      # 분석용 Jupyter 노트북
├── tests/
└── requirements.txt
```

## 데이터 소스

| 소스 | 내용 | 수집 방법 |
|------|------|----------|
| 중앙선거관리위원회 | 여론조사 공표 자료 | API / 크롤링 |
| 네이버 뉴스 | 정치 관련 뉴스 | RSS / 크롤링 |
| KRX / pykrx | KOSPI/KOSDAQ 주가·수급 | pykrx 라이브러리 |
| 금감원 DART | 주요주주 지분 공시 | OpenAPI |

## 참고 레포지토리

- [sharebook-kr/pykrx](https://github.com/sharebook-kr/pykrx) - KRX 주가 데이터
- [sharebook-kr/pykrx-mcp](https://github.com/sharebook-kr/pykrx-mcp) - MCP 연동 (포크)
- [jongheepark/poll-MBC](https://github.com/jongheepark/poll-MBC) - 베이지안 여론조사 분석

## 면책 조항

본 프로젝트는 교육/연구 목적으로 제작되었습니다.
테마주 투자는 높은 위험을 수반하며, 이 시스템의 분석 결과는 투자 권유가 아닙니다.
