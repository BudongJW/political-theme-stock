"""
정치인 재산 정보 수집기
- 뉴스타파 공직자 재산 정보 (jaesan.newstapa.org)
- opengirok/congress_asset_disclosure (GitHub 구글 시트)
"""
import requests
import csv
import io
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# opengirok 구글 시트 (국회의원 재산신고 — 정보공개센터 공개)
# 출처: https://github.com/opengirok/congress_asset_disclosure
OPENGIROK_SHEETS = {
    "2025": {
        "id": "182m4MFFj4Ho2cICo3PGy8RyxHZ3vwNklyo5yM4M5M4Q",
        "label": "2025 정기재산변동신고 (2025.03.27 공개)",
    },
    "22대_2024": {
        "id": "1DHOsfx3rMxZniGvr3bEIUVZCQLBPIAcpIfvozDbsDhw",
        "label": "22대 신규/재등록 + 21대 퇴직 (2024.08.29)",
    },
    "2024": {
        "id": "1_1j0TewzCXenNzI2tj2f_tc06iWI6gMX-okS0Q_J3i0",
        "label": "2024 정기재산변동신고 (2024.03.28 공개)",
    },
    "2023": {
        "id": "12F6gfUNlJGQM1uaIlhD75ria9OZBR0DejF9_IW-JAH0",
        "label": "2023 정기재산변동신고 (2023.03.31 공개)",
    },
    "2022": {
        "id": "124rioS6kCtJrbuiXNATQmbrFVsBX2TTkQpsSDjco3jA",
        "label": "2022 정기재산변동신고 (2022.03.31 공개)",
    },
}

# 뉴스타파 API 검색 결과 구조:
# {"totalLength": N, "results": [
#   {"peopleId","name","belong","position","uniqueId",
#    "price_total_last"(천원),"open_year_first","open_year_last",...}
# ]}
NEWSTAPA_SEARCH_URL = "https://jaesan.newstapa.org/api/search"
NEWSTAPA_DETAIL_BASE = "https://jaesan.newstapa.org/people"


class AssetCollector:
    def __init__(self, data_dir: str = "data/assets"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.data_dir / "asset_cache.json"
        self._cache = self._load_cache()

    def _load_cache(self) -> dict:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self):
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=2)

    def fetch_newstapa(self, name: str) -> dict:
        """
        뉴스타파 공직자 재산 검색 API
        https://jaesan.newstapa.org/api/search?q={name}
        → price_total_last (천원), position, belong, uniqueId
        """
        cache_key = f"newstapa_{name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            resp = requests.get(
                NEWSTAPA_SEARCH_URL, params={"q": name}, timeout=10
            )
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    # 동명이인 중 가장 최근 공개자 우선
                    best = max(results, key=lambda r: int(r.get("open_year_last", "0")))
                    total_천원 = int(best.get("price_total_last", "0"))
                    result = {
                        "name": name,
                        "source": "newstapa",
                        "people_id": best.get("peopleId", ""),
                        "unique_id": best.get("uniqueId", ""),
                        "position": best.get("position", ""),
                        "belong": best.get("belong", "").strip(),
                        "total_천원": total_천원,
                        "total_억원": round(total_천원 / 100000, 1),
                        "total_display": f"약 {round(total_천원 / 100000, 1)}억원",
                        "open_year_first": best.get("open_year_first", ""),
                        "open_year_last": best.get("open_year_last", ""),
                        "detail_url": f"{NEWSTAPA_DETAIL_BASE}/{best.get('uniqueId', '')}",
                        "feature_image": best.get("feature_image", ""),
                        "fetched_at": datetime.now().isoformat(),
                    }
                    self._cache[cache_key] = result
                    self._save_cache()
                    return result
        except Exception as e:
            logger.warning(f"뉴스타파 API 실패 ({name}): {e}")

        return {"name": name, "source": "none", "available": False}

    def _get_sheet_csv_url(self, sheet_id: str, gid: str = "0") -> str:
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

    def fetch_opengirok_sheet(self, sheet_key: str = "2025") -> list[dict]:
        """opengirok 구글 스프레드시트에서 국회의원 재산 CSV 다운로드"""
        sheet_info = OPENGIROK_SHEETS.get(sheet_key)
        if not sheet_info:
            logger.error(f"시트 키 없음: {sheet_key}")
            return []

        cache_path = self.data_dir / f"opengirok_{sheet_key}.json"
        if cache_path.exists():
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)

        url = self._get_sheet_csv_url(sheet_info["id"])
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            reader = csv.DictReader(io.StringIO(resp.text))
            rows = list(reader)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            logger.info(f"opengirok 시트 다운로드: {len(rows)}행 ({sheet_key})")
            return rows
        except Exception as e:
            logger.error(f"opengirok 시트 다운로드 실패 ({sheet_key}): {e}")
            return []

    def search_opengirok(self, name: str, sheet_key: str = "2025") -> list[dict]:
        """opengirok 데이터에서 이름으로 검색"""
        rows = self.fetch_opengirok_sheet(sheet_key)
        # 컬럼명이 시트마다 다를 수 있으므로 유연하게 매칭
        matched = []
        for r in rows:
            row_name = (
                r.get("성명", "") or r.get("이름", "") or r.get("name", "")
            ).strip()
            if row_name == name:
                matched.append(r)
        return matched

    def get_asset_summary(self, name: str) -> dict:
        """
        정치인 이름 → 재산 요약 정보 반환
        1순위: opengirok (구조화 데이터, 국회의원)
        2순위: 뉴스타파 API (총 재산액 + 상세 페이지 링크)
        """
        if name in self._cache:
            return self._cache[name]

        # 1. 뉴스타파 검색 (총 재산액 + 상세 페이지 링크, 범용)
        newstapa = self.fetch_newstapa(name)
        if newstapa.get("total_천원"):
            result = {
                "name": name,
                "source": "newstapa",
                "total_천원": newstapa["total_천원"],
                "total_억원": newstapa["total_억원"],
                "total_display": newstapa["total_display"],
                "position": newstapa.get("position", ""),
                "detail_url": newstapa.get("detail_url", ""),
                "open_year_range": f"{newstapa.get('open_year_first','')}~{newstapa.get('open_year_last','')}",
                "fetched_at": datetime.now().isoformat(),
            }
            self._cache[name] = result
            self._save_cache()
            return result

        # 2. opengirok 비활성화 (Google Sheets CSV export 차단됨 — link-shared이지만 published-to-web 아님)
        # 향후 opengirok가 published-to-web으로 전환되면 search_opengirok() 호출 복원 가능

        # 없으면 빈 결과
        return {
            "name": name,
            "source": "none",
            "total_display": "정보 없음",
            "note": "뉴스타파(jaesan.newstapa.org) 또는 선관위(info.nec.go.kr)에서 직접 확인",
        }

    def get_multiple(self, names: list[str]) -> dict[str, dict]:
        """여러 정치인 재산 일괄 조회"""
        return {name: self.get_asset_summary(name) for name in names}
