"""
당선예측 모델
- 여론조사 트렌드 + 선거이력 + 지역성향 기반 당선확률 산출
- 과거 지방선거 데이터(7~8회) 기반 보정 계수 적용
- 테마주 영향도 연동 (당선확률 ↑ → 관련주 호재)
"""
import json
import logging
import math
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# 과거 지방선거 여론조사→실제 결과 보정 데이터
# (여론조사 1위 후보의 실제 당선 확률, 격차별)
HISTORICAL_ACCURACY = {
    # 격차(pp): [당선확률, 사례 수]
    "gap_0_3": {"win_rate": 0.55, "label": "초접전"},       # 3%p 이내: 55% 당선
    "gap_3_7": {"win_rate": 0.72, "label": "경합"},         # 3~7%p: 72% 당선
    "gap_7_15": {"win_rate": 0.88, "label": "우세"},        # 7~15%p: 88% 당선
    "gap_15_plus": {"win_rate": 0.96, "label": "압도적"},   # 15%p+: 96% 당선
}

# 지역 성향 기본값 (정당별 기본 지지율)
REGIONAL_BASE = {
    "서울": {"더불어민주당": 52, "국민의힘": 42},
    "경기": {"더불어민주당": 53, "국민의힘": 40},
    "인천": {"더불어민주당": 50, "국민의힘": 43},
    "부산": {"더불어민주당": 38, "국민의힘": 55},
    "대구": {"더불어민주당": 20, "국민의힘": 72},
    "광주": {"더불어민주당": 85, "국민의힘": 5},
    "대전": {"더불어민주당": 48, "국민의힘": 45},
    "울산": {"더불어민주당": 42, "국민의힘": 50},
    "세종": {"더불어민주당": 55, "국민의힘": 38},
    "강원": {"더불어민주당": 42, "국민의힘": 52},
    "충북": {"더불어민주당": 48, "국민의힘": 45},
    "충남": {"더불어민주당": 47, "국민의힘": 46},
    "전북": {"더불어민주당": 82, "국민의힘": 8},
    "경북": {"더불어민주당": 18, "국민의힘": 75},
    "경남": {"더불어민주당": 38, "국민의힘": 55},
    "제주": {"더불어민주당": 50, "국민의힘": 40},
}

# D-day 기준 여론조사 정확도 가중치
# 선거가 가까울수록 여론조사가 정확
DDAY_WEIGHT = {
    "d_90_plus": 0.6,   # 90일 이상 전: 여론조사 신뢰도 낮음
    "d_60_90": 0.7,
    "d_30_60": 0.8,
    "d_14_30": 0.9,
    "d_7_14": 0.95,
    "d_0_7": 0.98,      # 7일 이내: 거의 정확
}


