"""
Gemini API 기반 정치 뉴스 분석 + 테마주 자동 매핑
- google.genai SDK (신규 패키지) 사용
- 뉴스 감성 분석 + 신규 테마주 후보 자동 제안
"""
import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-2.5-flash"


def _load_api_keys() -> list[str]:
    keys = []
    main_key = os.environ.get("GEMINI_API_KEY", "")
    if main_key:
        keys.append(main_key)
    key2 = os.environ.get("GEMINI_API_KEY_2", "")
    if key2:
        keys.append(key2)
    key3 = os.environ.get("GEMINI_API_KEY_3", "")
    if key3:
        keys.append(key3)
    return keys


GEMINI_API_KEYS = _load_api_keys()


class GeminiAnalyzer:
    def __init__(self, api_keys: list[str] = None, cache_dir: str = "data/gemini_cache"):
        from google import genai

        self._genai = genai
        self._keys = api_keys or GEMINI_API_KEYS
        if not self._keys:
            logger.warning("GEMINI_API_KEY 환경변수 미설정 — AI 분석 기능 비활성화")
            self._disabled = True
            self._cache_dir = Path(cache_dir)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            return
        self._disabled = False
        self._key_idx = 0
        self._configure_key(self._keys[self._key_idx])
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _configure_key(self, key: str):
        self._client = self._genai.Client(api_key=key)
        self._current_key = key

    def _rotate_key(self):
        """API 한도 초과 시 다음 키로 전환"""
        self._key_idx = (self._key_idx + 1) % len(self._keys)
        self._configure_key(self._keys[self._key_idx])
        logger.info(f"Gemini API 키 로테이션 → 키 #{self._key_idx + 1}")

    def _cache_key(self, prefix: str, data: str) -> str:
        h = hashlib.md5(data.encode()).hexdigest()[:12]
        today = datetime.now().strftime("%Y-%m-%d")
        return f"{prefix}_{today}_{h}"

    def _get_cache(self, key: str, ttl_hours: int = 24):
        path = self._cache_dir / f"{key}.json"
        if path.exists():
            try:
                age_hours = (datetime.now().timestamp() - path.stat().st_mtime) / 3600
                if age_hours > ttl_hours:
                    path.unlink()
                    logger.info(f"Gemini 캐시 만료 삭제: {key} ({age_hours:.1f}h)")
                    return None
                with open(path, encoding="utf-8") as f:
                    cached = json.load(f)
                logger.info(f"Gemini 캐시 히트: {key}")
                return cached.get("data")
            except Exception:
                pass
        return None

    def _set_cache(self, key: str, data):
        path = self._cache_dir / f"{key}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"key": key, "cached_at": datetime.now().isoformat(), "data": data}, f, ensure_ascii=False, indent=2)

    def _call(self, prompt: str, retries: int = 1) -> str:
        if getattr(self, '_disabled', False):
            return ""
        tried_keys = set()
        for attempt in range(retries + 1):
            try:
                response = self._client.models.generate_content(
                    model=MODEL_NAME, contents=prompt
                )
                return response.text.strip()
            except Exception as e:
                err_str = str(e).lower()
                is_quota = "quota" in err_str or "429" in err_str or "leaked" in err_str
                if is_quota:
                    tried_keys.add(self._key_idx)
                    if len(tried_keys) < len(self._keys):
                        logger.warning(f"Gemini 할당량 초과, 키 로테이션 시도")
                        self._rotate_key()
                        continue
                    else:
                        logger.error(f"Gemini API 모든 키({len(self._keys)}개) 할당량 초과 — 비활성화")
                        self._disabled = True
                        return ""
                logger.error(f"Gemini API 호출 실패: {e}")
                return ""

    def _parse_json(self, text: str):
        cleaned = text.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(f"JSON 파싱 실패: {cleaned[:200]}")
            return None

    def analyze_news_batch(
        self, news_items: list[dict], politicians: list[str]
    ) -> list[dict]:
        """
        뉴스 배치 감성 분석 (Claude 대안)
        동일한 출력 포맷으로 SentimentAnalyzer와 호환
        """
        if not news_items:
            return []

        # 캐시 확인 (같은 날 동일 뉴스 세트면 재사용)
        cache_input = "|".join(item.get("title", "") for item in news_items[:15])
        ck = self._cache_key("news", cache_input)
        cached = self._get_cache(ck)
        if cached is not None:
            return cached

        news_text = "\n".join(
            [
                f"[{i+1}] {item['title']}\n{item.get('summary', '')}"
                for i, item in enumerate(news_items[:15])
            ]
        )
        politicians_str = ", ".join(politicians)

        prompt = f"""다음 정치 뉴스들을 분석해서 정치 테마주 투자 시그널을 추출해줘.

분석 대상 정치인: {politicians_str}

뉴스 목록:
{news_text}

각 뉴스에 대해 JSON 배열로 응답해줘:
[
  {{
    "news_index": 1,
    "relevant_politicians": ["이름1"],
    "sentiment": "positive" | "negative" | "neutral",
    "impact_score": 1-10,
    "policy_themes": ["원전", "방산", "전기차" 등],
    "reason": "테마주에 영향을 줄 수 있는 이유 한 줄"
  }}
]

테마주에 영향 없는 뉴스는 impact_score 1-3. JSON만 응답."""

        raw = self._call(prompt)
        if not raw:
            return []

        results = self._parse_json(raw)
        if not results or not isinstance(results, list):
            return []

        for r in results:
            idx = r.get("news_index", 1) - 1
            if 0 <= idx < len(news_items):
                r["title"] = news_items[idx].get("title", "")
                r["link"] = news_items[idx].get("link", "")
        logger.info(f"Gemini 감성 분석 완료: {len(results)}건")
        self._set_cache(ck, results)
        return results

    def suggest_theme_stocks(
        self, politician_info: dict, existing_tickers: list[str] = None
    ) -> list[dict]:
        """
        정치인 프로필 기반 신규 테마주 후보 자동 제안
        politician_info: {name, party, role, profile, region, assets, keywords}
        → 관련 가능성 있는 종목 제안
        """
        # 캐시 확인 (같은 날 동일 정치인이면 재사용)
        ck = self._cache_key("suggest", politician_info.get("name", ""))
        cached = self._get_cache(ck)
        if cached is not None:
            return cached

        existing = ", ".join(existing_tickers or [])
        prompt = f"""한국 정치인의 정보를 기반으로 정치 테마주(관련주)를 제안해줘.

정치인 정보:
- 이름: {politician_info.get('name', '')}
- 정당: {politician_info.get('party', '')}
- 직책: {politician_info.get('role', '')}
- 프로필: {politician_info.get('profile', '')}
- 지역: {politician_info.get('region', '')}
- 재산: {politician_info.get('assets', '')}
- 키워드: {', '.join(politician_info.get('keywords', []))}

이미 등록된 종목: {existing}

아래 기준으로 신규 테마주 후보 5~10개를 JSON으로 제안해줘:
1. 공약/정책 관련 산업 종목 (반도체, 원전, 방산, SOC, 부동산 등)
2. 출신 지역 기반 기업 (본사/공장 소재지 — 고향, 지역구)
3. 경력/인맥 관련 기업 (전직장, 캠프 인사의 회사)
4. 본관(종친) 관련 기업 (예: 경주 김씨 → 관련 재벌/기업)
5. 학연 관련 기업 (출신 학교 동문이 운영하는 상장사)
6. 혈연/인척 관련 기업 (배우자, 자녀, 친인척 관련 기업)
7. 소속 위원회/전문분야 관련 기업
8. 정치적 후원자/측근이 관련된 기업

[
  {{
    "ticker": "KRX 종목코드 6자리",
    "name": "종목명",
    "relation": "테마주로 분류되는 이유 (구체적으로)",
    "confidence": "high" | "medium" | "low",
    "category": "policy" | "region" | "career" | "network" | "clan" | "school" | "family"
  }}
]

반드시 실제 존재하는 KRX 상장 종목만 제안. JSON만 응답."""

        raw = self._call(prompt)
        if not raw:
            return []

        suggestions = self._parse_json(raw)
        if not suggestions or not isinstance(suggestions, list):
            return []

        logger.info(
            f"테마주 제안: {politician_info.get('name','')} → {len(suggestions)}개"
        )
        self._set_cache(ck, suggestions)
        return suggestions

    def analyze_realtime_signal(
        self, news_title: str, politicians: list[str], themes: list[str]
    ) -> dict:
        """
        단건 뉴스에 대한 실시간 시그널 판단
        빠른 응답을 위해 간결한 프롬프트 사용
        """
        prompt = f"""다음 뉴스 제목이 정치 테마주에 미치는 영향을 판단해줘.

뉴스: {news_title}
추적 정치인: {', '.join(politicians)}
추적 테마: {', '.join(themes)}

JSON으로 응답:
{{
  "relevant": true/false,
  "politicians": ["관련 정치인"],
  "themes": ["관련 테마"],
  "impact": 1-10,
  "direction": "positive" | "negative" | "neutral",
  "action": "관련주 모니터링 강화 등 한 줄 제안"
}}
JSON만 응답."""

        raw = self._call(prompt)
        if not raw:
            return {"relevant": False}
        result = self._parse_json(raw)
        return result if result else {"relevant": False}

    def generate_daily_report(
        self, screening_data: dict, top_n: int = 5
    ) -> str:
        """
        스크리닝 결과 기반 일일 리포트 자동 생성
        """
        ck = self._cache_key("report", screening_data.get("date", ""))
        cached = self._get_cache(ck)
        if cached is not None:
            return cached

        results = screening_data.get("screening_results", [])
        summary = screening_data.get("summary", {})
        phase = screening_data.get("election_phase", {})
        candidates = screening_data.get("candidate_market_summary", {})

        top_gainers = sorted(
            results, key=lambda x: x.get("change_pct", 0), reverse=True
        )[:top_n]
        top_losers = sorted(results, key=lambda x: x.get("change_pct", 0))[
            :top_n
        ]

        prompt = f"""정치 테마주 스크리닝 결과로 일일 분석 리포트를 작성해줘.

선거 D-{phase.get('days_until_election', '?')} | 단계: {phase.get('phase', '')}

오늘 요약:
- 상승 {summary.get('up', 0)}개 / 하락 {summary.get('down', 0)}개 / 급등 {summary.get('surge_count', 0)}개

상승 TOP:
{chr(10).join(f"- {r.get('name','')} ({r.get('ticker','')}) +{r.get('change_pct',0):.2f}% | 태그: {', '.join(r.get('tags',[]))}" for r in top_gainers)}

하락 TOP:
{chr(10).join(f"- {r.get('name','')} ({r.get('ticker','')}) {r.get('change_pct',0):.2f}% | 태그: {', '.join(r.get('tags',[]))}" for r in top_losers)}

후보별 평균 등락률:
{chr(10).join(f"- {n}: {info.get('avg_change_pct',0):.2f}% ({info.get('party','')}, {info.get('stock_count',0)}종목)" for n, info in candidates.items())}

다음 형식으로 작성:
1. 오늘의 핵심 포인트 (2-3줄)
2. 주목할 움직임 (특이 종목/후보)
3. 선거 시즌 관점 해석
4. 내일 주의 사항

한국어로 깔끔하게, 600자 이내."""

        report = self._call(prompt)
        if report:
            self._set_cache(ck, report)
        return report

    def analyze_poll_impact(self, poll_signals: list[dict], news_titles: list[str] = None) -> str:
        """여론조사 + 뉴스 복합 분석 — 지지율 변동 원인과 테마주 영향 해석"""
        if not poll_signals:
            return ""
        signals_text = "\n".join(
            f"- {s.get('name','')}: {s.get('current_rate','')}% "
            f"(변동 {'+' if (s.get('change') or 0) > 0 else ''}{s.get('change','?')}%p) "
            f"→ {s.get('signal_kr','')}"
            for s in poll_signals if s.get("current_rate")
        )
        if not signals_text:
            return ""

        ck = self._cache_key("poll_impact", signals_text[:200])
        cached = self._get_cache(ck)
        if cached is not None:
            return cached

        news_text = ""
        if news_titles:
            news_text = "\n최근 관련 뉴스:\n" + "\n".join(f"- {t}" for t in news_titles[:10])

        prompt = f"""여론조사 지지율 변동 데이터와 뉴스를 분석해서, 정치 테마주에 미치는 영향을 해석해줘.

여론조사 시그널:
{signals_text}
{news_text}

다음 형식으로 작성:
1. 핵심 변동 (지지율 변동이 큰 후보 + 원인 추정)
2. 테마주 호재 분석 (지지율 상승 → 관련주 긍정 영향)
3. 테마주 악재 분석 (지지율 하락 → 관련주 부정 영향)
4. 지역별 경합 변화와 투자 전략

한국어, 500자 이내."""

        result = self._call(prompt)
        if result:
            self._set_cache(ck, result)
        return result
