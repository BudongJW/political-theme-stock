"""
예측 모델 자동 캘리브레이션 (v2)
- 적중률 분석 결과를 기반으로 가중치·임계값 보정
- 학습률(learning rate) 도입: 초기 보정 폭 크게, 안정화 후 미세 조정
- 메타 캘리브레이션: 이전 보정이 실제로 적중률을 개선했는지 추적
- Brier 스코어 기반 조정: 확률 예측 정확도 기반 보정
- 시그널별 Sharpe ratio 기반 가중치 조정
- 보정 내역을 기록하여 대시보드에서 확인 가능
"""
import json
import logging
import math
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# 기본 가중치 (stock_predictor.py 초기값)
DEFAULT_WEIGHTS = {
    "price_momentum": 0.30,
    "volume_signal": 0.20,
    "poll_impact": 0.30,
    "cycle_premium": 0.20,
}

# 기본 시그널 임계값
DEFAULT_THRESHOLDS = {
    "strong_buy": 75,
    "buy": 60,
    "hold": 40,
    "sell": 25,
}

# 보정 상한·하한 (과적합 방지)
WEIGHT_MIN = 0.10
WEIGHT_MAX = 0.45
THRESHOLD_SHIFT_MAX = 8  # 임계값 최대 ±8 이동

# 학습률 설정
INITIAL_LEARNING_RATE = 1.0   # 초기 보정 (v1~v3)
STABLE_LEARNING_RATE = 0.5    # 안정기 (v4~v8)
FINE_TUNE_LEARNING_RATE = 0.3 # 미세 조정 (v9+)


