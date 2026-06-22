"""
Discord Webhook 通知模块 — 零 Token 消耗的实时推送。

Escalation / Pipeline 完成时通过 Discord Webhook 直接 POST embed 消息，
不经过任何 LLM，不消耗 Token。
"""

import json
import logging
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

_log = logging.getLogger("multiagent.notify")

# Discord embed color constants
COLOR_STARTED = 0x3498DB    # 蓝色
COLOR_COMPLETED = 0x2ECC71  # 绿色
COLOR_FAILED = 0xE74C3C     # 红色
COLOR_ESCALATED = 0xE67E22  # 橙色

EVENT_COLORS = {
    "started": COLOR_STARTED,
    "completed": COLOR_COMPLETED,
    "failed": COLOR_FAILED,
    "escalated": COLOR_ESCALATED,
}

EVENT_LABELS = {
    "started": "🔄 Pipeline Started",
    "completed": "✅ Pipeline Completed",
    "failed": "❌ Pipeline Failed",
    "escalated": "🚨 Escalation — Human Action Required",
}


class DiscordNotifier:
    """Discord Webhook 通知器"""

    def __init__(self, webhook_url: str, username: str = "AgentForge"):
        self.webhook_url = webhook_url
        self.username = username

    def __call__(self, event: str, task_id: str, project_name: str,
                 task_dict: dict):
        """发送 Discord embed 通知"""
        color = EVENT_COLORS.get(event, COLOR_STARTED)
        title = EVENT_LABELS.get(event, event.title())

        status = task_dict.get("status", "unknown") if isinstance(task_dict, dict) else "unknown"
        task_type = task_dict.get("type", "?") if isinstance(task_dict, dict) else "?"

        embed = {
            "title": title,
            "color": color,
            "fields": [
                {"name": "Task", "value": f"`{task_id}`", "inline": True},
                {"name": "Project", "value": project_name, "inline": True},
                {"name": "Type", "value": task_type, "inline": True},
            ],
            "footer": {"text": f"AgentForge • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"},
        }

        # Add relevant details based on event type
        if isinstance(task_dict, dict):
            if task_dict.get("error"):
                embed["fields"].append(
                    {"name": "Error", "value": str(task_dict["error"])[:1024], "inline": False}
                )
            rejection_count = task_dict.get("rejection_count", 0)
            if rejection_count > 0:
                embed["fields"].append(
                    {"name": "Rejections", "value": str(rejection_count), "inline": True}
                )

        payload = {
            "username": self.username,
            "embeds": [embed],
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(
                self.webhook_url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "AgentForge/0.6.0",
                },
            )
            with urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 204):
                    _log.warning("Discord webhook returned %d", resp.status)
        except URLError as e:
            _log.error("Discord webhook failed: %s", e)
        except Exception:
            _log.exception("Unexpected Discord webhook error")


def create_notifier(webhook_url: str = None,
                    discord_webhook_url: str = None) -> list:
    """工厂函数：根据配置创建 notifier 列表"""
    notifiers = []
    url = webhook_url or discord_webhook_url
    if url:
        notifiers.append(DiscordNotifier(url))
    return notifiers
