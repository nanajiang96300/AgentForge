"""
WorkflowOrchestrator — 多步骤工作流编排器。

职责:
  - 加载 workflow YAML，解析步骤依赖关系
  - 确定哪些步骤就绪可执行
  - 调用 AgentSpawner 执行步骤
  - 处理步骤结果（成功/失败/ rejection loop / escalation）
  - 管理状态机转换
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from .db import StateDB, Task, now_iso
from .engine import AgentSpawner, StepResult, StepStatus, load_yaml


class StepState(Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    SKIPPED = "skipped"


@dataclass
class WorkflowStep:
    """工作流中的单个步骤"""
    id: str
    agent: str
    description: str = ""
    depends_on: list = field(default_factory=list)
    condition: Optional[str] = None
    timeout: int = 600
    retry: dict = field(default_factory=dict)
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    guard: dict = field(default_factory=dict)
    on_success: dict = field(default_factory=dict)
    on_failure: dict = field(default_factory=dict)
    on_verdict_rejected: Optional[dict] = None
    on_verdict_approved: Optional[dict] = None
    state: StepState = StepState.PENDING


class WorkflowOrchestrator:
    """
    工作流编排器。管理 PM→Dev→Test 多步骤工作流的执行。
    Phase 2 支持: 线性步骤 + 条件分支 (rejection loop) + escalation。
    Phase 3+ 可扩展为完整 DAG 引擎。
    """

    def __init__(self, db: StateDB, spawner: AgentSpawner, workflow_path: Path):
        self.db = db
        self.spawner = spawner
        self.workflow_path = workflow_path
        self.workflow_def: dict = {}
        self.steps: dict[str, WorkflowStep] = {}
        self._step_results: dict[str, dict] = {}  # step_id → last output
        self._lock = threading.Lock()  # Protects _step_results in parallel mode

    def load(self):
        """加载并验证工作流 YAML"""
        self.workflow_def = load_yaml(self.workflow_path)
        wf = self.workflow_def.get("workflow", {})
        for sdef in wf.get("steps", []):
            step = WorkflowStep(
                id=sdef["id"],
                agent=sdef.get("agent", ""),
                description=sdef.get("description", ""),
                depends_on=sdef.get("depends_on", []) if isinstance(sdef.get("depends_on", []), list) else [sdef["depends_on"]],
                condition=sdef.get("condition"),
                timeout=sdef.get("timeout", 600),
                retry=sdef.get("retry", {}),
                input=sdef.get("input", {}),
                output=sdef.get("output", {}),
                guard=sdef.get("guard", {}),
                on_success=sdef.get("on_success", {}),
                on_failure=sdef.get("on_failure", {}),
                on_verdict_rejected=sdef.get("on_verdict_rejected"),
                on_verdict_approved=sdef.get("on_verdict_approved"),
            )
            self.steps[step.id] = step
        return self

    def get_ready_steps(self, task: Task) -> list[WorkflowStep]:
        """返回当前就绪可执行的步骤列表"""
        ready = []
        for step in self.steps.values():
            if step.state != StepState.PENDING:
                continue
            # 检查依赖
            deps_met = True
            for dep_id in step.depends_on:
                dep_step = self.steps.get(dep_id)
                if dep_step is None:
                    deps_met = False
                    break
                # 检查是否有前一步骤的结果
                if dep_id not in self._step_results:
                    deps_met = False
                    break
                prev_result = self._step_results[dep_id]
                # 如果依赖步骤被跳过（条件不满足），也视为依赖满足
                if prev_result.get("_state") == "skipped":
                    continue
                # 检查依赖步骤的输出是否满足条件
                if dep_step.condition:
                    if not self._check_condition(dep_step.condition, prev_result):
                        deps_met = False
                        break
            if deps_met:
                # 检查 step 自身的 condition
                if step.condition:
                    ctx = {"task": task}
                    ctx.update(self._step_results)
                    if not self._check_condition(step.condition, ctx):
                        step.state = StepState.SKIPPED
                        self._step_results[step.id] = {"_state": "skipped"}
                        continue
                ready.append(step)
        return ready

    def build_step_input(self, step: WorkflowStep, task: Task) -> dict:
        """根据 workflow 定义解析步骤输入"""
        inp = {}
        input_def = step.input
        if not input_def:
            return inp

        source = input_def.get("from", "")
        fields = input_def.get("fields", [])

        if source == "task.context":
            context = task.context or {}
            if isinstance(context, str):
                try: context = json.loads(context)
                except: context = {}
            for f in fields:
                if f in context:
                    inp[f] = context[f]
        elif source.endswith(".output"):
            # e.g. "pm_analyze.output"
            prev_step_id = source.replace(".output", "")
            prev_result = self._step_results.get(prev_step_id, {})
            for f in fields:
                if f in prev_result:
                    inp[f] = prev_result[f]

        return inp

    def execute_step(self, task: Task, step: WorkflowStep) -> StepResult:
        """执行单个步骤"""
        # 构建步骤定义（传递 output.required 以生成精确 prompt）
        step_def = {
            "id": step.id,
            "agent": step.agent,
            "description": step.description,
            "timeout": step.timeout,
            "input": self.build_step_input(step, task),
            "output": step.output,  # Pass required fields to prompt builder
        }

        # Spawn
        process = self.spawner.spawn(task, step_def)
        step.state = StepState.RUNNING

        # Monitor
        result = self.spawner.monitor(task, step_def, process, timeout=step.timeout)
        return self._handle_result(task, step, result)

    def _handle_result(self, task: Task, step: WorkflowStep, result: StepResult) -> StepResult:
        """处理步骤执行结果，决定下一步动作"""
        # 保存结果
        self._step_results[step.id] = result.output

        if result.status == StepStatus.COMPLETED:
            # Schema 校验
            required = step.output.get("required", [])
            if required and not self.spawner.validate_output(result, required):
                retry_max = step.retry.get("max", 3)
                if result.retry_count < retry_max:
                    step.state = StepState.PENDING
                    return self.execute_step(task, step)
                else:
                    step.state = StepState.FAILED
                    self.db.update_task_status(task.id, "escalated", step.id)
                    return result

            # Rejection / Approval 检查 (case-insensitive)
            verdict = str(result.output.get("verdict", "")).lower()
            if step.on_verdict_rejected and verdict == "rejected":
                return self._handle_rejection(task, step, result)

            step.state = StepState.COMPLETED

            # 处理 test_verify on_verdict_approved
            if step.on_verdict_approved and verdict == "approved":
                approved_action = step.on_verdict_approved.get("action", "")
                if approved_action == "mark_complete":
                    self.db.update_task_status(task.id, "completed", step.id)

            # 成功后的动作 (pm_analyze on_success)
            action = step.on_success.get("action", "")
            if "assigned" in action or step.on_success.get("to_state"):
                self.db.update_task_status(task.id, step.on_success.get("to_state", "assigned"), step.id)

        elif result.status in (StepStatus.CRASHED, StepStatus.TIMED_OUT):
            retry_max = step.retry.get("max", 3)
            if result.retry_count < retry_max:
                result.retry_count += 1
                step.state = StepState.PENDING
                return self.execute_step(task, step)
            else:
                step.state = StepState.FAILED
                if step.on_failure.get("escalate_on_exhaust", False):
                    self.db.update_task_status(task.id, "escalated", step.id)

        elif result.status == StepStatus.VALIDATION_FAILED:
            step.state = StepState.FAILED

        return result

    def _handle_rejection(self, task: Task, step: WorkflowStep, result: StepResult) -> StepResult:
        """处理 Test Agent 的打回"""
        rejection_count = self.db.increment_rejection(task.id)
        max_rejections = self.workflow_def.get("workflow", {}).get("error_policy", {}).get("max_rejections", 3)

        if rejection_count < max_rejections:
            # 回到上一步（dev_fix），同时重置当前步骤等待重新验证
            step.state = StepState.PENDING
            dev_step_id = step.on_verdict_rejected.get("next", "")
            if dev_step_id and dev_step_id in self.steps:
                dev_step = self.steps[dev_step_id]
                dev_step.state = StepState.PENDING
                # 清除之前的 Dev 结果，强制重做
                if dev_step_id in self._step_results:
                    del self._step_results[dev_step_id]
        else:
            # 超过重试上限 → escalated
            step.state = StepState.FAILED
            self.db.update_task_status(task.id, "escalated", step.id)

        return result

    def _check_condition(self, condition: str, context: dict) -> bool:
        """检查条件表达式（简化版，支持 output.field == 'value'）"""
        try:
            # 只支持简单的 == and !=
            for op in ["==", "!="]:
                if op in condition:
                    left, right = condition.split(op)
                    left = left.strip(); right = right.strip().strip("'\"")
                    # 解析 left: e.g. "test_verify.output.verdict"
                    parts = left.split(".")
                    value = context
                    for p in parts:
                        if isinstance(value, dict):
                            value = value.get(p, "")
                    if op == "==": return str(value) == right
                    if op == "!=": return str(value) != right
            return True
        except Exception:
            return True  # 出错时放行

    def _execute_parallel(self, task: Task, steps: list[WorkflowStep]) -> list:
        """并行执行多个独立步骤，返回结果列表"""
        results = []
        with ThreadPoolExecutor(max_workers=len(steps)) as executor:
            futures = {
                executor.submit(self._execute_step_safe, task, step): step
                for step in steps
            }
            for future in as_completed(futures):
                step = futures[future]
                try:
                    result = future.result()
                    results.append((step, result))
                except Exception as e:
                    # Step failed catastrophically
                    from .engine import StepResult, StepStatus
                    result = StepResult(
                        step_id=step.id, agent=step.agent,
                        status=StepStatus.CRASHED, error=str(e),
                    )
                    with self._lock:
                        self._step_results[step.id] = {"error": str(e)}
                    step.state = StepState.FAILED
                    results.append((step, result))
        return results

    def _execute_step_safe(self, task: Task, step: WorkflowStep):
        """线程安全的步骤执行包装"""
        # execute_step handles _step_results internally via _handle_result
        # Use lock only when reading/writing shared state
        with self._lock:
            step.state = StepState.RUNNING
        return self.execute_step(task, step)

    def run(self, task: Task) -> dict:
        """运行完整工作流直到完成或阻塞。支持并行执行独立步骤。"""
        self.load()
        self._step_results = {}

        while True:
            ready = self.get_ready_steps(task)
            if not ready:
                # 检查是否所有步骤都处理完毕
                all_done = all(
                    s.state in (StepState.COMPLETED, StepState.SKIPPED, StepState.FAILED, StepState.REJECTED)
                    for s in self.steps.values()
                )
                if all_done:
                    break
                # 还有 pending 但有未满足的依赖 → 检查是否是 rejection loop
                pending = [s for s in self.steps.values() if s.state == StepState.PENDING]
                if not pending:
                    break
                # 等待 rejection loop 解决
                break

            # Fan-out: 如果多个步骤就绪且互相独立，并行执行
            if len(ready) > 1 and self._can_parallelize(ready):
                parallel_results = self._execute_parallel(task, ready)
                for step, result in parallel_results:
                    if step.on_verdict_rejected and str(result.output.get("verdict", "")).lower() == "rejected":
                        # Rejection breaks the current parallel batch
                        break
            else:
                # Sequential execution
                for step in ready:
                    result = self.execute_step(task, step)

                    # 如果是 rejection → 跳出当前循环，允许 dev_fix 重新执行
                    if step.on_verdict_rejected and str(result.output.get("verdict", "")).lower() == "rejected":
                        break

        return {
            "task_id": task.id,
            "steps": {sid: s.state.value for sid, s in self.steps.items()},
            "results": self._step_results,
        }

    def _can_parallelize(self, steps: list[WorkflowStep]) -> bool:
        """检查多个步骤是否可以并行执行（无交叉依赖）"""
        if len(steps) <= 1:
            return False
        step_ids = {s.id for s in steps}
        for step in steps:
            for dep_id in step.depends_on:
                if dep_id in step_ids:
                    # One of the ready steps depends on another ready step
                    return False
        return True
