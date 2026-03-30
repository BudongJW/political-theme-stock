"""
예측 적중률 분석 모듈
- 과거 일별 스냅샷의 예측(시그널·스코어)과 실제 주가 변동 비교
- 적중률, 방향성 정확도, 시그널별 수익률 분석
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


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
        """전체 예측 적중률 분석"""
        snapshots = self._load_snapshots()
        if len(snapshots) < 2:
            logger.info(f"스냅샷 {len(snapshots)}개 — 분석에 최소 2개 필요")
            return {"status": "insufficient_data", "snapshot_count": len(snapshots)}

        # 최신 스냅샷은 비교 대상에서 제외 (미래 데이터 없음)
        past_snapshots = snapshots[:-1][-max_snapshots:]
        latest = snapshots[-1]

        stock_results = []
        signal_performance = {"strong_buy": [], "buy": [], "hold": [], "sell": [], "strong_sell": []}
        score_vs_actual = []
        daily_accuracy = []

        for snap in past_snapshots:
            snap_date = snap["_file_date"]
            predictions = snap.get("stock_predictions", {})
            analyses = predictions.get("analyses", [])
            if not analyses:
                continue

            # 이 날의 예측 종목 티커 수집
            pred_tickers = [a["ticker"] for a in analyses if a.get("ticker")]
            if not pred_tickers:
                continue

            # 실제 다음날~5일 후 주가 조회
            actual = self._get_actual_prices(pred_tickers, snap_date, forward_days=5)
            if not actual:
                continue

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

                # 시그널별 수익률
                signal_performance[signal].append(next_chg)

                # 스코어 vs 실제 변동
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
                })

            if day_total > 0:
                daily_accuracy.append({
                    "date": snap_date,
                    "correct": day_correct,
                    "total": day_total,
                    "accuracy_pct": round(day_correct / day_total * 100, 1),
                })

        # 종합 통계 계산
        total_predictions = len(stock_results)
        total_correct = sum(1 for r in stock_results if r["correct"])

        # 시그널별 평균 수익률
        signal_stats = {}
        for sig, returns in signal_performance.items():
            if returns:
                signal_stats[sig] = {
                    "count": len(returns),
                    "avg_return_pct": round(float(np.nanmean(returns)), 2),
                    "median_return_pct": round(float(np.nanmedian(returns)), 2),
                    "win_rate_pct": round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1),
                    "max_return_pct": round(float(max(returns)), 2),
                    "min_return_pct": round(float(min(returns)), 2),
                }

        # 스코어 구간별 적중률
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

        # 스코어-수익률 상관계수
        correlation = None
        if len(score_vs_actual) >= 5:
            scores = [r["score"] for r in score_vs_actual]
            actuals = [r["next_change_pct"] for r in score_vs_actual]
            try:
                corr = float(np.corrcoef(scores, actuals)[0, 1])
                if not np.isnan(corr):
                    correlation = round(corr, 3)
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
            },
            "daily_accuracy": daily_accuracy,
            "signal_performance": signal_stats,
            "score_bins": score_bin_stats,
            "score_correlation": correlation,
            "recent_cases": recent_cases,
            "generated_at": datetime.now().isoformat(),
        }
