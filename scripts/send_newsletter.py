"""
뉴스레터 발송 스크립트 (GitHub Actions에서 실행)
스크리닝 결과(latest.json) 기반 HTML 이메일 생성 및 발송
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from newsletter.email_sender import NewsletterManager


def main():
    # 최신 스크리닝 데이터 로드
    latest_file = ROOT / "docs" / "data" / "latest.json"
    if not latest_file.exists():
        print("latest.json 없음 — 스크리닝 먼저 실행 필요")
        return

    with open(latest_file, encoding="utf-8") as f:
        data = json.load(f)

    nm = NewsletterManager(data_dir=str(ROOT / "data" / "newsletter"))

    # 환경변수에서 SMTP 설정 읽기
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("NEWSLETTER_FROM", smtp_user)

    if not smtp_user or not smtp_pass:
        print("SMTP 인증 정보 없음 (SMTP_USER, SMTP_PASS 환경변수 필요)")
        print("HTML 리포트만 생성합니다.")
        html = nm.generate_html_report(data)
        output_file = ROOT / "data" / "newsletter" / f"report_{data.get('date', 'latest')}.html"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"HTML 리포트 저장: {output_file}")
        return

    subscribers = nm.get_active_subscribers()
    print(f"활성 구독자: {len(subscribers)}명")

    if not subscribers:
        print("구독자 없음 — 발송 생략")
        return

    result = nm.send_newsletter(
        data,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_pass=smtp_pass,
        from_email=from_email,
    )
    print(f"발송 결과: {result['sent']}/{result.get('total', 0)}명 성공")
    if result.get("errors"):
        for err in result["errors"]:
            print(f"  오류: {err}")


if __name__ == "__main__":
    main()
