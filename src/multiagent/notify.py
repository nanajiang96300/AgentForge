"""
Discord Webhook 通知模块 — 零 Token 消耗的实时推送。
"""

import json
import logging
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

_log = logging.getLogger("multiagent.notify")

COLOR_STARTED = 0x3498DB
COLOR_COMPLETED = 0x2ECC71
COLOR_FAILED = 0xE74C3C
COLOR_ESCALATED = 0xE67E22
COLOR_REJECTED = 0xF39C12

EVENT_COLORS = {
    "started": COLOR_STARTED, "completed": COLOR_COMPLETED,
    "failed": COLOR_FAILED, "escalated": COLOR_ESCALATED,
    "rejected": COLOR_REJECTED,
}

EVENT_LABELS = {
    "started": "▶️ Step Started", "completed": "✅ Step Completed",
    "failed": "❌ Step Failed", "escalated": "🚨 Escalation",
    "rejected": "🔄 Rejected — Test found issues",
}

_notifier_registry: dict[str, type] = {}

def register_notifier(name, factory):
    _notifier_registry[name] = factory

def get_notifier(name, **kwargs):
    factory = _notifier_registry.get(name)
    return factory(**kwargs) if factory else None

def list_notifiers():
    return list(_notifier_registry.keys())

register_notifier("discord-webhook", lambda **kw: DiscordNotifier(kw.get("webhook_url", "")))
register_notifier("discord-channel", lambda **kw: DiscordChannelNotifier(
    kw.get("bot_token", ""), kw.get("channel_id", "")))


def _build_embed(event, task_id, project_name, task_dict):
    color = EVENT_COLORS.get(event, COLOR_STARTED)
    title = EVENT_LABELS.get(event, event.title())
    custom_fields = task_dict.get("_embed_fields", []) if isinstance(task_dict, dict) else []
    fields = list(custom_fields) if custom_fields else [
        {"name": "Task", "value": f"`{task_id}`", "inline": True},
        {"name": "Project", "value": project_name, "inline": True},
        {"name": "Type", "value": task_dict.get("type", "?"), "inline": True},
    ]
    if isinstance(task_dict, dict):
        if task_dict.get("error"):
            fields.append({"name": "Error", "value": str(task_dict["error"])[:1024], "inline": False})
        rc = task_dict.get("rejection_count", 0)
        if rc > 0:
            fields.append({"name": "Rejections", "value": str(rc), "inline": True})
    return {
        "title": title, "color": color, "fields": fields,
        "footer": {"text": f"AgentForge • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"},
    }


class DiscordNotifier:
    def __init__(self, webhook_url, username="AgentForge"):
        self.webhook_url = webhook_url
        self.username = username

    def __call__(self, event, task_id, project_name, task_dict):
        embed = _build_embed(event, task_id, project_name, task_dict)
        payload = {"username": self.username, "embeds": [embed]}
        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(self.webhook_url, data=data,
                          headers={"Content-Type": "application/json", "User-Agent": "AgentForge/0.7.0"})
            with urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 204):
                    _log.warning("Discord webhook returned %d", resp.status)
        except Exception as e:
            _log.error("Discord webhook failed: %s", e)


class DiscordChannelNotifier:
    def __init__(self, bot_token, channel_id):
        self.bot_token = bot_token
        self.channel_id = channel_id

    def __call__(self, event, task_id, project_name, task_dict):
        embed = _build_embed(event, task_id, project_name, task_dict)
        payload = json.dumps({"embeds": [embed]}).encode("utf-8")
        try:
            req = Request(
                f"https://discord.com/api/v10/channels/{self.channel_id}/messages",
                data=payload,
                headers={"Authorization": f"Bot {self.bot_token}",
                         "Content-Type": "application/json", "User-Agent": "AgentForge/0.7.0-dev"},
            )
            with urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201, 204):
                    _log.info("Discord notification sent: %s -> %s", event, task_id)
                else:
                    body = resp.read().decode("utf-8", errors="replace")[:200]
                    _log.warning("Discord channel message returned %d: %s", resp.status, body)
        except Exception as e:
            _log.error("Discord channel message failed: %s", e)


def _load_claudeclaw_config():
    try:
        import json as _j
        from pathlib import Path as _P
        import os as _os
        search_paths = [
            _P(".claude/claudeclaw/settings.json"),
            _P(_os.environ.get("HOME", "/root")) / ".claude" / "claudeclaw" / "settings.json",
        ]
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
                return _j.loads(cfg_path.read_text())
    except Exception:
        pass
    return {}


def create_notifier(webhook_url=None, discord_webhook_url=None,
                    discord_channel_id=None, bot_token=None):
    notifiers = []
    url = webhook_url or discord_webhook_url
    if url:
        _log.info("Notification: Discord webhook configured")
        notifiers.append(DiscordNotifier(url))
        return notifiers
    token = bot_token
    channel = discord_channel_id
    if not token or not channel:
        cc = _load_claudeclaw_config()
        if not token:
            token = cc.get("discord", {}).get("token", "")
        if not channel:
            channels = cc.get("discord", {}).get("listenChannels", [])
            if channels:
                channel = str(channels[0])
    if token and channel:
        _log.info("Notification: Discord channel bot configured (channel=%s)", channel)
        notifiers.append(DiscordChannelNotifier(token, channel))
    return notifiers


