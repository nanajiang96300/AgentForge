"""
Discord Webhook 通知模块 — 零 Token 消耗的实时推送。

支持中英文双语，默认中文。开源社区可通过 set_language('en') 切换。
"""

import json
import time as _time
import logging
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

_log = logging.getLogger("multiagent.notify")

from .notify_i18n import (t, set_language, get_language, _status_label,
                          EVENT_COLORS, COLOR_STARTED, COLOR_COMPLETED,
                          COLOR_FAILED, COLOR_ESCALATED, COLOR_REJECTED)

# ── Retry ──────────────────────────────────────────────────────────────

_MAX_RETRIES = 3
_RETRY_BACKOFF = [1, 4, 10]  # seconds between retries


def _send_with_retry(req, max_retries=_MAX_RETRIES, logger=None):
    """Send a urllib Request with exponential backoff retry. Returns HTTP status or None."""
    log = logger or _log
    last_error = None
    for attempt in range(max_retries):
        try:
            with urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 201, 204):
                    body = resp.read().decode("utf-8", errors="replace")[:200]
                    log.warning("Discord returned %d: %s (attempt %d/%d)",
                                resp.status, body, attempt + 1, max_retries)
                    if resp.status == 429:
                        # Rate limited — back off longer
                        _time.sleep(_RETRY_BACKOFF[attempt] * 2 if attempt < len(_RETRY_BACKOFF) else 30)
                        continue
                return resp.status
        except (URLError, OSError) as e:
            last_error = e
            log.warning("Discord send attempt %d/%d failed: %s",
                        attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                delay = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 10
                _time.sleep(delay)
        except Exception as e:
            last_error = e
            break  # Non-retryable
    log.error("Discord send failed after %d attempts: %s", max_retries, last_error)
    return None


# ── Field Limits ───────────────────────────────────────────────────────

_FIELD_LIMIT = 1024
_TOTAL_LIMIT = 6000  # Discord embed total field value character limit


def _truncate_field(text, limit=_FIELD_LIMIT):
    """Truncate text to limit, appending '...[truncated]' indicator when needed."""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit - 20] + "...[truncated]"


def _validate_embed_total(embed):
    """Ensure total embed field values don't exceed Discord's 6000-char limit."""
    fields = embed.get("fields", [])
    total = sum(len(str(f.get("value", ""))) for f in fields)
    if total > _TOTAL_LIMIT:
        sorted_fields = sorted(fields, key=lambda f: -len(str(f.get("value", ""))))
        overage = total - _TOTAL_LIMIT
        for field in sorted_fields:
            if overage <= 0:
                break
            val = str(field.get("value", ""))
            trimmed = val[:max(50, len(val) - overage - 20)] + "...[truncated]"
            overage -= len(val) - len(trimmed)
            field["value"] = trimmed
    return embed

# ── Notifier Registry ─────────────────────────────────────────────────

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


# ── Embed Builder ─────────────────────────────────────────────────────

def _build_embed(event, task_id, project_name, task_dict):
    color = EVENT_COLORS.get(event, COLOR_STARTED)
    title = t(event)

    custom_fields = task_dict.get("_embed_fields", []) if isinstance(task_dict, dict) else []
    if custom_fields:
        fields = list(custom_fields)
    else:
        fields = [
            {"name": t("field.task"), "value": f"`{task_id}`", "inline": True},
            {"name": t("field.project"), "value": project_name, "inline": True},
            {"name": t("field.type"), "value": task_dict.get("type", "?"), "inline": True},
        ]

    if isinstance(task_dict, dict):
        if task_dict.get("error"):
            fields.append({"name": t("field.error"),
                          "value": _truncate_field(str(task_dict["error"])), "inline": False})
        rc = task_dict.get("rejection_count", 0)
        if rc > 0:
            fields.append({"name": t("field.rejections"), "value": str(rc), "inline": True})

    embed = {
        "title": title, "color": color, "fields": fields,
        "footer": {"text": f"{t('footer')} • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"},
    }
    return _validate_embed_total(embed)


# ── Notifiers ─────────────────────────────────────────────────────────

class DiscordNotifier:
    def __init__(self, webhook_url, username="AgentForge"):
        self.webhook_url = webhook_url
        self.username = username

    def __call__(self, event, task_id, project_name, task_dict):
        embed = _build_embed(event, task_id, project_name, task_dict)
        payload = {"username": self.username, "embeds": [embed]}
        data = json.dumps(payload).encode("utf-8")
        req = Request(self.webhook_url, data=data,
                      headers={"Content-Type": "application/json", "User-Agent": "AgentForge/0.7.0"})
        status = _send_with_retry(req, logger=_log)
        if status not in (200, 204) and status is not None:
            _log.warning("Discord webhook returned %d", status)


class DiscordChannelNotifier:
    def __init__(self, bot_token, channel_id):
        self.bot_token = bot_token
        self.channel_id = channel_id

    def __call__(self, event, task_id, project_name, task_dict):
        embed = _build_embed(event, task_id, project_name, task_dict)
        payload = json.dumps({"embeds": [embed]}).encode("utf-8")
        req = Request(
            f"https://discord.com/api/v10/channels/{self.channel_id}/messages",
            data=payload,
            headers={"Authorization": f"Bot {self.bot_token}",
                     "Content-Type": "application/json", "User-Agent": "AgentForge/0.7.0-dev"},
        )
        status = _send_with_retry(req, logger=_log)
        if status in (200, 201, 204):
            _log.info("Discord notification sent: %s -> %s", event, task_id)


