# AgentForge Engine 架构与实现报告

> 版本：v1.0.0 | 最后更新：2026-06-28 | 覆盖范围：Phase 1 ~ Phase 8c

## 一、项目概述

AgentForge（原名 MultiAgent）是一个**项目无关的多 Agent 协作开发框架**。它编排 AI Agent 实例作为专业化角色（PM、Architect、Dev、Test），通过 Workflow Engine + Conductor 实现从需求到代码的全自动流水线。

### 1.1 核心设计理念

| 原则 | 实现 |
|------|------|
| **框架与项目分离** | `src/multiagent/` 纯 Python 框架；`architectures/` YAML + Markdown 配置 |
| **适配器模式** | `AgentAdapter` ABC -> 支持 Claude Code / OpenCode 等多种运行时 |
| **双层约束** | 硬约束 (`--disallowedTools`) + 软约束 (SKILL.md) |
| **线程安全** | SQLite WAL + `threading.Lock` + `busy_timeout` |
| **自动指标采集** | 每次 Agent 调用自动记录 Token/Cost/Duration |
| **零 Token 通知** | Discord Webhook 纯 HTTP POST，不经过 LLM |
| **AST 条件引擎** | 基于 `ast` 模块安全解析条件表达式，杜绝 `eval()` 安全风险 |
| **Repository 模式** | 持久化操作封装为 Repository 类，与业务逻辑分离 |

### 1.2 版本演进

| Version | Phase | 核心交付 |
|---------|-------|---------|
| **v1.0.0** | **8b+8c** | Condition Evaluator, Repository/Service 层, CLI 包, PM 验收标准, TDD 工作流, Swarms 模式, Architect Agent, 自动工作流选择, 基准测试, 3 种模式对比 (220+ tests) |
| **v0.8.0** | **8a** | CLI 包重构, Service 层 (Role/Dashboard/Recovery/Discovery/Workflow), Persistence 层 (Task/Escalation/Metrics Repository), 代码架构清理 |
| v0.7.0 | 7 | 3 层重试硬顶, 步骤生命周期钩子, i18n 中文通知, Git 工作流强制, 进程组管理, DB 自动清理 |
| v0.6.0 | 5 | Daemon 生产化, Dashboard, Discord, PM 发现, Prompt 模板 |
| v0.5.0 | 4 验证 | Prompt 修复, DevLog Web Server/Editor 管道实战 |
| v0.3.0 | 4 | Conductor + 全链路自动化, escalations 表 |
| v0.2.0 | 3 | Engine 生产化: `multiagent run`/`metrics`, 并行, 心跳 |
| v0.1.0 | 1+2 | Dev+Test 双 Agent, PM Agent, Flask TODO 验证 |

---

## 二、系统架构

### 2.1 总体架构

```
+-----------------------------------------------------------+
|                     CLI Layer                               |
|  multiagent pm | run | metrics | conductor | dashboard     |
|  role | workflow | checkpoint                              |
|  cli/ package: conductor.py metrics.py pm.py role.py       |
|               run.py workflow.py                           |
+-----------------------------------------------------------+
|                   Conductor (调度层)                        |
|  monitor_loop -> process_all -> Parallel Tasks              |
|  (slimmed: RecoveryService / DiscoveryService extracted)    |
+-----------------------------------------------------------+
|              WorkflowOrchestrator (编排层)                  |
|  ConditionEvaluator · DAG 依赖解析 · 条件分支              |
|  并行 fan-out · rejection loop                              |
+-----------------------------------------------------------+
|                AgentSpawner (执行层)                        |
|  spawn() · monitor() · metrics capture · prompt build      |
+-----------------------------------------------------------+
|             AgentAdapter (适配层)                           |
|  ClaudeCodeAdapter · OpenCodeAdapter                       |
+-----------------------------------------------------------+
|                  StateDB (持久层)                           |
|  tasks · step_results · heartbeat · agent_metrics          |
|  workflow_state · escalations                              |
|  (SQLite WAL + thread-safe, 379 行)                        |
+-----------------------------------------------------------+
|              Service Layer                                  |
|  RoleService · WorkflowService · DashboardService          |
|  RecoveryService · DiscoveryService · PidManager           |
|  CheckpointManager                                         |
+-----------------------------------------------------------+
|              Persistence Layer                              |
|  TaskRepository · EscalationRepository · MetricsRepository |
+-----------------------------------------------------------+
|              Agent Configurations                           |
|  roles.yaml · SKILL.md · workflow YAML · prompts/          |
+-----------------------------------------------------------+
```

### 2.2 数据流

