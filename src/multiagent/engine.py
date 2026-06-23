"""Workflow Engine — Agent 生命周期管理"""
import json, subprocess, time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
from .db import StateDB, Task, AgentMetrics, now_iso
from .adapters import AgentAdapter, create as create_adapter

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

    def spawn(self, task, step, work_dir=None):
        agent_config = self.roles.get("agents",{}).get(step["agent"],{})
        step_runtime = step.get("runtime") or agent_config.get("runtime")
        adapter = create_adapter(step_runtime) if step_runtime else self.adapter
        prompt = self._build_prompt(task, step)
        cmd = adapter.build_command(agent_config, prompt, step)
        cwd = str(work_dir or adapter.project_root)
        p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        self.db.heartbeat(task.id, step["id"], p.pid)
        self.db.record_step(task.id, step["id"], step["agent"], StepStatus.RUNNING.value,
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
        self.db.record_step(task.id, step["id"], step["agent"], result.status.value,
                            output=result.output, error=result.error,
                            retry_count=result.retry_count, started_at=result.started_at,
                            completed_at=result.completed_at, adapter_name=adapter.name())
        if raw: self._capture_metrics(task, step, result, raw, adapter.name())
        return result

    def _capture_metrics(self, task, step, result, raw, adapter_name):
        u = raw.get("usage",{}); mu = raw.get("modelUsage",{})
        fm = next(iter(mu.values()),{}) if mu else {}
        m = AgentMetrics(task_id=task.id, step_id=step["id"], agent=step["agent"],
            adapter=adapter_name, model=fm.get("model", raw.get("model","unknown")),
            duration_ms=raw.get("duration_ms",0), duration_api_ms=raw.get("duration_api_ms",0),
            input_tokens=u.get("input_tokens",0) or fm.get("inputTokens",0),
            output_tokens=u.get("output_tokens",0) or fm.get("outputTokens",0),
            cache_read_tokens=u.get("cache_read_input_tokens",0) or fm.get("cacheReadInputTokens",0),
            cost_usd=raw.get("total_cost_usd",0) or fm.get("costUSD",0.0),
            num_turns=raw.get("num_turns",1), ttft_ms=raw.get("ttft_ms",0), status=result.status.value)
        self.db.record_metrics(m)

    def validate_output(self, result, required_fields):
        for f in required_fields:
            if f not in result.output:
                result.status = StepStatus.VALIDATION_FAILED
                result.error = f"Missing: {f}"; return False
        return True

    @staticmethod
    def _kill_process(p):
        try: p.terminate(); time.sleep(5)
        except: pass
        try:
            if p.poll() is None: p.kill()
        except: pass

def load_yaml(path: Path) -> dict:
    import yaml
    with open(path) as f: return yaml.safe_load(f)
