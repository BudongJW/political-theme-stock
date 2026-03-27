"""
주가 예측 모델
- 지지율 변동 × 선거 시즌 × 거래량 상관분석
- 정치 이벤트 기반 주가 반응 패턴 감지
- 테마주 종합 스코어 산출
"""
import logging
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 선거 사이클별 테마주 반응 패턴 (과거 데이터 기반 경험치)
ELECTION_CYCLE_PATTERN = {
    "d_180_plus": {"label": "초기 관심", "avg_premium": 0, "volatility": "low"},
    "d_120_180": {"label": "인물 부상기", "avg_premium": 3, "volatility": "medium"},
    "d_60_120":  {"label": "경선 시즌", "avg_premium": 8, "volatility": "high"},
    "d_30_60":   {"label": "본선 돌입", "avg_premium": 15, "volatility": "very_high"},
    "d_14_30":   {"label": "막판 스퍼트", "avg_premium": 20, "volatility": "extreme"},
    "d_7_14":    {"label": "선거 직전", "avg_premium": 12, "volatility": "high"},
    "d_0_7":     {"label": "D-day 임박", "avg_premium": 5, "volatility": "medium"},
    "d_after":   {"label": "선거 후", "avg_premium": -10, "volatility": "high"},
}

# 시그널 강도별 예상 영향도 (%)
SIGNAL_IMPACT = {
    "strong_bull": {"price_range": (5, 15), "volume_mult": (3, 8)},
    "bull":        {"price_range": (2, 8),  "volume_mult": (2, 5)},
    "neutral":     {"price_range": (-2, 2), "volume_mult": (1, 2)},
    "bear":        {"price_range": (-8, -2), "volume_mult": (2, 5)},
    "strong_bear": {"price_range": (-15, -5), "volume_mult": (3, 8)},
}


