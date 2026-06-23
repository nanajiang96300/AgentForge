# AgentForge Engine 架构与实现报告

> 版本：v0.6.0 | 最后更新：2026-06-23 | 覆盖范围：Phase 1 ~ Phase 5

## 一、项目概述

AgentForge（原名 MultiAgent）是一个**项目无关的多 Agent 协作开发框架**。它编排 Claude Code 实例作为专业化 Agent（PM、Dev、Test），通过 Workflow Engine + Conductor 实现从需求到代码的全自动流水线。

### 1.1 核心设计理念

| 原则 | 实现 |
|------|------|
| **框架与项目分离** | `src/multiagent/` 纯 Python 框架；`architectures/` YAML + Markdown 配置 |
| **适配器模式** | `AgentAdapter` ABC → 支持 Claude Code / OpenCode 等多种运行时 |
| **双层约束** | 硬约束 (`--disallowedTools`) + 软约束 (SKILL.md) |
| **线程安全** | SQLite WAL + `threading.Lock` + `busy_timeout` |
| **自动指标采集** | 每次 Agent 调用自动记录 Token/Cost/Duration |
| **零 Token 通知** | Discord Webhook 纯 HTTP POST，不经过 LLM |

### 1.2 版本演进

| Version | Phase | 核心交付 |
|---------|-------|---------|
| v0.1.0 | 1+2 | Dev+Test 双 Agent, PM Agent, Flask TODO 验证 |
| v0.2.0 | 3 | Engine 生产化: `multiagent run`/`metrics`, 并行, 心跳, DevLog (115 tests, 92%) |
| v0.3.0 | 4 | Conductor + 全链路自动化, escalations 表 |
| v0.5.0 | 4 验证 | Prompt 修复, DevLog Web Server/Editor 管道实战 |
| v0.6.0 | 5 | Daemon 生产化, Dashboard, Discord, PM 发现, Prompt 模板 |

---

## 二、系统架构

### 2.1 总体架构

```
┌─────────────────────────────────────────────────────────┐
│                     CLI Layer                            │
│  multiagent pm | run | metrics | conductor | dashboard   │
├─────────────────────────────────────────────────────────┤
│                   Conductor (调度层)                     │
│  monitor_loop → process_all → Parallel Tasks             │
├─────────────────────────────────────────────────────────┤
│              WorkflowOrchestrator (编排层)               │
│  DAG依赖解析 · 条件分支 · 并行fan-out · rejection loop    │
├─────────────────────────────────────────────────────────┤
│                AgentSpawner (执行层)                      │
│  spawn() · monitor() · metrics capture · prompt build    │
├─────────────────────────────────────────────────────────┤
│             AgentAdapter (适配层)                         │
│  ClaudeCodeAdapter · OpenCodeAdapter                     │
├─────────────────────────────────────────────────────────┤
│                  StateDB (持久层)                         │
│  tasks · step_results · heartbeat · agent_metrics        │
│  workflow_state · escalations                            │
│  (SQLite WAL + thread-safe)                              │
├─────────────────────────────────────────────────────────┤
│              Agent Configurations                         │
│  roles.yaml · SKILL.md · workflow YAML · prompts/        │
└─────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
1. Human: multiagent pm submit requirements.md
         ↓
2. StateDB: INSERT INTO tasks (status=pending)
         ↓
3. Conductor._monitor_loop(): SELECT pending tasks
         ↓
4. process_all(): ThreadPoolExecutor dispatch
         ↓
5. cmd_run() → WorkflowOrchestrator.run(task)
         ↓
6. PM: AgentSpawner.spawn("claude -p <prompt>")
         → monitor() → parse JSON output → _capture_metrics()
         ↓
7. Dev: (depends_on: pm_analyze) → same spawn/monitor/metrics
         ↓
8. Test: (depends_on: dev_fix) → verdict: approved/rejected
         ↓
9. Verdict Handling:
   - approved → task completed ✅
   - rejected → back to Dev (max 3 times) 🔄
   - exhausted → escalated → Discord/CLI notification 🚨
```

---

## 三、核心组件详解

