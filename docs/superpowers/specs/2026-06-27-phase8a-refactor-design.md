# Phase 8a: 代码架构清理 — 设计规格

> 版本：v0.8.0 | 日期：2026-06-27 | 作者：nanajiang (架构监视者) + Claude (AI Agent)

## 一、背景与目标

### 1.1 问题诊断

AgentForge v0.7.5 功能完整但工程混乱，阻碍后续功能开发：

| 问题 | 影响 |
|------|------|
| `db.py` (338行) 混了连接管理、CRUD、指标、升级、清理、VACUUM | 改一处牵动全身 |
| `conductor.py` (695行) 是最大单文件，含 7 种职责 | 无法单独测试和修改 |
| `persistence/` Repository 层只有壳，没人用 | 接口形同虚设 |
| `orchestrator._check_condition()` 仅支持 `==`/`!=` | 无法表达复合条件 |
| `graph_engine` 和 `orchestrator` 的条件系统割裂 | 图的条件边不可用 |
| CLI 5 个文件全混了 argparse + 业务逻辑 | CLI 逻辑无法复用 |
| `dashboard.py` 路由直接 import `StateDB` | Web 和 CLI 数据不一致 |
| `notify.py` (497行) i18n 字典和 HTTP 逻辑混在一起 | 修改翻译要碰逻辑代码 |

### 1.2 目标

1. **单一职责**：每个文件只有一个修改理由
2. **接口先行**：跨层交互通过 `interfaces.py` ABC，不直接 import 实现
3. **分层清晰**：CLI → Service → Repository → DB，依赖单向
4. **向后兼容**：`multiagent run/conductor/status/dashboard` 重构期间始终可用
5. **预留扩展**：为 Phase 8b (PM强化+TDD) 和 Phase 8c (角色模板+多模式) 预留接口

---

## 二、目标架构

### 2.1 重构前后对比

```
重构后:
src/multiagent/
├── __init__.py              (不变)
├── interfaces.py            (扩展) +3 新接口
├── db.py                    (~150行) 纯连接管理 + schema
├── engine.py                (不变, 231行)
├── orchestrator.py          (~250行) 使用 ConditionEvaluator
├── conductor.py             (~300行) 纯调度循环
├── dashboard.py             (~200行) 纯 Flask 路由
├── notify.py                (~300行) 纯通知发送
├── notify_i18n.py           (~120行) 语言字典 [新建]
├── cli/                     [新建包]
│   ├── __init__.py
│   ├── run.py
│   ├── conductor.py
│   ├── role.py
│   ├── workflow.py
│   ├── pm.py
│   └── metrics.py
├── core/
│   ├── graph_engine.py      (修改) 使用 ConditionEvaluator
│   ├── conditions.py        [新建] 统一条件引擎
│   └── progress.py          (不变)
├── adapters/                (不变)
├── runtime/registry.py      (不变)
├── persistence/             (真正落地)
│   ├── __init__.py
│   ├── task_repo.py         (~120行) TaskStore 实现
│   ├── metrics_repo.py      (~80行)  MetricsStore 实现
│   └── escalation_repo.py   (~80行)  EscalationStore 实现
├── services/                [扩展]
│   ├── __init__.py
│   ├── pid_manager.py       (不变)
│   ├── checkpoint.py         (不变)
│   ├── workflow_service.py  [新建] 工作流执行
│   ├── role_service.py      [新建] 角色 CRUD
│   ├── dashboard_service.py [新建] 数据聚合
│   ├── recovery_service.py  [新建] 孤儿恢复
│   └── discovery_service.py [新建] PM 发现
└── config/loader.py         (不变)
```

### 2.2 分层依赖规则

```
┌─────────────────────────────────────────┐
│  CLI Layer (cli/*.py)                    │  只做 argparse + 调用 Service
│  depends on: services/*.py               │
├─────────────────────────────────────────┤
│  Service Layer (services/*.py)           │  业务逻辑
│  depends on: interfaces.py ABC           │
├─────────────────────────────────────────┤
│  Persistence Layer (persistence/*.py)    │  数据访问
│  implements: interfaces.py ABC           │
│  depends on: db.py (连接)                │
├─────────────────────────────────────────┤
│  Database Layer (db.py)                  │  纯连接 + schema
│  depends on: sqlite3                     │
└─────────────────────────────────────────┘

硬约束:
- CLI 不能直接 import StateDB
- CLI 不能直接执行 SQL
- 跨层调用必须通过 interfaces.py 的 ABC
```

---

## 三、核心接口定义

在现有 `interfaces.py` 基础上新增 3 个接口：

### 3.1 StepConditionEvaluator [新增]

```python
class StepConditionEvaluator(ABC):
    """统一条件表达式评估器。替代 orchestrator._check_condition()。"""

    @abstractmethod
    def evaluate(self, condition: str, context: dict) -> bool:
        """评估条件表达式。

        支持: ==, !=, >, <, >=, <=, in, not in, and, or, not
        示例:
          "verdict == 'approved' and complexity != 'high'"
          "test_count > 0 and coverage >= 0.8"
          "module in ['auth', 'api']"
        """
        ...

    @abstractmethod
    def validate(self, condition: str) -> tuple[bool, str | None]:
        """验证条件表达式。返回 (is_valid, error_message)。"""
        ...
```

