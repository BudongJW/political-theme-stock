"""
정치인-테마주 매핑 관리
"""
import yaml
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ThemeMapper:
    def __init__(self, map_file: str = "config/politician_stock_map.yaml"):
        self.map_file = Path(map_file)
        self.data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.map_file, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"매핑 파일 로드 실패: {e}")
            return {"politicians": [], "policy_themes": {}}

    def get_tickers_for_politician(self, name: str) -> list[dict]:
        """정치인 이름으로 관련 종목 반환"""
        for pol in self.data.get("politicians", []):
            if pol["name"] == name:
                return pol.get("related_stocks", [])
        return []

    def get_tickers_for_theme(self, theme: str) -> list[dict]:
        """정책 테마로 관련 종목 반환"""
        return self.data.get("policy_themes", {}).get(theme, [])

    def get_all_tickers(self) -> list[str]:
        """전체 추적 종목 코드 목록"""
        tickers = set()
        for pol in self.data.get("politicians", []):
            for s in pol.get("related_stocks", []):
                tickers.add(s["ticker"])
        for stocks in self.data.get("policy_themes", {}).values():
            for s in stocks:
                tickers.add(s["ticker"])
        return list(tickers)

    def get_all_politicians(self) -> list[str]:
        return [p["name"] for p in self.data.get("politicians", [])]

    def get_politician_keywords(self) -> dict[str, list[str]]:
        return {
            p["name"]: p.get("keywords", [p["name"]])
            for p in self.data.get("politicians", [])
        }

    def match_politician_from_themes(self, policy_themes: list[str]) -> list[str]:
        """분석된 테마 목록으로 관련 정치인 역추적"""
        matched = set()
        for pol in self.data.get("politicians", []):
            for s in pol.get("related_stocks", []):
                for theme in policy_themes:
                    if theme in s.get("relation", ""):
                        matched.add(pol["name"])
        return list(matched)