```
1. Human: multiagent pm submit requirements.md
         |
2. StateDB: INSERT INTO tasks (status=pending)
         |
3. Conductor._monitor_loop(): SELECT pending tasks
         |
4. process_all(): ThreadPoolExecutor dispatch
         |
5. WorkflowOrchestrator.run(task)
         |
6. PM/Architect: AgentSpawner.spawn("claude -p <prompt>")
         -> monitor() -> parse JSON output -> _capture_metrics()
         |
7. Dev: (depends_on: pm_analyze) -> same spawn/monitor/metrics
         |
8. Test: (depends_on: dev_fix) -> verdict: approved/rejected
         |
9. Verdict Handling:
   - approved -> task completed
   - rejected -> back to Dev (max 3 times)
   - exhausted -> escalated -> Discord/CLI notification
```

---

## 三、核心组件详解

### 3.1 StateDB (`db.py`, 379 行)

**6 张表：**

| 表 | 用途 | 关键字段 |
|----|------|---------|
| `tasks` | 任务生命周期 | id, type, status, workflow_id, retry_count, rejection_count, dedup_key, context |
| `step_results` | 每步执行记录 | task_id, step_id, agent, status, output, error |
| `workflow_state` | 工作流状态 | workflow_id, status, current_task_id |
| `heartbeat` | Agent 进程心跳 | task_id, step_id, agent_pid, last_beat |
| `agent_metrics` | Token/Cost 指标 | input_tokens, output_tokens, cost_usd, duration_ms |
| `escalations` | 升级事件 | task_id, step_id, reason, status, resolution |

**线程安全设计：**
```python
# 连接时
check_same_thread=False
PRAGMA busy_timeout=5000
PRAGMA journal_mode=WAL

# 写操作
with self._write_lock:
    self.conn.execute(...)
    self.conn.commit()
```

**v1.0.0 新增方法：**

```python
class StateDB:
    # --- 原有方法 ---
    def create_task(...)
    def get_task(task_id)
    def update_task_status(...)
    def add_step_result(...)
    def add_metrics(...)

    # --- v1.0.0 Repository 委托方法 ---
    def get_tasks_by_status(status, limit)
    def get_pending_tasks(limit)
    def get_escalated_tasks()
    def search_tasks(keyword, status, limit)
    def get_metrics_by_agent(agent, limit)
    def get_metrics_summary()

    # 执行封装
    def execute(sql, params=None) -> list[dict]    # 通用查询
    def execute_write(sql, params=None)             # 通用写操作（带锁）
```

**数据模型：**
```python
@dataclass
class Task:
    id: str              # task-{uuid8}
    type: str            # feature | bug | debug | design | enhancement | docs
    source: str          # pm | github | cli
    workflow_id: str     # pm-dev-test-loop | pm-testfirst-dev-test | pm-architect-test
    current_step: str    # pm_analyze | dev_fix | test_verify
    status: str          # pending -> running -> completed | escalated | failed
    retry_count: int     # 步骤内重试次数
    rejection_count: int # Dev<->Test 打回次数
    dedup_key: str       # GitHub Issue 去重
    context: str         # JSON 上下文传递
```

### 3.2 AgentSpawner (`engine.py`, 236 行)

**职责**：Agent 进程的创建、监控、指标采集。

```python
class AgentSpawner:
    def spawn(task, step) -> subprocess.Popen
        # 构建 prompt -> 获取 agent config -> 确定 adapter -> 构建命令
        # 返回 subprocess.Popen 对象

    def monitor(task, step, process) -> StepResult
        # process.communicate(timeout) -> 解析 JSON -> 记录指标
        # 返回 StepResult(status, output, error)

    def _build_prompt(task, step) -> str
        # 加载 prompt 模板 -> 拼接 task context -> 附加 JSON schema 要求

    def _capture_metrics(task, step, result, raw)
        # 从 Claude JSON 输出提取: usage, modelUsage, cost, duration

    def reap_lost_agents(db) -> int
        # v0.7.0: 清理僵尸 Agent 进程
```

**Agent 子进程命令：**
```bash
claude -p "<prompt>"
  --output-format json           # 结构化输出
  --permission-mode acceptEdits  # 允许写文件
  --append-system-prompt-file SKILL.md  # 软约束
  --disallowedTools Write(tests/*),Edit(tests/*)  # 硬约束
  --bare                         # 无交互 UI
  --add-dir <project_root>       # 项目目录
```

### 3.3 WorkflowOrchestrator (`orchestrator.py`, 419 行)

**职责**：工作流 DAG 编排，支持条件分支、并行执行、打回循环。v1.0.0 集成 ConditionEvaluator 实现条件路由。