class StockPredictor:
    def __init__(self, stock_collector, poll_data_collector, theme_mapper,
                 days_until_election: int = 68):
        self.sc = stock_collector
        self.pdc = poll_data_collector
        self.tm = theme_mapper
        self.days_until = days_until_election

    def _get_cycle_phase(self) -> dict:
        d = self.days_until
        if d < 0:
            return ELECTION_CYCLE_PATTERN["d_after"]
        elif d <= 7:
            return ELECTION_CYCLE_PATTERN["d_0_7"]
        elif d <= 14:
            return ELECTION_CYCLE_PATTERN["d_7_14"]
        elif d <= 30:
            return ELECTION_CYCLE_PATTERN["d_14_30"]
        elif d <= 60:
            return ELECTION_CYCLE_PATTERN["d_30_60"]
        elif d <= 120:
            return ELECTION_CYCLE_PATTERN["d_60_120"]
        elif d <= 180:
            return ELECTION_CYCLE_PATTERN["d_120_180"]
        else:
            return ELECTION_CYCLE_PATTERN["d_180_plus"]

    def analyze_ticker(self, ticker: str) -> dict:
        """개별 종목 종합 분석"""
        # 1) 주가 데이터
        ohlcv = self.sc.get_ohlcv(ticker, days=60)
        if ohlcv.empty:
            return {"ticker": ticker, "error": "주가 데이터 없음"}

        price_stats = self._calc_price_stats(ohlcv)
        volume_stats = self._calc_volume_stats(ohlcv)

        # 2) 관련 정치인 + 지지율 변동
        related = self._get_related_politicians(ticker)
        poll_impacts = []
        for pol in related:
            momentum = self.pdc.calculate_momentum(pol["name"], pol.get("region"))
            change = momentum.get("change") or 0
            poll_impacts.append({
                "name": pol["name"],
                "party": pol.get("party", ""),
                "region": pol.get("region", ""),
                "poll_change": change,
                "trend": momentum.get("trend", ""),
                "current_rate": momentum.get("current"),
            })

        # 3) 선거 사이클 위치
        cycle = self._get_cycle_phase()

        # 4) 복합 스코어 산출
        score = self._calculate_composite_score(
            price_stats, volume_stats, poll_impacts, cycle
        )

        return {
            "ticker": ticker,
            "name": price_stats.get("name", ticker),
            "price": price_stats,
            "volume": volume_stats,
            "related_politicians": poll_impacts,
            "cycle_phase": cycle["label"],
            "cycle_premium": cycle["avg_premium"],
            "composite_score": score,
        }

    def _calc_price_stats(self, df: pd.DataFrame) -> dict:
        """주가 통계 (추세, 변동성, MA)"""
        if df.empty:
            return {}

        closes = df["종가"].values.astype(float)
        current = closes[-1]

        # 이동평균
        ma5 = float(np.mean(closes[-5:])) if len(closes) >= 5 else current
        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else current

        # 변동성 (20일 표준편차 / 평균)
        if len(closes) >= 5:
            returns = np.diff(closes) / closes[:-1]
            volatility = float(np.std(returns)) * 100
        else:
            volatility = 0

        # 추세 (5일 선형 회귀 기울기)
        if len(closes) >= 5:
            x = np.arange(min(len(closes), 20))
            recent = closes[-len(x):]
            slope = float(np.polyfit(x, recent, 1)[0])
            trend_pct = (slope / current) * 100
        else:
            trend_pct = 0

        # RSI (14일)
        rsi = self._calc_rsi(closes)

        return {
            "current": int(current),
            "ma5": int(ma5),
            "ma20": int(ma20),
            "ma_signal": "golden_cross" if ma5 > ma20 else "dead_cross",
            "volatility": round(volatility, 2),
            "trend_pct": round(trend_pct, 2),
            "trend": "상승" if trend_pct > 0.3 else "하락" if trend_pct < -0.3 else "횡보",
            "rsi": round(rsi, 1),
            "rsi_signal": "과매수" if rsi > 70 else "과매도" if rsi < 30 else "중립",
            "change_5d": round((closes[-1] / closes[-6] - 1) * 100, 2) if len(closes) >= 6 else 0,
            "change_20d": round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else 0,
        }

    def _calc_rsi(self, closes: np.ndarray, period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _calc_volume_stats(self, df: pd.DataFrame) -> dict:
        """거래량 통계"""
        if df.empty or "거래량" not in df.columns:
            return {}

        vols = df["거래량"].values.astype(float)
        today = vols[-1]
        avg5 = float(np.mean(vols[-5:])) if len(vols) >= 5 else today
        avg20 = float(np.mean(vols[-20:])) if len(vols) >= 20 else today

        ratio_5d = today / avg5 if avg5 > 0 else 1
        ratio_20d = today / avg20 if avg20 > 0 else 1

        # 거래량 추세 (5일 대비 20일)
        vol_trend = avg5 / avg20 if avg20 > 0 else 1

        return {
            "today": int(today),
            "avg_5d": int(avg5),
            "avg_20d": int(avg20),
            "ratio_5d": round(ratio_5d, 2),
            "ratio_20d": round(ratio_20d, 2),
            "surge": ratio_20d >= 3.0,
            "vol_trend": "증가" if vol_trend > 1.5 else "감소" if vol_trend < 0.7 else "보합",
            "vol_trend_ratio": round(vol_trend, 2),
        }

    def _get_related_politicians(self, ticker: str) -> list[dict]:
        """종목 관련 정치인 목록"""
        related = []
        for pol in self.tm.data.get("politicians", []):
            for s in pol.get("related_stocks", []):
                if s.get("ticker") == ticker:
                    related.append({
                        "name": pol["name"],
                        "party": pol.get("party", ""),
                        "region": "전국",
                    })
        for cand in self.tm.data.get("local_candidates_2026", []):
            for s in cand.get("related_stocks", []):
                if s.get("ticker") == ticker:
                    related.append({
                        "name": cand["name"],
                        "party": cand.get("party", ""),
                        "region": cand.get("region", ""),
                    })
        return related

    def _calculate_composite_score(self, price: dict, volume: dict,
                                    poll_impacts: list, cycle: dict) -> dict:
        """
        복합 스코어 (0~100)
        = 주가 모멘텀(30%) + 거래량 시그널(20%) + 여론조사 영향(30%) + 선거사이클(20%)
        """
        # 1) 주가 모멘텀 (0~100)
        price_score = 50.0
        if price:
            trend = price.get("trend_pct", 0)
            price_score += min(max(trend * 5, -30), 30)  # 추세 기반 ±30
            if price.get("ma_signal") == "golden_cross":
                price_score += 10
            else:
                price_score -= 10
            rsi = price.get("rsi", 50)
            if rsi < 30:
                price_score += 10  # 과매도 → 반등 기대
            elif rsi > 70:
                price_score -= 10  # 과매수 → 조정 경고

        # 2) 거래량 시그널 (0~100)
        volume_score = 50.0
        if volume:
            ratio = volume.get("ratio_20d", 1)
            if ratio >= 5:
                volume_score = 90
            elif ratio >= 3:
                volume_score = 75
            elif ratio >= 2:
                volume_score = 65
            elif ratio >= 1.5:
                volume_score = 55
            else:
                volume_score = 40
            if volume.get("vol_trend") == "증가":
                volume_score += 10

        # 3) 여론조사 영향 (0~100)
        poll_score = 50.0
        if poll_impacts:
            total_impact = 0
            for p in poll_impacts:
                change = p.get("poll_change", 0) or 0
                # 지지율 1%p 상승 → +5점
                total_impact += change * 5
            poll_score += min(max(total_impact, -40), 40)

        # 4) 선거 사이클 (0~100)
        premium = cycle.get("avg_premium", 0)
        cycle_score = 50 + premium * 2  # 프리미엄 반영

        # 데이터 가용성 체크
        has_volume = bool(volume) and volume.get("avg_20d", 0) > 0
        has_poll = bool(poll_impacts) and any(
            (p.get("poll_change") or 0) != 0 for p in poll_impacts
        )

        # 가중 합산
        total = (
            price_score * 0.30 +
            volume_score * 0.20 +
            poll_score * 0.30 +
            cycle_score * 0.20
        )
        total = max(0, min(100, total))

        # 시그널 판정
        if total >= 75:
            signal = "strong_buy"
            signal_kr = "적극 매수"
        elif total >= 60:
            signal = "buy"
            signal_kr = "매수"
        elif total >= 40:
            signal = "hold"
            signal_kr = "관망"
        elif total >= 25:
            signal = "sell"
            signal_kr = "매도"
        else:
            signal = "strong_sell"
            signal_kr = "적극 매도"

        # 데이터 부족 시 신뢰도 하향 표시
        data_quality = "high" if has_volume and has_poll else "medium" if has_volume or has_poll else "low"

        return {
            "total": round(total, 1),
            "signal": signal,
            "signal_kr": signal_kr,
            "data_quality": data_quality,
            "has_volume_data": has_volume,
            "has_poll_data": has_poll,
            "components": {
                "price_momentum": round(price_score, 1),
                "volume_signal": round(volume_score, 1),
                "poll_impact": round(poll_score, 1),
                "cycle_premium": round(cycle_score, 1),
            },
        }

    def analyze_all_theme_stocks(self, max_tickers: int = 50) -> dict:
        """전체 테마주 종합 분석"""
        tickers = self.tm.get_all_tickers()[:max_tickers]
        analyses = []

        for ticker in tickers:
            try:
                result = self.analyze_ticker(ticker)
                if "error" not in result:
                    # close=0 (상장폐지·데이터없음) 필터링
                    current_price = result.get("price", {}).get("current", 0)
                    if current_price <= 0:
                        logger.info(f"종목 제외 (close=0): {ticker}")
                        continue
                    analyses.append(result)
            except Exception as e:
                logger.warning(f"종목 분석 실패 ({ticker}): {e}")

        # 스코어 기준 정렬
        analyses.sort(key=lambda x: x.get("composite_score", {}).get("total", 0), reverse=True)

        # 요약 통계
        scores = [a["composite_score"]["total"] for a in analyses if a.get("composite_score")]
        buy_signals = sum(1 for a in analyses if a.get("composite_score", {}).get("signal") in ("strong_buy", "buy"))
        sell_signals = sum(1 for a in analyses if a.get("composite_score", {}).get("signal") in ("strong_sell", "sell"))

        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "days_until_election": self.days_until,
            "cycle_phase": self._get_cycle_phase()["label"],
            "total_analyzed": len(analyses),
            "analyses": analyses,
            "summary": {
                "avg_score": round(np.mean(scores), 1) if scores else 0,
                "max_score": round(max(scores), 1) if scores else 0,
                "min_score": round(min(scores), 1) if scores else 0,
                "buy_signals": buy_signals,
                "sell_signals": sell_signals,
                "hold_signals": len(analyses) - buy_signals - sell_signals,
            },
            "top_picks": [
                {
                    "ticker": a["ticker"],
                    "name": a.get("name", ""),
                    "score": a["composite_score"]["total"],
                    "signal": a["composite_score"]["signal_kr"],
                    "reason": self._generate_reason(a),
                }
                for a in analyses[:10]
            ],
        }

    def _generate_reason(self, analysis: dict) -> str:
        """분석 결과 기반 한줄 사유"""
        parts = []
        cs = analysis.get("composite_score", {}).get("components", {})

        if cs.get("poll_impact", 50) >= 65:
            pols = [p["name"] for p in analysis.get("related_politicians", []) if (p.get("poll_change") or 0) > 0]
            if pols:
                parts.append(f"{','.join(pols[:2])} 지지율 상승")
        elif cs.get("poll_impact", 50) <= 35:
            parts.append("관련 후보 지지율 하락")

        if cs.get("volume_signal", 50) >= 70:
            parts.append("거래량 급증")

        price = analysis.get("price", {})
        if price.get("ma_signal") == "golden_cross":
            parts.append("골든크로스")
        if price.get("rsi_signal") == "과매도":
            parts.append("과매도 반등 기대")

        if cs.get("cycle_premium", 50) >= 70:
            parts.append(f"선거 시즌 프리미엄 ({analysis.get('cycle_phase', '')})")

        return " + ".join(parts) if parts else "종합 분석 기반"

    def get_correlation_matrix(self, tickers: list[str] = None, days: int = 30) -> dict:
        """종목간 상관관계 분석"""
        if tickers is None:
            tickers = self.tm.get_all_tickers()[:20]

        price_data = {}
        for ticker in tickers:
            ohlcv = self.sc.get_ohlcv(ticker, days=days)
            if not ohlcv.empty and len(ohlcv) >= 10:
                closes = ohlcv["종가"].values.astype(float)
                returns = np.diff(closes) / closes[:-1]
                price_data[ticker] = returns

        if len(price_data) < 2:
            return {"error": "상관분석 데이터 부족"}

        # 길이 맞추기
        min_len = min(len(v) for v in price_data.values())
        aligned = {k: v[-min_len:] for k, v in price_data.items()}
        tickers_list = list(aligned.keys())
        matrix = np.array([aligned[t] for t in tickers_list])

        corr = np.corrcoef(matrix)

        # 높은 상관관계 쌍 추출
        pairs = []
        for i in range(len(tickers_list)):
            for j in range(i + 1, len(tickers_list)):
                pairs.append({
                    "ticker_a": tickers_list[i],
                    "ticker_b": tickers_list[j],
                    "correlation": round(float(corr[i][j]), 3),
                })
        pairs.sort(key=lambda x: abs(x["correlation"]), reverse=True)

        return {
            "tickers": tickers_list,
            "period_days": days,
            "top_correlated": pairs[:10],
            "top_inverse": [p for p in pairs if p["correlation"] < -0.3][:5],
        }
