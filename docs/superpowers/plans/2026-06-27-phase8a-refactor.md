# Phase 8a: Code Architecture Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor AgentForge codebase into clean layered architecture (CLI → Service → Repository → DB) with proper interfaces, unified condition engine, and i18n split.

**Architecture:** 11-step bottom-up refactoring. Each step produces independently testable deliverables. Old files replaced with thin re-export wrappers for backward compatibility. All steps run `python gates/regression.py` after completion.

**Tech Stack:** Python 3.10+, sqlite3, ast module, pytest, PyYAML

## Global Constraints

- Branch-per-feature: `feature/phase8a-refactor`
- Never commit directly to main
- All 215 regression tests must pass after each step
- `multiagent run/conductor start/dashboard` must remain functional throughout
- CLI files MUST NOT import StateDB directly
- Cross-layer calls MUST use interfaces.py ABCs
- Old files replaced with re-export wrappers, not deleted

---

## File Structure Map

| Layer | Files Created | Files Modified | Files (old → wrapper) |
|-------|-------------|---------------|----------------------|
| DB | — | `db.py` (338→150行) | — |
| Persistence | — | `persistence/task_repo.py` (61→120行), `persistence/metrics_repo.py`, `persistence/escalation_repo.py` | — |
| Core | `core/conditions.py` (100行) | `core/graph_engine.py`, `orchestrator.py` | — |
| Services | `services/workflow_service.py`, `services/role_service.py`, `services/dashboard_service.py`, `services/recovery_service.py`, `services/discovery_service.py` | `services/__init__.py` | — |
| CLI | `cli/run.py`, `cli/conductor.py`, `cli/role.py`, `cli/workflow.py`, `cli/pm.py`, `cli/metrics.py`, `cli/__init__.py` | — | `engine_cli.py`, `conductor_cli.py`, `role_cli.py`, `workflow_cli.py`, `pm_cli.py`, `metrics_cli.py` → wrapper |
| Conductor | — | `conductor.py` (695→300行) | — |
| Dashboard | — | `dashboard.py` (395→200行) | — |
| Notify | `notify_i18n.py` (120行) | `notify.py` (497→300行) | — |
| Interfaces | — | `interfaces.py` (74→130行) | — |

---

### Task 1: Create feature branch + add new interfaces

**Files:**
- Create: (branch only)
- Modify: `src/multiagent/interfaces.py`

**Interfaces:**
- Produces: `StepConditionEvaluator` (ABC), `RoleTemplateLoader` (ABC), `WorkflowTopology` (ABC)

- [ ] **Step 1: Checkout feature branch**

```bash
git checkout -b feature/phase8a-refactor
```

- [ ] **Step 2: Add 3 new interfaces to interfaces.py**

Append to `src/multiagent/interfaces.py`:

```python
class StepConditionEvaluator(ABC):
    """Evaluate conditions on step outputs and task context.
    
    Supports: ==, !=, >, <, >=, <=, in, not in, and, or, not
    Example: 'verdict == "approved" and complexity != "high"'
    """
    
    @abstractmethod
    def evaluate(self, condition: str, context: dict) -> bool:
        """Evaluate condition expression against context. Returns True/False."""
        ...
    
    @abstractmethod
    def validate(self, condition: str) -> tuple:
        """Validate condition syntax. Returns (is_valid: bool, error: str|None)."""
        ...


class RoleTemplateLoader(ABC):
    """Load and resolve role templates to AgentConfig.
    Reserved for Phase 8c role template system."""
    @abstractmethod
    def list_builtins(self) -> list[str]: ...
    @abstractmethod
    def list_user_templates(self) -> list[str]: ...
    @abstractmethod
    def load(self, name: str) -> "AgentConfig": ...
    @abstractmethod
    def validate_template(self, name: str) -> list[str]: ...


class WorkflowTopology(ABC):
    """Query workflow graph topology independently of execution.
    Reserved for Phase 8c multi-agent collaboration modes."""
    @abstractmethod
    def entry_nodes(self) -> list[str]: ...
    @abstractmethod
    def successors_of(self, node_id: str) -> list[str]: ...
    @abstractmethod
    def predecessors_of(self, node_id: str) -> list[str]: ...
    @abstractmethod
    def parallel_groups(self) -> list[list[str]]: ...
    @abstractmethod
    def validate(self) -> list[str]: ...
```