class ElectionPredictor:
    def __init__(self, poll_collector, theme_mapper, days_until_election: int = 68):
        self.pc = poll_collector
        self.tm = theme_mapper
        self.days_until = days_until_election

    def _get_dday_weight(self) -> float:
        d = self.days_until
        if d > 90:
            return DDAY_WEIGHT["d_90_plus"]
        elif d > 60:
            return DDAY_WEIGHT["d_60_90"]
        elif d > 30:
            return DDAY_WEIGHT["d_30_60"]
        elif d > 14:
            return DDAY_WEIGHT["d_14_30"]
        elif d > 7:
            return DDAY_WEIGHT["d_7_14"]
        else:
            return DDAY_WEIGHT["d_0_7"]

    def _get_gap_category(self, gap: float) -> dict:
        if gap <= 3:
            return HISTORICAL_ACCURACY["gap_0_3"]
        elif gap <= 7:
            return HISTORICAL_ACCURACY["gap_3_7"]
        elif gap <= 15:
            return HISTORICAL_ACCURACY["gap_7_15"]
        else:
            return HISTORICAL_ACCURACY["gap_15_plus"]

    def predict_region(self, region: str) -> dict:
        """지역별 당선 예측"""
        latest = self.pc.get_latest_polls_by_region().get(region)
        if not latest or not latest.get("rates"):
            return self._predict_from_base(region)

        rates = latest["rates"]
        sorted_cands = sorted(rates.items(), key=lambda x: x[1], reverse=True)
        if not sorted_cands:
            return self._predict_from_base(region)

        dday_weight = self._get_dday_weight()
        regional_base = REGIONAL_BASE.get(region, {})

        predictions = []
        total_rate = sum(r for _, r in sorted_cands)
        leader_rate = sorted_cands[0][1]

        for i, (name, rate) in enumerate(sorted_cands):
            # 1) 여론조사 기반 기초 확률
            if total_rate > 0:
                poll_prob = rate / total_rate
            else:
                poll_prob = 1.0 / len(sorted_cands)

            # 2) 지역 성향 보정
            cand_party = self._get_party(name)
            base_rate = regional_base.get(cand_party, 30) / 100
            regional_factor = 1.0 + (base_rate - 0.5) * 0.15  # 약한 보정

            # 3) D-day 가중치 (여론조사 신뢰도)
            adjusted_prob = poll_prob * dday_weight + (1 - dday_weight) * base_rate

            # 4) 격차 기반 역사적 보정 (1위만)
            if i == 0 and len(sorted_cands) > 1:
                gap = rate - sorted_cands[1][1]
                gap_info = self._get_gap_category(gap)
                historical_win = gap_info["win_rate"]
                adjusted_prob = adjusted_prob * 0.6 + historical_win * 0.4

            # 5) 모멘텀 보정
            momentum = self.pc.calculate_momentum(name, region)
            momentum_change = momentum.get("change") or 0
            momentum_factor = 1.0 + momentum_change * 0.01  # 1%p 변동 → 1% 보정
            adjusted_prob *= momentum_factor

            # 확률 클리핑
            adjusted_prob = max(0.01, min(0.99, adjusted_prob))

            predictions.append({
                "name": name,
                "party": cand_party,
                "poll_rate": rate,
                "win_probability": round(adjusted_prob * 100, 1),
                "momentum": momentum.get("trend", ""),
                "momentum_change": momentum_change,
            })

        # 정규화 (합계 100%)
        total_prob = sum(p["win_probability"] for p in predictions)
        if total_prob > 0:
            for p in predictions:
                p["win_probability"] = round(p["win_probability"] / total_prob * 100, 1)

        predictions.sort(key=lambda x: x["win_probability"], reverse=True)

        gap = (predictions[0]["poll_rate"] - predictions[1]["poll_rate"]) if len(predictions) > 1 else 0
        gap_info = self._get_gap_category(abs(gap))

        return {
            "region": region,
            "predictions": predictions,
            "leader": predictions[0]["name"],
            "leader_prob": predictions[0]["win_probability"],
            "competitiveness": gap_info["label"],
            "gap": round(gap, 1),
            "confidence": round(self._get_dday_weight() * 100, 0),
            "poll_date": latest.get("date", ""),
            "source": latest.get("source_title", ""),
        }

    def _predict_from_base(self, region: str) -> dict:
        """여론조사 없을 때 지역 성향 기반 예측"""
        base = REGIONAL_BASE.get(region, {"더불어민주당": 50, "국민의힘": 50})
        predictions = []
        for party, rate in sorted(base.items(), key=lambda x: x[1], reverse=True):
            predictions.append({
                "name": f"{party} 후보",
                "party": party,
                "poll_rate": None,
                "win_probability": round(rate, 1),
                "momentum": "",
                "momentum_change": 0,
            })
        total = sum(p["win_probability"] for p in predictions)
        if total > 0:
            for p in predictions:
                p["win_probability"] = round(p["win_probability"] / total * 100, 1)

        return {
            "region": region,
            "predictions": predictions,
            "leader": predictions[0]["name"] if predictions else "-",
            "leader_prob": predictions[0]["win_probability"] if predictions else 50,
            "competitiveness": "기본 성향 기반",
            "gap": 0,
            "confidence": 30,
            "poll_date": "",
            "source": "지역 기본 성향 (여론조사 데이터 없음)",
        }

    def _get_party(self, name: str) -> str:
        """후보 이름 → 정당"""
        for pol in self.tm.data.get("politicians", []):
            if pol.get("name") == name:
                return pol.get("party", "")
        for cand in self.tm.data.get("local_candidates_2026", []):
            if cand.get("name") == name:
                return cand.get("party", "")
        return ""

    def predict_all_regions(self) -> dict:
        """전체 지역 당선 예측"""
        regions = set()
        for cand in self.tm.data.get("local_candidates_2026", []):
            if cand.get("region"):
                regions.add(cand["region"])
        # 여론조사 있는 지역도 포함
        for region in self.pc.get_latest_polls_by_region():
            regions.add(region)

        results = {}
        for region in sorted(regions):
            if not region or region == "전국":
                continue
            results[region] = self.predict_region(region)

        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "days_until_election": self.days_until,
            "model_confidence": round(self._get_dday_weight() * 100, 0),
            "regions": results,
            "generated_at": datetime.now().isoformat(),
        }

    def get_stock_impact(self, predictions: dict) -> list[dict]:
        """당선예측 → 테마주 영향 분석"""
        impacts = []
        for region, pred in predictions.get("regions", {}).items():
            for cand in pred.get("predictions", []):
                name = cand["name"]
                prob = cand["win_probability"]
                if prob < 10:
                    continue
                # 관련주 찾기
                stocks = []
                for pol in self.tm.data.get("politicians", []):
                    if pol.get("name") == name:
                        stocks = pol.get("related_stocks", [])
                for c in self.tm.data.get("local_candidates_2026", []):
                    if c.get("name") == name:
                        stocks = c.get("related_stocks", [])

                if not stocks:
                    continue

                # 시그널 판정
                if prob >= 60:
                    signal = "bull"
                    signal_kr = "호재 (당선 유력)"
                elif prob >= 40:
                    signal = "neutral"
                    signal_kr = "경합 (관망)"
                else:
                    signal = "bear"
                    signal_kr = "악재 (당선 불투명)"

                for s in stocks:
                    impacts.append({
                        "ticker": s["ticker"],
                        "stock_name": s.get("name", ""),
                        "candidate": name,
                        "party": cand.get("party", ""),
                        "region": region,
                        "win_probability": prob,
                        "signal": signal,
                        "signal_kr": signal_kr,
                        "relation": s.get("relation", ""),
                    })

        impacts.sort(key=lambda x: x["win_probability"], reverse=True)
        return impacts
