"""
주가/수급 데이터 수집기 (pykrx 기반)
"""
from pykrx import stock
import pandas as pd
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class StockCollector:
    def get_ohlcv(self, ticker: str, days: int = 20) -> pd.DataFrame:
        """OHLCV 조회"""
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
        try:
            df = stock.get_market_ohlcv(start, end, ticker)
            return df.tail(days)
        except Exception as e:
            logger.error(f"OHLCV 조회 실패 ({ticker}): {e}")
            return pd.DataFrame()

    def get_investor_trading(self, ticker: str, days: int = 5) -> pd.DataFrame:
        """투자자별 순매수 (외인/기관/개인)"""
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 5)).strftime("%Y%m%d")
        try:
            df = stock.get_market_trading_value_by_investor(start, end, ticker)
            return df.tail(days)
        except Exception as e:
            logger.error(f"투자자 매매 조회 실패 ({ticker}): {e}")
            return pd.DataFrame()

    def detect_volume_surge(self, ticker: str, surge_ratio: float = 3.0) -> dict:
        """거래량 급증 감지"""
        df = self.get_ohlcv(ticker, days=20)
        if df.empty or len(df) < 5:
            return {"surge": False}
        avg_volume = df["거래량"].iloc[:-1].mean()
        today_volume = df["거래량"].iloc[-1]
        ratio = today_volume / avg_volume if avg_volume > 0 else 0
        return {
            "surge": ratio >= surge_ratio,
            "ratio": round(ratio, 2),
            "today_volume": int(today_volume),
            "avg_volume": int(avg_volume),
            "close": int(df["종가"].iloc[-1]),
            "change_pct": round(
                (df["종가"].iloc[-1] - df["종가"].iloc[-2]) / df["종가"].iloc[-2] * 100, 2
            ) if len(df) >= 2 else 0,
        }

    def screen_theme_stocks(self, tickers: list[str], surge_ratio: float = 3.0) -> list[dict]:
        """테마 종목 리스트 일괄 스크리닝"""
        results = []
        for ticker in tickers:
            data = self.detect_volume_surge(ticker, surge_ratio)
            data["ticker"] = ticker
            try:
                name = stock.get_market_ticker_name(ticker)
                data["name"] = name
            except Exception:
                data["name"] = ticker
            if data.get("surge"):
                logger.info(f"급등 감지: {data['name']} ({ticker}) 거래량 {data['ratio']}배")
            results.append(data)
        return results