```python
class WorkflowOrchestrator:
    def load()                    # 从 YAML 加载步骤定义
    def get_ready_steps(task)     # 依赖解析 + 条件检查 (ConditionEvaluator)
    def execute_step(task, step)  # spawn -> monitor -> handle result
    def _handle_result()          # schema 校验 + 裁决处理
    def _handle_rejection()       # 打回重做 + escalation
    def run(task)                 # 主循环: ready -> execute -> loop
    def _execute_parallel()       # ThreadPoolExecutor fan-out
```

**ConditionEvaluator 集成：**

```python
# orchestrator.py:
from .core.conditions import ConditionEvaluator

# get_ready_steps() 中:
evaluator = ConditionEvaluator()
for step_def in step_defs:
    condition = step_def.get("condition")
    if condition and not evaluator.evaluate(condition, context):
        continue  # 条件不满足，跳过步骤
    ready_steps.append(step_def)
```

### 3.4 ConditionEvaluator (`core/conditions.py`, 136 行)

v1.0.0 新增的 AST 安全条件引擎，替代 `eval()` 实现工作流条件路由。

**核心设计：**

```python
class ConditionEvaluator:
    """Safely evaluate condition expressions against a context dict."""

    def evaluate(self, condition: str, context: dict) -> bool:
        # 1. ast.parse(condition, mode='eval') -- 安全解析
        # 2. _Evaluator visitor 遍历 AST
        # 3. 返回 bool 结果

    def validate(self, condition: str) -> tuple:
        # 语法验证 (不执行)
        # 返回 (is_valid, error_message)
```

**支持的语法：**

| 语法 | 示例 |
|------|------|
| 相等/不等 | `verdict == "approved"` / `status != "pending"` |
| 数值比较 | `files_changed > 5` / `tests_passed >= 10` |
| 集合包含 | `type in ("feature", "bug")` / `type not in ("docs",)` |
| 逻辑运算 | `complexity == "high" and priority == "critical"` |
| 路径访问 | `pm_analyze.output.complexity` (点分路径遍历 dict) |
| 否定 | `not test_verify.output.verdict == "rejected"` |

**安全机制：**

```python
class _Evaluator(ast.NodeVisitor):
    # 仅允许:
    # - ast.Compare (==, !=, >, <, >=, <=, in, not in)
    # - ast.BoolOp (and, or)
    # - ast.UnaryOp (not)
    # - ast.Attribute (点分路径)
    # - ast.Name / ast.Constant / ast.List / ast.Tuple
    # - 拒绝: Call, FunctionDef, Import, Exec, 等危险节点
```

### 3.5 Conductor (`conductor.py`, 679 行)

**职责**：长期运行的调度守护进程。v1.0.0 通过 Service 层瘦身，将角色管理、工作流管理、Dashboard 数据、自动发现等职责提取到独立的 Service 类。

```python
class Conductor:
    def start(blocking)           # 启动监控循环
    def stop()                    # 停止
    def process_all()             # 并行分发所有 pending 任务
    def _monitor_loop()           # 主轮询循环
    def _check_escalations()      # 检测升级事件
    def _check_stop_signal()      # DB 停止信号
    def _notify(event, ...)       # 调用所有 notifier
```

**并发模型：**
- `ThreadPoolExecutor(max_workers=N)`
- 每个任务独立线程执行完整工作流
- in-flight 任务跟踪 (`_in_flight: dict[str, InFlightTask]`)
- Slimmed via: `RecoveryService` (孤儿恢复), `DiscoveryService` (自动发现)

**进度计算：**
```python
def _calculate_task_progress(db, task_id):
    # 阶段权重: pm=10%, dev=30-60%, test=80-95%, done=100%
    # 子任务: PM task_breakdown[N] -> Dev subtasks_completed[M]
    # 进度 = stage_weight + M/N * phase_weight
```

### 3.6 AgentAdapter (`adapters/`)

**抽象基类：**
```python
class AgentAdapter(ABC):
    def build_command(config, prompt, step) -> list[str]
    def parse_output(stdout, stderr) -> StepResult
    def get_tool_restriction_flags(permissions) -> (allow, disallow)
```

**已实现适配器：**

| 适配器 | 运行时 | 输出格式 |
|--------|--------|---------|
| `ClaudeCodeAdapter` | `claude -p` | `--output-format json` |
| `OpenCodeAdapter` | `opencode` | (骨架) |

### 3.7 通知系统 (`notify.py`, 372 行 + `notify_i18n.py`, 131 行)

```
create_notifier(webhook_url, channel_id, bot_token)
    |
    +-- DiscordNotifier(webhook_url)
    |   +-- HTTP POST -> Discord embed
    |
    +-- DiscordChannelNotifier(bot_token, channel_id)
        +-- Discord REST API -> 频道消息
```

**国际化（`notify_i18n.py`）：**

