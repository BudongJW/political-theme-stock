"""
PollStock 뉴스레터 발송 시스템
- 일일 스크리닝 결과 기반 HTML 이메일 생성
- 구독자 관리 (JSON 기반)
- SMTP 또는 GitHub Actions 기반 발송
"""
import json
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)


class NewsletterManager:
    def __init__(self, data_dir: str = "data/newsletter"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.subscribers_file = self.data_dir / "subscribers.json"
        self._subscribers = self._load_subscribers()

    def _load_subscribers(self) -> list[dict]:
        if self.subscribers_file.exists():
            try:
                with open(self.subscribers_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_subscribers(self):
        with open(self.subscribers_file, "w", encoding="utf-8") as f:
            json.dump(self._subscribers, f, ensure_ascii=False, indent=2)

    def add_subscriber(self, email: str, name: str = "") -> bool:
        email = email.strip().lower()
        if any(s["email"] == email for s in self._subscribers):
            return False
        self._subscribers.append({
            "email": email,
            "name": name,
            "subscribed_at": datetime.now().isoformat(),
            "active": True,
        })
        self._save_subscribers()
        return True

    def remove_subscriber(self, email: str) -> bool:
        email = email.strip().lower()
        for s in self._subscribers:
            if s["email"] == email:
                s["active"] = False
                self._save_subscribers()
                return True
        return False

    def get_active_subscribers(self) -> list[dict]:
        return [s for s in self._subscribers if s.get("active", True)]

    def generate_html_report(self, data: dict) -> str:
        """스크리닝 데이터 → HTML 이메일 템플릿"""
        date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
        phase = data.get("election_phase", {})
        summary = data.get("summary", {})
        screening = data.get("screening_results", [])[:20]
        predictions = data.get("election_predictions", {})
        stock_preds = data.get("stock_predictions", {})
        poll_signals = data.get("poll_signals", {})
        ai_report = data.get("ai_report", "")

        # 상승/하락 종목
        up_stocks = sorted(
            [r for r in screening if r.get("change_pct", 0) > 0],
            key=lambda x: x["change_pct"], reverse=True
        )[:5]
        down_stocks = sorted(
            [r for r in screening if r.get("change_pct", 0) < 0],
            key=lambda x: x["change_pct"]
        )[:5]

        # TOP 매수 시그널
        top_picks = stock_preds.get("top_picks", [])[:5]

        # 여론조사 시그널
        bull_cnt = poll_signals.get("bull_count", 0)
        bear_cnt = poll_signals.get("bear_count", 0)

        # 당선예측 요약
        pred_regions = predictions.get("regions", {})

        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;font-family:-apple-system,'Pretendard',sans-serif;background:#0d1117;color:#e6edf3;">
<div style="max-width:640px;margin:0 auto;padding:20px;">

<!-- 헤더 -->
<div style="text-align:center;padding:24px 0;border-bottom:1px solid #30363d;">
  <h1 style="margin:0;font-size:1.6rem;color:#58a6ff;">PollStock 폴스탁</h1>
  <p style="margin:4px 0 0;color:#8b949e;font-size:.9rem;">정치 테마주 AI 분석 리포트 | {date}</p>
  <p style="margin:4px 0 0;color:#d29922;font-size:.95rem;font-weight:700;">
    D-{phase.get('days_until_election','?')} | {phase.get('phase','')}
  </p>
</div>

<!-- 요약 -->
<div style="display:flex;gap:12px;margin:16px 0;text-align:center;">
  <div style="flex:1;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;">
    <div style="font-size:1.4rem;font-weight:700;color:#3fb950;">{summary.get('up',0)}</div>
    <div style="font-size:.78rem;color:#8b949e;">상승</div>
  </div>
  <div style="flex:1;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;">
    <div style="font-size:1.4rem;font-weight:700;color:#f85149;">{summary.get('down',0)}</div>
    <div style="font-size:.78rem;color:#8b949e;">하락</div>
  </div>
  <div style="flex:1;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;">
    <div style="font-size:1.4rem;font-weight:700;color:#d29922;">{summary.get('surge_count',0)}</div>
    <div style="font-size:.78rem;color:#8b949e;">급등</div>
  </div>
  <div style="flex:1;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;">
    <div style="font-size:1.4rem;font-weight:700;color:#58a6ff;">{bull_cnt}/{bear_cnt}</div>
    <div style="font-size:.78rem;color:#8b949e;">호재/악재</div>
  </div>
</div>

<!-- TOP 상승 -->
<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px;margin:12px 0;">
  <h3 style="margin:0 0 8px;font-size:.95rem;color:#3fb950;">TOP 상승</h3>
  <table style="width:100%;font-size:.85rem;border-collapse:collapse;">
    {''.join(f'<tr><td style="padding:4px 0;"><strong>{s.get("name","")}</strong></td><td style="color:#3fb950;text-align:right;font-weight:700;">+{s["change_pct"]}%</td></tr>' for s in up_stocks) or '<tr><td style="color:#8b949e;">없음</td></tr>'}
  </table>
</div>

<!-- TOP 하락 -->
<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px;margin:12px 0;">
  <h3 style="margin:0 0 8px;font-size:.95rem;color:#f85149;">TOP 하락</h3>
  <table style="width:100%;font-size:.85rem;border-collapse:collapse;">
    {''.join(f'<tr><td style="padding:4px 0;"><strong>{s.get("name","")}</strong></td><td style="color:#f85149;text-align:right;font-weight:700;">{s["change_pct"]}%</td></tr>' for s in down_stocks) or '<tr><td style="color:#8b949e;">없음</td></tr>'}
  </table>
</div>

<!-- 복합 스코어 TOP -->
{'<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px;margin:12px 0;"><h3 style="margin:0 0 8px;font-size:.95rem;color:#58a6ff;">AI 매수 시그널</h3><table style="width:100%;font-size:.85rem;border-collapse:collapse;">' + ''.join(f'<tr><td style="padding:4px 0;"><strong>{p.get("name","")}</strong></td><td style="text-align:center;">{p.get("signal","")}</td><td style="text-align:right;color:#58a6ff;font-weight:700;">{p.get("score",0)}점</td></tr>' for p in top_picks) + '</table></div>' if top_picks else ''}

<!-- AI 리포트 요약 -->
{f'<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px;margin:12px 0;"><h3 style="margin:0 0 8px;font-size:.95rem;">AI 분석 요약</h3><p style="font-size:.85rem;color:#e6edf3;line-height:1.6;white-space:pre-line;">{ai_report[:500]}{"..." if len(ai_report)>500 else ""}</p></div>' if ai_report else ''}

<!-- 대시보드 링크 -->
<div style="text-align:center;margin:20px 0;">
  <a href="https://jaewon-im.github.io/political-theme-stock/"
     style="display:inline-block;padding:12px 28px;background:#58a6ff;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;font-size:.95rem;">
    전체 대시보드 보기
  </a>
</div>

<!-- 푸터 -->
<div style="text-align:center;padding:20px 0;border-top:1px solid #30363d;color:#8b949e;font-size:.75rem;">
  <p>PollStock 폴스탁 — 정치 테마주 AI 분석</p>
  <p>이 메일은 구독 신청에 의해 발송되었습니다.</p>
  <p><a href="#" style="color:#58a6ff;">구독 해지</a></p>
</div>

</div>
</body>
</html>"""
        return html

    def send_newsletter(self, data: dict, smtp_host: str = "", smtp_port: int = 587,
                        smtp_user: str = "", smtp_pass: str = "",
                        from_email: str = "", from_name: str = "PollStock 폴스탁") -> dict:
        """뉴스레터 발송"""
        subscribers = self.get_active_subscribers()
        if not subscribers:
            return {"sent": 0, "error": "구독자 없음"}
        if not smtp_host or not smtp_user:
            return {"sent": 0, "error": "SMTP 설정 없음"}

        html = self.generate_html_report(data)
        date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
        subject = f"[PollStock] D-{data.get('election_phase', {}).get('days_until_election', '?')} 정치 테마주 리포트 ({date})"

        sent = 0
        errors = []
        try:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
            server.login(smtp_user, smtp_pass)

            for sub in subscribers:
                try:
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"] = f"{from_name} <{from_email}>"
                    msg["To"] = sub["email"]
                    msg.attach(MIMEText(html, "html", "utf-8"))
                    server.sendmail(from_email, sub["email"], msg.as_string())
                    sent += 1
                except Exception as e:
                    errors.append(f"{sub['email']}: {e}")

            server.quit()
        except Exception as e:
            return {"sent": sent, "error": str(e), "detail_errors": errors}

        # 발송 기록
        log_file = self.data_dir / "send_log.json"
        log = []
        if log_file.exists():
            try:
                with open(log_file, encoding="utf-8") as f:
                    log = json.load(f)
            except Exception:
                pass
        log.append({
            "date": date,
            "sent": sent,
            "total_subscribers": len(subscribers),
            "errors": errors,
            "sent_at": datetime.now().isoformat(),
        })
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(log[-100:], f, ensure_ascii=False, indent=2)

        return {"sent": sent, "total": len(subscribers), "errors": errors}
