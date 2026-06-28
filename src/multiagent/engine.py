"""Workflow Engine — Agent 生命周期管理"""
import json, os, signal, subprocess, time, logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
from .db import StateDB, Task, AgentMetrics, now_iso
from .adapters import AgentAdapter, create as create_adapter
from .persistence.task_repo import TaskRepository
from .persistence.metrics_repo import MetricsRepository

_log = logging.getLogger("multiagent.engine")

class StepStatus(Enum):
    PENDING="pending"; SPAWNING="spawning"; RUNNING="running"; COMPLETED="completed"
    TIMED_OUT="timed_out"; LOST="lost"; VALIDATION_FAILED="validation_failed"
    GUARD_VIOLATION="guard_violation"; CRASHED="crashed"; BLOCKED="blocked"
    ESCALATED="escalated"; RETRYING="retrying"

class WorkflowStatus(Enum):
    IDLE="idle"; RUNNING="running"; PAUSED="paused"; COMPLETED="completed"
    ESCALATED="escalated"; FAILED="failed"

@dataclass
class StepResult:
    step_id: str; agent: str; status: StepStatus
    output: dict = field(default_factory=dict); error: Optional[str] = None
    retry_count: int = 0; started_at: Optional[str] = None; completed_at: Optional[str] = None

class AgentSpawner:
    def __init__(self, db: StateDB, roles_config: dict, adapter: Optional[AgentAdapter] = None,
                 prompt_search_paths: list = None):
        self.db = db; self.roles = roles_config
        if adapter is None:
            runtime = roles_config.get("global",{}).get("runtime","claude-code")
            adapter = create_adapter(runtime)
        self.adapter = adapter
        # Prompt template search paths (injected, not Path.cwd())
        self._prompt_search_paths = prompt_search_paths or [
            adapter.project_root / "architectures" / "dev-test-loop" / "prompts",
        ]
        # Repository references (created from db)
        self._task_repo = TaskRepository(db)
        self._metrics_repo = MetricsRepository(db)

    def spawn(self, task, step, work_dir=None):
        agent_config = self.roles.get("agents",{}).get(step["agent"],{})
        step_runtime = step.get("runtime") or agent_config.get("runtime")
        adapter = create_adapter(step_runtime) if step_runtime else self.adapter
        prompt = self._build_prompt(task, step)
        cmd = adapter.build_command(agent_config, prompt, step)
        cwd = str(work_dir or adapter.project_root)
        p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, start_new_session=True)
        self._task_repo.heartbeat(task.id, step["id"], p.pid)
        self._task_repo.record_step(task.id, step["id"], step["agent"], StepStatus.RUNNING.value,
                                    started_at=now_iso(), adapter_name=adapter.name())
        return p

    def _build_prompt(self, task, step):
        agent_type = step.get("agent", "")
        parts = []

        # Load prompt template if available
        template = self._load_prompt_template(agent_type)
        if template:
            parts.append(template)

        parts.append(step.get("description", f"Execute: {step['id']}"))
        parts.append(f"\nTask: {task.id} ({task.type})")
        inp = step.get("input", {})
        if inp:
            parts.append(f"\nContext: {json.dumps(inp, ensure_ascii=False)}")

        # Required output fields with validation guidance
        required = step.get("output", {}).get("required", [])
        if required:
            parts.append(
                f"\n## Required JSON Output\n"
                f"You MUST return a ```json block with these exact fields: "
                f"{json.dumps(required)}\n"
                f"If validation fails due to missing fields, you will be retried. "
                f"Ensure ALL required fields are present in your JSON output."
            )
        else:
            parts.append("\n\nReturn a JSON summary with required output fields.")

        return "\n".join(parts)

    def _load_prompt_template(self, agent_type: str) -> str:
        """Load prompt template from injected search paths."""
        try:
            template_file = None
            for search_dir in self._prompt_search_paths:
                candidate = search_dir / f"{agent_type}.md"
                if candidate.exists():
                    template_file = candidate
                    break
            if template_file:
                return template_file.read_text() + "\n\n---\n\n"
        except Exception:
            pass
        return ""

    def monitor(self, task, step, process, timeout=600, adapter=None):
        if adapter is None: adapter = self.adapter
        result = StepResult(step_id=step["id"], agent=step["agent"],
                            status=StepStatus.RUNNING, started_at=now_iso())
        raw = None
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            result.completed_at = now_iso()
            po = adapter.parse_output(stdout, stderr)
            if isinstance(po, tuple): parsed, raw = po
            else: parsed = po
            result.output = parsed.output
            result.error = parsed.error or (stderr[:1000] if process.returncode != 0 else parsed.error)
            result.status = parsed.status if process.returncode == 0 else StepStatus.CRASHED
        except subprocess.TimeoutExpired:
            result.status = StepStatus.TIMED_OUT; result.completed_at = now_iso()
            self._kill_process(process)
        self._task_repo.record_step(task.id, step["id"], step["agent"], result.status.value,
                                    output=result.output, error=result.error,
                                    retry_count=result.retry_count, started_at=result.started_at,
                                    completed_at=result.completed_at, adapter_name=adapter.name())
        if raw: self._capture_metrics(task, step, result, raw, adapter.name())
        return result

    def _capture_metrics(self, task, step, result, raw, adapter_name):
        u = raw.get("usage",{}); mu = raw.get("modelUsage",{})
        fm = next(iter(mu.values()),{}) if mu else {}
        model = fm.get("model", raw.get("model","unknown"))
        input_tokens = u.get("input_tokens",0) or fm.get("inputTokens",0)
        output_tokens = u.get("output_tokens",0) or fm.get("outputTokens",0)
        cost_usd = raw.get("total_cost_usd",0) or fm.get("costUSD",0.0)
        duration_ms = raw.get("duration_ms",0)
        self._metrics_repo.record(
            task_id=task.id, step_id=step["id"], agent=step["agent"],
            adapter=adapter_name, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, duration_ms=duration_ms, status=result.status.value)

    def validate_output(self, result, required_fields):
        for f in required_fields:
            if f not in result.output:
                result.status = StepStatus.VALIDATION_FAILED
                result.error = f"Missing: {f}"; return False
        return True

    @staticmethod
    def _kill_process(p):
        """Kill process and all its descendants via process group.

        Uses os.killpg() to signal the entire process group created by
        start_new_session=True in spawn(). Falls back to direct child
        terminate/kill if the process group is unavailable.
        """
        pgid = None
        try:
            pgid = os.getpgid(p.pid)
        except ProcessLookupError:
            return  # Already dead
        except Exception:
            pass

        if pgid is None or pgid == os.getpid():
            # Fallback: direct child only (no separate process group)
            try:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=2)
            except ProcessLookupError:
                pass
            except Exception as e:
                _log.warning("_kill_process fallback failed for PID %d: %s", p.pid, e)
            return

        # Kill entire process group (covers grandchildren)
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception as e:
            _log.warning("killpg SIGTERM failed for PGID %d (PID %d): %s",
                        pgid, p.pid, e)

        # Wait for graceful shutdown
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Force kill
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception as e:
                _log.warning("killpg SIGKILL failed for PGID %d: %s", pgid, e)
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _log.warning("Process PID %d (PGID %d) survived SIGKILL", p.pid, pgid)
            except Exception:
                pass

    def reap_lost_agents(self, heartbeat_timeout: int = 60) -> int:
        """Find agents with stale heartbeats and kill them.

        Called by Conductor to clean up orphaned agent processes.
        Returns the number of agents killed.
        """
        lost = self._task_repo.get_lost_agents(heartbeat_timeout)
        killed = 0
        for entry in lost:
            pid = entry.get("agent_pid")
            if not pid:
                continue
            try:
                # Create a dummy process object for _kill_process
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                killed += 1
            except ProcessLookupError:
                killed += 1  # Already dead, counts as cleaned
            except Exception as e:
                _log.warning("Failed to kill lost agent PID %d: %s", pid, e)
        if killed:
            _log.info("Reaped %d lost agent processes", killed)
        return killed


def load_yaml(path: Path) -> dict:
    import yaml
    with open(path) as f: return yaml.safe_load(f)
