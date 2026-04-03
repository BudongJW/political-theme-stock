"""
여론조사 지지율 데이터 수집기
- 한국갤럽 (주간 정당/대통령 지지율)
- 리얼미터 (일간 트래킹)
- 구글 뉴스 RSS (여론조사 기사 파싱)
→ 후보별 지지율 시계열 DB 구축
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?hl=ko&gl=KR&ceid=KR:ko&q="


MAX_POLL_RECORDS = 1000


class PollDataCollector:
    def __init__(self, data_dir: str = "data/polls"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_file = self.data_dir / "poll_history.json"
        self._db = self._load_db()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    def _load_db(self) -> dict:
        if self.db_file.exists():
            try:
                with open(self.db_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"polls": [], "last_updated": None}

    def _save_db(self):
        # 보관 정책: 최근 MAX_POLL_RECORDS건만 유지
        if len(self._db["polls"]) > MAX_POLL_RECORDS:
            self._db["polls"] = self._db["polls"][-MAX_POLL_RECORDS:]
        self._db["last_updated"] = datetime.now().isoformat()
        with open(self.db_file, "w", encoding="utf-8") as f:
            json.dump(self._db, f, ensure_ascii=False, indent=2)

    def fetch_poll_news(self, regions: list[str] = None) -> list[dict]:
        """구글 뉴스 RSS로 여론조사 기사 수집"""
        if regions is None:
            regions = ["서울시장", "경기지사", "인천시장", "부산시장", "대구시장",
                        "대전시장", "광주시장", "울산시장", "세종시장",
                        "강원도지사", "충북도지사", "충남도지사",
                        "전북도지사", "경북도지사", "경남도지사", "제주도지사",
                        "대통령 지지율", "정당 지지율"]
        all_articles = []
        seen_titles = set()

        for region in regions:
            query = f"{region} 여론조사 지지율"
            try:
                feed = feedparser.parse(f"{GOOGLE_NEWS_RSS}{quote(query)}")
                for entry in feed.entries[:5]:
                    title = entry.get("title", "")
                    if title in seen_titles:
                        continue
                    seen_titles.add(title)
                    all_articles.append({
                        "title": title,
                        "link": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "query": region,
                        "collected_at": datetime.now().isoformat(),
                    })
            except Exception as e:
                logger.warning(f"뉴스 수집 실패 ({region}): {e}")

        logger.info(f"여론조사 뉴스 {len(all_articles)}건 수집")
        return all_articles

    def parse_poll_from_text(self, text: str) -> list[dict]:
        """텍스트에서 후보명+지지율 추출"""
        # "정원오 35.2%", "오세훈 28.1%", "정원오(35.2%)" 등
        patterns = [
            r"([가-힣]{2,4})\s*(?:후보|전?\s*(?:시장|지사|도지사))?\s*(\d{1,2}(?:\.\d{1,2})?)\s*%",
            r"([가-힣]{2,4})\s*\(\s*(\d{1,2}(?:\.\d{1,2})?)\s*%\s*\)",
        ]
        results = []
        seen = set()
        for pattern in patterns:
            for name, pct in re.findall(pattern, text):
                if name not in seen and len(name) >= 2:
                    seen.add(name)
                    results.append({"name": name, "rate": float(pct)})
        return results

    def parse_poll_institution(self, text: str) -> str:
        """텍스트에서 조사기관명 추출"""
        institutions = [
            "한국갤럽", "갤럽", "리얼미터", "NBS", "한길리서치", "엠브레인",
            "케이스탯리서치", "한국리서치", "입소스", "메타보이스", "여론조사꽃",
        ]
        for inst in institutions:
            if inst in text:
                return inst
        return ""

    def parse_poll_region(self, text: str) -> str:
        """텍스트에서 지역 추출"""
        region_map = {
            "서울시장": "서울", "서울": "서울",
            "경기지사": "경기", "경기도": "경기",
            "인천시장": "인천", "인천": "인천",
            "부산시장": "부산", "부산": "부산",
            "대구시장": "대구", "대구": "대구",
            "대전시장": "대전", "대전": "대전",
            "광주시장": "광주", "광주": "광주",
            "울산시장": "울산", "울산": "울산",
            "세종시장": "세종", "세종": "세종",
            "강원도지사": "강원", "강원": "강원",
            "충북도지사": "충북", "충청북도": "충북",
            "충남도지사": "충남", "충청남도": "충남",
            "전북도지사": "전북", "전라북도": "전북",
            "경북도지사": "경북", "경상북도": "경북",
            "경남도지사": "경남", "경상남도": "경남",
            "제주도지사": "제주", "제주": "제주",
            "대통령": "전국", "정당 지지율": "전국",
        }
        for keyword, region in region_map.items():
            if keyword in text:
                return region
        return ""

    def collect_and_parse(self, regions: list[str] = None) -> list[dict]:
        """뉴스 수집 → 지지율 파싱 → DB 저장"""
        articles = self.fetch_poll_news(regions)
        new_polls = []

        for article in articles:
            title = article.get("title", "")
            rates = self.parse_poll_from_text(title)
            if not rates:
                continue

            region = self.parse_poll_region(title) or self.parse_poll_region(article.get("query", ""))
            institution = self.parse_poll_institution(title)

            poll_entry = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "region": region,
                "institution": institution,
                "source_title": title,
                "source_link": article.get("link", ""),
                "rates": {r["name"]: r["rate"] for r in rates},
                "collected_at": article.get("collected_at", ""),
            }

            # 중복 체크 (같은 날 같은 제목)
            is_dup = any(
                p.get("source_title") == title and p.get("date") == poll_entry["date"]
                for p in self._db["polls"]
            )
            if not is_dup:
                self._db["polls"].append(poll_entry)
                new_polls.append(poll_entry)

        if new_polls:
            self._save_db()
            logger.info(f"여론조사 {len(new_polls)}건 신규 저장 (총 {len(self._db['polls'])}건)")

        return new_polls

    def get_candidate_history(self, name: str, region: str = None) -> list[dict]:
        """특정 후보의 지지율 시계열 데이터"""
        history = []
        for poll in self._db["polls"]:
            if region and poll.get("region") != region:
                continue
            rates = poll.get("rates", {})
            if name in rates:
                history.append({
                    "date": poll["date"],
                    "rate": rates[name],
                    "institution": poll.get("institution", ""),
                    "region": poll.get("region", ""),
                })
        history.sort(key=lambda x: x["date"])
        return history

    def get_latest_polls_by_region(self) -> dict[str, dict]:
        """지역별 최신 여론조사 결과"""
        latest = {}
        for poll in sorted(self._db["polls"], key=lambda x: x.get("date", ""), reverse=True):
            region = poll.get("region", "")
            if region and region not in latest:
                latest[region] = poll
        return latest

    def calculate_momentum(self, name: str, region: str = None) -> dict:
        """후보 지지율 모멘텀 계산 (최근 2회 비교)"""
        history = self.get_candidate_history(name, region)
        if len(history) < 2:
            return {
                "name": name, "region": region,
                "current": history[-1]["rate"] if history else None,
                "previous": None, "change": None, "trend": "데이터 부족",
            }

        current = history[-1]["rate"]
        previous = history[-2]["rate"]
        change = round(current - previous, 1)

        if change >= 3:
            trend = "급등"
        elif change >= 1:
            trend = "상승"
        elif change <= -3:
            trend = "급락"
        elif change <= -1:
            trend = "하락"
        else:
            trend = "보합"

        return {
            "name": name,
            "region": region,
            "current": current,
            "previous": previous,
            "change": change,
            "trend": trend,
            "history": history[-10:],
        }

    def get_all_polls(self) -> list[dict]:
        return self._db.get("polls", [])