- [ ] **Step 3: Run regression tests**

```bash
source .venv/bin/activate && python gates/regression.py
```
Expected: All 215 tests pass (new interfaces are ABCs, no instantiation required)

- [ ] **Step 4: Commit**

```bash
git add src/multiagent/interfaces.py
git commit -m "feat: add StepConditionEvaluator, RoleTemplateLoader, WorkflowTopology interfaces

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Slim down db.py to connection-only layer

**Files:**
- Modify: `src/multiagent/db.py`

**Interfaces:**
- Produces: `StateDB.execute(sql, params)`, `StateDB.execute_write(sql, params)`, `StateDB.execute_many(sql, params_list)`

- [ ] **Step 1: Add thin execute methods to StateDB**

In `src/multiagent/db.py`, add the following methods to the `StateDB` class:

```python
def execute(self, sql: str, params: tuple = ()):
    """Read-only query shortcut. Returns cursor for .fetchone()/.fetchall()."""
    return self.conn.execute(sql, params)

def execute_write(self, sql: str, params: tuple = ()):
    """Write operation with lock + auto-commit. Returns cursor."""
    with self._write_lock:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

def execute_many(self, sql: str, params_list: list):
    """Batch write with lock + auto-commit."""
    with self._write_lock:
        cur = self.conn.executemany(sql, params_list)
        self.conn.commit()
        return cur
```

- [ ] **Step 2: Add deprecation markers to old methods**

Add `# DEPRECATED: use persistence.TaskRepository instead` docstring note at the top of each old CRUD method: `insert_task`, `claim_pending_task`, `update_task_status`, `increment_retry`, `increment_rejection`, `set_task_context`, `get_task`, `get_running_tasks`, `search_tasks`, `get_pending_tasks`, `get_escalated_tasks`, `record_step`, `heartbeat`, `get_lost_agents`, `record_metrics`, `get_metrics_summary`, `record_escalation`, `get_pending_escalations`, `resolve_escalation`, `prune_step_results`, `prune_agent_metrics`, `prune_heartbeat`, `cleanup_task_data`, `prune_all`.

- [ ] **Step 3: Run regression tests**

```bash
source .venv/bin/activate && python gates/regression.py
```
Expected: All 215 tests pass (old methods still work, new methods are additive)

- [ ] **Step 4: Commit**

```bash
git add src/multiagent/db.py
git commit -m "refactor(db): add thin execute/execute_write/execute_many, mark CRUD as deprecated

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Fill in persistence Repositories with inline SQL

**Files:**
- Modify: `src/multiagent/persistence/task_repo.py`
- Modify: `src/multiagent/persistence/metrics_repo.py`
- Modify: `src/multiagent/persistence/escalation_repo.py`

**Interfaces:**
- Consumes: `StateDB.execute_write()` from Task 2
- Produces: `TaskRepository` (implements `TaskStore`), `MetricsRepository` (implements `MetricsStore`), `EscalationRepository` (implements `EscalationStore`)

- [ ] **Step 1: Rewrite TaskRepository with inline SQL**

Replace `src/multiagent/persistence/task_repo.py` with full implementation containing: `get_pending()`, `get_task()`, `update_status()` (with status guard for terminal states), `get_escalated()`, `insert()`, `claim_pending()`, `increment_retry()`, `increment_rejection()`, `set_context()`, `get_running()`, `search()`, `record_step()`, `get_step_results()`, `get_last_output()`, `heartbeat()`, `get_lost_agents()`, `cleanup_task_data()`, `prune_step_results()`, `prune_heartbeat()`. All methods use `self._db.execute_write()` for writes and `self._db.execute()` for reads, with SQL written inline (not delegated to db.py).

- [ ] **Step 2: Rewrite MetricsRepository with inline SQL**

Replace `src/multiagent/persistence/metrics_repo.py` with: `record()`, `summary()`, `for_task()`, `prune()`. All with inline SQL.

- [ ] **Step 3: Rewrite EscalationRepository with inline SQL**

Replace `src/multiagent/persistence/escalation_repo.py` with: `record()`, `get_pending()`, `resolve()`. All with inline SQL.

- [ ] **Step 4: Run regression tests**

```bash
source .venv/bin/activate && python gates/regression.py
```
Expected: All 215 tests pass (existing code still uses db.py directly, not Repositories yet)

- [ ] **Step 5: Commit**

```bash
git add src/multiagent/persistence/
git commit -m "refactor: Repository implementations with inline SQL (TaskRepo/MetricsRepo/EscalationRepo)

