# 정치 테마주 분석 시스템 (Political Theme Stock Analyzer)

선거 여론조사 + 정치 뉴스 감성 분석 → 정치 테마주 예측 시스템

---

## 배경 및 목적

대한민국은 **선거가 있을 때마다 정치 테마주가 반복적으로 급등락**하는 패턴을 보입니다.

- **2025.06.03** 제21대 대통령선거 → 이재명 당선 (득표율 49.42%)
- **2026.06.03** 제9회 전국동시지방선거 → **여당 더불어민주당 압승 (광역단체장 12:4)**, 이재명 정부 출범 1년 중간평가 신임
- **2028.04.12** 제23대 국회의원선거(총선) → 차기 테마주 사이클 기준점

이 시스템은 **선거 시즌 테마주 흐름을 선제적으로 분석**하여, 정치 이벤트와 주가 변동의 상관관계를 파악합니다.
선거 종료 후에는 자동으로 **'청산 국면'** 모드로 전환되어 결과·예측 회고와 차기 사이클 전망을 제공합니다.

---

## 정치 테마주 패턴 (역대 반복 패턴)

```
선거 D-12개월  유력 후보 거론 → 관련주 1차 급등
선거 D-6개월   경선/여론조사 결과 → 관련주 2차 급등
선거 D-3개월   공천 확정 → 최고 과열 구간 ⚠️
선거 D-day     최고점 도달 후 급락 시작 🔻
선거 직후      당락 무관 인물 테마주 거품 청산 (D+0~60) 🧹
선거 이후      여당 승리 시 인물주 → 정책 수혜주로 성격 전환, 차기 사이클 대기
```

> ⚠️ **주의**: 정치 테마주는 기업 실적·사업성과 무관하게 움직이며, 작전 세력 개입 가능성이 높습니다.
> 금융감독원은 매 선거 시즌마다 정치 테마주 투자 주의보를 발령합니다.

---

## 2026 지방선거 결과 (2026.06.03 완료)

| 항목 | 내용 |
|---|---|
| 선거일 | 2026년 6월 3일 (수) |
| 전국 투표율 | **61.0%** (전회 대비 +10.1%p) |
| 광역단체장 | **더불어민주당 12 : 국민의힘 4** (총 16곳, 광주·전남 통합특별시 출범) |
| 성격 | 이재명 정부 출범 1년 중간평가 → **여당 압승, 국정 신임** |

### 주요 광역단체장 당선 결과 (추적 후보 중심)

| 지역 | 당선자 | 정당 | 비고 |
|---|---|---|---|
| 서울시장 | **오세훈** | 국민의힘 | 현직 수성 (49.08%, 막판 역전) |
| 경기지사 | **추미애** | 더불어민주당 | 헌정사상 첫 여성 광역단체장 (55.04%) |
| 인천시장 | **박찬대** | 더불어민주당 | 단수공천 → 당선 |
| 부산시장 | **전재수** | 더불어민주당 | 현직 박형준(국힘) 낙선 — 부산 탈환 |
| 대구시장 | **추경호** | 국민의힘 | 보수 텃밭 방어 |

> 군소 지역 득표율은 중앙선관위 공식 최종집계로 재확인 권장.
> 출처: 위키백과·나무위키·언론 종합 (2026.06.04 기준)

### 차기 사이클
- **제23대 국회의원선거 (총선): 2028.04.12** — 2027년 하반기부터 신규 테마주 사이클 형성 예상
- 차기 대통령선거: 2030년 예정

---

## 시스템 구조

```
수집 레이어
├── poll_collector.py    여론조사 (선관위 공표 + 네이버 뉴스)
├── news_collector.py    정치 뉴스 (네이버 RSS + 검색)
└── stock_collector.py   주가/수급 (pykrx — KRX 직접 조회)

분석 레이어
├── sentiment_analyzer.py  Claude API 뉴스 감성 분석
├── theme_mapper.py        정치인-테마주 매핑 (YAML DB)
└── signal_detector.py     시그널 통합 감지

알림
└── slack_notifier.py    Slack Webhook

스케줄러
└── main.py              APScheduler (장중 10분 간격)
```

---

## 데이터 구성

### `config/politician_stock_map.yaml`
- 대선 후보 (이재명·김문수·이준석) 관련주
- **2026 지방선거 후보** (정원오·오세훈·박찬대·김동연·박형준 등) 관련주
- 정책 테마 (원전·방산·전기차·SOC건설·반도체 등)

### `config/election_calendar.yaml`
- 제21대 대선 결과 (2025.06.03)
- **제9회 지방선거 개표 결과** (2026.06.03) — 정당별 광역단체장, 투표율, 주요 당선자, 테마주 전망
- 제23대 총선 (2028.04.12) 차기 사이클 정보
- 테마주 시즌별 패턴 타임라인 (선거 직후 청산 → 정책 선별 → 차기 사이클)

---

## 설치 및 실행

```bash
git clone https://github.com/BudongJW/political-theme-stock.git
cd political-theme-stock
pip install -r requirements.txt

cp config/settings.example.yaml config/settings.yaml
# settings.yaml 에 API 키 입력:
#   anthropic.api_key: "sk-ant-..."
#   slack.webhook_url: "https://hooks.slack.com/..."

cd src && python main.py
```

---

## 프로젝트 구조

```
political-theme-stock/
├── src/
│   ├── collectors/
│   │   ├── poll_collector.py
│   │   ├── news_collector.py
│   │   └── stock_collector.py
│   ├── analyzers/
│   │   ├── sentiment_analyzer.py
│   │   ├── theme_mapper.py
│   │   └── signal_detector.py
│   ├── notifiers/
│   │   └── slack_notifier.py
│   └── main.py
├── config/
│   ├── settings.example.yaml
│   ├── politician_stock_map.yaml   ← 정치인-테마주 DB
│   └── election_calendar.yaml     ← 선거 일정 & 패턴
├── data/
│   ├── raw/
│   └── processed/
├── notebooks/                      ← 분석용 Jupyter
└── requirements.txt
```

---

## 참고 레포지토리

- [sharebook-kr/pykrx](https://github.com/sharebook-kr/pykrx) — KRX 주가 데이터 (포크: BudongJW/pykrx)
- [sharebook-kr/pykrx-mcp](https://github.com/sharebook-kr/pykrx-mcp) — Claude MCP 연동 (포크: BudongJW/pykrx-mcp)
- [jongheepark/poll-MBC](https://github.com/jongheepark/poll-MBC) — 베이지안 여론조사 분석 (참고)
- [koreainvestment/open-trading-api](https://github.com/koreainvestment/open-trading-api) — 한국투자증권 공식 API (참고)

---

## 면책 조항

본 프로젝트는 교육/연구 목적으로 제작되었습니다.
분석 결과는 투자 권유가 아니며, 정치 테마주 투자에 따른 손실에 책임지지 않습니다.
