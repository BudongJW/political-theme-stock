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
from collectors.asset_collector import AssetCollector
from analyzers.gemini_analyzer import GeminiAnalyzer
from analyzers.auto_mapper import AutoMapper


class SafeEncoder(json.JSONEncoder):
    def default(self, o):
        if hasattr(o, "item"):
            return o.item()
        return str(o)


def main():
    sc = StockCollector()
    tm = ThemeMapper(ROOT / "config" / "politician_stock_map.yaml")
    pc = PollCollector()
    ac = AssetCollector(data_dir=str(ROOT / "data" / "assets"))

    tickers = tm.get_all_tickers()
    results = sc.screen_theme_stocks(tickers, surge_ratio=2.0)
    for r in results:
        r["surge"] = bool(r.get("surge", False))

    phase = pc.get_election_phase()
    candidates = pc.get_tracking_candidates()
    today = datetime.date.today().isoformat()

    # 후보별 관련주 매핑 (프로필·재산·정당·지역 포함)
    # 뉴스타파 API로 실시간 재산 데이터 수집
    all_candidate_names = []
    local_cands = tm.data.get("local_candidates_2026", [])
    for cand in local_cands:
        if cand.get("name"):
            all_candidate_names.append(cand["name"])
    for pol in tm.data.get("politicians", []):
        if pol.get("name"):
            all_candidate_names.append(pol["name"])

    asset_data = ac.get_multiple(all_candidate_names)
    print(f"재산 데이터 수집: {sum(1 for v in asset_data.values() if v.get('source') != 'none')}/{len(all_candidate_names)}명 성공")

    candidate_stocks = {}
    for cand in local_cands:
        name = cand.get("name", "")
        stocks = cand.get("related_stocks", [])
        if stocks:
            ticker_list = [s["ticker"] for s in stocks]
            matched = [r for r in results if r["ticker"] in ticker_list]
            ad = asset_data.get(name, {})
            candidate_stocks[name] = {
                "party": cand.get("party", ""),
                "region": cand.get("region", ""),
                "role": cand.get("role", ""),
                "profile": cand.get("profile", ""),
                "assets": ad.get("total_display") or cand.get("assets", ""),
                "assets_detail": {
                    "total_억원": ad.get("total_억원", 0),
                    "source": ad.get("source", ""),
                    "detail_url": ad.get("detail_url", ""),
                    "position": ad.get("position", ""),
                },
                "election": cand.get("election", ""),
                "poll_status": cand.get("poll_status", ""),
                "stocks": stocks,
                "screening": matched,
            }
    for pol in tm.data.get("politicians", []):
        pol_name = pol["name"]
        pol_stocks = pol.get("related_stocks", [])
        if pol_stocks:
            ticker_list = [s["ticker"] for s in pol_stocks]
            matched = [r for r in results if r["ticker"] in ticker_list]
            ad = asset_data.get(pol_name, {})
            candidate_stocks[pol_name] = {
                "party": pol.get("party", ""),
                "region": "전국(대선)",
                "role": pol.get("role", ""),
                "profile": pol.get("profile", ""),
                "assets": ad.get("total_display") or pol.get("assets", ""),
                "assets_detail": {
                    "total_억원": ad.get("total_억원", 0),
                    "source": ad.get("source", ""),
                    "detail_url": ad.get("detail_url", ""),
                    "position": ad.get("position", ""),
                },
                "election": pol.get("election", ""),
                "stocks": pol_stocks,
                "screening": matched,
            }

    # 종목별 테마 맥락 (왜 테마주인지)
    stock_contexts = tm.get_all_stock_contexts()

    # screening_results에 테마 태그 병합
    enriched = []
    for r in sorted(results, key=lambda x: x.get("change_pct", 0), reverse=True):
        ctx = stock_contexts.get(r["ticker"], {})
        r["tags"] = ctx.get("tags", [])
        r["reasons"] = ctx.get("reasons", [])
        # 시가총액 계산 (종가 × 상장주식수는 pykrx에서 못 가져오므로 close만)
        enriched.append(r)

    # 후보별 시가총액 합산 (컨설턴트용)
    candidate_market_summary = {}
    for cand_name, info in candidate_stocks.items():
        total_value = 0
        total_volume = 0
        stock_count = len(info.get("screening", []))
        avg_change = 0
        for s in info.get("screening", []):
            total_volume += s.get("today_volume", 0)
            avg_change += s.get("change_pct", 0)
        avg_change = round(avg_change / stock_count, 2) if stock_count else 0
        candidate_market_summary[cand_name] = {
            "stock_count": stock_count,
            "total_volume": total_volume,
            "avg_change_pct": avg_change,
            "party": info.get("party", ""),
            "region": info.get("region", ""),
        }

    # Gemini AI 분석 (캐싱 — 같은 날 재실행 시 API 미호출)
    ga = GeminiAnalyzer(cache_dir=str(ROOT / "data" / "gemini_cache"))
    am = AutoMapper(tm, ga, output_dir=str(ROOT / "data" / "suggestions"))

    # 일일 리포트 생성 (output 완성 전이므로 임시 데이터로)
    report_input = {
        "date": today,
        "screening_results": enriched,
        "summary": {
            "up": sum(1 for r in results if r.get("change_pct", 0) > 0),
            "down": sum(1 for r in results if r.get("change_pct", 0) < 0),
            "surge_count": sum(1 for r in results if r.get("surge")),
        },
        "election_phase": phase,
        "candidate_market_summary": candidate_market_summary,
    }
    daily_report = ""
    try:
        daily_report = ga.generate_daily_report(report_input)
        print(f"Gemini 일일 리포트 생성 완료")
    except Exception as e:
        print(f"Gemini 리포트 생성 실패 (무시): {e}")

    # 테마주 자동 제안 (캐싱 — 같은 날 재실행 시 API 미호출)
    suggestions = {}
    try:
        suggestions = am.suggest_for_all()
        new_tickers = am.get_new_tickers(suggestions)
        print(f"Gemini 테마주 제안: {sum(len(v) for v in suggestions.values())}개 ({len(new_tickers)}개 신규)")
    except Exception as e:
        print(f"Gemini 테마주 제안 실패 (무시): {e}")

    output = {
        "date": today,
        "election_phase": phase,
        "total_tracked": len(tickers),
        "candidates": candidates,
        "candidate_stocks": candidate_stocks,
        "candidate_market_summary": candidate_market_summary,
        "stock_contexts": stock_contexts,
        "screening_results": enriched,
        "ai_report": daily_report,
        "ai_suggestions": suggestions,
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