TaskRepo owns tasks + step_results + heartbeat + cleanup.
MetricsRepo owns agent_metrics. EscalationRepo owns escalations.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Create unified condition engine

**Files:**
- Create: `src/multiagent/core/conditions.py`
- Create: `gates/test_conditions.py`

**Interfaces:**
- Produces: `ConditionEvaluator(StepConditionEvaluator)`, `ConditionSyntaxError`

- [ ] **Step 1: Write the failing test**

Create `gates/test_conditions.py` with 12 test methods:
- `test_simple_equality` — `verdict == 'approved'`
- `test_not_equal` — `verdict != 'rejected'`
- `test_and_condition` — `verdict == 'approved' and complexity == 'low'`
- `test_or_condition` — `x == 'a' or y == 'b'`
- `test_in_condition` — `module in ['auth', 'api']`
- `test_not_in_condition` — `env not in ['prod']`
- `test_numeric_comparisons` — `count > 0`, `count >= 5`, `count < 10`, `count <= 5`
- `test_missing_key_returns_false` — unknown key evaluates to None/False
- `test_empty_condition_returns_true` — empty string is always true
- `test_validate_valid` / `test_validate_invalid`
- `test_nested_and_or` — `a == '1' and (b == '2' or c == 'x')`
- `test_not_operator` — `not verdict == 'rejected'`

```python
from multiagent.core.conditions import ConditionEvaluator, ConditionSyntaxError
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest gates/test_conditions.py -v
```
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement ConditionEvaluator**

Create `src/multiagent/core/conditions.py` (~100 lines):

```python
"""Unified condition expression evaluator using ast module (no eval())."""
import ast
import operator as _op


class ConditionSyntaxError(Exception):
    def __init__(self, message: str, line: int = 0, col: int = 0):
        self.line = line
        self.col = col
        super().__init__(f"{message} (line {line}, col {col})" if line else message)


_OPS = {
    ast.Eq: _op.eq, ast.NotEq: _op.ne,
    ast.Gt: _op.gt, ast.Lt: _op.lt,
    ast.GtE: _op.ge, ast.LtE: _op.le,
}


class _Evaluator(ast.NodeVisitor):
    def __init__(self, context: dict):
        self._ctx = context
        self._stack = []

    def push(self, v): self._stack.append(v)
    def pop(self): return self._stack.pop()

    def visit_Compare(self, node):
        self.generic_visit(node)
        right = self.pop()
        left = self.pop()
        for op_node in node.ops:
            if isinstance(op_node, ast.In):
                self.push(left in right)
            elif isinstance(op_node, ast.NotIn):
                self.push(left not in right)
            else:
                f = _OPS.get(type(op_node))
                if f is None: raise ConditionSyntaxError(f"Unsupported operator")
                self.push(f(left, right))

    def visit_Name(self, node):
        if node.id in self._ctx: self.push(self._ctx[node.id])
        elif node.id in ('True', 'False'): self.push(node.id == 'True')
        else: self.push(None)

    def visit_Constant(self, node): self.push(node.value)
    def visit_List(self, node): self.push([e.value for e in node.elts])
    def visit_Tuple(self, node): self.push(tuple(e.value for e in node.elts))

    def visit_BoolOp(self, node):
        for v in node.values: self.visit(v)
        vals = [self.pop() for _ in node.values]
        self.push(all(vals) if isinstance(node.op, ast.And) else any(vals))

    def visit_UnaryOp(self, node):
        self.visit(node.operand)
        if isinstance(node.op, ast.Not): self.push(not self.pop())


class ConditionEvaluator:
    def evaluate(self, condition: str, context: dict) -> bool:
        if not condition or not condition.strip(): return True
        try: tree = ast.parse(condition.strip(), mode='eval')
        except SyntaxError as e: raise ConditionSyntaxError(str(e), e.lineno or 0, e.offset or 0)
        visitor = _Evaluator(context)
        try: visitor.visit(tree.body); return bool(visitor.pop())
        except ConditionSyntaxError: raise
        except Exception as e: raise ConditionSyntaxError(f"Evaluation failed: {e}")

    def validate(self, condition: str) -> tuple:
        if not condition or not condition.strip(): return True, None
        try: ast.parse(condition.strip(), mode='eval')
        except SyntaxError as e: return False, str(e)
        return True, None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
source .venv/bin/activate && python -m pytest gates/test_conditions.py -v
```
Expected: All 12 tests PASS

