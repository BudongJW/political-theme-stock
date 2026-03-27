"""
여론조사 데이터 수집기
- 중앙선거관리위원회 여론조사 공표 목록
- 네이버 뉴스 여론조사 기사
"""
import requests
import json
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)


class PollCollector:
    NEC_BASE_URL = "https://www.nec.go.kr/portal/bbs/list/B0000338.do"
    NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

    def __init__(self, naver_client_id: str = None, naver_client_secret: str = None):
        self.naver_client_id = naver_client_id
        self.naver_client_secret = naver_client_secret
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    def fetch_nec_polls(self, days_back: int = 7) -> list[dict]:
        """선관위 여론조사 공표 목록 수집"""
        results = []
        try:
            resp = self.session.get(self.NEC_BASE_URL, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table.tbl_type01 tbody tr")
            cutoff = datetime.now() - timedelta(days=days_back)
            for row in rows:
                cols = row.select("td")
                if len(cols) < 5:
                    continue
                date_str = cols[3].get_text(strip=True)
                try:
                    pub_date = datetime.strptime(date_str, "%Y.%m.%d")
                except ValueError:
                    continue
                if pub_date < cutoff:
                    continue
                results.append({
                    "source": "선관위",
                    "title": cols[1].get_text(strip=True),
                    "institution": cols[2].get_text(strip=True),
                    "date": date_str,
                    "collected_at": datetime.now().isoformat(),
                })
            logger.info(f"선관위 여론조사 {len(results)}건 수집")
        except Exception as e:
            logger.error(f"선관위 수집 실패: {e}")
        return results

    def fetch_naver_poll_news(self, query: str = "대선 여론조사", display: int = 20) -> list[dict]:
        """네이버 뉴스 API로 여론조사 관련 기사 수집"""
        if not self.naver_client_id:
            logger.warning("네이버 API 키 없음 — 네이버 뉴스 수집 건너뜀")
            return []
        headers = {
            "X-Naver-Client-Id": self.naver_client_id,
            "X-Naver-Client-Secret": self.naver_client_secret,
        }
        params = {"query": query, "display": display, "sort": "date"}
        try:
            resp = requests.get(self.NAVER_NEWS_URL, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            results = []
            for item in items:
                results.append({
                    "source": "네이버뉴스",
                    "title": BeautifulSoup(item["title"], "lxml").get_text(),
                    "description": BeautifulSoup(item["description"], "lxml").get_text(),
                    "pub_date": item["pubDate"],
                    "link": item["link"],
                    "collected_at": datetime.now().isoformat(),
                })
            logger.info(f"네이버 여론조사 뉴스 {len(results)}건 수집")
            return results
        except Exception as e:
            logger.error(f"네이버 뉴스 수집 실패: {e}")
            return []

    def parse_support_rates(self, text: str) -> dict:
        """
        텍스트에서 지지율 수치 파싱 (정규식 기반)
        예: "이재명 40.2%, 김문수 32.1%" → {"이재명": 40.2, "김문수": 32.1}
        """
        import re
        pattern = r"([가-힣]{2,4})\s*(\d{1,2}(?:\.\d)?)\s*%"
        matches = re.findall(pattern, text)
        return {name: float(pct) for name, pct in matches}
