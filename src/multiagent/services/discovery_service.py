"""
DiscoveryService — automatic task discovery from GitHub Issues.

Extracted from conductor._discover_and_submit().
Depends on TaskRepository for persistence.
Uses logging, not print().
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

from ..persistence.task_repo import TaskRepository
from ..pm_discover import discover_github_issues, submit_issue_as_task, mark_issue_submitted

_log = logging.getLogger("multiagent.services.discovery")


class DiscoveryService:
    """Automatic task discovery from external sources (GitHub Issues via gh CLI)."""

    def __init__(self, repo_path: Optional[Path] = None):
        self.repo_path = repo_path or Path.cwd()

    @staticmethod
    def is_available() -> bool:
        """Check if the gh CLI is installed and accessible."""
        try:
            result = subprocess.run(
                ["gh", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
            return False

    def discover_and_submit(
        self,
        projects: list[dict],
        labels: Optional[list[str]] = None,
        task_repo_factory=None,
    ) -> int:
        """Discover open GitHub Issues and submit them as AgentForge tasks.

        Args:
            projects: List of project dicts with at least {'db_path': Path}.
            labels: List of GitHub labels to filter by (default: bug, feature, enhancement).
            task_repo_factory: Callable(db_path) -> TaskRepository. If not
                               provided, creates TaskRepository directly.

        Returns:
            Number of tasks created.
        """
        if not self.is_available():
            _log.debug("PM discovery skipped: gh CLI not available")
            return 0

        labels = labels or ["bug", "feature", "enhancement"]
        created = 0

        for project in projects:
            db_path = project.get("db_path")
            if isinstance(db_path, str):
                db_path = Path(db_path)
            if not db_path:
                continue

            try:
                issues = discover_github_issues(
                    repo_path=self.repo_path,
                    labels=labels,
                )
            except Exception as e:
                _log.debug("GitHub issue discovery failed: %s", e)
                continue

            if not issues:
                continue

            # Create repository
            if task_repo_factory:
                repo = task_repo_factory(db_path)
            else:
                from ..db import StateDB
                db = StateDB(db_path)
                db.connect()
                repo = TaskRepository(db)

            try:
                for issue in issues:
                    task_id = submit_issue_as_task(issue, repo._db)
                    if task_id:
                        mark_issue_submitted(self.repo_path, issue["number"])
                        _log.info("PM auto-discovered: GH #%d -> %s",
                                  issue["number"], task_id)
                        created += 1
            except Exception as e:
                _log.warning("Error submitting issues: %s", e)
            finally:
                try:
                    repo._db.close()
                except Exception:
                    pass

        if created:
            _log.info("Discovery: created %d task(s) from GitHub Issues", created)
        return created
