"""
여론조사 데이터 수집기
- 중앙선거관리위원회 여론조사 공표 목록
- 네이버 뉴스 여론조사 기사
"""
import re
import yaml
import requests
from datetime import datetime, timedelta
from pathlib import Path
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)

CALENDAR_FILE = Path(__file__).parent.parent.parent / "config" / "election_calendar.yaml"


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
        self._calendar = self._load_calendar()

    def _load_calendar(self) -> dict:
        try:
            with open(CALENDAR_FILE, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception:
            return {}

    def get_next_election_info(self) -> dict:
        """다가오는 선거 정보 반환"""
        elections = self._calendar.get("elections", {})
        today = datetime.now().date()
        upcoming = []
        for key, info in elections.items():
            if info.get("status") == "upcoming":
                election_date = datetime.strptime(info["date"], "%Y-%m-%d").date()
                days_until = (election_date - today).days
                upcoming.append({**info, "key": key, "days_until": days_until})
        if not upcoming:
            return {}
        upcoming.sort(key=lambda x: x["days_until"])
        return upcoming[0]

    def get_last_election_info(self) -> dict:
        """가장 최근에 완료된 선거 정보 반환 (결과 포함)"""
        elections = self._calendar.get("elections", {})
        today = datetime.now().date()
        completed = []
        for key, info in elections.items():
            if info.get("status") != "completed":
                continue
            try:
                election_date = datetime.strptime(info["date"], "%Y-%m-%d").date()
            except (ValueError, KeyError):
                continue
            if election_date > today:
                continue
            days_since = (today - election_date).days
            completed.append({**info, "key": key, "days_since": days_since})
        if not completed:
            return {}
        completed.sort(key=lambda x: x["days_since"])
        return completed[0]

    def get_election_phase(self) -> dict:
        """
        현재 선거 타임라인 단계 반환.
        선거 직후(최근 완료 선거가 차기 선거보다 가까움)에는 post-election 컨텍스트를 포함한다.
        """
        next_election = self.get_next_election_info()
        last_election = self.get_last_election_info()

        days_until = next_election.get("days_until", 999) if next_election else None
        days_since = last_election.get("days_since") if last_election else None

        # 선거 직후 여부: 최근 완료 선거가 120일 이내이고, 차기 선거가 그보다 멀 때
        is_post_election = bool(
            days_since is not None
            and days_since <= 120
            and (days_until is None or days_since < days_until)
        )

        # 최근 완료 선거 요약 (결과)
        last_summary = {}
        if last_election:
            res = last_election.get("result", {}) or {}
            last_summary = {
                "last_election_name": last_election.get("name", ""),
                "last_election_date": last_election.get("date", ""),
                "days_since_last": days_since,
                "last_verdict": res.get("verdict", ""),
                "last_turnout_pct": res.get("turnout_pct"),
            }

        # 타임라인에서 오늘 날짜가 속한 단계 탐색
        today = datetime.now().date()
        timeline = self._calendar.get("theme_stock_timeline", [])
        matched = None
        for phase_info in reversed(timeline):
            period = phase_info.get("period", "")
            parts = period.split(" ~ ")
            if len(parts) != 2:
                continue
            try:
                start = datetime.strptime(parts[0].strip(), "%Y-%m").date()
                end = datetime.strptime(parts[1].strip(), "%Y-%m").date()
            except ValueError:
                continue
            if start <= today <= end:
                matched = phase_info
                break

        if matched:
            base = {
                "phase": matched["phase"],
                "pattern": matched["pattern"],
                "signal": matched["signal"],
            }
        else:
            base = {"phase": "선거 준비 단계", "signal": "모니터링", "pattern": ""}

        return {
            **base,
            "days_until_election": days_until,
            "election_name": next_election.get("name", "") if next_election else "",
            "is_post_election": is_post_election,
            **last_summary,
        }

    def get_tracking_candidates(self, election_type: str = None) -> list[dict]:
        """추적 대상 후보 목록 반환 (election_calendar 기반)"""
        elections = self._calendar.get("elections", {})
        candidates = []
        for key, info in elections.items():
            if election_type and info.get("type") not in [election_type, None]:
                continue
            for region, region_data in info.get("candidates", {}).items():
                for party, party_candidates in region_data.items():
                    if not isinstance(party_candidates, list):
                        continue
                    for c in party_candidates:
                        if isinstance(c, dict) and "name" in c:
                            candidates.append({
                                "name": c["name"],
                                "party": party,
                                "region": region_data.get("region", region),
                                "status": c.get("status", ""),
                                "election": info.get("name", ""),
                            })
        return candidates

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

    def fetch_naver_poll_news(self, query: str = None, display: int = 20) -> list[dict]:
        """네이버 뉴스 API로 여론조사 관련 기사 수집"""
        if not self.naver_client_id:
            logger.warning("네이버 API 키 없음 — 네이버 뉴스 수집 건너뜀")
            return []
        # 다가오는 선거 기반으로 쿼리 자동 생성
        if query is None:
            next_election = self.get_next_election_info()
            election_name = next_election.get("name", "대선")
            query = f"{election_name} 여론조사"

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
            logger.info(f"네이버 여론조사 뉴스 {len(results)}건 수집 (쿼리: {query})")
            return results
        except Exception as e:
            logger.error(f"네이버 뉴스 수집 실패: {e}")
            return []

    def parse_support_rates(self, text: str) -> dict[str, float]:
        """
        텍스트에서 지지율 수치 파싱
        예: "정원오 35.2%, 오세훈 28.1%" → {"정원오": 35.2, "오세훈": 28.1}
        """
        pattern = r"([가-힣]{2,4})\s*(\d{1,2}(?:\.\d)?)\s*%"
        matches = re.findall(pattern, text)
        return {name: float(pct) for name, pct in matches}

    def get_last_election_result(self) -> dict:
        """가장 최근 완료 선거의 결과 블록 반환 (대시보드용)"""
        last = self.get_last_election_info()
        if not last:
            return {}
        return {
            "name": last.get("name", ""),
            "date": last.get("date", ""),
            "days_since": last.get("days_since"),
            "significance": last.get("significance", ""),
            "result": last.get("result", {}) or {},
            "theme_stock_outlook": last.get("theme_stock_outlook", ""),
        }

    def summarize_election_status(self) -> str:
        """현재 선거 상황 요약 텍스트 (Slack 알림용)"""
        phase = self.get_election_phase()
        if phase.get("is_post_election"):
            res = self.get_last_election_result().get("result", {})
            d_since = phase.get("days_since_last", "?")
            return (
                f"*[선거 종료 — 청산 국면]*\n"
                f"  선거: {phase.get('last_election_name', '')} (D+{d_since})\n"
                f"  결과: {res.get('verdict', '-')}\n"
                f"  현재 단계: {phase.get('phase', '')} | 시그널: {phase.get('signal', '')}\n"
                f"  차기: {phase.get('election_name', '')} (D-{phase.get('days_until_election', '?')})"
            )
        next_election = self.get_next_election_info()
        if not next_election:
            return "추적 중인 선거 없음"
        days = phase.get("days_until_election", "?")
        return (
            f"*[선거 현황]*\n"
            f"  선거: {phase.get('election_name', '')}\n"
            f"  D-{days} | 현재 단계: {phase.get('phase', '')}\n"
            f"  패턴: {phase.get('pattern', '')}\n"
            f"  시그널: {phase.get('signal', '')}"
        )