# ── Rich StepHook adapter ──

class NotifierStepHook:
    """Adapts notifiers to StepHook with rich DB-backed embeds."""

    def __init__(self, notifiers, db=None, project_name="AgentForge"):
        self._notifiers = notifiers
        self._project = project_name
        self._db = db

    def _task_ctx(self, task_id):
        if not self._db:
            return {}
        try:
            t = self._db.get_task(task_id)
            if not t:
                return {}
            ctx = t.get("context", {})
            if isinstance(ctx, str):
                try: ctx = json.loads(ctx)
                except: ctx = {}
            return {"task_type": t.get("type", "?"), "requirements": ctx.get("requirements_text", "")[:300]}
        except:
            return {}

    def _last_output(self, task_id, step_id):
        if not self._db:
            return {}
        try:
            rows = self._db.conn.execute(
                "SELECT output FROM step_results WHERE task_id=? AND step_id=? AND status='completed' ORDER BY id DESC LIMIT 1",
                (task_id, step_id)).fetchall()
            if rows and rows[0][0]:
                raw = rows[0][0]
                if isinstance(raw, str):
                    return json.loads(raw) if raw.startswith("{") else {}
                elif isinstance(raw, dict):
                    return raw
        except:
            pass
        return {}

    def before_step(self, task_id, step_id):
        tc = self._task_ctx(task_id)
        fields = [
            {"name": "Task", "value": f"`{task_id}`", "inline": True},
            {"name": "Step", "value": step_id, "inline": True},
            {"name": "Type", "value": tc.get("task_type", "?"), "inline": True},
        ]
        if tc.get("requirements"):
            fields.append({"name": "Requirements", "value": tc["requirements"][:200], "inline": False})
        for n in self._notifiers:
            try: n("started", task_id, self._project, {"status": "running", "type": f"step:{step_id}", "_embed_fields": fields})
            except: pass

    def after_step(self, task_id, step_id, result=None):
        output = getattr(result, "output", {}) or {}
        status = getattr(result, "status", None)
        status_str = str(status.value) if hasattr(status, "value") else str(status or "unknown")

        fields = [
            {"name": "Task", "value": f"`{task_id}`", "inline": True},
            {"name": "Step", "value": step_id, "inline": True},
            {"name": "Status", "value": status_str, "inline": True},
        ]
        # Rich per-agent fields
        if "pm_analyze" in step_id:
            if output.get("root_cause"):
                fields.append({"name": "Root Cause", "value": str(output["root_cause"])[:200], "inline": False})
            if output.get("complexity"):
                fields.append({"name": "Complexity", "value": str(output["complexity"]), "inline": True})
            bd = output.get("task_breakdown", [])
            if bd:
                fields.append({"name": f"Subtasks ({len(bd)})", "value": "\n".join(f"• {t}" for t in bd[:5])[:300], "inline": False})
        elif "dev" in step_id:
            if output.get("branch_name"):
                fields.append({"name": "Branch", "value": str(output["branch_name"]), "inline": True})
            if output.get("commit_hash"):
                fields.append({"name": "Commit", "value": f"`{str(output['commit_hash'])[:8]}`", "inline": True})
            files = output.get("files_changed", [])
            if files:
                fields.append({"name": "Files", "value": "\n".join(f"`{f}`" for f in files[:5]), "inline": False})
        elif "test" in step_id:
            verdict = str(output.get("verdict", "?")).upper()
            fields.append({"name": "Verdict", "value": verdict, "inline": True})
            if output.get("test_summary"):
                fields.append({"name": "Summary", "value": str(output["test_summary"])[:300], "inline": False})
        if hasattr(result, "error") and result.error:
            fields.append({"name": "Error", "value": str(result.error)[:500], "inline": False})

        for n in self._notifiers:
            try:
                evt = "completed" if status_str == "completed" else "failed"
                n(evt, task_id, self._project, {"status": status_str, "type": f"step:{step_id}", "_embed_fields": fields})
            except: pass

    def on_rejection(self, task_id, step_id, count):
        test_out = self._last_output(task_id, step_id)
        reason = test_out.get("reason", test_out.get("test_summary", "Issues found"))[:300]
        fields = [
            {"name": "Task", "value": f"`{task_id}`", "inline": True},
            {"name": "Rejected by", "value": step_id, "inline": True},
            {"name": "Rejection", "value": f"{count}/3", "inline": True},
            {"name": "Reason", "value": reason, "inline": False},
            {"name": "Next", "value": f"Dev will re-implement (attempt {count+1}/3)", "inline": False},
        ]
        for n in self._notifiers:
            try: n("rejected", task_id, self._project, {"status": "rejected", "type": f"step:{step_id}", "rejection_count": count, "_embed_fields": fields})
            except: pass

    def on_escalation(self, task_id, step_id, reason):
        fields = [
            {"name": "Task", "value": f"`{task_id}`", "inline": True},
            {"name": "Step", "value": step_id, "inline": True},
            {"name": "Reason", "value": str(reason)[:500], "inline": False},
            {"name": "Action", "value": "`multiagent conductor retry <task_id>` or `reject`", "inline": False},
        ]
        for n in self._notifiers:
            try: n("escalated", task_id, self._project, {"status": "escalated", "type": f"step:{step_id}", "error": str(reason), "_embed_fields": fields})
            except: pass