class Calibrator:
    def __init__(self, calibration_dir: str):
        self.cal_dir = Path(calibration_dir)
        self.cal_dir.mkdir(parents=True, exist_ok=True)
        self.cal_file = self.cal_dir / "calibration.json"

    def load_calibration(self) -> dict:
        """현재 캘리브레이션 로드 (없으면 기본값)"""
        if self.cal_file.exists():
            try:
                with open(self.cal_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"캘리브레이션 로드 실패: {e}")
        return {
            "weights": dict(DEFAULT_WEIGHTS),
            "thresholds": dict(DEFAULT_THRESHOLDS),
            "adjustments": [],
            "version": 0,
        }

    def _save_calibration(self, cal: dict):
        with open(self.cal_file, "w", encoding="utf-8") as f:
            json.dump(cal, f, ensure_ascii=False, indent=2)

    def _get_learning_rate(self, version: int) -> float:
        """버전에 따른 학습률: 초기엔 크게, 안정화 후 작게"""
        if version < 3:
            return INITIAL_LEARNING_RATE
        elif version < 8:
            return STABLE_LEARNING_RATE
        else:
            return FINE_TUNE_LEARNING_RATE

    def _evaluate_meta_calibration(self, cal: dict, accuracy_data: dict) -> dict:
        """메타 캘리브레이션: 이전 보정이 적중률을 개선했는지 평가"""
        adjustments = cal.get("adjustments", [])
        if len(adjustments) < 2:
            return {"status": "insufficient_history", "trend": "unknown"}

        recent = adjustments[-5:]  # 최근 5건
        acc_series = [a.get("accuracy_pct", 50) for a in recent if a.get("accuracy_pct")]
        brier_series = [a.get("brier_score") for a in recent if a.get("brier_score") is not None]

        result = {"status": "ok", "history_count": len(recent)}

        # 적중률 추이
        if len(acc_series) >= 2:
            acc_trend = acc_series[-1] - acc_series[0]
            result["accuracy_trend"] = round(acc_trend, 1)
            result["accuracy_improving"] = acc_trend > 0

        # Brier 스코어 추이 (낮을수록 좋음)
        if len(brier_series) >= 2:
            brier_trend = brier_series[-1] - brier_series[0]
            result["brier_trend"] = round(brier_trend, 4)
            result["brier_improving"] = brier_trend < 0

        # 보정이 역효과인 경우 감지
        if len(acc_series) >= 3:
            # 최근 3번 연속 적중률 하락이면 "보정 역효과"
            declining = all(acc_series[i] > acc_series[i + 1] for i in range(len(acc_series) - 3, len(acc_series) - 1))
            result["calibration_counterproductive"] = declining
        else:
            result["calibration_counterproductive"] = False

        return result

    def calibrate(self, accuracy_data: dict) -> dict:
        """
        적중률 데이터를 기반으로 가중치·임계값 보정 (v2).
        최소 10건 이상의 예측 데이터가 필요.
        """
        if not accuracy_data or accuracy_data.get("status") != "ok":
            return {"adjusted": False, "reason": "적중률 데이터 부족"}

        overall = accuracy_data.get("overall", {})
        total = overall.get("total_predictions", 0)
        if total < 10:
            return {"adjusted": False, "reason": f"예측 건수 부족 ({total}/10)"}

        cal = self.load_calibration()
        old_weights = dict(cal["weights"])
        old_thresholds = dict(cal["thresholds"])
        changes = []

        version = cal.get("version", 0)
        lr = self._get_learning_rate(version)

        # === 메타 캘리브레이션 평가 ===
        meta = self._evaluate_meta_calibration(cal, accuracy_data)
        if meta.get("calibration_counterproductive"):
            # 3회 연속 악화 → 기본값으로 리셋
            changes.append("메타 캘리브레이션: 3회 연속 악화 감지 → 기본값 리셋")
            new_w = dict(DEFAULT_WEIGHTS)
            new_th = dict(DEFAULT_THRESHOLDS)
            lr = INITIAL_LEARNING_RATE  # 리셋 후 학습률 초기화
        else:
            new_w, new_th, changes = self._compute_adjustments(
                cal, accuracy_data, lr, changes
            )

        # 가중치 합 정규화 (1.0)
        w_sum = sum(new_w.values())
        new_w = {k: round(v / w_sum, 3) for k, v in new_w.items()}

        # === 변경 사항 없으면 스킵 ===
        weight_changed = new_w != old_weights
        threshold_changed = new_th != old_thresholds

        if not weight_changed and not threshold_changed:
            return {
                "adjusted": False,
                "reason": "현재 설정이 적절 (변경 불필요)",
                "current_weights": old_weights,
                "current_thresholds": old_thresholds,
                "accuracy_pct": overall.get("accuracy_pct", 0),
                "brier_score": accuracy_data.get("brier_score"),
                "meta_calibration": meta,
                "learning_rate": lr,
            }

        # === 저장 ===
        acc_pct = overall.get("accuracy_pct", 50)
        sig_perf = accuracy_data.get("signal_performance", {})
        buy_avg = _weighted_avg(sig_perf, ["strong_buy", "buy"])
        sell_avg = _weighted_avg(sig_perf, ["strong_sell", "sell"])
        separation = (buy_avg or 0) - (sell_avg or 0)

        adjustment_record = {
            "date": datetime.now().isoformat(),
            "accuracy_pct": acc_pct,
            "weighted_accuracy_pct": overall.get("weighted_accuracy_pct", acc_pct),
            "brier_score": accuracy_data.get("brier_score"),
            "weighted_brier_score": accuracy_data.get("weighted_brier_score"),
            "total_predictions": total,
            "score_correlation": accuracy_data.get("score_correlation"),
            "weighted_score_correlation": accuracy_data.get("weighted_score_correlation"),
            "signal_separation": round(separation, 2) if separation else None,
            "learning_rate": lr,
            "old_weights": old_weights,
            "new_weights": new_w,
            "old_thresholds": old_thresholds,
            "new_thresholds": new_th,
            "changes": changes,
            "meta_calibration": meta,
            # 시그널별 Sharpe ratio 스냅샷
            "signal_sharpe": {
                sig: stats.get("sharpe_ratio", 0)
                for sig, stats in sig_perf.items()
            },
        }

        cal["weights"] = new_w
        cal["thresholds"] = new_th
        cal["adjustments"].append(adjustment_record)
        cal["version"] = version + 1
        cal["last_updated"] = datetime.now().isoformat()

        # 최근 30건만 보관
        cal["adjustments"] = cal["adjustments"][-30:]

        self._save_calibration(cal)
        logger.info(f"캘리브레이션 v{cal['version']} (lr={lr}): {', '.join(changes)}")

        return {
            "adjusted": True,
            "version": cal["version"],
            "learning_rate": lr,
            "accuracy_pct": acc_pct,
            "weighted_accuracy_pct": overall.get("weighted_accuracy_pct", acc_pct),
            "brier_score": accuracy_data.get("brier_score"),
            "changes": changes,
            "weights": new_w,
            "thresholds": new_th,
            "weight_diff": {k: round(new_w[k] - old_weights.get(k, 0), 3) for k in new_w},
            "threshold_diff": {k: new_th[k] - old_thresholds.get(k, 0) for k in new_th},
            "meta_calibration": meta,
        }

    def _compute_adjustments(self, cal: dict, accuracy_data: dict,
                              lr: float, changes: list) -> tuple[dict, dict, list]:
        """가중치·임계값 보정값 계산"""
        sig_perf = accuracy_data.get("signal_performance", {})
        score_corr = accuracy_data.get("weighted_score_correlation") or accuracy_data.get("score_correlation")
        brier = accuracy_data.get("weighted_brier_score") or accuracy_data.get("brier_score")
        acc_pct = accuracy_data.get("overall", {}).get("weighted_accuracy_pct") or \
                  accuracy_data.get("overall", {}).get("accuracy_pct", 50)

        buy_avg = _weighted_avg(sig_perf, ["strong_buy", "buy"])
        sell_avg = _weighted_avg(sig_perf, ["strong_sell", "sell"])
        separation = (buy_avg or 0) - (sell_avg or 0)

        # === 1. Sharpe ratio 기반 가중치 보정 ===
        new_w = dict(cal["weights"])

        # 시그널별 Sharpe ratio 수집 — 양수 Sharpe인 시그널이 많으면 모델이 잘 작동
        buy_sharpe = sig_perf.get("buy", {}).get("sharpe_ratio", 0)
        strong_buy_sharpe = sig_perf.get("strong_buy", {}).get("sharpe_ratio", 0)
        avg_buy_sharpe = (buy_sharpe + strong_buy_sharpe) / 2

        if acc_pct < 40:
            # 심각한 부정확 → 균등화 (학습률 적용)
            target = {k: 0.25 for k in DEFAULT_WEIGHTS}
            new_w = _blend_weights(new_w, target, lr)
            changes.append(f"적중률 {acc_pct:.1f}% < 40% → 가중치 균등화 (lr={lr})")

        elif brier is not None and brier > 0.30:
            # Brier 스코어 나쁨 → 확률 예측이 부정확 → poll_impact 하향
            delta = min(0.05, 0.05 * lr)
            new_w["poll_impact"] = max(new_w["poll_impact"] - delta, WEIGHT_MIN)
            new_w["price_momentum"] = min(new_w["price_momentum"] + delta / 2, WEIGHT_MAX)
            new_w["volume_signal"] = min(new_w["volume_signal"] + delta / 2, WEIGHT_MAX)
            changes.append(f"Brier {brier:.3f} > 0.30 → 여론 가중치 하향 (lr={lr})")

        elif separation < -0.5:
            # buy가 sell보다 수익 낮음 → 시그널 역전
            delta = 0.03 * lr
            new_w["price_momentum"] = min(new_w["price_momentum"] + delta, WEIGHT_MAX)
            new_w["volume_signal"] = min(new_w["volume_signal"] + delta, WEIGHT_MAX)
            new_w["poll_impact"] = max(new_w["poll_impact"] - delta, WEIGHT_MIN)
            new_w["cycle_premium"] = max(new_w["cycle_premium"] - delta, WEIGHT_MIN)
            changes.append(f"시그널 역전 (buy {buy_avg:.1f}% < sell {sell_avg:.1f}%) → 기술적 지표 상향 (lr={lr})")

        elif avg_buy_sharpe > 0.5 and acc_pct >= 55:
            # 좋은 성과 → 현재 방향 강화 (미세 조정만)
            changes.append(f"Sharpe buy={avg_buy_sharpe:.2f}, 적중률 {acc_pct:.1f}% → 현재 가중치 유지")

        else:
            # 스코어-수익률 상관관계 기반 미세 조정
            bins = accuracy_data.get("score_bins", {})
            high_wr = bins.get("60-75", {}).get("win_rate_pct", 50)
            top_wr = bins.get("75-100", {}).get("win_rate_pct", 50)
            avg_high_wr = (high_wr + top_wr) / 2

            if avg_high_wr < 45 and score_corr is not None and score_corr < 0:
                delta = 0.04 * lr
                new_w["poll_impact"] = max(new_w["poll_impact"] - delta, WEIGHT_MIN)
                new_w["price_momentum"] = min(new_w["price_momentum"] + delta, WEIGHT_MAX)
                changes.append(
                    f"스코어-수익률 음의 상관 ({score_corr:.3f}) → 여론 하향, 모멘텀 상향 (lr={lr})"
                )

            # 캘리브레이션 커브 기반 보정: 과신(overconfident) 검출
            cal_curve = accuracy_data.get("calibration_curve", [])
            if len(cal_curve) >= 5:
                # 상위 구간에서 예측 > 실제이면 과신
                top_bins = cal_curve[-3:]
                avg_gap = sum(b["gap"] for b in top_bins) / len(top_bins)
                if avg_gap > 0.1:
                    # 과신 → 사이클 프리미엄 줄이기
                    delta = min(0.03 * lr, new_w["cycle_premium"] - WEIGHT_MIN)
                    if delta > 0.005:
                        new_w["cycle_premium"] = max(new_w["cycle_premium"] - delta, WEIGHT_MIN)
                        new_w["price_momentum"] = min(new_w["price_momentum"] + delta, WEIGHT_MAX)
                        changes.append(f"과신 감지 (gap={avg_gap:.2f}) → 사이클 프리미엄 하향 (lr={lr})")

        # === 2. 시그널 임계값 보정 ===
        new_th = dict(cal["thresholds"])

        # buy 시그널 Sharpe & 승률 기반 조정
        buy_wr = sig_perf.get("buy", {}).get("win_rate_pct", 50)
        buy_count = sig_perf.get("buy", {}).get("count", 0)

        if buy_wr < 40 and buy_count >= 3:
            shift = min(int(3 * lr), THRESHOLD_SHIFT_MAX - abs(new_th["buy"] - DEFAULT_THRESHOLDS["buy"]))
            if shift > 0:
                new_th["buy"] = min(new_th["buy"] + shift, DEFAULT_THRESHOLDS["buy"] + THRESHOLD_SHIFT_MAX)
                changes.append(f"매수 승률 {buy_wr:.0f}% < 40% → 매수 임계값 +{shift} ({new_th['buy']})")
        elif buy_wr > 65 and buy_sharpe > 0.3 and buy_count >= 5:
            # 매수 시그널이 매우 좋으면 → 임계값 낮춰서 더 많은 기회 포착
            shift = min(int(2 * lr), abs(new_th["buy"] - DEFAULT_THRESHOLDS["buy"]))
            if shift > 0 and new_th["buy"] > DEFAULT_THRESHOLDS["buy"] - THRESHOLD_SHIFT_MAX:
                new_th["buy"] = max(new_th["buy"] - shift, DEFAULT_THRESHOLDS["buy"] - THRESHOLD_SHIFT_MAX)
                changes.append(f"매수 승률 {buy_wr:.0f}% + Sharpe {buy_sharpe:.2f} → 매수 임계값 -{shift} ({new_th['buy']})")

        # sell 시그널 보정
        sell_avg_ret = sig_perf.get("sell", {}).get("weighted_avg_return_pct") or \
                       sig_perf.get("sell", {}).get("avg_return_pct", 0)
        sell_count = sig_perf.get("sell", {}).get("count", 0)
        if sell_avg_ret > 1 and sell_count >= 3:
            shift = min(int(3 * lr), THRESHOLD_SHIFT_MAX - abs(new_th["sell"] - DEFAULT_THRESHOLDS["sell"]))
            if shift > 0:
                new_th["sell"] = max(new_th["sell"] - shift, DEFAULT_THRESHOLDS["sell"] - THRESHOLD_SHIFT_MAX)
                changes.append(f"매도 시그널 평균수익 +{sell_avg_ret:.1f}% → 매도 임계값 -{shift} ({new_th['sell']})")

        # hold 구간 조정
        hold_wr = sig_perf.get("hold", {}).get("win_rate_pct", 50)
        hold_count = sig_perf.get("hold", {}).get("count", 0)
        if hold_wr < 35 and hold_count >= 5:
            new_th["hold"] = min(new_th["hold"] + int(2 * lr), new_th["buy"] - 5)
            changes.append(f"관망 승률 {hold_wr:.0f}% < 35% → 관망 구간 축소")

        return new_w, new_th, changes


def _blend_weights(current: dict, target: dict, lr: float) -> dict:
    """현재 가중치를 목표 가중치로 학습률만큼 이동"""
    return {
        k: current.get(k, 0.25) + (target.get(k, 0.25) - current.get(k, 0.25)) * lr
        for k in current
    }


def _weighted_avg(sig_perf: dict, signals: list) -> float | None:
    """시그널 그룹의 가중평균 수익률 (시간 가중 우선)"""
    total_w = 0
    total_v = 0
    for sig in signals:
        s = sig_perf.get(sig)
        if s and s.get("count", 0) > 0:
            avg = s.get("weighted_avg_return_pct") or s.get("avg_return_pct", 0)
            total_w += s["count"]
            total_v += avg * s["count"]
    return total_v / total_w if total_w > 0 else None
