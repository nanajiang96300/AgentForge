"""StateDB — SQLite WAL 模式状态持久化 + 指标追踪"""
import sqlite3, json, threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

def now_iso(): return datetime.now(timezone.utc).isoformat()

@dataclass
class Task:
    id: str; type: str; source: Optional[str]; workflow_id: str
    current_step: Optional[str]; status: str = "pending"
    retry_count: int = 0; rejection_count: int = 0
    dedup_key: Optional[str] = None; context: Optional[dict] = None
    created_at: Optional[str] = None; claimed_at: Optional[str] = None
    completed_at: Optional[str] = None

@dataclass
class AgentMetrics:
    task_id: str; step_id: str; agent: str; adapter: str
    model: str = "unknown"; duration_ms: int = 0; duration_api_ms: int = 0
    input_tokens: int = 0; output_tokens: int = 0; cache_read_tokens: int = 0
    cost_usd: float = 0.0; num_turns: int = 1; ttft_ms: int = 0; status: str = "unknown"

class StateDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path; self.conn: Optional[sqlite3.Connection] = None
        self._write_lock = threading.Lock()

    def connect(self):
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")  # 5s timeout for concurrent writes
        self._init_schema()
        # Lightweight pruning on connect (only if DB has accumulated rows)
        try:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM step_results").fetchone()
            if row and row[0] > 1000:
                self.prune_all()
        except Exception:
            pass

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, type TEXT NOT NULL, source TEXT,
                workflow_id TEXT NOT NULL, current_step TEXT, status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0, rejection_count INTEGER DEFAULT 0,
                dedup_key TEXT UNIQUE, context TEXT DEFAULT '{}',
                created_at TEXT, claimed_at TEXT, completed_at TEXT);
            CREATE TABLE IF NOT EXISTS step_results (id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL, step_id TEXT NOT NULL, agent TEXT NOT NULL,
                adapter TEXT NOT NULL DEFAULT 'claude-code', status TEXT NOT NULL,
                output TEXT DEFAULT '{}', error TEXT, retry_count INTEGER DEFAULT 0,
                started_at TEXT, completed_at TEXT, FOREIGN KEY (task_id) REFERENCES tasks(id));
            CREATE TABLE IF NOT EXISTS workflow_state (workflow_id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'idle', current_task_id TEXT, metadata TEXT DEFAULT '{}', updated_at TEXT);
            CREATE TABLE IF NOT EXISTS heartbeat (task_id TEXT PRIMARY KEY, step_id TEXT NOT NULL,
                agent_pid INTEGER NOT NULL, last_beat TEXT NOT NULL, FOREIGN KEY (task_id) REFERENCES tasks(id));
            CREATE TABLE IF NOT EXISTS agent_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL, step_id TEXT NOT NULL, agent TEXT NOT NULL,
                adapter TEXT NOT NULL DEFAULT 'claude-code', model TEXT, duration_ms INTEGER DEFAULT 0,
                duration_api_ms INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0, num_turns INTEGER DEFAULT 1, ttft_ms INTEGER DEFAULT 0,
                status TEXT DEFAULT 'unknown', recorded_at TEXT, FOREIGN KEY (task_id) REFERENCES tasks(id));
            CREATE TABLE IF NOT EXISTS escalations (id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL, step_id TEXT NOT NULL, reason TEXT NOT NULL,
                context TEXT DEFAULT '{}', status TEXT DEFAULT 'pending',
                created_at TEXT, resolved_at TEXT, resolution TEXT,
                FOREIGN KEY (task_id) REFERENCES tasks(id));
        """)
        self.conn.commit()
        # Phase 2 migration: add rejection_count to existing DBs
        try:
            self.conn.execute("ALTER TABLE tasks ADD COLUMN rejection_count INTEGER DEFAULT 0")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

    def insert_task(self, task: Task) -> bool:
        with self._write_lock:
            try:
                self.conn.execute("""INSERT INTO tasks (id, type, source, workflow_id, current_step,
                    status, retry_count, rejection_count, dedup_key, context, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (task.id, task.type, task.source, task.workflow_id, task.current_step,
                     task.status, task.retry_count, task.rejection_count,
                     task.dedup_key, json.dumps(task.context or {}), task.created_at or now_iso()))
                self.conn.commit(); return True
            except sqlite3.IntegrityError: return False

    def claim_pending_task(self, workflow_id: str) -> Optional[Task]:
        with self._write_lock:
            row = self.conn.execute("""SELECT id, type, source, workflow_id, current_step, status,
                retry_count, rejection_count, dedup_key, context, created_at, claimed_at, completed_at
                FROM tasks WHERE workflow_id = ? AND status = 'pending'
                ORDER BY created_at LIMIT 1""", (workflow_id,)).fetchone()
            if row is None: return None
            self.conn.execute("UPDATE tasks SET status = 'running', claimed_at = ? WHERE id = ?",
                              (now_iso(), row[0])); self.conn.commit()
        context_val = row[9]
        if isinstance(context_val, str):
            try: context_val = json.loads(context_val)
            except: pass
        return Task(id=row[0], type=row[1], source=row[2], workflow_id=row[3],
                    current_step=row[4], status=row[5], retry_count=row[6],
                    rejection_count=row[7], dedup_key=row[8], context=context_val,
                    created_at=row[10], claimed_at=row[11], completed_at=row[12])

    def update_task_status(self, task_id: str, status: str, current_step: Optional[str] = None):
        with self._write_lock:
            # Status guard: never downgrade from a terminal status
            row = self.conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            current_status = row[0] if row else None
            if current_status in ("completed", "failed", "escalated") and status not in ("completed", "failed", "escalated"):
                return  # Refuse to overwrite terminal status with non-terminal

            fields = ["status = ?"]; params: list[Any] = [status]
            if current_step: fields.append("current_step = ?"); params.append(current_step)
            if status == "completed": fields.append("completed_at = ?"); params.append(now_iso())
            params.append(task_id)
            self.conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", params)
            self.conn.commit()

    def increment_retry(self, task_id: str) -> int:
        with self._write_lock:
            row = self.conn.execute("UPDATE tasks SET retry_count = retry_count + 1 WHERE id = ? RETURNING retry_count",
                                    (task_id,)).fetchone()
            self.conn.commit(); return row[0] if row else 0

    def increment_rejection(self, task_id: str) -> int:
        with self._write_lock:
            row = self.conn.execute("UPDATE tasks SET rejection_count = rejection_count + 1 WHERE id = ? RETURNING rejection_count",
                                    (task_id,)).fetchone()
            self.conn.commit(); return row[0] if row else 0

    def set_task_context(self, task_id: str, context: dict):
        with self._write_lock:
            self.conn.execute("UPDATE tasks SET context = ? WHERE id = ?",
                              (json.dumps(context), task_id)); self.conn.commit()

    def get_task(self, task_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None: return None
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(tasks)").fetchall()]
        return dict(zip(cols, row))

    def get_running_tasks(self) -> list[Task]:
        return [Task(*row) for row in self.conn.execute(
            """SELECT id, type, source, workflow_id, current_step, status, retry_count,
               dedup_key, created_at, claimed_at, completed_at FROM tasks WHERE status = 'running'""")]

    def record_step(self, task_id, step_id, agent, status, output=None, error=None,
                    retry_count=0, started_at=None, completed_at=None, adapter_name="claude-code"):
        with self._write_lock:
            self.conn.execute("""INSERT INTO step_results (task_id, step_id, agent, adapter, status,
                output, error, retry_count, started_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (task_id, step_id, agent, adapter_name, status, json.dumps(output or {}),
                 error, retry_count, started_at, completed_at))
            self.conn.commit()

    def heartbeat(self, task_id, step_id, agent_pid):
        with self._write_lock:
            self.conn.execute("INSERT OR REPLACE INTO heartbeat (task_id, step_id, agent_pid, last_beat) VALUES (?,?,?,?)",
                              (task_id, step_id, agent_pid, now_iso())); self.conn.commit()

    def get_lost_agents(self, heartbeat_timeout=60) -> list[dict]:
        rows = self.conn.execute("""SELECT task_id, step_id, agent_pid, last_beat FROM heartbeat
            WHERE datetime(last_beat) < datetime(?, ?)""", (now_iso(), f"-{heartbeat_timeout} seconds"))
        return [{"task_id": r[0], "step_id": r[1], "agent_pid": r[2], "last_beat": r[3]} for r in rows]

    def record_metrics(self, metrics: AgentMetrics):
        with self._write_lock:
            self.conn.execute("""INSERT INTO agent_metrics (task_id, step_id, agent, adapter, model,
                duration_ms, duration_api_ms, input_tokens, output_tokens, cache_read_tokens,
                cost_usd, num_turns, ttft_ms, status, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (metrics.task_id, metrics.step_id, metrics.agent, metrics.adapter, metrics.model,
                 metrics.duration_ms, metrics.duration_api_ms, metrics.input_tokens, metrics.output_tokens,
                 metrics.cache_read_tokens, metrics.cost_usd, metrics.num_turns, metrics.ttft_ms,
                 metrics.status, now_iso())); self.conn.commit()

    # ── Escalations (Phase 4: Conductor human notification) ──

    def record_escalation(self, task_id: str, step_id: str, reason: str, context: Optional[dict] = None) -> int:
        """记录升级事件，返回 escalation ID"""
        with self._write_lock:
            cur = self.conn.execute(
                """INSERT INTO escalations (task_id, step_id, reason, context, status, created_at)
                   VALUES (?, ?, ?, ?, 'pending', ?)""",
                (task_id, step_id, reason, json.dumps(context or {}), now_iso()))
            self.conn.commit()
            return cur.lastrowid

    def get_pending_escalations(self) -> list[dict]:
        """获取所有未处理的升级事件"""
        rows = self.conn.execute(
            """SELECT id, task_id, step_id, reason, context, status, created_at
               FROM escalations WHERE status = 'pending' ORDER BY created_at""").fetchall()
        return [dict(zip(["id", "task_id", "step_id", "reason", "context", "status", "created_at"], r))
                for r in rows]

    def resolve_escalation(self, escalation_id: int, resolution: str) -> bool:
        """处理升级事件（accept/retry/reject）"""
        with self._write_lock:
            self.conn.execute(
                """UPDATE escalations SET status = 'resolved', resolution = ?, resolved_at = ?
                   WHERE id = ?""", (resolution, now_iso(), escalation_id))
            self.conn.commit()
            return True

    def search_tasks(self, keyword: str, status: str = None) -> list[dict]:
        """Search tasks by keyword in requirements text, optionally filtered by status."""
        query = """SELECT id, type, source, workflow_id, current_step, status,
                   retry_count, rejection_count, dedup_key, context,
                   created_at, claimed_at, completed_at
            FROM tasks WHERE context LIKE ?"""
        params = [f"%{keyword}%"]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = self.conn.execute(query, params).fetchall()
        cols = ["id", "type", "source", "workflow_id", "current_step", "status",
                "retry_count", "rejection_count", "dedup_key", "context",
                "created_at", "claimed_at", "completed_at"]
        results = []
        for r in rows:
            d = dict(zip(cols, r))
            # Parse context from JSON string to dict
            ctx = d.get("context")
            if isinstance(ctx, str):
                try: parsed = json.loads(ctx)
                except: parsed = {}
                d["context"] = parsed
                d["context_parsed"] = parsed
            elif isinstance(ctx, dict):
                d["context_parsed"] = ctx
            results.append(d)
        return results

    def get_pending_tasks(self) -> list[dict]:
        """获取所有状态为 pending 的任务（Conductor 用）"""
        rows = self.conn.execute(
            """SELECT id, type, source, workflow_id, current_step, status, retry_count,
               rejection_count, dedup_key, context, created_at, claimed_at, completed_at
               FROM tasks WHERE status = 'pending' ORDER BY created_at""").fetchall()
        cols = ["id", "type", "source", "workflow_id", "current_step", "status", "retry_count",
                "rejection_count", "dedup_key", "context", "created_at", "claimed_at", "completed_at"]
        return [dict(zip(cols, r)) for r in rows]

    def get_escalated_tasks(self) -> list[dict]:
        """获取所有状态为 escalated 的任务"""
        rows = self.conn.execute(
            """SELECT id, type, source, workflow_id, current_step, status, retry_count,
               rejection_count, dedup_key, context, created_at, claimed_at, completed_at
               FROM tasks WHERE status = 'escalated' ORDER BY created_at""").fetchall()
        cols = ["id", "type", "source", "workflow_id", "current_step", "status", "retry_count",
                "rejection_count", "dedup_key", "context", "created_at", "claimed_at", "completed_at"]
        return [dict(zip(cols, r)) for r in rows]

    def get_metrics_summary(self, agent: Optional[str] = None) -> dict:
        if agent:
            row = self.conn.execute("""SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens),
                SUM(cost_usd), AVG(duration_ms) FROM agent_metrics WHERE agent = ?""", (agent,)).fetchone()
        else:
            row = self.conn.execute("""SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens),
                SUM(cost_usd), AVG(duration_ms) FROM agent_metrics""").fetchone()
        return {"total_calls": row[0] or 0, "total_input_tokens": row[1] or 0,
                "total_output_tokens": row[2] or 0, "total_cost_usd": round(row[3] or 0.0, 6),
                "avg_duration_ms": int(row[4] or 0)}

    # ── Data Retention & Cleanup ──

    DEFAULT_RETENTION_DAYS = {
        "step_results": 30,
        "agent_metrics": 90,
        "heartbeat": 7,
        "escalations": 90,
    }

    def prune_step_results(self, days: int = None):
        """Delete step_results older than `days` (default 30)."""
        if days is None:
            days = self.DEFAULT_RETENTION_DAYS["step_results"]
        with self._write_lock:
            self.conn.execute(
                "DELETE FROM step_results WHERE completed_at IS NOT NULL "
                "AND datetime(completed_at) < datetime('now', ?)",
                (f"-{days} days",))
            self.conn.commit()

    def prune_agent_metrics(self, days: int = None):
        """Delete agent_metrics older than `days` (default 90)."""
        if days is None:
            days = self.DEFAULT_RETENTION_DAYS["agent_metrics"]
        with self._write_lock:
            self.conn.execute(
                "DELETE FROM agent_metrics WHERE recorded_at IS NOT NULL "
                "AND datetime(recorded_at) < datetime('now', ?)",
                (f"-{days} days",))
            self.conn.commit()

    def prune_heartbeat(self, days: int = None):
        """Delete heartbeat rows older than `days` (default 7)."""
        if days is None:
            days = self.DEFAULT_RETENTION_DAYS["heartbeat"]
        with self._write_lock:
            self.conn.execute(
                "DELETE FROM heartbeat WHERE datetime(last_beat) < datetime('now', ?)",
                (f"-{days} days",))
            self.conn.commit()

    def cleanup_task_data(self, task_id: str):
        """Remove step_results, agent_metrics, and heartbeat for a task."""
        with self._write_lock:
            for table in ("step_results", "agent_metrics", "heartbeat"):
                self.conn.execute(f"DELETE FROM {table} WHERE task_id = ?", (task_id,))
            self.conn.commit()

    def vacuum(self):
        """Reclaim disk space after large deletes."""
        with self._write_lock:
            self.conn.execute("VACUUM")

    def prune_all(self, retention_days: dict = None):
        """Prune all tables. Called periodically or on connect."""
        r = retention_days or self.DEFAULT_RETENTION_DAYS
        self.prune_step_results(r.get("step_results", 30))
        self.prune_agent_metrics(r.get("agent_metrics", 90))
        self.prune_heartbeat(r.get("heartbeat", 7))

    def close(self):
        if self.conn: self.conn.close()