- [ ] **Step 5: Run full regression**

```bash
source .venv/bin/activate && python gates/regression.py
```
Expected: 227 tests pass

- [ ] **Step 6: Commit**

```bash
git add src/multiagent/core/conditions.py gates/test_conditions.py
git commit -m "feat: add ConditionEvaluator — safe ast-based condition expression engine

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Enhance WorkflowGraph with ConditionEvaluator + WorkflowTopology

**Files:**
- Modify: `src/multiagent/core/graph_engine.py`
- Modify: `gates/test_graph_engine.py`

**Interfaces:**
- Consumes: `StepConditionEvaluator` from Task 4
- Produces: `WorkflowGraph` implements `WorkflowTopology`

- [ ] **Step 1: Add tests for new graph methods**

Append 5 tests to `gates/test_graph_engine.py`:
- `TestWorkflowTopology::test_entry_nodes`
- `TestWorkflowTopology::test_successors_and_predecessors`
- `TestWorkflowTopology::test_parallel_groups_diamond`
- `TestWorkflowTopology::test_validate_returns_empty_for_valid_graph`
- `TestWorkflowTopology::test_validate_detects_orphan`

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest gates/test_graph_engine.py::TestWorkflowTopology -v
```
Expected: FAIL

- [ ] **Step 3: Implement new methods**

Modify `WorkflowGraph` class to inherit from `WorkflowTopology` and implement:
- `__init__` accepts optional `evaluator: StepConditionEvaluator = None`
- `set_evaluator(evaluator)` — setter for lazy injection
- `evaluate_edge(edge, context)` — evaluate conditional edge
- `get_active_successors(node_id, context)` — filtered by condition
- `entry_nodes()`, `successors_of()`, `predecessors_of()` — topology queries
- `parallel_groups()` — return nodes grouped by independence
- `validate()` — check orphans, invalid refs, self-loops, condition syntax

- [ ] **Step 4: Run tests to verify they pass**

```bash
source .venv/bin/activate && python -m pytest gates/test_graph_engine.py -v
```
Expected: All existing + new tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/multiagent/core/graph_engine.py gates/test_graph_engine.py
git commit -m "feat: WorkflowGraph implements WorkflowTopology + ConditionEvaluator integration

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Replace orchestrator._check_condition with ConditionEvaluator

**Files:**
- Modify: `src/multiagent/orchestrator.py`

**Interfaces:**
- Consumes: `StepConditionEvaluator` from Task 4

- [ ] **Step 1: Inject ConditionEvaluator into WorkflowOrchestrator**

Modify `orchestrator.py`:
- `__init__` adds `evaluator: StepConditionEvaluator = None` parameter
- If None, construct `ConditionEvaluator()` internally
- Delete `_check_condition()` method (~20 lines)
- Replace all calls to `self._check_condition(...)` with `self._evaluator.evaluate(...)`

