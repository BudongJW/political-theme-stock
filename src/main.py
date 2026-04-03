"""
정치 테마주 분석 시스템 — 메인 스케줄러
"""
import signal
import sys
import yaml
import logging
import os
from pathlib import Path
from apscheduler.schedulers.blocking import BlockingScheduler

from collectors.poll_collector import PollCollector
from collectors.news_collector import NewsCollector
from collectors.stock_collector import StockCollector
from analyzers.sentiment_analyzer import SentimentAnalyzer
from analyzers.theme_mapper import ThemeMapper
from analyzers.signal_detector import SignalDetector
from notifiers.slack_notifier import SlackNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 설정 로드
BASE_DIR = Path(__file__).parent.parent
CONFIG_FILE = BASE_DIR / "config" / "settings.yaml"

with open(CONFIG_FILE, encoding="utf-8") as f:
    config = yaml.safe_load(f)

# 컴포넌트 초기화
poll_collector = PollCollector(
    naver_client_id=config.get("naver", {}).get("client_id"),
    naver_client_secret=config.get("naver", {}).get("client_secret"),
)
news_collector = NewsCollector(
    naver_client_id=config.get("naver", {}).get("client_id"),
    naver_client_secret=config.get("naver", {}).get("client_secret"),
)
stock_collector = StockCollector()
sentiment_analyzer = SentimentAnalyzer(api_key=config["anthropic"]["api_key"])
theme_mapper = ThemeMapper(BASE_DIR / "config" / "politician_stock_map.yaml")
signal_detector = SignalDetector(config.get("thresholds", {}))
slack = SlackNotifier(config["slack"]["webhook_url"])

# 지지율 이전값 저장 (변동 감지용)
_last_poll_rates: dict[str, float] = {}


def run_analysis():
    """핵심 분석 파이프라인"""
    logger.info("=== 정치 테마주 분석 시작 ===")

    politicians = theme_mapper.get_all_politicians()
    keywords = []
    for kws in theme_mapper.get_politician_keywords().values():
        keywords.extend(kws)

    # 1. 뉴스 수집
    news = news_collector.collect_all(keywords[:5])

    # 2. LLM 감성 분석
    analyzed = sentiment_analyzer.analyze_news_batch(news, politicians)
    if analyzed:
        summary = sentiment_analyzer.summarize_signals(analyzed)
        logger.info(f"\n{summary}")

    # 3. 주가 스크리닝
    all_tickers = theme_mapper.get_all_tickers()
    stock_data = stock_collector.screen_theme_stocks(
        all_tickers,
        config.get("thresholds", {}).get("volume_surge_ratio", 3.0),
    )

    # 4. 시그널 생성
    signals = signal_detector.detect(
        stock_data=stock_data,
        analyzed_news=analyzed,
        poll_changes=_last_poll_rates,
        theme_mapper=theme_mapper,
    )

    # 5. Slack 알림
    if signals:
        slack.send_signals(signals)
    else:
        logger.info("발생 시그널 없음")

    logger.info("=== 분석 완료 ===")


def run_poll_check():
    """여론조사 수집 및 변동 감지"""
    global _last_poll_rates
    logger.info("여론조사 체크...")
    polls = poll_collector.fetch_nec_polls(days_back=3)
    # 추후 지지율 파싱 로직 추가
    logger.info(f"여론조사 {len(polls)}건 수집")


if __name__ == "__main__":
    logger.info("정치 테마주 분석 시스템 시작")

    # 시작 시 즉시 1회 실행
    run_analysis()

    sched_config = config.get("schedule", {})
    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        run_analysis,
        "cron",
        minute=f"*/{sched_config.get('stock_check_minutes', 10)}",
        hour="9-15",
        day_of_week="mon-fri",
        id="stock_analysis",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        run_poll_check,
        "interval",
        hours=sched_config.get("poll_interval_hours", 6),
        id="poll_check",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    def _shutdown(signum, _frame):
        logger.info(f"시그널 {signum} 수신 — 스케줄러 종료 중...")
        scheduler.shutdown(wait=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("스케줄러 실행 중 (Ctrl+C로 종료)")
    scheduler.start()
