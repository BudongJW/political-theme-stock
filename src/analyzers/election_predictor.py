"""
당선예측 모델 (v2)
- 여론조사 트렌드 + 선거이력 + 지역성향 기반 당선확률 산출
- 과거 지방선거 데이터(7~8회) 기반 보정 계수 적용
- 테마주 영향도 연동 (당선확률 ↑ → 관련주 호재)
- v2 신규:
  - 조사기관별 편향(house effect) 보정
  - 다중 여론조사 가중 집계 (recency × sample reliability)
  - EMA 기반 모멘텀 (단순 2회 비교 → 지수이동평균)
  - 현직 프리미엄 (incumbency advantage)
"""
import json
import logging
import math
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# 과거 지방선거 여론조사→실제 결과 보정 데이터
HISTORICAL_ACCURACY = {
    "gap_0_3": {"win_rate": 0.55, "label": "초접전"},
    "gap_3_7": {"win_rate": 0.72, "label": "경합"},
    "gap_7_15": {"win_rate": 0.88, "label": "우세"},
    "gap_15_plus": {"win_rate": 0.96, "label": "압도적"},
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
DDAY_WEIGHT = {
    "d_90_plus": 0.6,
    "d_60_90": 0.7,
    "d_30_60": 0.8,
    "d_14_30": 0.9,
    "d_7_14": 0.95,
    "d_0_7": 0.98,
}

# 조사기관별 편향 보정 (양수 = 민주당 과대 추정, 음수 = 국민의힘 과대 추정)
# 과거 선거 결과 대비 여론조사 오차 기반 (경험치)
HOUSE_EFFECT = {
    "한국갤럽": {"bias": 0.0, "reliability": 0.95},   # 기준 조사기관
    "갤럽": {"bias": 0.0, "reliability": 0.95},
    "리얼미터": {"bias": 1.5, "reliability": 0.85},    # 민주당 과대 추정 경향
    "NBS": {"bias": -0.5, "reliability": 0.80},
    "한길리서치": {"bias": 0.5, "reliability": 0.75},
    "한국리서치": {"bias": 0.0, "reliability": 0.90},
    "엠브레인": {"bias": 0.0, "reliability": 0.88},
    "케이스탯리서치": {"bias": 0.3, "reliability": 0.78},
    "입소스": {"bias": -0.3, "reliability": 0.80},
    "메타보이스": {"bias": 0.0, "reliability": 0.70},
    "여론조사꽃": {"bias": 0.0, "reliability": 0.65},
}

# 현직 프리미엄 (incumbency advantage)
INCUMBENCY_BONUS = 3.0  # 현직 후보에게 +3%p 가산

# 여론조사 recency 반감기 (일)
POLL_HALF_LIFE_DAYS = 5


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

    def _is_incumbent(self, name: str) -> bool:
        """현직 여부 확인 (역할에 '현직' 포함)"""
        for cand in self.tm.data.get("local_candidates_2026", []):
            if cand.get("name") == name and "현직" in (cand.get("role") or ""):
                return True
        for pol in self.tm.data.get("politicians", []):
            if pol.get("name") == name and "현직" in (pol.get("role") or ""):
                return True
        return False

    def _aggregate_polls(self, name: str, region: str) -> dict:
        """다중 여론조사 가중 집계: recency × pollster reliability"""
        history = self.pc.get_candidate_history(name, region)
        if not history:
            return {"rate": None, "polls_used": 0, "confidence": 0}

        today = datetime.now()
        weighted_sum = 0
        weight_total = 0

        for poll in history[-10:]:  # 최근 10건
            rate = poll.get("rate", 0)
            institution = poll.get("institution", "")
            poll_date = poll.get("date", "")

            # recency 가중치 (지수 감쇠)
            try:
                days_ago = (today - datetime.strptime(poll_date, "%Y-%m-%d")).days
            except (ValueError, TypeError):
                days_ago = 30
            recency_w = math.exp(-0.693 * days_ago / POLL_HALF_LIFE_DAYS)

            # 조사기관 신뢰도 가중치
            house = HOUSE_EFFECT.get(institution, {"bias": 0, "reliability": 0.70})
            reliability_w = house["reliability"]

            # 편향 보정
            party = self._get_party(name)
            bias = house["bias"]
            if party == "더불어민주당":
                rate -= bias
            elif party == "국민의힘":
                rate += bias

            w = recency_w * reliability_w
            weighted_sum += rate * w
            weight_total += w

        if weight_total == 0:
            return {"rate": None, "polls_used": 0, "confidence": 0}

        aggregated_rate = weighted_sum / weight_total
        return {
            "rate": round(aggregated_rate, 1),
            "polls_used": len(history[-10:]),
            "confidence": round(min(100, len(history[-10:]) * 15), 0),
        }

    def _calculate_ema_momentum(self, name: str, region: str,
                                 span: int = 4) -> dict:
        """EMA 기반 모멘텀 (단순 2회 비교 대신 지수이동평균 추세)"""
        history = self.pc.get_candidate_history(name, region)
        if len(history) < 2:
            return {
                "name": name, "trend": "데이터 부족",
                "ema_change": 0, "raw_change": 0,
                "current": history[-1]["rate"] if history else None,
            }

        rates = [h["rate"] for h in history]

        # EMA 계산
        alpha = 2 / (span + 1)
        ema = rates[0]
        prev_ema = rates[0]
        for i, r in enumerate(rates[1:], 1):
            if i < len(rates) - 1:
                prev_ema = ema
            ema = alpha * r + (1 - alpha) * ema

        current = rates[-1]
        ema_change = round(ema - prev_ema, 1)
        raw_change = round(current - rates[-2], 1)

        if ema_change >= 3:
            trend = "급등"
        elif ema_change >= 1:
            trend = "상승"
        elif ema_change <= -3:
            trend = "급락"
        elif ema_change <= -1:
            trend = "하락"
        else:
            trend = "보합"

        return {
            "name": name, "trend": trend,
            "ema_change": ema_change, "raw_change": raw_change,
            "current": current, "ema": round(ema, 1),
        }

    def predict_region(self, region: str) -> dict:
        """지역별 당선 예측 (v2: 다중 여론조사 집계 + 기관 편향 보정 + 현직 프리미엄)"""
        latest = self.pc.get_latest_polls_by_region().get(region)
        if not latest or not latest.get("rates"):
            return self._predict_from_base(region)

        rates = latest["rates"]
        sorted_cands = sorted(rates.items(), key=lambda x: x[1], reverse=True)
        if not sorted_cands:
            return self._predict_from_base(region)

        if len(sorted_cands) == 1:
            return self._predict_single_candidate(region, sorted_cands[0], latest)

        dday_weight = self._get_dday_weight()
        regional_base = REGIONAL_BASE.get(region, {})

        predictions = []

        # 다중 여론조사 가중 집계
        aggregated_rates = {}
        for name, rate in sorted_cands:
            agg = self._aggregate_polls(name, region)
            aggregated_rates[name] = agg["rate"] if agg["rate"] is not None else rate

        total_rate = sum(aggregated_rates.values())
        if total_rate == 0:
            total_rate = sum(r for _, r in sorted_cands)

        for i, (name, raw_rate) in enumerate(sorted_cands):
            rate = aggregated_rates.get(name, raw_rate)

            # 1) 여론조사 기반 기초 확률
            poll_prob = rate / total_rate if total_rate > 0 else 1.0 / len(sorted_cands)

            # 2) 지역 성향 보정
            cand_party = self._get_party(name)
            base_rate = regional_base.get(cand_party, 30) / 100

            # 3) D-day 가중치
            adjusted_prob = poll_prob * dday_weight + (1 - dday_weight) * base_rate

            # 4) 격차 기반 역사적 보정 (1위만)
            if i == 0 and len(sorted_cands) > 1:
                second_rate = aggregated_rates.get(sorted_cands[1][0], sorted_cands[1][1])
                gap = rate - second_rate
                gap_info = self._get_gap_category(gap)
                historical_win = gap_info["win_rate"]
                adjusted_prob = adjusted_prob * 0.6 + historical_win * 0.4

            # 5) EMA 모멘텀 보정
            momentum = self._calculate_ema_momentum(name, region)
            ema_change = momentum.get("ema_change", 0)
            momentum_factor = 1.0 + ema_change * 0.012
            adjusted_prob *= momentum_factor

            # 6) 현직 프리미엄
            is_incumbent = self._is_incumbent(name)
            if is_incumbent:
                adjusted_prob += INCUMBENCY_BONUS / 100

            adjusted_prob = max(0.01, min(0.99, adjusted_prob))

            predictions.append({
                "name": name,
                "party": cand_party,
                "poll_rate": raw_rate,
                "aggregated_rate": round(rate, 1),
                "win_probability": round(adjusted_prob * 100, 1),
                "momentum": momentum.get("trend", ""),
                "momentum_change": ema_change,
                "ema": momentum.get("ema"),
                "is_incumbent": is_incumbent,
            })

        # 정규화 (합계 100%)
        total_prob = sum(p["win_probability"] for p in predictions)
        if total_prob > 0:
            for p in predictions:
                p["win_probability"] = round(p["win_probability"] / total_prob * 100, 1)

        predictions.sort(key=lambda x: x["win_probability"], reverse=True)

        gap = (predictions[0]["aggregated_rate"] - predictions[1]["aggregated_rate"]) if len(predictions) > 1 else 0
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

    def _predict_single_candidate(self, region: str, cand_tuple: tuple, latest: dict) -> dict:
        """단독 후보 처리 — 상대 후보 없으므로 확률 상한 제한"""
        name, rate = cand_tuple
        party = self._get_party(name)
        regional_base = REGIONAL_BASE.get(region, {})
        base_rate = regional_base.get(party, 40) / 100

        # 단독 후보: 지역 기본 성향 기반 최대 75%로 제한
        # (상대 후보가 아직 미정이므로 경합 가능성 존재)
        max_prob = 75.0
        prob = min(max_prob, 50 + base_rate * 25)

        momentum = self._calculate_ema_momentum(name, region)

        # 현직 프리미엄
        if self._is_incumbent(name):
            prob = min(max_prob, prob + INCUMBENCY_BONUS)

        predictions = [{
            "name": name,
            "party": party,
            "poll_rate": rate,
            "win_probability": round(prob, 1),
            "momentum": momentum.get("trend", ""),
            "momentum_change": momentum.get("ema_change", 0),
            "ema": momentum.get("ema"),
            "is_incumbent": self._is_incumbent(name),
        }, {
            "name": "미정 (상대 후보 미등록)",
            "party": "",
            "poll_rate": None,
            "win_probability": round(100 - prob, 1),
            "momentum": "",
            "momentum_change": 0,
        }]

        return {
            "region": region,
            "predictions": predictions,
            "leader": name,
            "leader_prob": round(prob, 1),
            "competitiveness": "단독 후보",
            "gap": 0,
            "confidence": 20,  # 단독 후보는 신뢰도 낮음
            "poll_date": latest.get("date", ""),
            "source": latest.get("source_title", ""),
            "note": "상대 후보 미등록 — 경선 결과에 따라 변동 가능",
        }

    def _predict_from_base(self, region: str) -> dict:
        """여론조사 없을 때 지역 성향 기반 예측"""
        base = REGIONAL_BASE.get(region, {"더불어민주당": 50, "국민의힘": 50})

        # 후보 등록 확인 — YAML에 등록된 후보가 있으면 이름 사용
        registered = []
        for cand in self.tm.data.get("local_candidates_2026", []):
            if cand.get("region") == region:
                registered.append({
                    "name": cand["name"],
                    "party": cand.get("party", ""),
                })

        predictions = []
        if registered:
            # 등록된 후보 기반
            for cand in registered:
                rate = base.get(cand["party"], 40)
                predictions.append({
                    "name": cand["name"],
                    "party": cand["party"],
                    "poll_rate": None,
                    "win_probability": round(rate, 1),
                    "momentum": "",
                    "momentum_change": 0,
                })
        else:
            # 후보 미등록 — 정당 기반 추정
            for party, rate in sorted(base.items(), key=lambda x: x[1], reverse=True):
                predictions.append({
                    "name": f"{party} 후보",
                    "party": party,
                    "poll_rate": None,
                    "win_probability": round(rate, 1),
                    "momentum": "",
                    "momentum_change": 0,
                })

        # 정규화 (합계 100%)
        total = sum(p["win_probability"] for p in predictions)
        if total > 0:
            for p in predictions:
                p["win_probability"] = round(p["win_probability"] / total * 100, 1)

        # 단독 등록 후보는 상한 제한
        if len(predictions) == 1:
            predictions[0]["win_probability"] = min(75.0, predictions[0]["win_probability"])
            predictions.append({
                "name": "미정 (상대 후보 미등록)",
                "party": "",
                "poll_rate": None,
                "win_probability": round(100 - predictions[0]["win_probability"], 1),
                "momentum": "",
                "momentum_change": 0,
            })

        return {
            "region": region,
            "predictions": predictions,
            "leader": predictions[0]["name"] if predictions else "-",
            "leader_prob": predictions[0]["win_probability"] if predictions else 50,
            "competitiveness": "단독 후보" if len(registered) == 1 else "기본 성향 기반",
            "gap": 0,
            "confidence": 20 if len(registered) == 1 else 30,
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