```python
class I18nManager:
    def __init__(self, lang="zh"):
        # 加载翻译表 (zh / en)
    def get(self, key, **kwargs) -> str:
        # 获取翻译文本，支持 format 参数
    @property
    def lang(self) -> str:
        # 当前语言
```

**事件类型与颜色：**

| 事件 | Embed 颜色 | 触发时机 |
|------|-----------|---------|
| `started` | 蓝色 `#3498DB` | 任务开始执行 |
| `completed` | 绿色 `#2ECC71` | Pipeline 完成 |
| `failed` | 红色 `#E74C3C` | 执行失败 |
| `escalated` | 橙色 `#E67E22` | 3 次打回/异常 |

### 3.8 Web Dashboard (`dashboard.py`, 310 行)

Flask 应用，暗色 GitHub 风格，15 秒自动刷新。v1.0.0 使用 DashboardService 聚合数据。

**路由：**
- `GET /` -- 完整仪表盘 HTML（统计卡片 + 进度条 + 任务表）
- `GET /designer` -- Workflow Designer V2（SVG 拖拽设计）
- `GET /commands` -- Command Center
- `GET /api/state` -- JSON API（pending/running/escalated 计数）
- `GET /api/status` -- 完整状态 API
- `GET /api/timeseries` -- 时间序列数据
- `GET /api/workflow-dag` -- DAG 图数据

**前端资源：**
- `templates/base.html` -- 基础布局 (导航 + CDN)
- `templates/index.html` -- Pipeline Monitoring
- `templates/designer.html` -- Workflow Designer V2
- `templates/commands.html` -- Command Center
- `static/dashboard.css` -- 暗色主题 CSS
- `static/dashboard.js` -- Charts + Search + DAG
- `static/designer.js` -- Designer 拖拽 + 边类型 + localStorage

### 3.9 RoleService (`services/role_service.py`, 538 行)

v1.0.0 新增：角色配置的加载、校验、模板管理。

```python
class RoleService:
    def __init__(self, roles_path: str)
    def get_role(self, name: str) -> dict | None
    def list_roles(self) -> list[str]
    def list_templates(self) -> list[dict]
    def create_from_template(self, name: str, template: str, overrides: dict) -> dict
    def validate(self, roles: dict) -> list[str]
    def get_all_output_required(self) -> dict[str, list[str]]
```

### 3.10 WorkflowService (`services/workflow_service.py`, 311 行)

v1.0.0 新增：工作流 YAML 的加载、校验、模板生成、DAG 可视化。

```python
class WorkflowService:
    def __init__(self, workflows_dir: str)
    def load(self, path: str) -> dict
    def list_workflows(self) -> list[dict]
    def validate(self, workflow: dict, role_service) -> list[str]
    def create_from_template(self, template_name: str, overrides: dict) -> dict
    def generate_graph(self, workflow: dict) -> str  # Mermaid 格式
```

**工作流模板（与 Generator 配合）：**

| 模板 | 拓扑 | 步骤数 |
|------|------|--------|
| `pm-dev-test` | 线性 | 3 |
| `pm-testfirst-dev-test` | TDD 线性 | 4 |
| `pm-architect-test` | 线性 | 4 |
| `diamond` | 菱形并行 | 4 |
| `pm-dev-reviewer-test` | 线性 | 4 |

### 3.11 服务层与持久层

**Services 包 (`services/`):**

| 服务 | 行数 | 职责 |
|------|------|------|
| `RoleService` | 538 | 角色加载/校验/模板/创建 |
| `WorkflowService` | 311 | 工作流加载/校验/模板/图形 |
| `DashboardService` | 232 | Dashboard 数据聚合 |
| `RecoveryService` | 197 | 中断任务恢复逻辑 |
| `DiscoveryService` | 108 | GitHub Issue 自动发现 |
| `CheckpointManager` | 107 | 任务状态保存/恢复 |
| `PidManager` | 75 | PID 文件管理 |

**Persistence 包 (`persistence/`):**

| 仓库 | 行数 | 职责 |
|------|------|------|
| `TaskRepository` | 326 | 任务 CRUD、状态查询、搜索 |
| `EscalationRepository` | 55 | 升级事件创建/查询/解决 |
| `MetricsRepository` | 90 | 指标记录/聚合/统计 |

---

## 四、Agent 角色定义

### 4.1 PM Agent

| 属性 | 值 |
|------|-----|
| 模型 | deepseek-v4-pro |
| 超时 | 300s |
| 写权限 | docs/, .agents/memory/pm/ |
| 禁写 | src/, scripts/, tests/ |
| 软约束 | `pm/SKILL.md` |
| 输出 (v1.0.0) | root_cause, target_module, complexity, complexity_rationale, task_breakdown, **acceptance_criteria**, estimated_files |

