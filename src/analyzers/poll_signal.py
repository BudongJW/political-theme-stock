"""
여론조사 기반 호재/악재 시그널 엔진
- 지지율 변동 → 관련주 매수/매도 시그널 자동 판단
- 뉴스 + 여론조사 교차 분석
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# 시그널 판정 기준
THRESHOLDS = {
    "strong_bull": 5.0,    # +5%p 이상 → 강한 호재
    "bull": 2.0,           # +2%p 이상 → 호재
    "bear": -2.0,          # -2%p 이하 → 악재
    "strong_bear": -5.0,   # -5%p 이하 → 강한 악재
    "rank_change": True,   # 순위 변동 → 강한 시그널
}


class PollSignalEngine:
    def __init__(self, poll_collector, theme_mapper):
        self.pc = poll_collector
        self.tm = theme_mapper

    def analyze_candidate_signal(self, name: str, region: str = None) -> dict:
        """단일 후보의 여론조사 시그널 분석"""
        momentum = self.pc.calculate_momentum(name, region)
        change = momentum.get("change")
        current = momentum.get("current")

        if change is None:
            return {
                "name": name, "region": region,
                "signal": "neutral", "signal_kr": "데이터 부족",
                "strength": 0, "detail": "여론조사 데이터 2건 이상 필요",
                "momentum": momentum,
            }

        # 시그널 판정
        if change >= THRESHOLDS["strong_bull"]:
            signal, signal_kr, strength = "strong_bull", "강한 호재", 3
        elif change >= THRESHOLDS["bull"]:
            signal, signal_kr, strength = "bull", "호재", 2
        elif change <= THRESHOLDS["strong_bear"]:
            signal, signal_kr, strength = "strong_bear", "강한 악재", -3
        elif change <= THRESHOLDS["bear"]:
            signal, signal_kr, strength = "bear", "악재", -2
        else:
            signal, signal_kr, strength = "neutral", "보합", 0

        # 관련주 영향 판단
        affected_stocks = self._get_affected_stocks(name)

        detail = f"지지율 {current}% (변동 {'+' if change > 0 else ''}{change}%p)"
        if momentum.get("trend") == "급등":
            detail += " — 지지율 급상승, 관련주 모니터링 강화"
        elif momentum.get("trend") == "급락":
            detail += " — 지지율 급락, 관련주 하락 주의"

        return {
            "name": name,
            "region": region,
            "signal": signal,
            "signal_kr": signal_kr,
            "strength": strength,
            "change": change,
            "current_rate": current,
            "detail": detail,
            "affected_stocks": affected_stocks,
            "momentum": momentum,
            "analyzed_at": datetime.now().isoformat(),
        }

    def _get_affected_stocks(self, name: str) -> list[dict]:
        """후보 관련 주식 목록"""
        stocks = []
        # 대선 후보
        for pol in self.tm.data.get("politicians", []):
            if pol.get("name") == name:
                for s in pol.get("related_stocks", []):
                    stocks.append({
                        "ticker": s["ticker"], "name": s.get("name", ""),
                        "relation": s.get("relation", ""),
                    })
        # 지방선거 후보
        for cand in self.tm.data.get("local_candidates_2026", []):
            if cand.get("name") == name:
                for s in cand.get("related_stocks", []):
                    stocks.append({
                        "ticker": s["ticker"], "name": s.get("name", ""),
                        "relation": s.get("relation", ""),
                    })
        return stocks

    def analyze_all_candidates(self) -> list[dict]:
        """모든 추적 후보의 시그널 일괄 분석"""
        signals = []
        analyzed_names = set()

        for pol in self.tm.data.get("politicians", []):
            name = pol.get("name", "")
            if name and name not in analyzed_names:
                sig = self.analyze_candidate_signal(name)
                signals.append(sig)
                analyzed_names.add(name)

        for cand in self.tm.data.get("local_candidates_2026", []):
            name = cand.get("name", "")
            region = cand.get("region", "")
            if name and name not in analyzed_names:
                sig = self.analyze_candidate_signal(name, region)
                signals.append(sig)
                analyzed_names.add(name)

        # 시그널 강도 순 정렬
        signals.sort(key=lambda x: abs(x.get("strength", 0)), reverse=True)
        return signals

    def analyze_region_battle(self, region: str) -> dict:
        """지역별 후보 간 경쟁 구도 분석"""
        latest = self.pc.get_latest_polls_by_region().get(region)
        if not latest:
            return {"region": region, "status": "데이터 없음"}

        rates = latest.get("rates", {})
        if not rates:
            return {"region": region, "status": "지지율 데이터 없음"}

        sorted_cands = sorted(rates.items(), key=lambda x: x[1], reverse=True)
        leader = sorted_cands[0] if sorted_cands else ("", 0)
        runner_up = sorted_cands[1] if len(sorted_cands) > 1 else ("", 0)
        gap = round(leader[1] - runner_up[1], 1) if runner_up[1] else leader[1]

        # 경합도 판단
        if gap <= 3:
            competitiveness = "초접전"
        elif gap <= 7:
            competitiveness = "경합"
        elif gap <= 15:
            competitiveness = "우세"
        else:
            competitiveness = "압도적 우세"

        return {
            "region": region,
            "leader": {"name": leader[0], "rate": leader[1]},
            "runner_up": {"name": runner_up[0], "rate": runner_up[1]},
            "gap": gap,
            "competitiveness": competitiveness,
            "all_rates": dict(sorted_cands),
            "source": latest.get("source_title", ""),
            "date": latest.get("date", ""),
        }

    def generate_signal_summary(self) -> dict:
        """전체 시그널 요약 (대시보드 + 리포트용)"""
        signals = self.analyze_all_candidates()
        bulls = [s for s in signals if s["strength"] > 0]
        bears = [s for s in signals if s["strength"] < 0]
        neutrals = [s for s in signals if s["strength"] == 0]

        # 지역별 경쟁 구도
        regions_with_data = set()
        for poll in self.pc.get_all_polls():
            if poll.get("region"):
                regions_with_data.add(poll["region"])
        battles = {}
        for region in regions_with_data:
            battles[region] = self.analyze_region_battle(region)

        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total_analyzed": len(signals),
            "bull_count": len(bulls),
            "bear_count": len(bears),
            "neutral_count": len(neutrals),
            "signals": signals,
            "top_bulls": bulls[:5],
            "top_bears": bears[:5],
            "regional_battles": battles,
            "generated_at": datetime.now().isoformat(),
        }
