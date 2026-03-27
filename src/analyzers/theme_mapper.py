"""
정치인-테마주 매핑 관리
- YAML: 수동 큐레이션 매핑 (대선 후보, 주요 지방선거 후보, 정책 테마)
- JSON: 전체 정치인 DB (22대 국회의원 306명, 지방선거 후보 74명)
"""
import json
import yaml
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ThemeMapper:
    def __init__(self, map_file: str = "config/politician_stock_map.yaml",
                 data_dir: str = None):
        self.map_file = Path(map_file)
        self.data_dir = Path(data_dir) if data_dir else self.map_file.parent.parent / "data" / "raw"
        self.data = self._load()
        self._assembly_members = self._load_assembly_members()
        self._local_candidates_full = self._load_local_candidates_full()
        self._merge_local_candidates()

    def _load(self) -> dict:
        try:
            with open(self.map_file, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"매핑 파일 로드 실패: {e}")
            return {"politicians": [], "policy_themes": {}}

    def _load_assembly_members(self) -> list[dict]:
        path = self.data_dir / "assembly_members_22.json"
        try:
            with open(path, encoding="utf-8") as f:
                members = json.load(f)
            logger.info(f"22대 국회의원 {len(members)}명 로드")
            return members
        except Exception as e:
            logger.warning(f"국회의원 데이터 로드 실패: {e}")
            return []

    def _load_local_candidates_full(self) -> list[dict]:
        path = self.data_dir / "local_candidates_2026_full.json"
        try:
            with open(path, encoding="utf-8") as f:
                cands = json.load(f)
            logger.info(f"지방선거 후보 {len(cands)}명 로드")
            return cands
        except Exception as e:
            logger.warning(f"지방선거 후보 데이터 로드 실패: {e}")
            return []

    def _merge_local_candidates(self):
        """JSON 후보 데이터를 YAML에 없는 경우 추가 (YAML이 우선)"""
        yaml_names = {c.get("name") for c in self.data.get("local_candidates_2026", [])}
        for cand in self._local_candidates_full:
            if cand.get("name") and cand["name"] not in yaml_names:
                self.data.setdefault("local_candidates_2026", []).append({
                    "name": cand["name"],
                    "party": cand.get("party", ""),
                    "role": cand.get("role", cand.get("position", "")),
                    "region": cand.get("region", ""),
                    "election": "2026지방선거",
                    "related_stocks": [],
                    "keywords": [cand["name"]],
                })

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
        """전체 추적 종목 코드 목록 (대선+지방선거+정책테마 통합)"""
        tickers = set()
        for pol in self.data.get("politicians", []):
            for s in pol.get("related_stocks", []):
                tickers.add(s["ticker"])
        for cand in self.data.get("local_candidates_2026", []):
            for s in cand.get("related_stocks", []):
                tickers.add(s["ticker"])
        for stocks in self.data.get("policy_themes", {}).values():
            for s in stocks:
                tickers.add(s["ticker"])
        return list(tickers)

    def get_all_politicians(self) -> list[str]:
        """전체 정치인 이름 (대선후보 + 지방선거후보 + 국회의원)"""
        names = set()
        for p in self.data.get("politicians", []):
            names.add(p["name"])
        for c in self.data.get("local_candidates_2026", []):
            names.add(c.get("name", ""))
        for m in self._assembly_members:
            names.add(m.get("name", ""))
        names.discard("")
        return list(names)

    def get_assembly_members(self) -> list[dict]:
        return self._assembly_members

    def get_assembly_member(self, name: str) -> dict | None:
        for m in self._assembly_members:
            if m.get("name") == name:
                return m
        return None

    def get_members_by_region(self, region: str) -> list[dict]:
        return [m for m in self._assembly_members if region in m.get("region", "")]

    def get_members_by_party(self, party: str) -> list[dict]:
        return [m for m in self._assembly_members if m.get("party") == party]

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

    def get_stock_context(self, ticker: str) -> dict:
        """
        종목 코드 → 이 종목이 왜 테마주인지 전체 맥락 반환
        {
          "ticker": "005930",
          "name": "삼성전자",
          "tags": ["이재명 관련주", "반도체 테마"],
          "reasons": [
            {"type": "politician", "name": "이재명", "party": "더불어민주당", "relation": "공약(반도체 지원)", "election": "대선"},
            {"type": "policy_theme", "theme": "반도체", "description": "경기도 반도체 클러스터(용인·평택)"}
          ]
        }
        """
        reasons = []
        tags = []
        stock_name = ""

        # 대선 후보 검색
        for pol in self.data.get("politicians", []):
            for s in pol.get("related_stocks", []):
                if s["ticker"] == ticker:
                    stock_name = stock_name or s.get("name", "")
                    tag = f"{pol['name']} 관련주"
                    tags.append(tag)
                    reasons.append({
                        "type": "politician",
                        "name": pol["name"],
                        "party": pol.get("party", ""),
                        "role": pol.get("role", ""),
                        "relation": s.get("relation", ""),
                        "election": pol.get("election", ""),
                    })

        # 지방선거 후보 검색
        for cand in self.data.get("local_candidates_2026", []):
            for s in cand.get("related_stocks", []):
                if s["ticker"] == ticker:
                    stock_name = stock_name or s.get("name", "")
                    tag = f"{cand['name']} 관련주"
                    tags.append(tag)
                    reasons.append({
                        "type": "local_candidate",
                        "name": cand["name"],
                        "party": cand.get("party", ""),
                        "role": cand.get("role", ""),
                        "region": cand.get("region", ""),
                        "relation": s.get("relation", ""),
                        "election": cand.get("election", ""),
                    })

        # 정책 테마 검색
        for theme_name, stocks in self.data.get("policy_themes", {}).items():
            for s in stocks:
                if s["ticker"] == ticker:
                    stock_name = stock_name or s.get("name", "")
                    tag = f"{theme_name} 테마"
                    if tag not in tags:
                        tags.append(tag)
                    reasons.append({
                        "type": "policy_theme",
                        "theme": theme_name,
                        "description": s.get("description", ""),
                    })

        return {
            "ticker": ticker,
            "name": stock_name,
            "tags": tags,
            "reasons": reasons,
        }

    def get_all_stock_contexts(self) -> dict[str, dict]:
        """전체 추적 종목의 테마 맥락 일괄 반환"""
        return {t: self.get_stock_context(t) for t in self.get_all_tickers()}