- [ ] **Step 2: Run regression tests**

```bash
source .venv/bin/activate && python gates/regression.py
```
Expected: All tests pass (orchestrator uses ConditionEvaluator now)

- [ ] **Step 3: Commit**

```bash
git add src/multiagent/orchestrator.py
git commit -m "refactor: orchestrator uses ConditionEvaluator, remove _check_condition hack

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Create Service layer (5 new files)

**Files:**
- Create: `src/multiagent/services/workflow_service.py`
- Create: `src/multiagent/services/role_service.py`
- Create: `src/multiagent/services/dashboard_service.py`
- Create: `src/multiagent/services/recovery_service.py`
- Create: `src/multiagent/services/discovery_service.py`
- Modify: `src/multiagent/services/__init__.py`

**Interfaces:**
- Consumes: `TaskStore`, `MetricsStore`, `EscalationStore` from Task 3; `StepConditionEvaluator` from Task 4
- Produces: `WorkflowService`, `RoleService`, `DashboardService`, `RecoveryService`, `DiscoveryService`

- [ ] **Step 1: Create WorkflowService**

`src/multiagent/services/workflow_service.py` — extracts `cmd_run()` logic from `engine_cli.py`:
- `execute(db_path, workflow_path, task_id, roles_path) -> str|None`
- `execute_dry_run(db_path, workflow_path, roles_path) -> list[str]`
- Depends on: `TaskRepository`, `AgentSpawner`, `WorkflowOrchestrator`, `ConditionEvaluator`
- No CLI-level code (no print, no argparse)

- [ ] **Step 2: Create RoleService**

`src/multiagent/services/role_service.py` — extracts role CRUD from `role_cli.py`:
- `create_from_template(template_name, name, model, ...) -> AgentConfig`
- `list_all() -> list[AgentConfig]`
- `get(name) -> AgentConfig`
- `delete(name) -> bool`
- `clone(source_name, new_name) -> AgentConfig`
- `validate(name) -> list[str]`

- [ ] **Step 3: Create DashboardService**

`src/multiagent/services/dashboard_service.py` — data aggregation for both Web and CLI:
- `queue_summary() -> dict` — {pending, running, escalated, alerts}
- `task_progress(task_id) -> dict` — {pct, stage, subtasks, bar}
- `timeseries(days=7) -> dict` — {token_trend, pass_rate}
- `workflow_dag(workflow_path) -> dict` — {nodes, edges, workflow_id}
- Depends on: `TaskRepository`, `MetricsRepository`, `EscalationRepository`

- [ ] **Step 4: Create RecoveryService**

`src/multiagent/services/recovery_service.py` — from `conductor._recover_orphaned_tasks` + `_kill_in_flight_agents`:
- `recover_all(projects) -> int` — find + kill orphaned tasks, mark as failed
- `kill_in_flight(task_ids, projects) -> int` — kill agent processes for given tasks

- [ ] **Step 5: Create DiscoveryService**

`src/multiagent/services/discovery_service.py` — from `conductor._discover_and_submit`:
- `discover_and_submit(projects, labels) -> int` — GitHub Issues → tasks
- `is_available() -> bool` — check if gh CLI is available

- [ ] **Step 6: Update services/__init__.py**

```python
from .pid_manager import PidManager
from .checkpoint import CheckpointManager
from .workflow_service import WorkflowService
from .role_service import RoleService
from .dashboard_service import DashboardService
from .recovery_service import RecoveryService
from .discovery_service import DiscoveryService
```

- [ ] **Step 7: Run regression tests**

```bash
source .venv/bin/activate && python gates/regression.py
```
Expected: All tests pass (services exist but not yet wired into CLI/Conductor)

- [ ] **Step 8: Commit**

```bash
git add src/multiagent/services/
git commit -m "feat: add Service layer — WorkflowService, RoleService, DashboardService, RecoveryService, DiscoveryService

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Reorganize CLI into cli/ package