**v1.0.0 新增：acceptance_criteria 字段**
```json
{
  "acceptance_criteria": [
    "Given <前提条件>, When <操作>, Then <预期结果>"
  ]
}
```
PM Agent 使用 Given/When/Then 格式编写验收标准，作为 Dev 实现和 Test 验证的依据。

### 4.2 Architect Agent

v1.0.0 新增角色，用于系统设计和技术选型任务。

| 属性 | 值 |
|------|-----|
| 模型 | deepseek-v4-pro |
| 超时 | 900s |
| 写权限 | docs/architecture/, .agents/memory/architect/ |
| 禁写 | src/, tests/ |
| 软约束 | `architect/SKILL.md` |
| 输出 | architecture_doc, adrs, component_diagram, tech_stack, tradeoffs |

**使用场景：**
- 自动选择：`select_workflow()` 在 `type=design` 或 `complexity=high` 时自动匹配
- 手动指定：`multiagent run architectures/dev-test-loop/workflow/pm-architect-test.yaml`

### 4.3 Dev Agent

| 属性 | 值 |
|------|-----|
| 模型 | deepseek-v4-pro |
| 超时 | 600s (global default), dev_fix: 1800s |
| 写权限 | src/, scripts/, .agents/memory/dev/ |
| 禁写 | tests/, docs/ |
| 软约束 | `dev/SKILL.md` |
| 输出 (v1.0.0) | branch_name, files_changed, **implementation_summary** |

**v1.0.0 新增：implementation_summary 字段**
```json
{
  "implementation_summary": "修改了 login.py 中的连接超时配置，从 30s 改为 3s"
}
```

### 4.4 Test Agent

| 属性 | 值 |
|------|-----|
| 模型 | **deepseek-v4-flash** (v1.0.0 从 chat 升级为 flash) |
| 超时 | 300s (global default), test_verify: 900s |
| 写权限 | tests/, .agents/memory/test/ |
| 禁写 | src/, scripts/ |
| 软约束 | `test/SKILL.md` |
| 输出 (v1.0.0) | verdict, test_summary, **tests_total**, **tests_passed**, **tests_failed** |

**v1.0.0 新增字段：**
```json
{
  "verdict": "approved",
  "test_summary": "全部 5 个测试通过",
  "tests_total": 5,
  "tests_passed": 5,
  "tests_failed": 0
}
```

### 4.5 双层权限模型

| 层级 | 机制 | 效果 |
|------|------|------|
| **硬约束** | `claude --disallowedTools Write(tests/*),Edit(tests/*)` | 框架层拦截，Agent 无法绕过 |
| **软约束** | `claude --append-system-prompt-file SKILL.md` | Agent 自行遵守 |

---

## 五、Prompt 工程

### 5.1 Prompt 构建流程

```
_build_prompt(task, step)
    |
    +-- 1. 加载 prompt 模板 (architectures/*/prompts/<agent>.md)
    |      +-- 含 few-shot examples + 字段 schema
    |
    +-- 2. 拼接任务描述 (step.description)
    |
    +-- 3. 拼接任务上下文 (task.id, task.type)
    |
    +-- 4. 拼接步骤输入 (step.input -> task.context or prev_step.output)
    |
    +-- 5. 附加 JSON schema 要求
           "You MUST return a JSON block with these exact fields: [...]"
```

### 5.2 Prompt 模板示例

**PM 模板 (`pm.md`)：**
```markdown
# PM Agent Prompt Template
## Your Task: Analyze the requirements document
## Output Format: JSON with root_cause, target_module, complexity,
##                complexity_rationale, task_breakdown, acceptance_criteria,
##                estimated_files
## Few-Shot Example: [search command -> analysis with 4 subtasks]
```

**Test 模板 (`test.md`)：**
```markdown
# Test Agent Prompt Template
## Output Format: {"verdict": "approved", "test_summary": "...",
##                 "tests_total": 5, "tests_passed": 5, "tests_failed": 0}
## Verdict must be "approved" (lowercase) or "rejected" (lowercase)
## tests_total / tests_passed / tests_failed are integers
## Approved Example / Rejection Example
```

---

## 六、目录结构

