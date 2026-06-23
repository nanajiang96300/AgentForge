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

# ── Notifier Registry (P1 fix) ──

_notifier_registry: dict[str, type] = {}


def register_notifier(name: str, factory):
    """Register a notification channel factory."""
    _notifier_registry[name] = factory


def get_notifier(name: str, **kwargs):
    """Create a notifier by name from registry."""
    factory = _notifier_registry.get(name)
    if factory:
        return factory(**kwargs)
    return None


def list_notifiers() -> list[str]:
    """List registered notification channels."""
    return list(_notifier_registry.keys())


# Register built-in notifiers
register_notifier("discord-webhook", lambda **kw: DiscordNotifier(kw.get("webhook_url", "")))
register_notifier("discord-channel", lambda **kw: DiscordChannelNotifier(
    kw.get("bot_token", ""), kw.get("channel_id", "")))


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


class DiscordChannelNotifier:
    """通过 Bot Token 直接发送消息到 Discord 频道。

    使用 ClaudeClaw 的 Bot Token，消息以 Bot 身份发送。
    """

    def __init__(self, bot_token: str, channel_id: str):
        self.bot_token = bot_token
        self.channel_id = channel_id

    def __call__(self, event: str, task_id: str, project_name: str,
                 task_dict: dict):
        """发送消息到 Discord 频道"""
        color = EVENT_COLORS.get(event, COLOR_STARTED)
        title = EVENT_LABELS.get(event, event.title())
        status = task_dict.get("status", "?") if isinstance(task_dict, dict) else "?"
        task_type = task_dict.get("type", "?") if isinstance(task_dict, dict) else "?"

        # Build embed
        fields = [
            {"name": "Task", "value": f"`{task_id}`", "inline": True},
            {"name": "Project", "value": project_name, "inline": True},
            {"name": "Type", "value": task_type, "inline": True},
        ]
        if isinstance(task_dict, dict):
            if task_dict.get("error"):
                fields.append({"name": "Error", "value": str(task_dict["error"])[:1024]})
            rc = task_dict.get("rejection_count", 0)
            if rc > 0:
                fields.append({"name": "Rejections", "value": str(rc), "inline": True})

        payload = json.dumps({
            "embeds": [{
                "title": title,
                "color": color,
                "fields": fields,
                "footer": {"text": f"AgentForge • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"},
            }]
        }).encode("utf-8")

        try:
            req = Request(
                f"https://discord.com/api/v10/channels/{self.channel_id}/messages",
                data=payload,
                headers={
                    "Authorization": f"Bot {self.bot_token}",
                    "Content-Type": "application/json",
                    "User-Agent": "AgentForge/0.6.0-dev",
                },
            )
            with urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201, 204):
                    _log.info("Discord notification sent: %s → %s", event, task_id)
                else:
                    body = resp.read().decode("utf-8", errors="replace")[:200]
                    _log.warning("Discord channel message returned %d: %s", resp.status, body)
        except Exception as e:
            _log.error("Discord channel message failed for %s/%s: %s", event, task_id, e)


def _load_claudeclaw_config() -> dict:
    """尝试加载 ClaudeClaw settings.json 获取 bot token 和 channel 信息。
    搜索多个可能路径以兼容 daemon 子进程的工作目录变化。"""
    try:
        import json as _j
        from pathlib import Path as _P
        import os as _os
        # Search multiple paths: relative CWD, and via env/known locations
        search_paths = [
            _P(".claude/claudeclaw/settings.json"),
            _P(_os.environ.get("HOME", "/root")) / ".claude" / "claudeclaw" / "settings.json",
        ]
        # Also search from project root if detectable
        try:
            for p in [_P.cwd()] + list(_P.cwd().parents):
                candidate = p / ".claude" / "claudeclaw" / "settings.json"
                if candidate.exists():
                    search_paths.insert(0, candidate)
                    break
        except Exception:
            pass
        for cfg_path in search_paths:
            if cfg_path.exists():
                _log.debug("Loaded ClaudeClaw config from %s", cfg_path)
                return _j.loads(cfg_path.read_text())
    except Exception:
        pass
    return {}


def create_notifier(webhook_url: str = None,
                    discord_webhook_url: str = None,
                    discord_channel_id: str = None,
                    bot_token: str = None) -> list:
    """工厂函数：根据配置创建 notifier 列表。

    优先级: webhook URL > bot token + channel ID > ClaudeClaw config
    """
    notifiers = []
    url = webhook_url or discord_webhook_url
    if url:
        _log.info("Notification: Discord webhook configured")
        notifiers.append(DiscordNotifier(url))
        return notifiers

    # Try bot token + channel
    token = bot_token
    channel = discord_channel_id

    # Fallback to ClaudeClaw config
    if not token or not channel:
        cc = _load_claudeclaw_config()
        if not token:
            token = cc.get("discord", {}).get("token", "")
        if not channel:
            channels = cc.get("discord", {}).get("listenChannels", [])
            if channels:
                channel = str(channels[0])
            # NOTE: We intentionally do NOT fall back to listenGuilds.
            # A guild ID is not a valid channel ID for sending messages.

    if token and channel:
        _log.info("Notification: Discord channel bot configured (channel=%s)", channel)
        notifiers.append(DiscordChannelNotifier(token, channel))
    else:
        if token and not channel:
            _log.warning("Notification: Bot token found but no channel ID configured. "
                         "Add 'listenChannels' to .claude/claudeclaw/settings.json")
        elif not token:
            _log.info("Notification: No Discord configuration found, notifications disabled")

    return notifiers


# ── StepHook adapter for automatic pipeline progress notifications ──

class NotifierStepHook:
    """Adapts a list of notifier callables to the StepHook interface.

    Register with orchestrator.register_hook() to auto-send Discord/Slack
    messages on every step lifecycle event — no manual trigger needed.
    """

    def __init__(self, notifiers: list, project_name: str = "AgentForge"):
        self._notifiers = notifiers
        self._project = project_name

    def before_step(self, task_id: str, step_id: str) -> None:
        for n in self._notifiers:
            try:
                n("started", task_id, self._project,
                  {"status": "running", "type": f"step:{step_id}"})
            except Exception:
                pass

    def after_step(self, task_id: str, step_id: str, result=None) -> None:
        output = getattr(result, "output", {}) or {}
        status = getattr(result, "status", None)
        status_str = str(status.value) if hasattr(status, "value") else str(status or "unknown")
        task_dict = {"status": status_str, "type": f"step:{step_id}"}
        if hasattr(result, "error") and result.error:
            task_dict["error"] = str(result.error)[:500]
        for n in self._notifiers:
            try:
                event = "completed" if status_str == "completed" else "failed"
                n(event, task_id, self._project, task_dict)
            except Exception:
                pass

    def on_rejection(self, task_id: str, step_id: str, count: int) -> None:
        for n in self._notifiers:
            try:
                n("escalated", task_id, self._project,
                  {"status": "rejected", "type": f"step:{step_id}",
                   "rejection_count": count})
            except Exception:
                pass

    def on_escalation(self, task_id: str, step_id: str, reason: str) -> None:
        for n in self._notifiers:
            try:
                n("escalated", task_id, self._project,
                  {"status": "escalated", "type": f"step:{step_id}",
                   "error": reason})
            except Exception:
                pass