**Files:**
- Create: `src/multiagent/cli/__init__.py`
- Create: `src/multiagent/cli/run.py`
- Create: `src/multiagent/cli/conductor.py`
- Create: `src/multiagent/cli/role.py`
- Create: `src/multiagent/cli/workflow.py`
- Create: `src/multiagent/cli/pm.py`
- Create: `src/multiagent/cli/metrics.py`
- Modify: `src/multiagent/engine_cli.py` → wrapper
- Modify: `src/multiagent/conductor_cli.py` → wrapper
- Modify: `src/multiagent/role_cli.py` → wrapper
- Modify: `src/multiagent/workflow_cli.py` → wrapper
- Modify: `src/multiagent/pm_cli.py` → wrapper
- Modify: `src/multiagent/metrics_cli.py` → wrapper
- Modify: `src/multiagent/__init__.py` → update imports

**Interfaces:**
- Consumes: Service layer from Task 7

- [ ] **Step 1: Create cli/__init__.py with dispatch table**

```python
"""CLI package — argparse + Service calls. No direct StateDB imports."""
```

- [ ] **Step 2: Create cli/run.py**

Migrate `parse_run_args()` and wiring logic from `engine_cli.py`. Business logic (cmd_run) now calls `WorkflowService.execute()`. CLI handles: argparse → call service → format output.

- [ ] **Step 3: Create cli/conductor.py**

Migrate conductor subcommands from `conductor_cli.py`. CLI handles: argparse → call service → format output.

- [ ] **Step 4: Create cli/role.py**

Migrate role subcommands from `role_cli.py`. CLI handles: argparse → call `RoleService` → format output.

- [ ] **Step 5: Create cli/workflow.py**

Migrate workflow subcommands from `workflow_cli.py`. CLI handles: argparse → call service → format output.

- [ ] **Step 6: Create cli/pm.py**

Migrate pm subcommands from `pm_cli.py`. CLI handles: argparse → call service → format output.

- [ ] **Step 7: Create cli/metrics.py**

Migrate metrics subcommands from `metrics_cli.py`. CLI handles: argparse → call service → format output.

- [ ] **Step 8: Replace old files with re-export wrappers**

Each old file (e.g., `engine_cli.py`) becomes:
```python
"""DEPRECATED: import from cli.run instead."""
from .cli.run import parse_run_args, cmd_run  # noqa: F401
```

- [ ] **Step 9: Update __init__.py imports**

```python
from .cli.run import parse_run_args
from .cli.conductor import register_conductor_parser, handle_conductor
# ... etc
```

- [ ] **Step 10: Run regression tests**

```bash
source .venv/bin/activate && python gates/regression.py
```
Expected: All tests pass (all imports resolve through wrappers)

- [ ] **Step 11: Commit**

```bash
git add src/multiagent/cli/ src/multiagent/engine_cli.py src/multiagent/conductor_cli.py \
        src/multiagent/role_cli.py src/multiagent/workflow_cli.py src/multiagent/pm_cli.py \
        src/multiagent/metrics_cli.py src/multiagent/__init__.py
git commit -m "refactor: reorganize CLI into cli/ package with Service layer

Old files replaced with re-export wrappers for backward compatibility.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: Slim down conductor.py using Services

**Files:**
- Modify: `src/multiagent/conductor.py`

- [ ] **Step 1: Inject services into Conductor**

Modify `Conductor.__init__` to accept service instances:
- `workflow_service: WorkflowService` (for `_execute_task`)
- `recovery_service: RecoveryService` (for `_recover_orphaned_tasks` + `_kill_in_flight_agents`)
- `discovery_service: DiscoveryService` (for `_discover_and_submit`)

Remove the corresponding methods from `Conductor` class. Replace calls with service delegation.

- [ ] **Step 2: Update conductor_cli.py to instantiate services**

In `cli/conductor.py`, wire up service instances when creating Conductor:
```python
from ..services.workflow_service import WorkflowService
from ..services.recovery_service import RecoveryService
from ..services.discovery_service import DiscoveryService
# ... create Conductor with injected services
```

- [ ] **Step 3: Run regression tests**

```bash
source .venv/bin/activate && python gates/regression.py
```
Expected: All tests pass

- [ ] **Step 4: Verify conductor.py ≤ 300 lines**

```bash
wc -l src/multiagent/conductor.py
```
Expected: ≤ 300

- [ ] **Step 5: Commit**

```bash
git add src/multiagent/conductor.py src/multiagent/cli/conductor.py
git commit -m "refactor: slim conductor.py using WorkflowService/RecoveryService/DiscoveryService

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 10: DashboardService integration + notify i18n split

