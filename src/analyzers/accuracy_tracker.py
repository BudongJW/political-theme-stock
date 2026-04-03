"""
예측 적중률 분석 모듈 (v2)
- 과거 일별 스냅샷의 예측(시그널·스코어)과 실제 주가 변동 비교
- 적중률, 방향성 정확도, 시그널별 수익률 분석
- Brier 스코어 (확률 예측 정확도)
- 시간 가중치 (최근 예측에 가중치 부여)
- 시그널별 Sharpe ratio
- 캘리브레이션 커브 (예측 확률 vs 실제 확률)
"""
import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 시간 가중치 반감기 (일)
HALF_LIFE_DAYS = 7


def _time_weight(days_ago: int, half_life: int = HALF_LIFE_DAYS) -> float:
    """최근 데이터에 높은 가중치를 부여하는 지수 감쇠 함수"""
    return math.exp(-0.693 * days_ago / half_life)


class AccuracyTracker:
    def __init__(self, stock_collector, processed_dir: str, docs_data_dir: str = None):
        self.sc = stock_collector
        self.processed_dir = Path(processed_dir)
        self.docs_data_dir = Path(docs_data_dir) if docs_data_dir else None

    def _load_snapshots(self) -> list[dict]:
        """날짜순 정렬된 일별 스냅샷 로드"""
        snapshots = []
        dirs = [self.processed_dir]
        if self.docs_data_dir:
            dirs.append(self.docs_data_dir)

        seen_dates = set()
        for d in dirs:
            if not d.exists():
                continue
            for f in d.glob("202?-??-??.json"):
                date_str = f.stem
                if date_str in seen_dates:
                    continue
                try:
                    with open(f, encoding="utf-8") as fh:
                        data = json.load(fh)
                    data["_file_date"] = date_str
                    snapshots.append(data)
                    seen_dates.add(date_str)
                except Exception as e:
                    logger.warning(f"스냅샷 로드 실패 ({f.name}): {e}")

        snapshots.sort(key=lambda x: x["_file_date"])
        return snapshots

    def _get_actual_prices(self, tickers: list[str], base_date: str,
                           forward_days: int = 5) -> dict:
        """base_date 이후 forward_days 동안의 실제 종가 조회"""
        results = {}
        base_dt = datetime.strptime(base_date, "%Y-%m-%d")
        end_dt = base_dt + timedelta(days=forward_days + 5)
        start_str = base_dt.strftime("%Y%m%d")
        end_str = end_dt.strftime("%Y%m%d")

        for ticker in tickers:
            try:
                from pykrx import stock
                df = stock.get_market_ohlcv(start_str, end_str, ticker)
                if df.empty:
                    continue
                # base_date 이후의 데이터만
                df.index = pd.to_datetime(df.index)
                future = df[df.index > pd.Timestamp(base_date)]
                if future.empty:
                    continue
                results[ticker] = {
                    "next_close": int(future["종가"].iloc[0]),
                    "next_change_pct": round(
                        (future["종가"].iloc[0] - df["종가"].iloc[-len(future) - 1])
                        / df["종가"].iloc[-len(future) - 1] * 100, 2
                    ) if len(df) > len(future) else 0,
                    "week_close": int(future["종가"].iloc[-1]) if len(future) >= 3 else None,
                    "week_change_pct": round(
                        (future["종가"].iloc[-1] - df["종가"].iloc[-len(future) - 1])
                        / df["종가"].iloc[-len(future) - 1] * 100, 2
                    ) if len(future) >= 3 and len(df) > len(future) else None,
                }
            except Exception as e:
                logger.debug(f"실제 주가 조회 실패 ({ticker}): {e}")
        return results

    def analyze_accuracy(self, max_snapshots: int = 30) -> dict:
        """전체 예측 적중률 분석 (v2: Brier 스코어, 시간 가중치, Sharpe ratio)"""
        snapshots = self._load_snapshots()
        if len(snapshots) < 2:
            logger.info(f"스냅샷 {len(snapshots)}개 — 분석에 최소 2개 필요")
            return {"status": "insufficient_data", "snapshot_count": len(snapshots)}

        # 최신 스냅샷은 비교 대상에서 제외 (미래 데이터 없음)
        past_snapshots = snapshots[:-1][-max_snapshots:]

        today_str = datetime.now().strftime("%Y-%m-%d")
        stock_results = []
        signal_performance = {"strong_buy": [], "buy": [], "hold": [], "sell": [], "strong_sell": []}
        score_vs_actual = []
        daily_accuracy = []
        brier_samples = []  # (predicted_prob, actual_outcome) for Brier score

        for snap in past_snapshots:
            snap_date = snap["_file_date"]
            predictions = snap.get("stock_predictions", {})
            analyses = predictions.get("analyses", [])
            if not analyses:
                continue

            pred_tickers = [a["ticker"] for a in analyses if a.get("ticker")]
            if not pred_tickers:
                continue

            actual = self._get_actual_prices(pred_tickers, snap_date, forward_days=5)
            if not actual:
                continue

            # 시간 가중치: 오래된 스냅샷일수록 낮은 가중치
            days_ago = (datetime.strptime(today_str, "%Y-%m-%d") -
                        datetime.strptime(snap_date, "%Y-%m-%d")).days
            tw = _time_weight(days_ago)

            day_correct = 0
            day_total = 0

            for a in analyses:
                ticker = a.get("ticker")
                if ticker not in actual:
                    continue

                cs = a.get("composite_score", {})
                signal = cs.get("signal", "hold")
                score = cs.get("total", 50)
                predicted_name = a.get("name", ticker)

                act = actual[ticker]
                next_chg = act.get("next_change_pct", 0)
                week_chg = act.get("week_change_pct")

                # 방향 예측 정확도
                predicted_up = signal in ("strong_buy", "buy")
                predicted_down = signal in ("strong_sell", "sell")
                actual_up = next_chg > 0
                actual_down = next_chg < 0

                direction_correct = False
                if predicted_up and actual_up:
                    direction_correct = True
                elif predicted_down and actual_down:
                    direction_correct = True
                elif signal == "hold" and abs(next_chg) < 2:
                    direction_correct = True

                if direction_correct:
                    day_correct += 1
                day_total += 1

                # Brier score 샘플: 스코어를 상승 확률로 변환
                predicted_up_prob = score / 100.0
                actual_up_outcome = 1.0 if next_chg > 0 else 0.0
                brier_samples.append({
                    "prob": predicted_up_prob,
                    "outcome": actual_up_outcome,
                    "weight": tw,
                })

                # 시그널별 수익률 (가중치 포함)
                signal_performance[signal].append({
                    "return_pct": next_chg,
                    "weight": tw,
                    "date": snap_date,
                })

                score_vs_actual.append({
                    "date": snap_date,
                    "ticker": ticker,
                    "name": predicted_name,
                    "score": score,
                    "signal": signal,
                    "signal_kr": cs.get("signal_kr", ""),
                    "predicted_close": a.get("price", {}).get("current", 0),
                    "next_change_pct": next_chg,
                    "week_change_pct": week_chg,
                    "direction_correct": direction_correct,
                    "time_weight": round(tw, 3),
                })

                stock_results.append({
                    "date": snap_date,
                    "ticker": ticker,
                    "name": predicted_name,
                    "signal": signal,
                    "score": score,
                    "next_change_pct": next_chg,
                    "week_change_pct": week_chg,
                    "correct": direction_correct,
                    "time_weight": round(tw, 3),
                })

            if day_total > 0:
                daily_accuracy.append({
                    "date": snap_date,
                    "correct": day_correct,
                    "total": day_total,
                    "accuracy_pct": round(day_correct / day_total * 100, 1),
                    "time_weight": round(tw, 3),
                })

        # === 종합 통계 계산 ===
        total_predictions = len(stock_results)
        total_correct = sum(1 for r in stock_results if r["correct"])

        # 시간 가중 적중률
        weighted_correct = sum(r["time_weight"] for r in stock_results if r["correct"])
        weighted_total = sum(r["time_weight"] for r in stock_results)
        weighted_accuracy = round(weighted_correct / weighted_total * 100, 1) if weighted_total else 0

        # === Brier 스코어 (0=완벽, 1=최악, 0.25=랜덤) ===
        brier_score = None
        weighted_brier = None
        if brier_samples:
            bs_vals = [(s["prob"] - s["outcome"]) ** 2 for s in brier_samples]
            brier_score = round(float(np.nanmean(bs_vals)), 4)
            # 가중 Brier
            w_sum = sum(s["weight"] for s in brier_samples)
            if w_sum > 0:
                weighted_brier = round(
                    sum((s["prob"] - s["outcome"]) ** 2 * s["weight"] for s in brier_samples) / w_sum, 4
                )

        # === 시그널별 통계 (Sharpe ratio 포함) ===
        signal_stats = {}
        for sig, entries in signal_performance.items():
            if not entries:
                continue
            returns = [e["return_pct"] for e in entries]
            weights = [e["weight"] for e in entries]
            w_sum = sum(weights)

            # 가중 평균 수익률
            w_avg = sum(r * w for r, w in zip(returns, weights)) / w_sum if w_sum else 0
            # 가중 표준편차
            w_var = sum(w * (r - w_avg) ** 2 for r, w in zip(returns, weights)) / w_sum if w_sum else 0
            w_std = math.sqrt(w_var) if w_var > 0 else 0

            # Sharpe ratio (무위험수익률 0 가정, 일간 기준)
            sharpe = round(w_avg / w_std, 2) if w_std > 0.01 else 0

            signal_stats[sig] = {
                "count": len(returns),
                "avg_return_pct": round(float(np.nanmean(returns)), 2),
                "weighted_avg_return_pct": round(w_avg, 2),
                "median_return_pct": round(float(np.nanmedian(returns)), 2),
                "std_return_pct": round(float(np.nanstd(returns)), 2),
                "sharpe_ratio": sharpe,
                "win_rate_pct": round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1),
                "max_return_pct": round(float(max(returns)), 2),
                "min_return_pct": round(float(min(returns)), 2),
                "profit_factor": round(
                    sum(r for r in returns if r > 0) / abs(sum(r for r in returns if r < 0)), 2
                ) if any(r < 0 for r in returns) and any(r > 0 for r in returns) else None,
            }

        # === 스코어 구간별 적중률 ===
        score_bins = {"0-25": [], "25-40": [], "40-60": [], "60-75": [], "75-100": []}
        for r in stock_results:
            s = r["score"]
            if s < 25:
                score_bins["0-25"].append(r)
            elif s < 40:
                score_bins["25-40"].append(r)
            elif s < 60:
                score_bins["40-60"].append(r)
            elif s < 75:
                score_bins["60-75"].append(r)
            else:
                score_bins["75-100"].append(r)

        score_bin_stats = {}
        for bin_label, items in score_bins.items():
            if items:
                returns = [r["next_change_pct"] for r in items]
                score_bin_stats[bin_label] = {
                    "count": len(items),
                    "avg_return_pct": round(float(np.nanmean(returns)), 2),
                    "win_rate_pct": round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1),
                }

        # === 캘리브레이션 커브 (10분위) ===
        calibration_curve = self._build_calibration_curve(brier_samples)

        # === 스코어-수익률 상관계수 (일반 + 시간 가중) ===
        correlation = None
        weighted_correlation = None
        if len(score_vs_actual) >= 5:
            scores = np.array([r["score"] for r in score_vs_actual])
            actuals = np.array([r["next_change_pct"] for r in score_vs_actual])
            tw_arr = np.array([r["time_weight"] for r in score_vs_actual])
            try:
                corr = float(np.corrcoef(scores, actuals)[0, 1])
                if not np.isnan(corr):
                    correlation = round(corr, 3)
                # 가중 상관계수
                wcorr = self._weighted_corr(scores, actuals, tw_arr)
                if wcorr is not None:
                    weighted_correlation = round(wcorr, 3)
            except Exception:
                pass

        # 최근 적중/미적중 사례 (최대 10개)
        recent_cases = sorted(score_vs_actual, key=lambda x: x["date"], reverse=True)[:10]

        return {
            "status": "ok",
            "snapshot_count": len(past_snapshots),
            "analysis_period": {
                "from": past_snapshots[0]["_file_date"] if past_snapshots else None,
                "to": past_snapshots[-1]["_file_date"] if past_snapshots else None,
            },
            "overall": {
                "total_predictions": total_predictions,
                "correct": total_correct,
                "accuracy_pct": round(total_correct / total_predictions * 100, 1) if total_predictions else 0,
                "weighted_accuracy_pct": weighted_accuracy,
            },
            "brier_score": brier_score,
            "weighted_brier_score": weighted_brier,
            "calibration_curve": calibration_curve,
            "daily_accuracy": daily_accuracy,
            "signal_performance": signal_stats,
            "score_bins": score_bin_stats,
            "score_correlation": correlation,
            "weighted_score_correlation": weighted_correlation,
            "recent_cases": recent_cases,
            "generated_at": datetime.now().isoformat(),
        }

    @staticmethod
    def _build_calibration_curve(brier_samples: list, n_bins: int = 10) -> list[dict]:
        """예측 확률 vs 실제 상승 비율 — 캘리브레이션 커브 데이터"""
        if len(brier_samples) < n_bins:
            return []
        sorted_s = sorted(brier_samples, key=lambda x: x["prob"])
        bin_size = len(sorted_s) // n_bins
        curve = []
        for i in range(n_bins):
            start = i * bin_size
            end = start + bin_size if i < n_bins - 1 else len(sorted_s)
            chunk = sorted_s[start:end]
            if not chunk:
                continue
            avg_pred = sum(s["prob"] for s in chunk) / len(chunk)
            avg_actual = sum(s["outcome"] for s in chunk) / len(chunk)
            curve.append({
                "bin": i + 1,
                "predicted_prob": round(avg_pred, 3),
                "actual_prob": round(avg_actual, 3),
                "count": len(chunk),
                "gap": round(avg_pred - avg_actual, 3),
            })
        return curve

    @staticmethod
    def _weighted_corr(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> float | None:
        """가중 피어슨 상관계수"""
        try:
            w_sum = w.sum()
            if w_sum == 0:
                return None
            mx = np.average(x, weights=w)
            my = np.average(y, weights=w)
            cov = np.sum(w * (x - mx) * (y - my)) / w_sum
            sx = math.sqrt(np.sum(w * (x - mx) ** 2) / w_sum)
            sy = math.sqrt(np.sum(w * (y - my) ** 2) / w_sum)
            if sx * sy == 0:
                return None
            return cov / (sx * sy)
        except Exception:
            return None