### 3.1 StateDB (`db.py`, 370行)

**6 张表：**

| 表 | 用途 | 关键字段 |
|----|------|---------|
| `tasks` | 任务生命周期 | id, type, status, workflow_id, retry_count, rejection_count, dedup_key |
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

**数据模型：**
```python
@dataclass
class Task:
    id: str              # task-{uuid8}
    type: str            # feature | bug | debug | enhancement | docs
    source: str          # pm | github | cli
    workflow_id: str     # pm-dev-test-loop
    current_step: str    # pm_analyze | dev_fix | test_verify
    status: str          # pending → running → completed | escalated | failed
    retry_count: int     # 步骤内重试次数
    rejection_count: int # Dev↔Test 打回次数
    dedup_key: str       # GitHub Issue 去重
```

### 3.2 AgentSpawner (`engine.py`, 135行)

**职责**：Agent 进程的创建、监控、指标采集。

```python
class AgentSpawner:
    def spawn(task, step) → subprocess.Popen
        # 构建 prompt → 获取 agent config → 确定 adapter → 构建命令
        # 返回 subprocess.Popen 对象

    def monitor(task, step, process) → StepResult
        # process.communicate(timeout) → 解析 JSON → 记录指标
        # 返回 StepResult(status, output, error)

    def _build_prompt(task, step) → str
        # 加载 prompt 模板 → 拼接 task context → 附加 JSON schema 要求

    def _capture_metrics(task, step, result, raw)
        # 从 Claude JSON 输出提取: usage, modelUsage, cost, duration
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

### 3.3 WorkflowOrchestrator (`orchestrator.py`, 362行)

**职责**：工作流 DAG 编排，支持条件分支、并行执行、打回循环。

```python
class WorkflowOrchestrator:
    def load()                    # 从 YAML 加载步骤定义
    def get_ready_steps(task)     # 依赖解析 + 条件检查
    def execute_step(task, step)  # spawn → monitor → handle result
    def _handle_result()          # schema 校验 + 裁决处理
    def _handle_rejection()       # 打回重做 + escalation
    def run(task)                 # 主循环: ready → execute → loop
    def _execute_parallel()       # ThreadPoolExecutor fan-out
```

**工作流 YAML 定义 (`pm-dev-test.yaml`)：**
```yaml
steps:
  - id: pm_analyze
    agent: pm
    output: {required: [root_cause, task_breakdown, ...]}
    on_success: {to_state: assigned}

  - id: dev_fix
    agent: dev
    depends_on: pm_analyze
    output: {required: [branch_name, files_changed]}

  - id: test_verify
    agent: test
    depends_on: dev_fix
    output: {required: [verdict, test_summary]}
    on_verdict_rejected: {next: dev_fix}
    on_verdict_approved: {action: mark_complete}

error_policy:
  max_rejections: 3
```

### 3.4 Conductor (`conductor.py`, 702行)

**职责**：长期运行的调度守护进程。

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
- 每个任务独立线程执行完整 PM→Dev→Test
- in-flight 任务跟踪 (`_in_flight: dict[str, InFlightTask]`)

**进度计算：**
```python
def _calculate_task_progress(db, task_id):
    # 阶段权重: pm=10% dev=30-60% test=80-95% done=100%
    # 子任务: PM task_breakdown[N] → Dev subtasks_completed[M]
    # 进度 = stage_weight + M/N × phase_weight
```

### 3.5 AgentAdapter (`adapters/`)

**抽象基类：**
```python
class AgentAdapter(ABC):
    def build_command(config, prompt, step) → list[str]
    def parse_output(stdout, stderr) → StepResult
    def get_tool_restriction_flags(permissions) → (allow, disallow)
```

**已实现适配器：**

| 适配器 | 运行时 | 输出格式 |
|--------|--------|---------|
| `ClaudeCodeAdapter` | `claude -p` | `--output-format json` |
| `OpenCodeAdapter` | `opencode` | (骨架) |

### 3.6 通知系统 (`notify.py`, 208行)

```
create_notifier(webhook_url, channel_id, bot_token)
    │
    ├── DiscordNotifier(webhook_url)
    │   └── HTTP POST → Discord embed
    │
    └── DiscordChannelNotifier(bot_token, channel_id)
        └── Discord REST API → 频道消息