**Files:**
- Modify: `src/multiagent/dashboard.py`
- Create: `src/multiagent/notify_i18n.py`
- Modify: `src/multiagent/notify.py`

- [ ] **Step 1: Introduce DashboardService into dashboard.py**

- Change `create_dashboard_app()` to accept `DashboardService` parameter
- Replace direct `StateDB` calls in routes with `dashboard_service.*` calls
- Update caller in `cli/pm.py` (`_cmd_dashboard`) to pass `DashboardService`

- [ ] **Step 2: Create notify_i18n.py**

Extract from `notify.py`:
- `_LANG`, `_I18N` dict (~100 lines)
- `set_language(lang)`, `get_language()`, `t(key, **kwargs)`, `_status_label(status_str)`
- `EVENT_COLORS`, `COLOR_*` constants

- [ ] **Step 3: Update notify.py to import from notify_i18n**

```python
from .notify_i18n import t, set_language, get_language, EVENT_COLORS, COLOR_STARTED, ...
```

- [ ] **Step 4: Run regression tests**

```bash
source .venv/bin/activate && python gates/regression.py
```
Expected: All tests pass

- [ ] **Step 5: Verify db.py ≤ 150 lines and db imports clean**

```bash
wc -l src/multiagent/db.py
grep -rn "from .db import StateDB" src/multiagent/cli/
```
Expected: No StateDB imports in cli/

- [ ] **Step 6: Commit**

```bash
git add src/multiagent/dashboard.py src/multiagent/notify_i18n.py src/multiagent/notify.py
git commit -m "refactor: DashboardService integration + notify i18n split

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 11: Full regression + merge to main

- [ ] **Step 1: Run full regression test suite**

```bash
source .venv/bin/activate && python gates/regression.py
```
Expected: All 227+ tests pass

- [ ] **Step 2: Smoke test CLI commands**

```bash
multiagent pm list
multiagent metrics --json
multiagent workflow list
multiagent role list
```

- [ ] **Step 3: Verify all success criteria**

| Criterion | Check |
|-----------|-------|
| `db.py` ≤ 150 lines | `wc -l` |
| `conductor.py` ≤ 300 lines | `wc -l` |
| Repository implementations use inline SQL | `grep db.record_step persistence/` → 0 results |
| `orchestrator._check_condition()` deleted | `grep _check_condition orchestrator.py` → 0 results |
| `WorkflowGraph` implements `WorkflowTopology` | `grep WorkflowTopology core/graph_engine.py` → found |
| `cli/` no direct StateDB imports | `grep -r "StateDB" cli/` → 0 results |
| `DashboardService` used by Web + CLI | `grep DashboardService dashboard.py` → found |
| `notify_i18n.py` no project imports | `grep "from \." notify_i18n.py` → 0 results |

- [ ] **Step 4: Merge to main**

```bash
git checkout main
git merge feature/phase8a-refactor --no-ff
```

- [ ] **Step 5: Final commit + tag**

```bash
git tag v0.8.0
git commit --allow-empty -m "release: v0.8.0 — Phase 8a architecture cleanup

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Execution Note

The complete implementation code for each task is in the design spec at `docs/superpowers/specs/2026-06-27-phase8a-refactor-design.md`. Task steps reference exact SQL, method signatures, and imports from the spec. When implementing, consult the spec's Section 4 (逐模块重构详解) for the detailed code of each module.