```
src/multiagent/                 # 框架源码 (~7,500 行)
+-- __init__.py
+-- db.py                       # StateDB -- SQLite WAL + 6 表 + 线程安全 (379 行)
+-- engine.py                   # AgentSpawner -- 进程组管理 + metrics (236 行)
+-- engine_cli.py               # multiagent run CLI
+-- orchestrator.py             # WorkflowOrchestrator + ConditionEvaluator (419 行)
+-- conductor.py                # Conductor -- 调度守护进程 + 孤儿恢复 (679 行)
+-- conductor_cli.py            # Conductor CLI -- double-fork daemon
+-- pm_cli.py                   # 统一命令分发
+-- metrics_cli.py              # Metrics CLI
+-- metrics.py                  # 指标聚合/导出
+-- notify.py                   # Discord 通知 + retry + debounce (372 行)
+-- notify_i18n.py              # [v1.0.0] 通知国际化 i18n (131 行)
+-- dashboard.py                # Flask 路由 + API (310 行)
+-- pm_discover.py              # GitHub Issues 自动发现
+-- role_cli.py                 # 角色管理 CLI
+-- workflow_cli.py             # 工作流 CLI + 验证引擎
+-- interfaces.py               # ABC 抽象接口 (150 行)
|
+-- cli/                        # [v1.0.0] CLI 命令包
|   +-- __init__.py
|   +-- conductor.py            # Conductor CLI (513 行)
|   +-- metrics.py              # Metrics CLI (204 行)
|   +-- pm.py                   # PM CLI (391 行)
|   +-- role.py                 # Role CLI (337 行)
|   +-- run.py                  # Run CLI (237 行)
|   +-- workflow.py             # Workflow CLI (429 行)
|
+-- templates/                  # Jinja2 模板
|   +-- base.html               # 基础布局 (导航 + CDN)
|   +-- index.html              # Pipeline Monitoring
|   +-- designer.html           # Workflow Designer V2
|   +-- commands.html           # Command Center
|
+-- static/                     # 静态资源
|   +-- dashboard.css           # 暗色主题 CSS
|   +-- dashboard.js            # Charts + Search + DAG
|   +-- designer.js             # Designer 拖拽 + 边类型 + localStorage
|
+-- adapters/                   # Agent 适配器
|   +-- __init__.py             # 适配器注册表
|   +-- base.py                 # AgentAdapter ABC
|   +-- claude_code.py          # ClaudeCodeAdapter
|   +-- opencode.py             # OpenCodeAdapter (骨架)
|
+-- config/                     # 配置发现
|   +-- __init__.py
|   +-- loader.py               # 统一路径发现
|
+-- core/                       # 核心引擎
|   +-- __init__.py
|   +-- graph_engine.py         # WorkflowGraph DAG
|   +-- conditions.py           # [v1.0.0] ConditionEvaluator (136 行)
|   +-- progress.py             # 进度计算
|
+-- runtime/                    # 运行时
|   +-- __init__.py
|   +-- registry.py             # AgentRegistry
|
+-- services/                   # [v1.0.0] 服务层
|   +-- __init__.py
|   +-- checkpoint.py           # 检查点管理 (107 行)
|   +-- pid_manager.py          # PID 文件管理 (75 行)
|   +-- dashboard_service.py    # Dashboard 数据聚合 (232 行)
|   +-- discovery_service.py    # Issue 自动发现 (108 行)
|   +-- recovery_service.py     # 中断恢复 (197 行)
|   +-- role_service.py         # 角色管理 (538 行)
|   +-- workflow_service.py     # 工作流管理 (311 行)
|
+-- persistence/                # [v1.0.0] 持久化仓库
    +-- __init__.py
    +-- task_repo.py            # TaskRepository (326 行)
    +-- escalation_repo.py      # EscalationRepository (55 行)
    +-- metrics_repo.py         # MetricsRepository (90 行)

architectures/dev-test-loop/    # Agent 配置
+-- config/roles.yaml           # Agent 角色 + 权限 + 模型 (7 agent types)
+-- workflow/                   # 工作流定义
|   +-- pm-dev-test.yaml        # Traditional 模式: PM->Dev->Test
|   +-- pm-testfirst-dev-test.yaml # TDD 模式: PM->Test->Dev->Test (默认)
|   +-- pm-architect-test.yaml  # Architect 模式: PM->Architect->Dev->Test
|   +-- pm-dev-reviewer-test.yaml # 四步: PM->Dev->Reviewer->Test
|   +-- dev-test.yaml           # 精简: Dev->Test
|   +-- swarm-dev.yaml          # [v1.0.0] Swarms: 多 Dev 并行
|   +-- bench-*.yaml            # 基准测试工作流 (4 个)
+-- skills/                     # Agent 软约束 (SKILL.md)
|   +-- pm/SKILL.md
|   +-- dev/SKILL.md
|   +-- test/SKILL.md
|   +-- conductor/SKILL.md
|   +-- architect/SKILL.md      # [v1.0.0]
+-- prompts/                    # Prompt 模板
|   +-- pm.md
|   +-- dev.md
|   +-- test.md
+-- templates/                  # 骨架模板
    +-- SKILL.md.skeleton
    +-- prompt.md.skeleton

gates/                          # 门禁测试 (21 文件, 232 tests)
+-- regression.py               # 总入口
+-- test_db.py                  # 5 tests
+-- test_adapters.py            # 5 tests
+-- test_orchestrator.py        # 6 tests
+-- test_engine_cli.py          # 11 tests
+-- test_pm_engine.py           # 8 tests
+-- test_parallel.py            # 6 tests
+-- test_heartbeat.py           # 7 tests
+-- test_metrics_cli.py         # 9 tests
+-- test_conductor.py           # 19 tests
+-- test_retry_cap.py           # 5 tests
+-- test_hooks.py               # 4 tests
+-- test_db_cleanup.py          # 10 tests
+-- test_engine_process.py      # 7 tests
+-- test_dashboard.py           # 20 tests
+-- test_notify.py              # 19 tests
+-- test_graph_engine.py        # 21 tests
+-- test_agent_registry.py      # 12 tests
+-- test_pm_search.py           # 7 tests
+-- test_workflow_cli.py        # 20 tests
+-- test_topology.py            # 19 tests
+-- test_conditions.py          # [v1.0.0] 11 tests

docs/                           # 文档与报告
+-- engine-architecture.md      # 本文档
+-- phase3-report.md            # Phase 3 实施报告
+-- phase4-report.md            # Phase 4 实施报告
+-- phase5-report.md            # Phase 5 实施报告

examples/devlog/                # DevLog 验证项目 (独立 Git)
+-- src/devlog/                 # 7 模块, ~915 行
+-- tests/                      # 115 tests, 92.16% 覆盖率
```