```

**事件类型与颜色：**

| 事件 | Embed 颜色 | 触发时机 |
|------|-----------|---------|
| `started` | 蓝色 `#3498DB` | 任务开始执行 |
| `completed` | 绿色 `#2ECC71` | Pipeline 完成 |
| `failed` | 红色 `#E74C3C` | 执行失败 |
| `escalated` | 橙色 `#E67E22` | 3次打回/异常 |

### 3.7 Web Dashboard (`dashboard.py`, 242行)

Flask 应用，暗色 GitHub 风格，15 秒自动刷新。

**路由：**
- `GET /` — 完整仪表盘 HTML（统计卡片 + 进度条 + 任务表）
- `GET /api/state` — JSON API（pending/running/escalated 计数）

---

## 四、Agent 角色定义

### 4.1 PM Agent

| 属性 | 值 |
|------|-----|
| 模型 | deepseek/deepseek-chat |
| 时超 | 300s |
| 写权限 | docs/ |
| 禁写 | src/, scripts/, tests/ |
| 软约束 | `pm/SKILL.md` |
| 输出 | root_cause, target_module, complexity, task_breakdown, estimated_files |

### 4.2 Dev Agent

| 属性 | 值 |
|------|-----|
| 模型 | deepseek/deepseek-chat |
| 时超 | 600s |
| 写权限 | src/, scripts/ |
| 禁写 | tests/, docs/ |
| 软约束 | `dev/SKILL.md` |
| 输出 | branch_name, files_changed |

### 4.3 Test Agent

| 属性 | 值 |
|------|-----|
| 模型 | deepseek/deepseek-chat |
| 时超 | 300s |
| 写权限 | tests/ |
| 禁写 | src/, scripts/ |
| 软约束 | `test/SKILL.md` |
| 输出 | verdict, test_summary |

### 4.4 双层权限模型

| 层级 | 机制 | 效果 |
|------|------|------|
| **硬约束** | `claude --disallowedTools Write(tests/*),Edit(tests/*)` | 框架层拦截，Agent 无法绕过 |
| **软约束** | `claude --append-system-prompt-file SKILL.md` | Agent 自行遵守 |

---

## 五、Prompt 工程

### 5.1 Prompt 构建流程

```
_build_prompt(task, step)
    │
    ├── 1. 加载 prompt 模板 (architectures/*/prompts/<agent>.md)
    │      └── 含 few-shot examples + 字段 schema
    │
    ├── 2. 拼接任务描述 (step.description)
    │
    ├── 3. 拼接任务上下文 (task.id, task.type)
    │
    ├── 4. 拼接步骤输入 (step.input → task.context or prev_step.output)
    │
    └── 5. 附加 JSON schema 要求
           "You MUST return a JSON block with these exact fields: [...]"
```

### 5.2 Prompt 模板示例

**PM 模板 (`pm.md`)：**
```markdown
# PM Agent Prompt Template
## Your Task: Analyze the requirements document
## Output Format: JSON with root_cause, target_module, complexity, task_breakdown, estimated_files
## Few-Shot Example: [search command → analysis with 4 subtasks]
```

**Test 模板 (`test.md`)：**
```markdown
# Test Agent Prompt Template
## Output Format: {"verdict": "approved", "test_summary": "..."}
## Verdict must be "approved" (lowercase) or "rejected" (lowercase)
## Approved Example / Rejection Example
```

---

## 六、目录结构

