"""StateDB — SQLite WAL 模式状态持久化 + 指标追踪"""
import sqlite3, json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

def now_iso(): return datetime.now(timezone.utc).isoformat()

@dataclass
class Task:
    id: str; type: str; source: Optional[str]; workflow_id: str
    current_step: Optional[str]; status: str = "pending"
    retry_count: int = 0; dedup_key: Optional[str] = None
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

    def connect(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, type TEXT NOT NULL, source TEXT,
                workflow_id TEXT NOT NULL, current_step TEXT, status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0, dedup_key TEXT UNIQUE, context TEXT DEFAULT '{}',
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
        """)
        self.conn.commit()

    def insert_task(self, task: Task) -> bool:
        try:
            self.conn.execute("""INSERT INTO tasks (id, type, source, workflow_id, current_step,
                status, retry_count, dedup_key, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (task.id, task.type, task.source, task.workflow_id, task.current_step,
                 task.status, task.retry_count, task.dedup_key, task.created_at or now_iso()))
            self.conn.commit(); return True
        except sqlite3.IntegrityError: return False

    def claim_pending_task(self, workflow_id: str) -> Optional[Task]:
        row = self.conn.execute("""SELECT id, type, source, workflow_id, current_step, status,
            retry_count, dedup_key, created_at, claimed_at, completed_at FROM tasks
            WHERE workflow_id = ? AND status = 'pending' ORDER BY created_at LIMIT 1""",
            (workflow_id,)).fetchone()
        if row is None: return None
        self.conn.execute("UPDATE tasks SET status = 'running', claimed_at = ? WHERE id = ?",
                          (now_iso(), row[0])); self.conn.commit()
        return Task(*row)

    def update_task_status(self, task_id: str, status: str, current_step: Optional[str] = None):
        fields = ["status = ?"]; params: list[Any] = [status]
        if current_step: fields.append("current_step = ?"); params.append(current_step)
        if status == "completed": fields.append("completed_at = ?"); params.append(now_iso())
        params.append(task_id)
        self.conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", params)
        self.conn.commit()

    def increment_retry(self, task_id: str) -> int:
        row = self.conn.execute("UPDATE tasks SET retry_count = retry_count + 1 WHERE id = ? RETURNING retry_count",
                                (task_id,)).fetchone()
        self.conn.commit(); return row[0] if row else 0

    def get_running_tasks(self) -> list[Task]:
        return [Task(*row) for row in self.conn.execute(
            """SELECT id, type, source, workflow_id, current_step, status, retry_count,
               dedup_key, created_at, claimed_at, completed_at FROM tasks WHERE status = 'running'""")]

    def record_step(self, task_id, step_id, agent, status, output=None, error=None,
                    retry_count=0, started_at=None, completed_at=None, adapter_name="claude-code"):
        self.conn.execute("""INSERT INTO step_results (task_id, step_id, agent, adapter, status,
            output, error, retry_count, started_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (task_id, step_id, agent, adapter_name, status, json.dumps(output or {}),
             error, retry_count, started_at, completed_at))
        self.conn.commit()

    def heartbeat(self, task_id, step_id, agent_pid):
        self.conn.execute("INSERT OR REPLACE INTO heartbeat (task_id, step_id, agent_pid, last_beat) VALUES (?,?,?,?)",
                          (task_id, step_id, agent_pid, now_iso())); self.conn.commit()

    def get_lost_agents(self, heartbeat_timeout=60) -> list[dict]:
        rows = self.conn.execute("""SELECT task_id, step_id, agent_pid, last_beat FROM heartbeat
            WHERE datetime(last_beat) < datetime(?, ?)""", (now_iso(), f"-{heartbeat_timeout} seconds"))
        return [{"task_id": r[0], "step_id": r[1], "agent_pid": r[2], "last_beat": r[3]} for r in rows]

    def record_metrics(self, metrics: AgentMetrics):
        self.conn.execute("""INSERT INTO agent_metrics (task_id, step_id, agent, adapter, model,
            duration_ms, duration_api_ms, input_tokens, output_tokens, cache_read_tokens,
            cost_usd, num_turns, ttft_ms, status, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (metrics.task_id, metrics.step_id, metrics.agent, metrics.adapter, metrics.model,
             metrics.duration_ms, metrics.duration_api_ms, metrics.input_tokens, metrics.output_tokens,
             metrics.cache_read_tokens, metrics.cost_usd, metrics.num_turns, metrics.ttft_ms,
             metrics.status, now_iso())); self.conn.commit()

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

    def close(self):
        if self.conn: self.conn.close()
