"""
선거 종료 일회성 패치 — 기존 docs/data/latest.json에 post-election 컨텍스트 주입.
정식 데이터는 다음 run_screening.py 실행(GitHub Action) 시 자동 생성된다.
이 스크립트는 배포 사이트가 즉시 선거 결과를 반영하도록 하는 보조 용도.
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from collectors.poll_collector import PollCollector


def main():
    pc = PollCollector()
    phase = pc.get_election_phase()
    result = pc.get_last_election_result()

    target = ROOT / "docs" / "data" / "latest.json"
    with open(target, encoding="utf-8") as f:
        data = json.load(f)

    data["election_phase"] = phase
    data["election_result"] = result
    data.setdefault("ai_post_election_review", "")

    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"patched: {target}")
    print(f"phase={phase['phase']} | is_post={phase['is_post_election']} | D+{phase['days_since_last']}")
    print(f"verdict={result['result']['verdict']} | turnout={result['result']['turnout_pct']}% | races={len(result['result']['key_races'])}")


if __name__ == "__main__":
    main()
