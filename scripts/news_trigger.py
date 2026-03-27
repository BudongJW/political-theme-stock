"""
뉴스 트리거 기반 실시간 테마주 시그널 감지
- RSS/네이버 뉴스에서 정치인+주식 키워드 감지
- Gemini API로 임팩트 판단
- 고임팩트 뉴스 발견 시 스크리닝 재실행 트리거
"""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from collectors.news_collector import NewsCollector
from analyzers.theme_mapper import ThemeMapper
from analyzers.gemini_analyzer import GeminiAnalyzer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("news_trigger")

TRIGGER_THRESHOLD = 7  # impact_score 7 이상이면 스크리닝 트리거


def check_news_triggers():
    tm = ThemeMapper(ROOT / "config" / "politician_stock_map.yaml",
                     data_dir=str(ROOT / "data" / "raw"))
    nc = NewsCollector()
    ga = GeminiAnalyzer(cache_dir=str(ROOT / "data" / "gemini_cache"))

    politicians = tm.get_all_politicians()
    # 지방선거 후보도 포함
    for cand in tm.data.get("local_candidates_2026", []):
        if cand.get("name") and cand["name"] not in politicians:
            politicians.append(cand["name"])

    themes = list(tm.data.get("policy_themes", {}).keys())

    # 최근 뉴스 수집 (구글 뉴스 RSS — API 키 불필요)
    all_news = []
    for pol_name in politicians[:5]:  # 상위 5명만 (API 절약)
        news = nc.fetch_google_news(f"{pol_name} 테마주", max_items=5)
        all_news.extend(news)

    if not all_news:
        logger.info("수집된 뉴스 없음")
        return []

    # 중복 제거
    seen = set()
    unique_news = []
    for n in all_news:
        title = n.get("title", "")
        if title not in seen:
            seen.add(title)
            unique_news.append(n)

    logger.info(f"수집된 뉴스: {len(unique_news)}건")

    # Gemini로 실시간 시그널 판단
    triggers = []
    for news in unique_news[:10]:  # 최대 10건
        result = ga.analyze_realtime_signal(
            news.get("title", ""), politicians, themes
        )
        if result.get("relevant") and result.get("impact", 0) >= TRIGGER_THRESHOLD:
            triggers.append({
                "title": news.get("title", ""),
                "link": news.get("link", ""),
                "impact": result.get("impact", 0),
                "direction": result.get("direction", ""),
                "politicians": result.get("politicians", []),
                "themes": result.get("themes", []),
                "action": result.get("action", ""),
                "detected_at": datetime.now().isoformat(),
            })

    if triggers:
        logger.info(f"고임팩트 뉴스 감지: {len(triggers)}건!")
        # 트리거 저장
        trigger_dir = ROOT / "data" / "triggers"
        trigger_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d_%H%M")
        with open(trigger_dir / f"trigger_{today}.json", "w", encoding="utf-8") as f:
            json.dump(triggers, f, ensure_ascii=False, indent=2)
        for t in triggers:
            print(f"[TRIGGER] impact={t['impact']} | {t['direction']} | {t['title'][:60]}")
    else:
        logger.info("고임팩트 뉴스 없음 (threshold={TRIGGER_THRESHOLD})")

    return triggers


if __name__ == "__main__":
    triggers = check_news_triggers()
    if triggers:
        print(f"\n→ {len(triggers)}건의 고임팩트 뉴스 감지. 스크리닝 재실행 권장.")
        sys.exit(1)  # GitHub Actions에서 후속 스텝 트리거용
    else:
        print("→ 정상. 특이 뉴스 없음.")
        sys.exit(0)