# ── Config Loader ─────────────────────────────────────────────────────

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
                    discord_channel_id=None, bot_token=None, language=None):
    """工厂函数。language='zh'|'en' 设置通知语言。"""
    if language:
        set_language(language)
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


# ── Rich StepHook Adapter ─────────────────────────────────────────────

class NotifierStepHook:
    """将通知器适配为 StepHook，自动从 DB 读取上下文生成富文本 embed。"""

    def __init__(self, notifiers, db=None, project_name="AgentForge",
                 rejection_cooldown_seconds: int = 30):
        self._notifiers = notifiers
        self._project = project_name
        self._db = db
        self._rejection_cooldown_seconds = rejection_cooldown_seconds
        self._last_rejection_time: dict[str, float] = {}  # task_id -> timestamp

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
            rows = self._db.execute(
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
            {"name": t("field.task"), "value": f"`{task_id}`", "inline": True},
            {"name": t("field.step"), "value": step_id, "inline": True},
            {"name": t("field.type"), "value": tc.get("task_type", "?"), "inline": True},
        ]
        if tc.get("requirements"):
            fields.append({"name": t("field.requirements"),
                          "value": _truncate_field(tc["requirements"], 200), "inline": False})
        for n in self._notifiers:
            try: n("started", task_id, self._project, {"status": "running", "type": f"step:{step_id}", "_embed_fields": fields})
            except: pass

    def after_step(self, task_id, step_id, result=None):
        output = getattr(result, "output", {}) or {}
        status = getattr(result, "status", None)
        status_str = str(status.value) if hasattr(status, "value") else str(status or "unknown")

        fields = [
            {"name": t("field.task"), "value": f"`{task_id}`", "inline": True},
            {"name": t("field.step"), "value": step_id, "inline": True},
            {"name": t("field.status"), "value": _status_label(status_str), "inline": True},
        ]
        # Rich per-agent output
        if "pm_analyze" in step_id:
            if output.get("root_cause"):
                fields.append({"name": t("field.root_cause"),
                              "value": _truncate_field(str(output["root_cause"]), 200), "inline": False})
            if output.get("complexity"):
                fields.append({"name": t("field.complexity"),
                              "value": _truncate_field(str(output["complexity"])), "inline": True})
            bd = output.get("task_breakdown", [])
            if bd:
                fields.append({"name": t("field.subtasks") + f" ({len(bd)})",
                              "value": _truncate_field("\n".join(f"• {x}" for x in bd[:5]), 300), "inline": False})
        elif "dev" in step_id:
            if output.get("branch_name"):
                fields.append({"name": t("field.branch"),
                              "value": _truncate_field(str(output["branch_name"])), "inline": True})
            if output.get("commit_hash"):
                fields.append({"name": t("field.commit"),
                              "value": f"`{str(output['commit_hash'])[:8]}`", "inline": True})
            files = output.get("files_changed", [])
            if files:
                fields.append({"name": t("field.files"),
                              "value": _truncate_field("\n".join(f"`{f}`" for f in files[:5])), "inline": False})
        elif "test" in step_id:
            verdict = str(output.get("verdict", "?")).upper()
            fields.append({"name": t("field.verdict"), "value": verdict, "inline": True})
            if output.get("test_summary"):
                fields.append({"name": t("field.summary"),
                              "value": _truncate_field(str(output["test_summary"]), 300), "inline": False})
        if hasattr(result, "error") and result.error:
            fields.append({"name": t("field.error"),
                          "value": _truncate_field(str(result.error), 500), "inline": False})

        for n in self._notifiers:
            try:
                evt = "completed" if status_str == "completed" else "failed"
                n(evt, task_id, self._project, {"status": status_str, "type": f"step:{step_id}", "_embed_fields": fields})
            except: pass

    def on_rejection(self, task_id, step_id, count):
        # Cooldown guard: don't flood Discord with rapid rejections
        now = _time.time()
        last = self._last_rejection_time.get(task_id, 0)
        if now - last < self._rejection_cooldown_seconds:
            _log.debug("Rejection notification for %s debounced (cooldown %ds)",
                       task_id, self._rejection_cooldown_seconds)
            return
        self._last_rejection_time[task_id] = now

        test_out = self._last_output(task_id, step_id)
        reason = _truncate_field(
            test_out.get("reason", test_out.get("test_summary", "Issues found")), 300)
        fields = [
            {"name": t("field.task"), "value": f"`{task_id}`", "inline": True},
            {"name": t("field.rejected_by"), "value": step_id, "inline": True},
            {"name": t("field.rejection"), "value": f"{count}/3", "inline": True},
            {"name": t("field.reason"), "value": reason, "inline": False},
            {"name": t("field.next"), "value": t("rejection.next", count=count + 1), "inline": False},
        ]
        for n in self._notifiers:
            try: n("rejected", task_id, self._project, {"status": "rejected", "type": f"step:{step_id}", "rejection_count": count, "_embed_fields": fields})
            except: pass

    def on_escalation(self, task_id, step_id, reason):
        fields = [
            {"name": t("field.task"), "value": f"`{task_id}`", "inline": True},
            {"name": t("field.step"), "value": step_id, "inline": True},
            {"name": t("field.reason"), "value": _truncate_field(str(reason), 500), "inline": False},
            {"name": t("field.action"), "value": t("escalation.action"), "inline": False},
        ]
        for n in self._notifiers:
            try: n("escalated", task_id, self._project, {"status": "escalated", "type": f"step:{step_id}", "error": str(reason), "_embed_fields": fields})
            except: pass