---

## 七、技术决策记录

| 决策 | 理由 | 影响 |
|------|------|------|
| SQLite 而非 PostgreSQL | 零依赖部署，单文件数据库 | 需手动管理并发（WAL + Lock） |
| Flask 而非 FastAPI | 轻量依赖，DevLog 已使用 | Dashboard 共享 Flask 模式 |
| `claude -p` 子进程而非 API | 直接使用 Claude Code 订阅 | 每个 Agent 是独立进程 |
| CLI 而非 gRPC/REST | 简单可靠，符合 Unix 哲学 | 跨语言调用困难 |
| daemon fork 而非 systemd | 平台无关，简单可控 | 需自行管理 PID/日志 |
| ThreadPoolExecutor 而非 asyncio | blocking 工作负载，线程更简单 | 每个任务占一个线程 |
| YAML 而非 JSON 配置 | 可读性好，支持注释 | 需 pyyaml 依赖 |
| WAL 模式 SQLite | 并发读写，crash 安全 | 需 busy_timeout 处理锁冲突 |
| AST 而非 eval() | 安全解析条件表达式 | 有限语法子集 |

---

## 八、Phase 8: v1.0 交付

### 8.1 Phase 8a -- 代码架构清理 (v0.8.0)

| 交付件 | 说明 | 关键文件 |
|--------|------|---------|
| CLI 包重构 | 将分散的 CLI 命令重组为 `cli/` 包 | `cli/conductor.py`, `cli/pm.py`, `cli/role.py`, `cli/run.py`, `cli/workflow.py`, `cli/metrics.py` |
| Service 层 | 提取业务逻辑到 7 个 Service 类 | `services/role_service.py`, `services/workflow_service.py`, `services/dashboard_service.py`, `services/recovery_service.py`, `services/discovery_service.py` |
| Persistence 层 | Repository 模式封装 DB 操作 | `persistence/task_repo.py`, `persistence/escalation_repo.py`, `persistence/metrics_repo.py` |
| DashboardService | Dashboard 数据聚合分离 | `services/dashboard_service.py` |
| notify_i18n | 通知国际化提取为独立模块 | `notify_i18n.py` |

### 8.2 Phase 8b -- 功能增强 (v1.0.0)

| 交付件 | 说明 |
|--------|------|
| ConditionEvaluator | AST 安全条件引擎，替换 `eval()` |
| PM 验收标准 | `acceptance_criteria` 字段，Given/When/Then 格式 |
| Dev 实现摘要 | `implementation_summary` 字段 |
| Test 统计字段 | `tests_total`, `tests_passed`, `tests_failed` 字段 |
| Architect Agent | 新角色，架构设计与技术选型 |
| TDD 工作流 | `pm-testfirst-dev-test.yaml` -- 先测试后实现 |
| Architect 工作流 | `pm-architect-test.yaml` -- 含架构设计步骤 |
| Swarms 模式 | `swarm-dev.yaml` -- 多 Dev 并行执行 |
| 自动工作流选择 | `select_workflow()` 基于任务类型/复杂度 |
| Role 模板系统 | `role create --from-template`, `role list-templates` |
| Workflow 管理 | `workflow list`, `workflow validate`, `workflow graph` |

