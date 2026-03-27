"""
테마주 자동 매핑 엔진
Gemini API로 정치인 프로필 기반 관련주 자동 제안 → 검증 → 매핑 추가

매핑 기준 (한국 정치 테마주 관행):
1. 정책/공약: 반도체, 원전, 방산, SOC, 부동산 등
2. 지역(지연): 출신지, 지역구 → 해당 지역 기반 기업
3. 본관(종친): 경주 김씨, 전주 이씨 등 → 같은 본관 재벌/기업
4. 학연: 출신 학교 동문이 운영하는 상장사
5. 혈연/인척: 배우자, 자녀, 친인척 관련 기업
6. 인맥/측근: 캠프 인사, 정치적 후원자 관련 기업
7. 경력: 전직장, 전 소속 기업/기관 관련
"""
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class AutoMapper:
    def __init__(self, theme_mapper, gemini_analyzer, output_dir: str = "data/suggestions"):
        self.tm = theme_mapper
        self.ga = gemini_analyzer
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_politician_info(self, pol_data: dict) -> dict:
        """YAML 정치인 데이터 → Gemini 제안용 입력"""
        return {
            "name": pol_data.get("name", ""),
            "party": pol_data.get("party", ""),
            "role": pol_data.get("role", ""),
            "profile": pol_data.get("profile", ""),
            "region": pol_data.get("region", ""),
            "assets": pol_data.get("assets", ""),
            "keywords": pol_data.get("keywords", []),
        }

    def suggest_for_all(self) -> dict:
        """
        모든 후보에 대해 Gemini 테마주 자동 제안 실행
        Returns: {후보이름: [제안 종목 리스트]}
        """
        all_suggestions = {}

        # 대선 후보
        for pol in self.tm.data.get("politicians", []):
            existing = [s["ticker"] for s in pol.get("related_stocks", [])]
            info = self._get_politician_info(pol)
            suggestions = self.ga.suggest_theme_stocks(info, existing)
            if suggestions:
                all_suggestions[pol["name"]] = suggestions

        # 지방선거 후보
        for cand in self.tm.data.get("local_candidates_2026", []):
            existing = [s["ticker"] for s in cand.get("related_stocks", [])]
            info = self._get_politician_info(cand)
            suggestions = self.ga.suggest_theme_stocks(info, existing)
            if suggestions:
                all_suggestions[cand["name"]] = suggestions

        # 결과 저장
        today = datetime.now().strftime("%Y-%m-%d")
        output_path = self.output_dir / f"suggestions_{today}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_suggestions, f, ensure_ascii=False, indent=2)
        logger.info(f"테마주 제안 저장: {output_path} ({len(all_suggestions)}명)")

        return all_suggestions

    def get_new_tickers(self, suggestions: dict) -> list[str]:
        """기존 매핑에 없는 신규 종목만 추출"""
        existing = set(self.tm.get_all_tickers())
        new_tickers = set()
        for name, items in suggestions.items():
            for item in items:
                ticker = item.get("ticker", "")
                if ticker and ticker not in existing:
                    new_tickers.add(ticker)
        return sorted(new_tickers)

    def generate_mapping_report(self, suggestions: dict) -> str:
        """
        사람이 검토할 수 있는 매핑 리포트 생성
        자동 추가 전 검토용
        """
        lines = ["# 테마주 자동 제안 리포트", f"생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]

        existing = set(self.tm.get_all_tickers())

        for name, items in suggestions.items():
            lines.append(f"## {name}")
            for item in items:
                ticker = item.get("ticker", "")
                is_new = "🆕" if ticker not in existing else "✅기존"
                confidence = item.get("confidence", "?")
                category = item.get("category", "?")
                lines.append(
                    f"  {is_new} [{confidence}] {item.get('name','')} ({ticker}) "
                    f"[{category}] → {item.get('relation','')}"
                )
            lines.append("")

        return "\n".join(lines)
