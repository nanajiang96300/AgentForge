"""
RecoveryService — orphaned task recovery and in-flight agent termination.

Extracted from conductor._recover_orphaned_tasks() and conductor._kill_in_flight_agents().
Depends on Repository interfaces and TaskRepository factory, not StateDB directly.
Uses logging, not print().
"""

import os
import signal
import logging
from pathlib import Path
from typing import Callable, Optional

from ..persistence.task_repo import TaskRepository
from ..db import StateDB

_log = logging.getLogger("multiagent.services.recovery")


class RecoveryService:
    """Orphaned task recovery and in-flight agent process termination."""

    @staticmethod
    def recover_all(
        projects: list[dict],
        task_repo_factory: Callable[[Path], TaskRepository],
    ) -> int:
        """Find tasks stuck in 'running' status and mark them as failed.

        On startup, tasks that were running when the system crashed are
        detected, their agent processes killed (if still alive), and the
        task data cleaned up.

        Args:
            projects: List of project dicts with at least {'db_path': Path}.
            task_repo_factory: Callable that takes a db_path and returns a
                               TaskRepository instance.

        Returns:
            Number of tasks recovered.
        """
        recovered = 0

        for project in projects:
            db_path = project.get("db_path")
            if db_path is None and "name" in project:
                # Try to construct db_path from project name
                continue
            if isinstance(db_path, str):
                db_path = Path(db_path)
            if not db_path:
                continue

            try:
                repo = task_repo_factory(db_path)
            except Exception as e:
                _log.warning("Cannot create TaskRepository for %s: %s", db_path, e)
                continue

            try:
                running_tasks = repo.get_running()
                for task in running_tasks:
                    task_id = task.id

                    # Check heartbeat table and kill orphan agents
                    RecoveryService._kill_orphan_agents(repo, task_id)

                    # Clean up stale data and mark task as failed
                    repo.cleanup_task_data(task_id)
                    repo.update_status(task_id, "failed")
                    _log.info("Recovered orphaned task: %s", task_id)
                    recovered += 1
            except Exception as e:
                _log.error("Error recovering tasks from %s: %s", db_path, e)
            finally:
                _close_repo_db(repo)

        if recovered:
            _log.info("Recovered %d orphaned tasks", recovered)
        return recovered

    @staticmethod
    def kill_in_flight(
        task_ids: list[str],
        projects: list[dict],
        task_repo_factory: Callable[[Path], TaskRepository],
    ) -> int:
        """Kill agent subprocesses for given task IDs via heartbeat table.

        Args:
            task_ids: List of task IDs whose agents should be killed.
            projects: List of project dicts with at least {'db_path': Path}.
            task_repo_factory: Callable that takes a db_path and returns a
                               TaskRepository instance.

        Returns:
            Number of agent processes killed.
        """
        if not task_ids:
            return 0

        killed = 0
        for task_id in task_ids:
            killed += RecoveryService._kill_task_agents(
                task_id, projects, task_repo_factory
            )

        if killed:
            _log.info("Killed %d agent processes for %d task(s)", killed, len(task_ids))
        return killed

    # ── Internal ───────────────────────────────────────────────────────

    @staticmethod
    def _kill_orphan_agents(repo: TaskRepository, task_id: str):
        """Kill agent processes for a specific task using heartbeat data.

        Accesses the underlying DB directly via the TaskRepository's
        internal connection to query the heartbeat table.
        """
        try:
            # Query heartbeat directly via the repo's db connection
            rows = repo._db.execute(
                "SELECT agent_pid FROM heartbeat "
                "WHERE task_id = ? ORDER BY last_beat DESC LIMIT 1",
                (task_id,),
            ).fetchall()
            for (agent_pid,) in rows:
                if agent_pid:
                    _kill_process_group(agent_pid)
        except Exception as e:
            _log.debug("Failed to kill orphan agent for %s: %s", task_id, e)

    @staticmethod
    def _kill_task_agents(
        task_id: str,
        projects: list[dict],
        task_repo_factory: Callable[[Path], TaskRepository],
    ) -> int:
        """Kill all agents for a task across all projects."""
        killed = 0
        for project in projects:
            db_path = project.get("db_path")
            if isinstance(db_path, str):
                db_path = Path(db_path)
            if not db_path or not db_path.exists():
                continue

            try:
                repo = task_repo_factory(db_path)
            except Exception:
                continue

            try:
                rows = repo._db.execute(
                    "SELECT agent_pid FROM heartbeat WHERE task_id = ?",
                    (task_id,),
                ).fetchall()
                for (agent_pid,) in rows:
                    if agent_pid:
                        if _kill_process_group(agent_pid):
                            killed += 1
            except Exception as e:
                _log.warning("Failed to kill agents for %s in %s: %s",
                             task_id, db_path, e)
            finally:
                _close_repo_db(repo)

        return killed


def _kill_process_group(pid: int) -> bool:
    """Send SIGTERM to the entire process group of pid. Returns True on success."""
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return True  # Already dead, counts as cleaned
    except Exception as e:
        _log.debug("Failed to kill PID %d (PGID unknown): %s", pid, e)
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except ProcessLookupError:
            return True
        except Exception:
            return False


def _close_repo_db(repo: TaskRepository):
    """Close the underlying database connection of a repository."""
    try:
        repo._db.close()
    except Exception:
        pass
