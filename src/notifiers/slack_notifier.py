"""
Slack 알림 발송
"""
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class SlackNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, text: str, blocks: list = None) -> bool:
        payload = {"text": text}
        if blocks:
            payload["blocks"] = blocks
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Slack 알림 실패: {e}")
            return False

    def send_signals(self, signals: list) -> bool:
        if not signals:
            return True

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"정치 테마주 시그널 | {datetime.now().strftime('%m/%d %H:%M')}",
                }
            },
            {"type": "divider"},
        ]

        for sig in signals[:8]:
            icon = "🟢" if sig.signal_type == "BUY_WATCH" else "🔴" if sig.signal_type == "SELL_WATCH" else "🟡"
            pol_str = f" | 관련: {', '.join(sig.politicians)}" if sig.politicians else ""
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{icon} *{sig.name}* ({sig.ticker}) — 강도 {sig.strength}/10\n"
                        f"  {' / '.join(sig.reasons)}{pol_str}"
                    ),
                }
            })

        return self.send(
            text=f"정치 테마주 시그널 {len(signals)}건",
            blocks=blocks,
        )

    def send_poll_update(self, poll_changes: dict) -> bool:
        if not poll_changes:
            return True
        lines = ["*[지지율 변동 감지]*"]
        for name, change in poll_changes.items():
            arrow = "▲" if change > 0 else "▼"
            lines.append(f"  {arrow} {name}: {change:+.1f}%p")
        return self.send("\n".join(lines))