```
src/multiagent/                 # 框架源码 (~2,500 行)
├── __init__.py
├── db.py                       # StateDB (370行) — 6 表 + 线程安全
├── engine.py                   # AgentSpawner (135行) — 进程管理 + metrics
├── engine_cli.py               # multiagent run CLI (295行)
├── orchestrator.py             # WorkflowOrchestrator (362行) — DAG 编排
├── conductor.py                # Conductor (702行) — 调度守护进程
├── conductor_cli.py            # Conductor CLI (424行) — daemon 管理
├── pm_cli.py                   # PM CLI + 统一 dispatch (250行)
├── metrics_cli.py              # Metrics CLI (207行)
├── notify.py                   # Discord 通知 (208行)
├── dashboard.py                # Web 仪表盘 (242行)
├── pm_discover.py              # PM 自动发现 (148行)
├── adapters/                   # Agent 适配器
│   ├── __init__.py             # 适配器注册表
│   ├── base.py                 # AgentAdapter ABC
│   ├── claude_code.py          # ClaudeCodeAdapter
│   └── opencode.py             # OpenCodeAdapter (骨架)

architectures/dev-test-loop/    # Agent 配置 (~600 行)
├── config/roles.yaml           # Agent 角色 + 权限 + 模型
├── workflow/pm-dev-test.yaml   # 工作流定义
├── skills/                     # Agent 软约束
│   ├── pm/SKILL.md
│   ├── dev/SKILL.md
│   └── test/SKILL.md
└── prompts/                    # Prompt 模板 (Phase 5)
    ├── pm.md                   # PM 模板 + few-shot
    ├── dev.md                  # Dev 模板 + few-shot
    └── test.md                 # Test 模板 + few-shot

gates/                          # 门禁测试 (9 模块, 76 tests)
├── regression.py               # 总入口
├── test_db.py                  # 5 tests
├── test_adapters.py            # 5 tests
├── test_orchestrator.py        # 6 tests
├── test_engine_cli.py          # 11 tests
├── test_pm_engine.py           # 8 tests
├── test_parallel.py            # 6 tests
├── test_heartbeat.py           # 7 tests
├── test_metrics_cli.py         # 9 tests
└── test_conductor.py           # 19 tests

docs/                           # 文档与报告
├── phase3-report.md            # Phase 3 实施报告
├── phase4-report.md            # Phase 4 实施报告
├── phase5-report.md            # Phase 5 实施报告
└── engine-architecture.md      # 本文档

examples/devlog/                # DevLog 验证项目 (独立 Git)
├── src/devlog/                 # 7 模块, ~915 行
└── tests/                      # 115 tests, 92.16% 覆盖率
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

---

## 八、未来规划

### Phase 6 (候选)

1. **多架构支持** — 架构注册表，支持除 dev-test-loop 外的自定义架构
2. **Web Dashboard 增强** — 实时 WebSocket 推送、Token 消耗图表
3. **Agent 输出稳定性** — 结构化输出验证 + 自动修复重试
4. **多 Git 平台** — GitLab / Gitee Issue 发现
5. **Systemd 集成** — systemd unit 模板、journald 日志
6. **Metrics 告警** — Token 超限/Agent 超时自动告警

---

## 附录 A: CLI 命令参考

```bash
# 任务管理
multiagent pm init                    # 初始化 .pm/ 目录
multiagent pm submit <req.md>         # 提交需求
multiagent pm list                    # 列出任务
multiagent pm status <id>             # 查看任务详情

# 工作流
multiagent run <workflow.yaml>        # 执行工作流
multiagent run --dry-run              # 干运行验证

# Conductor
multiagent conductor start            # 启动守护进程
multiagent conductor start --workers 5 --pm-auto-discover --discord-webhook <URL>
multiagent conductor stop             # 停止
multiagent conductor restart          # 重启
multiagent conductor status           # 查看状态
multiagent conductor alerts           # 查看升级
multiagent conductor retry <id>       # 重试
multiagent conductor reject <id>      # 放弃

# 监控
multiagent metrics                    # Token/Cost 统计
multiagent metrics --agent dev        # 按 Agent 过滤
multiagent dashboard                  # Web 仪表盘

# 测试
python gates/regression.py            # 全部门禁
```

## 附录 B: 环境依赖

```
Python >= 3.10
pyyaml >= 6.0        # YAML 配置解析
flask >= 3.0         # Web Dashboard (可选)
claude CLI           # Agent 运行时
gh CLI               # GitHub Issues 发现 (可选)
bun / node           # ClaudeHUD 状态栏 (可选)
```