### 8.3 Phase 8c -- 基准测试与稳定性

| 交付件 | 说明 |
|--------|------|
| 基准测试工作流 | `bench-single-agent.yaml`, `bench-dev-only.yaml`, `bench-pm-only.yaml`, `bench-test-only.yaml` |
| 三种模式对比 | 单 Agent ($0.43/108s) vs TDD ($1.06/241s) vs Swarms ($1.41/330s) |
| 测试覆盖 | 21 测试文件, 232 个测试用例 |
| 稳定性改进 | ConditionEvaluator 安全验证, test_conditions.py (11 tests) |

---

## 附录 A: CLI 命令参考

```bash
# 任务管理
multiagent pm init                    # 初始化 .pm/ 目录
multiagent pm submit <req.md>         # 提交需求
multiagent pm list                    # 列出任务
multiagent pm status <id>             # 查看任务详情
multiagent pm search                  # 搜索任务

# 工作流
multiagent run <workflow.yaml>        # 执行工作流
multiagent run --dry-run              # 干运行验证
multiagent workflow list              # [v1.0.0] 列出工作流
multiagent workflow validate          # [v1.0.0] 验证工作流

# Conductor
multiagent conductor start            # 启动守护进程
multiagent conductor start --workers 5 --pm-auto-discover --discord-webhook <URL>
multiagent conductor stop             # 停止
multiagent conductor restart          # 重启
multiagent conductor status           # 查看状态
multiagent conductor alerts           # 查看升级
multiagent conductor retry <id>       # 重试
multiagent conductor reject <id>      # 放弃

# 角色管理
multiagent role list                  # [v1.0.0] 列出角色
multiagent role show <name>           # [v1.0.0] 查看角色
multiagent role create --from-template <template>  # [v1.0.0] 创建角色
multiagent role list-templates        # [v1.0.0] 列出模板
multiagent role validate <file>       # [v1.0.0] 验证角色

# 监控
multiagent metrics                    # Token/Cost 统计
multiagent metrics --agent dev        # 按 Agent 过滤
multiagent dashboard                  # Web 仪表盘

# 测试
python gates/regression.py            # 全部门禁 (232 tests)
```

## 附录 B: 环境依赖

```
Python >= 3.10
pyyaml >= 6.0        # YAML 配置解析
flask >= 3.0         # Web Dashboard (可选)
claude CLI           # Agent 运行时
gh CLI               # GitHub Issues 发现 (可选)
```

## 附录 C: Benchmark 结果

### 测试配置

- **硬件**: 开发机 (Linux 6.8, 16GB RAM)
- **任务**: URL Parser 实现（完整需求含验收标准）
- **模型**: deepseek-v4-pro (PM/Dev/Architect), deepseek-v4-flash (Test/Conductor)
- **任务 ID**: task-a3d9ec30 (单 Agent), task-b9e93846 (TDD), task-42e02055 (Swarms)
- **运行次数**: 每种模式 1 次（2026-06-28）

### 执行时间

| 模式 | 步骤 | 执行时间 | Agent 调用 | 状态 |
|------|------|---------|:---:|------|
| 单 Agent | 1 | 108s | 1 | ✅ |
| TDD (PM+Dev+Test) | 4 | 241s | 3 | ✅ |
| Swarms (多 Dev 并行) | 5 | 330s | 5 | ✅ |

### Token 消耗

| 模式 | 总成本 | 输入 Token | 输出 Token | 总 Token |
|------|-------|-----------|-----------|---------|
| 单 Agent | $0.43 | 16,439 | 9,471 | 25,910 |
| TDD | $1.06 | 50,965 | 20,076 | 71,041 |
| Swarms | $1.41 | 64,239 | 25,423 | 89,662 |

### 结论

1. **单 Agent ($0.43, 108s)** 适合简单独立任务，成本最低但没有验收标准和测试先行保障
2. **TDD ($1.06, 241s)** 生产推荐选择，2.5x 成本换来 Test 先写失败测试 + Dev 最小实现 + 再次验证的完整闭环
3. **Swarms ($1.41, 330s)** 适合可拆解为独立子任务的复杂需求，3 个 Dev 并行，3.3x 成本
4. 多 Agent 协作的额外开销 (~2.5x) 是专业化分工和验收流程的合理成本

> ⚠️ 以上数据均来自实际实验记录，非臆测。不同任务的实际 Token 消耗会有差异。
> 基准测试配置文件见 `architectures/dev-test-loop/workflow/bench-*.yaml`
