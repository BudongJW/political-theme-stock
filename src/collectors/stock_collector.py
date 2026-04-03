"""
주가/수급 데이터 수집기 (pykrx 기반)
"""
from pykrx import stock
import pandas as pd
from datetime import datetime, timedelta
import logging
import time

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


def _retry(fn, label: str, retries: int = MAX_RETRIES):
    """pykrx API 호출 재시도 래퍼"""
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if attempt < retries - 1:
                logger.warning(f"{label} 재시도 ({attempt+1}/{retries}): {e}")
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                logger.error(f"{label} 최종 실패: {e}")
                raise


class StockCollector:
    def get_ohlcv(self, ticker: str, days: int = 20) -> pd.DataFrame:
        """OHLCV 조회 (재시도 포함)"""
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
        try:
            df = _retry(
                lambda: stock.get_market_ohlcv(start, end, ticker),
                f"OHLCV({ticker})"
            )
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
        close_price = int(df["종가"].iloc[-1])
        if close_price <= 0:
            logger.warning(f"유효하지 않은 종가 ({ticker}): {close_price}")
            return {"surge": False, "close": 0}

        prev_close = df["종가"].iloc[-2] if len(df) >= 2 else 0
        change_pct = round(
            (close_price - prev_close) / prev_close * 100, 2
        ) if prev_close > 0 else 0.0

        return {
            "surge": ratio >= surge_ratio,
            "ratio": round(ratio, 2),
            "today_volume": int(today_volume),
            "avg_volume": int(avg_volume),
            "close": close_price,
            "change_pct": change_pct,
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
