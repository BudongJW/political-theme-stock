"""
GitHub Actions용 스크리닝 스크립트
결과를 data/processed/YYYY-MM-DD.json으로 저장
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
    today = datetime.date.today().isoformat()

    output = {
        "date": today,
        "election_phase": phase,
        "total_tracked": len(tickers),
        "screening_results": results,
        "summary": {
            "up": sum(1 for r in results if r.get("change_pct", 0) > 0),
            "down": sum(1 for r in results if r.get("change_pct", 0) < 0),
            "surge_count": sum(1 for r in results if r.get("surge")),
            "top_gainer": max(results, key=lambda x: x.get("change_pct", 0), default={}).get("name", "-"),
            "top_loser": min(results, key=lambda x: x.get("change_pct", 0), default={}).get("name", "-"),
        },
    }

    out_dir = ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{today}.json"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, cls=SafeEncoder)

    print(f"저장 완료: {out_file}")
    print(f"D-{phase.get('days_until_election','?')} | {phase.get('phase','')}")
    print(f"상승 {output['summary']['up']}개 / 하락 {output['summary']['down']}개 / 급등 {output['summary']['surge_count']}개")
    print(f"최고 상승: {output['summary']['top_gainer']} | 최고 하락: {output['summary']['top_loser']}")


if __name__ == "__main__":
    main()
