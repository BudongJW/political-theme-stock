"""
정치 테마주 매매 시그널 통합 감지기
여론조사 변동 + 뉴스 임팩트 + 수급 이상을 종합해 시그널 생성
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    ticker: str
    name: str
    signal_type: str          # "BUY_WATCH" | "SELL_WATCH" | "MONITOR"
    strength: int             # 1-10
    reasons: list[str] = field(default_factory=list)
    politicians: list[str] = field(default_factory=list)
    volume_ratio: float = 0.0
    price_change_pct: float = 0.0
    news_impact_score: int = 0


class SignalDetector:
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.poll_change_threshold = self.config.get("poll_change_pct", 2.0)
        self.volume_surge_ratio = self.config.get("volume_surge_ratio", 3.0)
        self.price_change_threshold = self.config.get("price_change_pct", 5.0)

    def detect(
        self,
        stock_data: list[dict],
        analyzed_news: list[dict],
        poll_changes: dict,           # {"이재명": +2.3, "김문수": -1.5}
        theme_mapper,
    ) -> list[Signal]:
        """
        stock_data: StockCollector.screen_theme_stocks() 결과
        analyzed_news: SentimentAnalyzer.analyze_news_batch() 결과
        poll_changes: 지지율 변동 dict
        """
        signals = []

        # 뉴스 임팩트를 종목별로 집계
        ticker_news_impact: dict[str, int] = {}
        ticker_news_politicians: dict[str, list] = {}
        for news in analyzed_news:
            if news.get("impact_score", 0) < 5:
                continue
            for pol_name in news.get("relevant_politicians", []):
                for stock_info in theme_mapper.get_tickers_for_politician(pol_name):
                    t = stock_info["ticker"]
                    ticker_news_impact[t] = max(ticker_news_impact.get(t, 0), news["impact_score"])
                    ticker_news_politicians.setdefault(t, [])
                    if pol_name not in ticker_news_politicians[t]:
                        ticker_news_politicians[t].append(pol_name)
            for theme in news.get("policy_themes", []):
                for stock_info in theme_mapper.get_tickers_for_theme(theme):
                    t = stock_info["ticker"]
                    ticker_news_impact[t] = max(ticker_news_impact.get(t, 0), news["impact_score"])

        # 종목별 시그널 생성
        for sd in stock_data:
            ticker = sd["ticker"]
            reasons = []
            strength = 0

            # 1. 거래량 급증
            if sd.get("surge"):
                ratio = sd.get("ratio", 0)
                reasons.append(f"거래량 {ratio}배 급증")
                strength += min(4, int(ratio))

            # 2. 주가 변동
            pct = abs(sd.get("change_pct", 0))
            if pct >= self.price_change_threshold:
                reasons.append(f"주가 {sd.get('change_pct', 0):+.1f}%")
                strength += 2

            # 3. 뉴스 임팩트
            news_score = ticker_news_impact.get(ticker, 0)
            if news_score >= 5:
                reasons.append(f"정치 뉴스 임팩트 {news_score}/10")
                strength += news_score // 2

            # 4. 지지율 변동 연동
            related_politicians = ticker_news_politicians.get(ticker, [])
            for pol, change in poll_changes.items():
                if pol in [p["name"] for stocks in [theme_mapper.get_tickers_for_politician(pol)] for p in stocks if p.get("ticker") == ticker]:
                    if abs(change) >= self.poll_change_threshold:
                        reasons.append(f"{pol} 지지율 {change:+.1f}%p 변동")
                        strength += 2

            if strength < 3:
                continue

            signal_type = "BUY_WATCH" if sd.get("change_pct", 0) > 0 else "SELL_WATCH"
            if strength < 5:
                signal_type = "MONITOR"

            signals.append(Signal(
                ticker=ticker,
                name=sd.get("name", ticker),
                signal_type=signal_type,
                strength=min(strength, 10),
                reasons=reasons,
                politicians=related_politicians,
                volume_ratio=sd.get("ratio", 0),
                price_change_pct=sd.get("change_pct", 0),
                news_impact_score=news_score,
            ))

        signals.sort(key=lambda s: s.strength, reverse=True)
        logger.info(f"시그널 {len(signals)}건 생성")
        return signals
