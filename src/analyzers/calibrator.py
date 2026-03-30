"""
예측 모델 자동 캘리브레이션
- 적중률 분석 결과를 기반으로 가중치·임계값 보정
- 보정 내역을 기록하여 대시보드에서 확인 가능
"""
import json
import logging
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

    def calibrate(self, accuracy_data: dict) -> dict:
        """
        적중률 데이터를 기반으로 가중치·임계값 보정.
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

        # === 1. 컴포넌트별 가중치 보정 ===
        # 각 스코어 구간의 승률을 분석하여, 실제 수익률과 상관이 높은 컴포넌트에 가중치 부여
        sig_perf = accuracy_data.get("signal_performance", {})
        score_corr = accuracy_data.get("score_correlation")

        # 시그널 분리도 계산: buy계열 평균수익 - sell계열 평균수익
        buy_avg = _weighted_avg(sig_perf, ["strong_buy", "buy"])
        sell_avg = _weighted_avg(sig_perf, ["strong_sell", "sell"])
        separation = (buy_avg or 0) - (sell_avg or 0)

        # 전체 적중률이 50% 미만이면 → 모델이 역방향 → 보수적으로 가중치 재조정
        acc_pct = overall.get("accuracy_pct", 50)

        if acc_pct < 45:
            # 적중률이 낮으면 → 모든 컴포넌트를 균등화 (0.25씩)
            new_w = {k: 0.25 for k in DEFAULT_WEIGHTS}
            changes.append(f"적중률 {acc_pct}% < 45% → 가중치 균등화 (각 25%)")
        elif separation < 0:
            # buy가 sell보다 수익 낮으면 → 시그널이 역전 → poll/cycle 비중 줄이고 price/volume 올림
            new_w = dict(cal["weights"])
            new_w["price_momentum"] = min(new_w["price_momentum"] + 0.03, WEIGHT_MAX)
            new_w["volume_signal"] = min(new_w["volume_signal"] + 0.03, WEIGHT_MAX)
            new_w["poll_impact"] = max(new_w["poll_impact"] - 0.03, WEIGHT_MIN)
            new_w["cycle_premium"] = max(new_w["cycle_premium"] - 0.03, WEIGHT_MIN)
            changes.append(f"시그널 역전 (buy {buy_avg:.1f}% < sell {sell_avg:.1f}%) → 기술적 지표 가중치 상향")
        else:
            # 개별 컴포넌트 성과 기반 미세 조정
            new_w = dict(cal["weights"])
            bins = accuracy_data.get("score_bins", {})

            # 고스코어(60+) 승률이 높으면 → 현재 가중치 유지·강화
            high_wr = bins.get("60-75", {}).get("win_rate_pct", 50)
            top_wr = bins.get("75-100", {}).get("win_rate_pct", 50)
            avg_high_wr = (high_wr + top_wr) / 2

            if avg_high_wr >= 60:
                changes.append(f"고스코어 승률 {avg_high_wr:.0f}% → 현재 가중치 유지")
            elif avg_high_wr < 45:
                # 고스코어인데 승률 낮으면 → 스코어가 잘못됨 → 상관계수 기반 보정
                if score_corr is not None and score_corr < 0:
                    # 음의 상관 → 여론조사/사이클이 잘못된 시그널
                    new_w["poll_impact"] = max(new_w["poll_impact"] - 0.05, WEIGHT_MIN)
                    new_w["price_momentum"] = min(new_w["price_momentum"] + 0.05, WEIGHT_MAX)
                    changes.append(f"스코어-수익률 음의 상관 ({score_corr}) → 여론 가중치 하향, 모멘텀 상향")

        # 가중치 합 정규화 (1.0)
        w_sum = sum(new_w.values())
        new_w = {k: round(v / w_sum, 3) for k, v in new_w.items()}

        # === 2. 시그널 임계값 보정 ===
        new_th = dict(cal["thresholds"])

        # buy 시그널 승률이 낮으면 → 임계값 올려서 더 보수적으로
        buy_wr = sig_perf.get("buy", {}).get("win_rate_pct", 50)
        strong_buy_wr = sig_perf.get("strong_buy", {}).get("win_rate_pct", 50)

        if buy_wr < 40 and sig_perf.get("buy", {}).get("count", 0) >= 3:
            shift = min(3, THRESHOLD_SHIFT_MAX - abs(new_th["buy"] - DEFAULT_THRESHOLDS["buy"]))
            if shift > 0:
                new_th["buy"] = min(new_th["buy"] + shift, DEFAULT_THRESHOLDS["buy"] + THRESHOLD_SHIFT_MAX)
                changes.append(f"매수 승률 {buy_wr}% < 40% → 매수 임계값 +{shift} ({new_th['buy']})")

        # sell 시그널이 오히려 수익이면 → 임계값 내려서 매도 기준 완화
        sell_avg_ret = sig_perf.get("sell", {}).get("avg_return_pct", 0)
        if sell_avg_ret > 1 and sig_perf.get("sell", {}).get("count", 0) >= 3:
            shift = min(3, THRESHOLD_SHIFT_MAX - abs(new_th["sell"] - DEFAULT_THRESHOLDS["sell"]))
            if shift > 0:
                new_th["sell"] = max(new_th["sell"] - shift, DEFAULT_THRESHOLDS["sell"] - THRESHOLD_SHIFT_MAX)
                changes.append(f"매도 시그널 평균수익 +{sell_avg_ret}% → 매도 임계값 -{shift} ({new_th['sell']})")

        # hold 범위가 너무 넓으면 조정
        hold_wr = sig_perf.get("hold", {}).get("win_rate_pct", 50)
        if hold_wr < 35 and sig_perf.get("hold", {}).get("count", 0) >= 5:
            new_th["hold"] = min(new_th["hold"] + 2, new_th["buy"] - 5)
            changes.append(f"관망 승률 {hold_wr}% < 35% → 관망 구간 축소")

        # === 변경 사항 없으면 스킵 ===
        weight_changed = new_w != old_weights
        threshold_changed = new_th != old_thresholds

        if not weight_changed and not threshold_changed:
            return {
                "adjusted": False,
                "reason": "현재 설정이 적절 (변경 불필요)",
                "current_weights": old_weights,
                "current_thresholds": old_thresholds,
                "accuracy_pct": acc_pct,
            }

        # === 저장 ===
        adjustment_record = {
            "date": datetime.now().isoformat(),
            "accuracy_pct": acc_pct,
            "total_predictions": total,
            "score_correlation": score_corr,
            "signal_separation": round(separation, 2) if separation else None,
            "old_weights": old_weights,
            "new_weights": new_w,
            "old_thresholds": old_thresholds,
            "new_thresholds": new_th,
            "changes": changes,
        }

        cal["weights"] = new_w
        cal["thresholds"] = new_th
        cal["adjustments"].append(adjustment_record)
        cal["version"] = cal.get("version", 0) + 1
        cal["last_updated"] = datetime.now().isoformat()

        # 최근 30건만 보관
        cal["adjustments"] = cal["adjustments"][-30:]

        self._save_calibration(cal)
        logger.info(f"캘리브레이션 v{cal['version']}: {', '.join(changes)}")

        return {
            "adjusted": True,
            "version": cal["version"],
            "accuracy_pct": acc_pct,
            "changes": changes,
            "weights": new_w,
            "thresholds": new_th,
            "weight_diff": {k: round(new_w[k] - old_weights.get(k, 0), 3) for k in new_w},
            "threshold_diff": {k: new_th[k] - old_thresholds.get(k, 0) for k in new_th},
        }


def _weighted_avg(sig_perf: dict, signals: list) -> float | None:
    """시그널 그룹의 가중평균 수익률"""
    total_w = 0
    total_v = 0
    for sig in signals:
        s = sig_perf.get(sig)
        if s and s.get("count", 0) > 0:
            total_w += s["count"]
            total_v += s["avg_return_pct"] * s["count"]
    return total_v / total_w if total_w > 0 else None