### 3.2 RoleTemplateLoader [新增]

```python
class RoleTemplateLoader(ABC):
    """角色模板加载器。为 Phase 8c 模板系统预留。"""

    @abstractmethod
    def list_builtins(self) -> list[str]: ...
    @abstractmethod
    def list_user_templates(self) -> list[str]: ...
    @abstractmethod
    def load(self, name: str) -> "AgentConfig": ...
    @abstractmethod
    def validate_template(self, name: str) -> list[str]: ...
```

### 3.3 WorkflowTopology [新增]

```python
class WorkflowTopology(ABC):
    """图拓扑查询接口。为 Phase 8c 多 Agent 协作模式预留。"""

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

### 3.4 现有接口保持不变

`TaskStore`、`MetricsStore`、`EscalationStore`、`AgentRuntime`、`Notifier`、`StepHook` 保持不动。

---

## 四、逐模块重构详解

### 4.1 db.py — 瘦身为纯连接层

**移除的方法**（移到对应 Repository）：
- 任务: `insert_task`, `claim_pending_task`, `update_task_status`, `increment_retry`, `increment_rejection`, `set_task_context`, `get_task`, `get_running_tasks`, `search_tasks`, `get_pending_tasks`, `get_escalated_tasks`
- 步骤: `record_step`, `heartbeat`, `get_lost_agents`
- 指标: `record_metrics`, `get_metrics_summary`
- 升级: `record_escalation`, `get_pending_escalations`, `resolve_escalation`
- 清理: `prune_step_results`, `prune_agent_metrics`, `prune_heartbeat`, `cleanup_task_data`, `prune_all`

**保留的方法**：
- `connect()`, `_init_schema()`, `close()`, `vacuum()`
- 新增: `execute(sql, params)` — 只读快捷，直接转发到 `conn.execute(sql, params)`
- 新增: `execute_write(sql, params)` — 获取 `_write_lock` → 执行 → commit，用于 Repository 的写操作
- 新增: `execute_many(sql, params_list)` — 批量写入（如批量记录指标）

**注意**：`tasks` 表的 `retry_count` / `rejection_count` 自增逻辑（`UPDATE ... RETURNING`）在 `TaskRepository.increment_*()` 中实现，直接调用 `db.execute_write()`。

### 4.2 persistence/ — Repository 落地

三个 Repository 各自实现对应接口，持有 `StateDB` 引用：

| Repository | 实现接口 | 管理的数据 | 行数 |
|-----------|---------|-----------|------|
| `TaskRepository` | `TaskStore` | `tasks` + `step_results` + `heartbeat` + 清理 | ~120 |
| `MetricsRepository` | `MetricsStore` | `agent_metrics` + 指标查询 | ~80 |
| `EscalationRepository` | `EscalationStore` | `escalations` + 升级查询 | ~80 |

**设计决策**：`step_results` 和 `heartbeat` 归属 `TaskRepository` 而非独立 Repository，因为它们与任务生命周期紧密耦合，不存在跨任务的独立查询场景。

### 4.3 core/conditions.py — 统一条件引擎

```python
class ConditionEvaluator(StepConditionEvaluator):
    """基于 ast 模块的安全条件解析器。

    不使用 eval() — 用 ast.NodeVisitor 遍历语法树。
    不支持的语法抛出 ConditionSyntaxError，带行列号。
    """

class ConditionSyntaxError(Exception):
    """条件表达式语法错误，含 line:col 定位。"""
    def __init__(self, message: str, line: int = 0, col: int = 0): ...
