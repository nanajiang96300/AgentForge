"""
PM Auto-Discovery — Phase 5 Step 5

从 Git remote (GitHub Issues) 自动发现新需求，转化为 AgentForge 任务。
与 Conductor 集成，作为 poll 周期的一部分运行。
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

_log = logging.getLogger("multiagent.pm_discover")


def discover_github_issues(
    repo_path: Path = None,
    labels: list[str] = None,
    state: str = "open",
    limit: int = 10,
) -> list[dict]:
    """通过 gh CLI 发现 GitHub Issues。

    返回尚未被 AgentForge 处理的 issue 列表。
    已处理的 issue 会有 `agentforge:submitted` label。
    """
    if repo_path is None:
        repo_path = Path.cwd()

    labels = labels or ["bug", "feature", "enhancement"]
    label_filter = ",".join(labels)

    try:
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo", str(_get_github_repo(repo_path)),
                "--label", label_filter,
                "--state", state,
                "--limit", str(limit),
                "--json", "number,title,body,labels,createdAt",
            ],
            capture_output=True, text=True, timeout=15,
            cwd=str(repo_path),
        )
        if result.returncode != 0:
            _log.debug("gh CLI error: %s", result.stderr.strip())
            return []

        issues = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        _log.debug("GitHub discovery unavailable: %s", e)
        return []

    # Filter out already-processed issues
    new_issues = []
    for issue in issues:
        label_names = [l["name"] for l in issue.get("labels", [])]
        if "agentforge:submitted" in label_names or "agentforge:done" in label_names:
            continue
        new_issues.append({
            "number": issue["number"],
            "title": issue["title"],
            "body": issue.get("body", ""),
            "labels": label_names,
            "created_at": issue.get("createdAt", ""),
        })

    return new_issues


def submit_issue_as_task(issue: dict, db, workflow_id: str = "pm-dev-test-loop") -> Optional[str]:
    """将 GitHub Issue 转化为 AgentForge 任务。返回 task_id。"""
    import uuid
    from .db import Task, now_iso

    # Map GitHub labels to AgentForge task types
    _LABEL_TYPE_MAP = {
        "bug": "bug",
        "fix": "bug",
        "debug": "debug",
        "feature": "feature",
        "enhancement": "enhancement",
        "docs": "docs",
        "documentation": "docs",
        "refactor": "enhancement",
        "performance": "enhancement",
    }
    issue_type = "feature"  # default
    for label in issue.get("labels", []):
        mapped = _LABEL_TYPE_MAP.get(label.lower(), "")
        if mapped:
            issue_type = mapped
            break

    # Build requirements text
    requirements = f"# {issue['title']}\n\n{issue['body'] or 'No description.'}"
    requirements += f"\n\n---\nSource: GitHub Issue #{issue['number']}"

    task_id = f"task-gh-{issue['number']}-{uuid.uuid4().hex[:6]}"

    # Check dedup
    existing = db.conn.execute(
        "SELECT id FROM tasks WHERE dedup_key = ?", (f"gh-{issue['number']}",)
    ).fetchone()
    if existing:
        return None

    task = Task(
        id=task_id, type=issue_type, source="github",
        workflow_id=workflow_id, current_step="pm_analyze",
        dedup_key=f"gh-{issue['number']}",
        context={
            "requirements_text": requirements,
            "source_file": f"github:issue#{issue['number']}",
            "issue_number": issue["number"],
            "issue_title": issue["title"],
        },
        created_at=now_iso(),
    )

    if db.insert_task(task):
        _log.info("Auto-submitted GH issue #%d → %s", issue["number"], task_id)
        return task_id

    return None


def mark_issue_submitted(repo_path: Path, issue_number: int):
    """给 GitHub Issue 添加 agentforge:submitted 标签"""
    try:
        subprocess.run(
            ["gh", "issue", "edit", str(issue_number),
             "--add-label", "agentforge:submitted",
             "--repo", str(_get_github_repo(repo_path))],
            capture_output=True, timeout=10,
            cwd=str(repo_path),
        )
    except Exception:
        pass


def _get_github_repo(repo_path: Path) -> str:
    """从 git remote 获取 GitHub repo (owner/name)"""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
            cwd=str(repo_path),
        )
        url = result.stdout.strip()
        # Extract owner/repo from various URL formats
        for prefix in ["git@github.com:", "https://github.com/"]:
            if prefix in url:
                path = url.split(prefix)[-1].replace(".git", "")
                return path
    except Exception:
        pass
    return ""
