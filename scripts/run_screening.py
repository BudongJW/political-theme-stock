"""
GitHub Actions용 스크리닝 스크립트
결과를 docs/data/latest.json + docs/data/YYYY-MM-DD.json으로 저장
(GitHub Pages 대시보드가 latest.json을 읽음)
"""
import sys, json, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from collectors.stock_collector import StockCollector
from analyzers.theme_mapper import ThemeMapper
from collectors.poll_collector import PollCollector


class SafeEncoder(json.JSONEncoder):
    def default(self, o):
        if hasattr(o, "item"):
            return o.item()
        return str(o)


def main():
    sc = StockCollector()
    tm = ThemeMapper(ROOT / "config" / "politician_stock_map.yaml")
    pc = PollCollector()

    tickers = tm.get_all_tickers()
    results = sc.screen_theme_stocks(tickers, surge_ratio=2.0)
    for r in results:
        r["surge"] = bool(r.get("surge", False))

    phase = pc.get_election_phase()
    candidates = pc.get_tracking_candidates()
    today = datetime.date.today().isoformat()

    # 후보별 관련주 매핑
    local_cands = tm.data.get("local_candidates_2026", [])
    candidate_stocks = {}
    for cand in local_cands:
        name = cand.get("name", "")
        stocks = cand.get("related_stocks", [])
        if stocks:
            ticker_list = [s["ticker"] for s in stocks]
            matched = [r for r in results if r["ticker"] in ticker_list]
            candidate_stocks[name] = {
                "party": cand.get("party", ""),
                "region": cand.get("region", ""),
                "stocks": stocks,
                "screening": matched,
            }
    for pol_name in tm.get_all_politicians():
        pol_stocks = tm.get_tickers_for_politician(pol_name)
        if pol_stocks:
            ticker_list = [s["ticker"] for s in pol_stocks]
            matched = [r for r in results if r["ticker"] in ticker_list]
            candidate_stocks[pol_name] = {
                "party": "",
                "region": "전국(대선)",
                "stocks": pol_stocks,
                "screening": matched,
            }

    output = {
        "date": today,
        "election_phase": phase,
        "total_tracked": len(tickers),
        "candidates": candidates,
        "candidate_stocks": candidate_stocks,
        "screening_results": sorted(
            results, key=lambda x: x.get("change_pct", 0), reverse=True
        ),
        "summary": {
            "up": sum(1 for r in results if r.get("change_pct", 0) > 0),
            "down": sum(1 for r in results if r.get("change_pct", 0) < 0),
            "surge_count": sum(1 for r in results if r.get("surge")),
            "top_gainer": max(
                results, key=lambda x: x.get("change_pct", 0), default={}
            ).get("name", "-"),
            "top_loser": min(
                results, key=lambda x: x.get("change_pct", 0), default={}
            ).get("name", "-"),
        },
    }

    # docs/data/에 저장 (GitHub Pages용)
    docs_dir = ROOT / "docs" / "data"
    docs_dir.mkdir(parents=True, exist_ok=True)

    for fname in [f"{today}.json", "latest.json"]:
        with open(docs_dir / fname, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2, cls=SafeEncoder)

    # data/processed/에도 백업
    bak_dir = ROOT / "data" / "processed"
    bak_dir.mkdir(parents=True, exist_ok=True)
    with open(bak_dir / f"{today}.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, cls=SafeEncoder)

    print(f"저장 완료: docs/data/latest.json, docs/data/{today}.json")
    print(f"D-{phase.get('days_until_election','?')} | {phase.get('phase','')}")
    print(
        f"상승 {output['summary']['up']}개 / 하락 {output['summary']['down']}개 / 급등 {output['summary']['surge_count']}개"
    )
    print(
        f"최고 상승: {output['summary']['top_gainer']} | 최고 하락: {output['summary']['top_loser']}"
    )


if __name__ == "__main__":
    main()
