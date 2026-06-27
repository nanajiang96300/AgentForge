"""
Internationalization (i18n) support for notification module.

Extracted from notify.py. Zero project imports — only stdlib.
"""

# ── i18n ──────────────────────────────────────────────────────────────
_LANG = "zh"

_I18N = {
    "zh": {
        "started": "▶️ 步骤开始",
        "completed": "✅ 步骤完成",
        "failed": "❌ 步骤失败",
        "escalated": "🚨 需人工介入",
        "rejected": "🔄 测试不通过，开发修复中",
        "field.task": "任务",
        "field.step": "步骤",
        "field.type": "类型",
        "field.project": "项目",
        "field.status": "状态",
        "field.requirements": "需求描述",
        "field.root_cause": "根因分析",
        "field.complexity": "复杂度",
        "field.subtasks": "子任务",
        "field.branch": "分支",
        "field.commit": "提交",
        "field.files": "修改文件",
        "field.verdict": "判定",
        "field.summary": "测试摘要",
        "field.error": "错误",
        "field.rejections": "打回次数",
        "field.rejected_by": "打回步骤",
        "field.rejection": "打回",
        "field.reason": "原因",
        "field.next": "下一步",
        "field.action": "处理方式",
        "field.reason_short": "原因",
        "rejection.next": "开发 Agent 将重新实现并通过测试验证（第 {count}/3 次）",
        "escalation.action": "`multiagent conductor retry <task_id>` 重试\n`multiagent conductor reject <task_id>` 放弃",
        "status.running": "运行中",
        "status.completed": "已完成",
        "status.failed": "失败",
        "status.escalated": "已升级",
        "status.rejected": "已打回",
        "status.crashed": "崩溃",
        "status.timed_out": "超时",
        "footer": "AgentForge",
    },
    "en": {
        "started": "▶️ Step Started",
        "completed": "✅ Step Completed",
        "failed": "❌ Step Failed",
        "escalated": "🚨 Escalation — Human Action Required",
        "rejected": "🔄 Rejected — Test found issues, Dev fixing",
        "field.task": "Task",
        "field.step": "Step",
        "field.type": "Type",
        "field.project": "Project",
        "field.status": "Status",
        "field.requirements": "Requirements",
        "field.root_cause": "Root Cause",
        "field.complexity": "Complexity",
        "field.subtasks": "Subtasks",
        "field.branch": "Branch",
        "field.commit": "Commit",
        "field.files": "Files Changed",
        "field.verdict": "Verdict",
        "field.summary": "Test Summary",
        "field.error": "Error",
        "field.rejections": "Rejections",
        "field.rejected_by": "Rejected by",
        "field.rejection": "Rejection",
        "field.reason": "Reason",
        "field.next": "Next Action",
        "field.action": "Action",
        "field.reason_short": "Reason",
        "rejection.next": "Dev agent will re-implement and Test will re-verify (attempt {count}/3)",
        "escalation.action": "`multiagent conductor retry <task_id>` to retry\n`multiagent conductor reject <task_id>` to abandon",
        "status.running": "running",
        "status.completed": "completed",
        "status.failed": "failed",
        "status.escalated": "escalated",
        "status.rejected": "rejected",
        "status.crashed": "crashed",
        "status.timed_out": "timed out",
        "footer": "AgentForge",
    },
}


def set_language(lang: str) -> None:
    """切换通知语言。'zh'=中文，'en'=English。"""
    global _LANG
    if lang in _I18N:
        _LANG = lang


def get_language() -> str:
    return _LANG


def t(key: str, **kwargs) -> str:
    """获取翻译文本，支持格式化参数。"""
    text = _I18N.get(_LANG, _I18N["zh"]).get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text


def _status_label(status_str: str) -> str:
    """将内部状态值翻译为显示文本。"""
    key = f"status.{status_str}"
    return _I18N.get(_LANG, _I18N["zh"]).get(key, status_str)


# ── Colors ────────────────────────────────────────────────────────────

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

EVENT_LABELS = {k: v for k, v in _I18N["zh"].items() if k in EVENT_COLORS}