```

### 4.4 orchestrator.py — 替换条件引擎

- 删除 `_check_condition()` 方法（~20 行）
- 构造时注入 `ConditionEvaluator`
- `get_ready_steps()` 和 `_handle_result()` 改用 `self._evaluator.evaluate()`

### 4.5 core/graph_engine.py — 增强

- 新增 `evaluate_edge(edge, context)` — 使用 `ConditionEvaluator`
- 新增 `get_active_successors(node_id, context)` — 获取条件满足的后继节点
- 实现 `WorkflowTopology` 接口
- `parallel_groups()` — 返回可并行执行的节点组

### 4.6 conductor.py — 拆出 3 个 Service

`conductor.py` (~300 行) 只保留：
- `_monitor_loop()` — 主循环
- `process_all()` / `process_one()` — 任务分发
- `_handle_signal()` — 信号处理
- `start()` / `stop()` / `status()` — 公共 API
- PID 文件管理

拆出到 Service：
- `_execute_task()` + 通知逻辑 → `services/workflow_service.py`
- `_recover_orphaned_tasks()` + `_kill_in_flight_agents()` → `services/recovery_service.py`
- `_discover_and_submit()` → `services/discovery_service.py`

### 4.7 cli/ — 新建 CLI 包

| 新文件 | 来源 | 职责 |
|--------|------|------|
| `cli/run.py` | `engine_cli.py` | `multiagent run` 命令 |
| `cli/conductor.py` | `conductor_cli.py` | `multiagent conductor` 命令 |
| `cli/role.py` | `role_cli.py` | `multiagent role` 命令 |
| `cli/workflow.py` | `workflow_cli.py` | `multiagent workflow` 命令 |
| `cli/pm.py` | `pm_cli.py` | `multiagent pm` 命令 |
| `cli/metrics.py` | `metrics_cli.py` | `multiagent metrics` 命令 |

每个文件只做 argparse + 调用 Service + 格式化输出。不 import StateDB。

### 4.8 services/ — 新增 Service

| 文件 | 职责 |
|------|------|
| `workflow_service.py` | 工作流执行逻辑（从 engine_cli + conductor 合并） |
| `role_service.py` | 角色 CRUD 逻辑（从 role_cli 抽离） |
| `dashboard_service.py` | 仪表盘数据聚合（供 CLI status 和 Web 共用） |
| `recovery_service.py` | 孤儿恢复 + Agent 进程清理 |
| `discovery_service.py` | PM GitHub Issue 自动发现 |

### 4.9 dashboard.py — 通过 Service 访问数据

```python
# 改前
from .db import StateDB
db = StateDB(find_state_db()); db.connect()
pending = db.get_pending_tasks()

# 改后
@app.route('/api/state')
def api_state():
    return jsonify(dashboard_service.queue_summary())
```

`DashboardService` 同时被 `dashboard.py` (Web) 和 `conductor.status()` (CLI) 使用。

### 4.10 notify.py — 拆分 i18n

```
notify.py        (~300行)  Notifier 实现 + HTTP 发送 + NotifierStepHook
notify_i18n.py   (~120行)  语言字典 + t() + set_language() + get_language()
```

`notify_i18n.py` 不 import 任何项目模块。

---

## 五、执行顺序

按依赖关系，自底向上：

```
Step 1: db.py 瘦身                  ← 无依赖
Step 2: persistence/ 落地            ← 依赖 Step 1
Step 3: core/conditions.py 新建      ← 无依赖
Step 4: graph_engine.py 增强         ← 依赖 Step 3
Step 5: orchestrator.py 替换条件     ← 依赖 Step 3
Step 6: services/ 新建 (5 个文件)    ← 依赖 Step 2+5
Step 7: cli/ 重组 (6 个文件)         ← 依赖 Step 6
Step 8: conductor.py 瘦身            ← 依赖 Step 6
Step 9: dashboard.py Service 层引入  ← 依赖 Step 6
Step 10: notify.py 拆分 i18n         ← 无依赖
Step 11: 全量回归测试                ← 依赖 Step 1-10
```

每步完成后运行 `python gates/regression.py` 确保不引入回归。

---

## 六、向后兼容策略

### 6.1 旧 CLI 文件处理

旧文件（`engine_cli.py`、`conductor_cli.py` 等）替换为**薄包装层**：

```python
# engine_cli.py — 向后兼容重新导出
"""DEPRECATED: import from cli.run instead."""
from .cli.run import parse_run_args, cmd_run  # noqa: F401
```

这保证：任何 `from .engine_cli import cmd_run` 的代码（如 `conductor.py`）继续工作。

### 6.2 命令签名不变

| 命令 | 保持 |
|------|------|
| `multiagent run <workflow.yaml>` | ✅ |
| `multiagent run <workflow.yaml> --task-id <id>` | ✅ |
| `multiagent conductor start/stop/status` | ✅ |
| `multiagent role create/list/show` | ✅ |
| `multiagent workflow validate/create` | ✅ |

---

## 七、风险与缓解

| 风险 | 缓解 |
|------|------|
| 大爆炸重构引入回归 | 每 Step 后运行 `python gates/regression.py` |
| `db.py` 方法迁移遗漏 | 每步用 `grep -rn "db\." src/` 检查残留的直接调用 |
| `step_results` 归属不清 | 明确归属 `TaskRepository`，不建独立 Repo |
| CLI 向后兼容破坏 | 保持子命令签名不变，旧文件留薄包装层重新导出 |
| `notify_i18n.py` key 不一致 | 抽取后用 `grep -roh 't("[^"]*")'` 交叉验证 |

---

## 七、成功标准

- [ ] `db.py` ≤ 150 行，只含连接管理 + schema
- [ ] `conductor.py` ≤ 300 行，只含调度循环
- [ ] 所有 `persistence/` Repository 真正实现对应接口
- [ ] `orchestrator._check_condition()` 替换为 `ConditionEvaluator`
- [ ] `graph_engine.WorkflowGraph` 实现 `WorkflowTopology`
- [ ] `cli/` 包下 6 个文件，无直接 `import StateDB`
- [ ] `DashboardService` 同时被 Web 和 CLI 使用
- [ ] `notify_i18n.py` 无项目模块 import
- [ ] 215 个回归测试全部通过
- [ ] `multiagent run / conductor start / dashboard` 功能不变
