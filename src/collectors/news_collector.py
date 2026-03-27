"""
정치 뉴스 수집기 (네이버 RSS / 구글 뉴스)
"""
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

NAVER_RSS_TOPICS = {
    "정치": "https://news.naver.com/main/rss/section.naver?sid1=100",
    "선거": "https://news.naver.com/main/rss/section.naver?sid1=100&sid2=269",
}


class NewsCollector:
    def __init__(self, naver_client_id: str = None, naver_client_secret: str = None):
        self.naver_client_id = naver_client_id
        self.naver_client_secret = naver_client_secret

    def fetch_rss(self, url: str) -> list[dict]:
        """RSS 피드 수집"""
        try:
            feed = feedparser.parse(url)
            results = []
            for entry in feed.entries:
                results.append({
                    "title": entry.get("title", ""),
                    "summary": BeautifulSoup(entry.get("summary", ""), "lxml").get_text(),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "collected_at": datetime.now().isoformat(),
                })
            return results
        except Exception as e:
            logger.error(f"RSS 수집 실패 ({url}): {e}")
            return []

    def fetch_naver_news(self, query: str, display: int = 20) -> list[dict]:
        """네이버 뉴스 검색 API"""
        if not self.naver_client_id:
            return []
        headers = {
            "X-Naver-Client-Id": self.naver_client_id,
            "X-Naver-Client-Secret": self.naver_client_secret,
        }
        params = {"query": query, "display": display, "sort": "date"}
        try:
            resp = requests.get(
                "https://openapi.naver.com/v1/search/news.json",
                headers=headers, params=params, timeout=10
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [
                {
                    "title": BeautifulSoup(i["title"], "lxml").get_text(),
                    "summary": BeautifulSoup(i["description"], "lxml").get_text(),
                    "link": i["link"],
                    "published": i["pubDate"],
                    "collected_at": datetime.now().isoformat(),
                }
                for i in items
            ]
        except Exception as e:
            logger.error(f"네이버 뉴스 수집 실패 ({query}): {e}")
            return []

    def collect_all(self, keywords: list[str]) -> list[dict]:
        """RSS + 키워드 검색 통합 수집"""
        all_news = []
        for _, url in NAVER_RSS_TOPICS.items():
            all_news.extend(self.fetch_rss(url))
        for kw in keywords:
            all_news.extend(self.fetch_naver_news(kw))
        seen = set()
        deduped = []
        for item in all_news:
            key = item.get("link") or item.get("title")
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        logger.info(f"뉴스 {len(deduped)}건 수집 (중복 제거 후)")
        return deduped
