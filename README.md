# AgentForge

**Multi-Agent Collaborative Development Framework** -- 编排 AI Agent 实例作为专业化角色（PM、Architect、Dev、Test），通过 Workflow Engine + Conductor 实现从需求到代码的全自动流水线。

> 原始仓库: [github.com/nanajiang/MutiAgent](https://github.com/nanajiang/MutiAgent)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-1.0.0-purple.svg)](https://github.com/nanajiang/MutiAgent)
[![Tests](https://img.shields.io/badge/tests-220%20passed-green.svg)](./gates)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

---

## 目录

- [概述](#概述)
- [架构设计](#架构设计)
- [核心功能](#核心功能)
- [快速开始](#快速开始)
- [工作流选择](#工作流选择)
- [CLI 命令参考](#cli-命令参考)
- [配置说明](#配置说明)
- [部署指南](#部署指南)
- [使用案例](#使用案例)
- [Benchmark 结果](#benchmark-结果)
- [项目结构](#项目结构)
- [开发与测试](#开发与测试)
- [版本历史](#版本历史)
- [License](#license)

---

## 概述

AgentForge 是一个**项目无关**的多 Agent 协作开发框架。它编排 AI Agent 实例作为专业化 Agent，通过工作流引擎和调度守护进程实现从需求分析到代码实现再到测试验证的全自动闭环。

**核心设计理念：**

| 原则 | 实现 |
|------|------|
| **框架与项目分离** | `src/multiagent/` 纯 Python 框架；`architectures/` YAML + Markdown 配置 |
| **适配器模式** | `AgentAdapter` ABC -> 支持 Claude Code / OpenCode 等多种运行时 |
| **双层约束** | 硬约束（`--disallowedTools`）+ 软约束（SKILL.md）|
| **零 Token 通知** | Discord Webhook 纯 HTTP POST，不经过 LLM |
| **自动指标采集** | 每次 Agent 调用自动记录 Token / Cost / Duration |
| **AST 条件引擎** | 基于 `ast` 模块安全解析条件表达式，杜绝 `eval()` 安全风险 |

**工作流自动化：**

```
需求文档 -> PM 分析 -> Dev 实现 -> Test 验证
               ^                      |
               +------ 打回修复 <-----+ (最多 3 次)
                          |
                          +-- 超过上限 -> 人工介入
```

---

## 架构设计

```
+-----------------------------------------------------------+
|                     CLI Layer                               |
|  multiagent pm | run | metrics | conductor | dashboard     |
|  role | workflow | checkpoint                              |
+-----------------------------------------------------------+
|                   Conductor (调度层)                        |
|  monitor_loop -> process_all -> Parallel Tasks              |
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
|  (SQLite WAL + thread-safe)                                |
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

### 数据流

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

### Agent 角色

| Agent | 模型 | 职责 | 输出字段 | 权限 |
|-------|------|------|---------|------|
| **PM** | deepseek-v4-pro | 分析需求、拆解任务、评估复杂度、验收标准 | root_cause, task_breakdown, acceptance_criteria, complexity | 写 docs/，禁写 src/tests/ |
| **Architect** | deepseek-v4-pro | 设计系统架构、C4 图 + ADR、技术选型 | architecture_doc, adrs, component_diagram, tech_stack | 写 docs/architecture/ |
| **Dev** | deepseek-v4-pro | 在隔离分支实现代码、提交 commit、实现摘要 | branch_name, files_changed, implementation_summary | 写 src/，禁写 tests/ |
| **Test** | deepseek-v4-flash | 拉取分支、运行测试、给出判决 | verdict, test_summary, tests_total, tests_passed | 写 tests/，禁写 src/ |
| **Conductor** | deepseek-v4-flash | 人类唯一入口，翻译指令，分发汇总 | -- | 读写配置和记忆 |
| **User Agent** | deepseek-v4-pro | 业务闭环驱动，可自定义 | -- | 项目专用 |

---

## 核心功能

### 工作流引擎

- **DAG 依赖解析** -- 步骤间依赖关系和条件分支
- **Condition Evaluator** -- AST 安全解析条件表达式 (`test_verify.output.verdict == "approved"`)，支持 `and`/`or`/`not`/`in`/比较操作符
- **并行执行** -- 独立步骤自动 fan-out 并行
- **Rejection Loop** -- Test 打回 -> Dev 重做（最多 3 次）
- **条件路由** -- `on_verdict_approved` / `on_verdict_rejected` 分支
- **Schema 校验** -- 强制 Agent 输出符合要求的 JSON 字段
- **Git 工作流强制** -- 三层硬约束要求 commit + push

### 调度守护进程 (Conductor)

- **自动轮询** -- 监控 pending 任务，自动触发工作流
- **多任务并行** -- ThreadPoolExecutor 同时执行多个任务
- **PID 管理** -- 完整的 start / stop / restart 生命周期
- **孤儿任务恢复** -- 重启后自动恢复中断的任务
- **优雅停止** -- SIGTERM -> 杀 Agent 进程树 -> 清理 PID 文件
- **Double-fork Daemon** -- 正确的 POSIX 守护进程化
- **进程组管理** -- `os.killpg()` 确保 Agent 孙子进程全部清理

### 通知系统

- **Discord Webhook** -- 零 Token 消耗的实时推送
- **Discord Channel Bot** -- 通过 Bot Token 发送频道消息
- **富文本 Embed** -- 每种 Agent 的输出对应专属字段
- **中英文双语** -- 默认中文，`AGENTFORGE_LANG=en` 切换英文（`notify_i18n.py`）
- **步骤生命周期钩子** -- before_step / after_step / on_rejection / on_escalation
- **重试 + 退避** -- 发送失败自动重试（1s / 4s / 10s）
- **打回消抖** -- 30 秒冷却期防止 Discord 刷屏

### Web Dashboard

- **实时仪表盘** -- 任务队列、进度条、Token 统计
- **历史图表** -- 7 天 Token 用量柱状图 + 任务通过率折线图
- **搜索/筛选** -- 客户端实时过滤任务
- **Workflow DAG 可视化** -- Mermaid.js 渲染工作流图
- **Workflow Designer** -- SVG 拖拽设计工作流
- **Command Center** -- Web 界面执行 CLI 命令
- **REST API** -- `/api/state`、`/api/status`、`/api/timeseries`、`/api/workflow-dag`

### 指标与监控

- **自动指标采集** -- Token 输入/输出、Cost、Duration、Cache 命中
- **心跳监控** -- Agent 进程存活检测
- **丢失 Agent 清理** -- 僵尸进程自动 reaping
- **DB 自动清理** -- 过期数据定期 pruning + VACUUM

### 安全与可靠性

- **3 层重试硬顶** -- `MAX_TOTAL_STEP_EXECUTIONS=50`、每步 `retry.max=3`、打回 `max_rejections=3`
- **双层权限模型** -- `--disallowedTools`（框架层硬约束）+ SKILL.md（Agent 层软约束）
- **SQLite WAL** -- 并发安全 + crash 恢复
- **线程安全** -- `threading.Lock()` 保护写操作
- **AST 安全条件引擎** -- `ast.parse()` 代替 `eval()`，杜绝注入风险

### 扩展性

- **Agent 注册表** -- 动态注册自定义 Agent 类型（YAML 加载）
- **Workflow Graph Engine** -- LangGraph 风格 DAG，YAML/JSON 双向序列化
- **多运行时适配** -- Claude Code + OpenCode（骨架）
- **Checkpoint 管理** -- 任务状态保存/恢复/列表/删除
- **多项目支持** -- 一个 Conductor 监控多个项目
- **Role 模板系统** -- 从内置模板快速创建自定义角色
- **Swarms 模式** -- 多个 Dev Agent 并行执行子任务

### Repository / Service 层 (Phase 8)

- **TaskRepository** -- 任务 CRUD + 查询，封装所有 `tasks` 表操作
- **EscalationRepository** -- 升级事件管理
- **MetricsRepository** -- 指标聚合与统计
- **RoleService** -- 角色加载、校验、模板创建
- **WorkflowService** -- 工作流加载、校验、模板生成
- **DashboardService** -- Dashboard 数据聚合
- **RecoveryService** -- 中断任务恢复逻辑
- **DiscoveryService** -- GitHub Issue 自动发现

---

## 快速开始

### 前置依赖

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | >= 3.10 | 运行框架 |
| Claude Code CLI | 最新版 | Agent 运行时 |
| Git | 2.x | 分支管理 |
| `gh` CLI | （可选） | GitHub Issues 自动发现 |

### 安装

```bash
git clone https://github.com/nanajiang/MutiAgent.git
cd MutiAgent
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 验证安装

```bash
multiagent
# 输出:
# MultiAgent CLI v1.0.0
# Commands:
#   multiagent run <workflow.yaml>           Run a workflow through Engine
#   multiagent metrics [--agent] [--json]     View token/cost metrics
#   multiagent conductor start|status|stop    Conductor monitoring loop
#   multiagent dashboard                       Web Dashboard
#   multiagent pm init                       Initialize .pm/ workspace
#   multiagent pm submit <requirements.md>    Submit requirements
#   multiagent pm list                        List all tasks
#   multiagent pm status <task_id>            Show task details
#   multiagent role create --from-template    Create role from template
#   multiagent role list-templates            List role templates
#   multiagent workflow list                  List available workflows
#   multiagent workflow validate              Validate workflow YAML
```

### 第一次运行

```bash
# 1. 创建需求文档
cat > /tmp/test-requirements.md << 'EOF'
# 需求：添加 README 徽章
在 README.md 顶部添加 Python 版本和测试状态徽章。
EOF

# 2. 提交任务
multiagent pm submit /tmp/test-requirements.md

# 3. 启动 Conductor（前台模式观察）
multiagent conductor start --foreground

# 4. 在另一个终端查看状态
multiagent conductor status
multiagent pm list
multiagent pm status <task-id>

# 5. 查看指标
multiagent metrics
```

---

## 工作流选择

AgentForge v1.0.0 内置三种标准化工作流，根据任务类型自动匹配。

### 内置工作流

| 工作流 | 适用场景 | 步骤 | 特点 |
|--------|---------|------|------|
| **TDD** (默认) | 功能开发、Bug 修复 | PM -> Test -> Dev -> Test | 先写测试再实现，质量最高 |
| **Traditional** | 快速迭代、原型验证 | PM -> Dev -> Test | 标准三步，速度优先 |
| **Architect** | 系统设计、技术选型 | PM -> Architect -> Dev -> Test | 含架构设计评审 |

### 自动选择逻辑 (`select_workflow()`)

```python
def select_workflow(task_type: str, complexity: str) -> str:
    if task_type == "design" or complexity == "high":
        return "pm-architect-test.yaml"   # Architect 模式
    elif task_type in ("feature", "bug") or "test" in task_type.lower():
        return "pm-testfirst-dev-test.yaml"  # TDD 模式
    else:
        return "pm-dev-test.yaml"             # Traditional 模式
```

### 手动指定

```bash
# 使用 TDD 工作流
multiagent run architectures/dev-test-loop/workflow/pm-testfirst-dev-test.yaml

# 使用 Architect 工作流
multiagent run architectures/dev-test-loop/workflow/pm-architect-test.yaml

# Swarms 模式（多 Dev 并行）
multiagent run architectures/dev-test-loop/workflow/swarm-dev.yaml
```

---

## CLI 命令参考

### 任务管理 (`multiagent pm`)

```bash
# 初始化 PM 工作目录 (.pm/inbox, .pm/outbox, .pm/archive)
multiagent pm init

# 提交需求文档
multiagent pm submit requirements.md

# 提交并自动执行工作流
multiagent pm submit requirements.md --run

# 提交并干运行（仅验证，不执行）
multiagent pm submit requirements.md --run --dry-run

# 列出所有任务
multiagent pm list

# 查看任务详情（含步骤结果和 Token 消耗）
multiagent pm status <task_id>

# 搜索任务
multiagent pm search --keyword "login" --status pending
```

### 工作流执行 (`multiagent run`)

```bash
# 执行工作流（创建新任务）
multiagent run architectures/dev-test-loop/workflow/pm-dev-test.yaml

# 干运行验证（仅解析步骤，不生成 Agent）
multiagent run architectures/dev-test-loop/workflow/pm-dev-test.yaml --dry-run

# 对已有任务执行工作流
multiagent run architectures/dev-test-loop/workflow/pm-dev-test.yaml --task-id task-xxxxxxxx
```

### Conductor 调度 (`multiagent conductor`)

```bash
# 后台启动守护进程
multiagent conductor start

# 前台启动（调试用）
multiagent conductor start --foreground

# 自定义参数
multiagent conductor start \
  --workers 5 \                    # 并行任务数
  --interval 10 \                  # 轮询间隔（秒）
  --discord-webhook <URL> \        # Discord 通知
  --pm-auto-discover                # GitHub Issues 自动发现
  --pid-file /path/to/conductor.pid

# 查看状态
multiagent conductor status

# 查看待处理的升级事件
multiagent conductor alerts

# 重新执行升级的任务
multiagent conductor retry <task_id>

# 放弃升级的任务
multiagent conductor reject <task_id>

# 停止守护进程
multiagent conductor stop

# 重启
multiagent conductor restart
```

### Token 指标 (`multiagent metrics`)

```bash
# 全局统计（总调用次数、输入/输出 Token、成本）
multiagent metrics

# 按 Agent 过滤
multiagent metrics --agent dev

# 按任务过滤
multiagent metrics --task-id task-xxxxxxxx

# JSON 格式输出
multiagent metrics --json

# 查看每次调用的详细信息
multiagent metrics --details
```

### Web Dashboard (`multiagent dashboard`)

```bash
# 启动 Dashboard（默认 http://127.0.0.1:5001）
multiagent dashboard

# 指定端口和地址
multiagent dashboard --port 8080 --host 0.0.0.0
```

### Agent 管理 (`multiagent agent`)

```bash
# 列出所有注册的 Agent 类型
multiagent agent list

# 查看某个 Agent 的详细配置
multiagent agent show pm

# 从 YAML 文件注册新 Agent
multiagent agent register my-agents.yaml
```

### 角色管理 (`multiagent role`)

```bash
# 列出所有可用角色
multiagent role list

# 查看角色详情
multiagent role show dev

# 从模板创建新角色
multiagent role create my-custom-role --from-template dev

# 列出内置角色模板
multiagent role list-templates

# 验证角色配置
multiagent role validate my-agents.yaml
```

### 工作流管理 (`multiagent workflow`)

```bash
# 列出所有可用工作流
multiagent workflow list

# 验证工作流 YAML（检查依赖、循环、Agent 存在性）
multiagent workflow validate my-workflow.yaml

# 从模板创建新工作流
multiagent workflow create --from-template diamond

# 可视化工作流 DAG（Mermaid 格式）
multiagent workflow graph my-workflow.yaml
```

### 检查点管理 (`multiagent checkpoint`)

```bash
# 保存任务检查点
multiagent checkpoint save <task_id> "label"

# 列出所有检查点
multiagent checkpoint list

# 列出特定任务的检查点
multiagent checkpoint list <task_id>

# 恢复检查点
multiagent checkpoint restore <checkpoint_id>

# 删除检查点
multiagent checkpoint delete <checkpoint_id>
```

### 全自动模式（一键启动所有功能）

```bash
multiagent conductor start \
  --workers 5 \
  --pm-auto-discover \
  --discord-webhook https://discord.com/api/webhooks/xxx/yyy

# 同时启动 Dashboard
multiagent dashboard --port 5001 &
```

---

## 配置说明

### roles.yaml -- Agent 角色定义

位于 `architectures/dev-test-loop/config/roles.yaml`，定义每个 Agent 的模型、权限、技能文件、超时等。

```yaml
agents:
  pm:
    description: "项目经理，分析需求，拆解任务"
    model: "deepseek-v4-pro"
    permissions:
      write: ["docs/"]
      read: ["src/", "tests/"]
      deny: ["src/", "scripts/", "tests/"]
    skill: "architectures/dev-test-loop/skills/pm/SKILL.md"
    timeout: 300
    output_required:
      - root_cause
      - target_module
      - complexity
      - task_breakdown
      - acceptance_criteria
      - estimated_files
```

### workflow YAML -- 工作流定义

位于 `architectures/dev-test-loop/workflow/`，定义步骤之间的依赖关系和条件分支。

```yaml
workflow:
  id: "pm-dev-test-loop"
  steps:
    - id: "pm_analyze"
      agent: "pm"
      output:
        required:
          - "root_cause"
          - "task_breakdown"
          - "acceptance_criteria"
      on_success:
        to_state: "assigned"

    - id: "dev_fix"
      agent: "dev"
      depends_on: "pm_analyze"
      output:
        required:
          - "branch_name"
          - "files_changed"
          - "implementation_summary"

    - id: "test_verify"
      agent: "test"
      depends_on: "dev_fix"
      condition: 'pm_analyze.output.complexity != "low"'
      on_verdict_rejected:
        next: "dev_fix"
      on_verdict_approved:
        action: "mark_complete"

  error_policy:
    max_rejections: 3
```

**所有工作流文件：**

| 文件 | 说明 |
|------|------|
| `pm-dev-test.yaml` | PM -> Dev -> Test 标准三步（Traditional 模式） |
| `pm-testfirst-dev-test.yaml` | PM -> Test -> Dev -> Test（TDD 模式，默认） |
| `pm-architect-test.yaml` | PM -> Architect -> Dev -> Test（设计任务） |
| `pm-dev-reviewer-test.yaml` | PM -> Dev -> Reviewer -> Test（四步带审查） |
| `dev-test.yaml` | Dev -> Test 两步骤（跳过 PM 分析） |
| `swarm-dev.yaml` | 多个 Dev Agent 并行（Swarms 模式） |
| `bench-single-agent.yaml` | 单 Agent 基准测试 |
| `bench-dev-only.yaml` | 仅 Dev 基准测试 |
| `bench-pm-only.yaml` | 仅 PM 基准测试 |
| `bench-test-only.yaml` | 仅 Test 基准测试 |

### Condition 表达式

使用 AST 安全解析引擎，支持在工作流中设置条件路由。

```yaml
# 条件字段语法
condition: 'pm_analyze.output.complexity in ("high", "critical")'
condition: 'test_verify.output.verdict == "rejected"'
condition: 'dev_fix.output.files_changed > 0'
```

| 操作符 | 说明 | 示例 |
|--------|------|------|
| `==` | 等于 | `verdict == "approved"` |
| `!=` | 不等于 | `complexity != "low"` |
| `>` / `<` | 大于/小于 | `files_changed > 5` |
| `>=` / `<=` | 大于等于/小于等于 | `tests_passed >= 10` |
| `in` | 包含于 | `type in ("feature", "bug")` |
| `not in` | 不包含于 | `type not in ("docs", "chore")` |
| `and` / `or` | 逻辑与/或 | `complexity == "high" and priority == "critical"` |
| `not` | 逻辑非 | `not test_verify.output.verdict == "rejected"` |

### Prompt 模板

位于 `architectures/dev-test-loop/prompts/`，为每个 Agent 提供 few-shot 示例和输出格式指导：

- `pm.md` -- 分析需求、任务拆解模板
- `dev.md` -- 代码实现模板（含 Git 工作流要求）
- `test.md` -- 测试验证模板（含判决格式）

### SKILL.md -- Agent 软约束

位于 `architectures/dev-test-loop/skills/`：

- `pm/SKILL.md` -- PM 行为准则
- `dev/SKILL.md` -- Dev Git 分支工作流要求
- `test/SKILL.md` -- Test 测试编写规范
- `conductor/SKILL.md` -- Conductor 调度规则
- `architect/SKILL.md` -- Architect 架构设计规范

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AGENTFORGE_LANG` | 通知语言 (`zh` / `en`) | `zh` |
| `HOME` | 用于查找 `.claude/claudeclaw/settings.json` | 系统默认 |

### Discord 集成

```bash
# 通过 CLI 使用 Webhook
multiagent conductor start --discord-webhook https://discord.com/api/webhooks/xxx/yyy
```

Bot 配置位于 `.claude/claudeclaw/settings.json`（gitignored）：

```json
{
  "discord": {
    "token": "<bot-token>",
    "listenChannels": ["<channel-id>"]
  },
  "language": "zh"
}
```

---

## 部署指南

### 开发环境

```bash
source .venv/bin/activate
pip install -e .
multiagent conductor start --foreground
```

### 生产环境（后台 Daemon）

```bash
# 启动 daemon（后台常驻）
multiagent conductor start --workers 5

# 验证运行状态
multiagent conductor status

# 查看日志
tail -f runs/conductor.log

# 停止
multiagent conductor stop
```

### 生产环境（systemd）

```ini
# /etc/systemd/system/agentforge.service
[Unit]
Description=AgentForge Conductor
After=network.target

[Service]
Type=forking
User=nanajiang
WorkingDirectory=/home/nanajiang/projects/MutiAgent
ExecStart=/home/nanajiang/projects/MutiAgent/.venv/bin/multiagent conductor start
ExecStop=/home/nanajiang/projects/MutiAgent/.venv/bin/multiagent conductor stop
PIDFile=/home/nanajiang/projects/MutiAgent/runs/.conductor.pid
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agentforge
sudo systemctl status agentforge
```

### Docker（推荐）

```dockerfile
FROM python:3.10-slim

RUN apt-get update && apt-get install -y git curl
RUN curl -fsSL https://claude.ai/install.sh | bash

WORKDIR /app
COPY . .
RUN pip install -e .

CMD ["multiagent", "conductor", "start", "--foreground", "--workers", "3"]
```

```bash
docker build -t agentforge .
docker run -d --name agentforge \
  -v $(pwd)/runs:/app/runs \
  -v $(pwd)/.claude:/app/.claude \
  agentforge
```

---

## 使用案例

### 案例 1：Bug 修复流水线

```bash
# 1. 创建 Bug 报告
cat > .pm/inbox/bug-login-timeout.md << 'EOF'
# Bug: 登录接口超时

## 现象
POST /api/login 在生产环境响应时间超过 30 秒

## 预期
登录请求应在 3 秒内返回

## 验收标准 (Given/When/Then)
- Given 有效凭据
- When 发送 POST /api/login
- Then 响应时间 < 3s
EOF

# 2. 提交并自动执行
multiagent pm submit .pm/inbox/bug-login-timeout.md --run

# 3. PM 自动分析根因并拆解任务
# 4. Dev 自动创建分支 -> 修复 -> Commit
# 5. Test 自动验证 -> 判决

# 6. 查看结果
multiagent pm status task-xxxxxxxx
```

### 案例 2：新功能开发（TDD 模式）

```bash
cat > /tmp/feature-search.md << 'EOF'
# Feature: 全文搜索

## 需求
在 DevLog Web 应用中添加全文搜索功能。

## 验收标准
- 搜索框在页面顶部
- 支持模糊匹配
- 搜索结果高亮关键词
- 响应时间 < 500ms
EOF

# TDD 是默认工作流，自动使用 pm-testfirst-dev-test.yaml
multiagent pm submit /tmp/feature-search.md --run
```

### 案例 3：架构设计任务

```bash
# 系统设计任务自动匹配 Architect 工作流
cat > /tmp/design-auth.md << 'EOF'
# Design: 认证系统重构

## 目标
将当前 Session 认证重构为 JWT + OAuth2.0

## 约束
- 兼容现有 Session 机制
- 支持第三方登录（GitHub、Google）
- 安全审计无高危漏洞
EOF

# select_workflow() 检测到 type=design，自动选择 pm-architect-test.yaml
multiagent pm submit /tmp/design-auth.md --run
```

### 案例 4：Swarms 模式（多 Dev 并行）

```bash
# 使用 Swarms 工作流加速大型任务
multiagent run architectures/dev-test-loop/workflow/swarm-dev.yaml \
  --task-id task-xxxxxxxx
```

### 案例 5：使用 Workflow Designer 自定义工作流

```bash
# 1. 打开 Dashboard
multiagent dashboard

# 2. 浏览器访问 http://127.0.0.1:5001/designer
# 3. 拖拽创建自定义 Agent 工作流
# 4. 点击 "Export YAML" 导出配置

# 5. 使用自定义工作流
multiagent run /path/to/custom-workflow.yaml
```

### 案例 6：PM 验收标准（Given/When/Then）

PM Agent v1.0.0 输出包含 `acceptance_criteria` 字段，使用 Given/When/Then 格式：

```json
{
  "root_cause": "数据库连接池配置不足",
  "complexity": "medium",
  "acceptance_criteria": [
    "Given 高并发场景，When 同时发起 100 个登录请求，Then 95% 在 3s 内完成",
    "Given 数据库连接池耗尽，When 新请求到达，Then 返回 503 而非超时"
  ],
  "task_breakdown": [
    {"id": "1", "description": "增加连接池配置项", "effort": 1},
    {"id": "2", "description": "添加重试和熔断逻辑", "effort": 2}
  ]
}
```

### 案例 7：多项目并发监控

```python
# multi_project_config.py
from multiagent.conductor import Conductor, ProjectConfig

projects = [
    ProjectConfig(
        name="backend-api",
        db_path=Path("./runs/api_state.db"),
        workflow_path=Path("./workflows/api-workflow.yaml"),
    ),
    ProjectConfig(
        name="frontend-app",
        db_path=Path("./runs/frontend_state.db"),
        workflow_path=Path("./workflows/frontend-workflow.yaml"),
    ),
]

c = Conductor(projects=projects, poll_interval=10, max_workers=5)
c.start(blocking=True)
```

### 案例 8：Discord Bot 中文通知

```bash
# 默认中文
multiagent conductor start --foreground --discord-webhook <URL>

# English
AGENTFORGE_LANG=en multiagent conductor start --foreground --discord-webhook <URL>
```

---

## Benchmark 结果

v1.0.0 使用同一任务（URL Parser 实现）在三种模式下进行了对照实验。

### 实验任务

实现 `URLParser` 类，解析 URL 为结构化 dict，含协议验证、域名 TLD 检查、查询字符串解析、错误处理。

### 执行结果

| 模式 | 步骤 | Agent | 状态 | 打回 | 耗时 |
|------|------|-------|------|:---:|------|
| **单 Agent** | 1 | dev | ✅ completed | 0 | 108s |
| **TDD** | 4 | PM→Test→Dev→Test | ✅ completed | 0 | 241s |
| **Swarms** | 5 | PM→3×Dev→Test | ✅ completed | 0 | 330s |

### Token 与成本

| 模式 | 输入 Token | 输出 Token | 总 Token | 总成本 | 相对成本 |
|------|-----------|-----------|---------|-------|:---:|
| **单 Agent** | 16,439 | 9,471 | 25,910 | $0.43 | 1x |
| **TDD** | 50,965 | 20,076 | 71,041 | $1.06 | 2.5x |
| **Swarms** | 64,239 | 25,423 | 89,662 | $1.41 | 3.3x |

### TDD 工作流明细

| 步骤 | Agent | 输入 | 输出 | 费用 | 耗时 |
|------|-------|------|------|------|------|
| pm_analyze | PM | 9,336 | 2,255 | $0.13 | 28s |
| test_write | Test | 12,631 | 5,602 | $0.25 | 52s |
| dev_implement | Dev | 14,438 | 7,127 | $0.39 | 101s |
| test_verify | Test | 14,560 | 5,092 | $0.29 | 60s |

### Swarms 工作流明细

| 步骤 | Agent | 输入 | 输出 | 费用 | 耗时 |
|------|-------|------|------|------|------|
| pm_analyze | PM | 8,282 | 4,022 | $0.17 | 47s |
| dev_swarm_1 | Dev | 13,380 | 3,335 | $0.23 | 50s |
| dev_swarm_2 | Dev | 9,711 | 4,895 | $0.26 | 65s |
| dev_swarm_3 | Dev | 19,588 | 9,141 | $0.52 | 122s |
| test_verify | Test | 13,278 | 4,030 | $0.23 | 46s |

### 结论

基于以上实验数据（非猜测，仅放实测值）：

- **单 Agent ($0.43)** 适合简单独立任务，成本最低但没有验收标准和测试先行保障
- **TDD ($1.06)** 是生产推荐选择，多花 2.5x 成本换来 Test Agent 先写失败测试 + Dev 最小实现 + 再次验证的完整闭环
- **Swarms ($1.41)** 适合可拆解为独立子任务的复杂需求，3 个 Dev 并行开发，成本最高

> ⚠️ 以上数据来自 2026-06-28 的 URL Parser 实验 (task-a3d9ec30 / task-b9e93846 / task-42e02055)。不同任务的实际 Token 消耗会有差异。基准测试文件位于 `architectures/dev-test-loop/workflow/bench-*.yaml`。

---

## 项目结构

```
MutiAgent/
+-- src/multiagent/                   # 框架源码 (~7,500 行)
|   +-- __init__.py
|   +-- db.py                         # StateDB -- SQLite WAL + 6 表 + 线程安全 (379 行)
|   +-- engine.py                     # AgentSpawner -- 进程管理 + metrics (236 行)
|   +-- engine_cli.py                 # multiagent run CLI
|   +-- orchestrator.py               # WorkflowOrchestrator -- DAG 编排 (419 行)
|   +-- conductor.py                  # Conductor -- 调度守护进程 (679 行)
|   +-- conductor_cli.py              # Conductor CLI
|   +-- pm_cli.py                     # PM CLI
|   +-- metrics_cli.py                # Metrics CLI
|   +-- metrics.py                    # 指标聚合/导出
|   +-- notify.py                     # Discord 通知 (372 行)
|   +-- notify_i18n.py                # 通知国际化 (131 行)
|   +-- dashboard.py                  # Flask Web 仪表盘 (310 行)
|   +-- pm_discover.py                # GitHub Issues 自动发现
|   +-- role_cli.py                   # 角色管理 CLI
|   +-- workflow_cli.py               # 工作流 CLI
|   +-- interfaces.py                 # ABC 抽象接口 (150 行)
|   |
|   +-- cli/                          # CLI 命令包
|   |   +-- __init__.py
|   |   +-- conductor.py              # Conductor CLI 命令
|   |   +-- metrics.py                # Metrics CLI 命令
|   |   +-- pm.py                     # PM CLI 命令
|   |   +-- role.py                   # Role CLI 命令
|   |   +-- run.py                    # Run CLI 命令
|   |   +-- workflow.py               # Workflow CLI 命令
|   |
|   +-- adapters/                     # Agent 适配器
|   |   +-- __init__.py
|   |   +-- base.py                   # AgentAdapter ABC
|   |   +-- claude_code.py            # ClaudeCodeAdapter
|   |   +-- opencode.py               # OpenCodeAdapter (骨架)
|   |
|   +-- config/                       # 配置发现
|   |   +-- __init__.py
|   |   +-- loader.py                 # 统一路径发现
|   |
|   +-- core/                         # 核心引擎
|   |   +-- __init__.py
|   |   +-- graph_engine.py           # WorkflowGraph DAG
|   |   +-- conditions.py             # ConditionEvaluator AST 安全引擎 (136 行)
|   |   +-- progress.py               # 进度计算
|   |
|   +-- runtime/                      # 运行时
|   |   +-- __init__.py
|   |   +-- registry.py               # AgentRegistry
|   |
|   +-- services/                     # 服务层
|   |   +-- __init__.py
|   |   +-- checkpoint.py             # 检查点管理
|   |   +-- pid_manager.py            # PID 文件管理
|   |   +-- dashboard_service.py      # Dashboard 数据聚合
|   |   +-- discovery_service.py      # Issue 自动发现
|   |   +-- recovery_service.py       # 中断恢复
|   |   +-- role_service.py           # 角色管理
|   |   +-- workflow_service.py       # 工作流管理
|   |
|   +-- persistence/                  # 持久化仓库
|       +-- __init__.py
|       +-- task_repo.py              # TaskRepository
|       +-- escalation_repo.py        # EscalationRepository
|       +-- metrics_repo.py           # MetricsRepository
|
+-- architectures/dev-test-loop/      # Agent 配置
|   +-- config/roles.yaml             # Agent 角色 + 权限 + 模型
|   +-- workflow/                     # 工作流定义
|   |   +-- pm-dev-test.yaml          # Traditional 模式
|   |   +-- pm-testfirst-dev-test.yaml # TDD 模式（默认）
|   |   +-- pm-architect-test.yaml    # Architect 模式
|   |   +-- pm-dev-reviewer-test.yaml # 四步带审查
|   |   +-- dev-test.yaml             # 精简两步
|   |   +-- swarm-dev.yaml            # Swarms 模式
|   |   +-- bench-*.yaml              # 基准测试工作流
|   +-- skills/                       # Agent 软约束
|   |   +-- pm/SKILL.md
|   |   +-- dev/SKILL.md
|   |   +-- test/SKILL.md
|   |   +-- conductor/SKILL.md
|   |   +-- architect/SKILL.md
|   +-- prompts/                      # Prompt 模板
|   |   +-- pm.md
|   |   +-- dev.md
|   |   +-- test.md
|   +-- templates/                    # 骨架模板
|       +-- SKILL.md.skeleton
|       +-- prompt.md.skeleton
|
+-- gates/                            # 门禁测试 (21 文件, 232 tests)
|   +-- regression.py                 # 总入口
|   +-- test_db.py                    # StateDB 5 tests
|   +-- test_adapters.py              # 适配器 5 tests
|   +-- test_orchestrator.py          # 编排器 6 tests
|   +-- test_engine_cli.py            # 引擎 CLI 11 tests
|   +-- test_pm_engine.py             # PM 引擎 8 tests
|   +-- test_parallel.py              # 并行 6 tests
|   +-- test_heartbeat.py             # 心跳 7 tests
|   +-- test_metrics_cli.py           # 指标 CLI 9 tests
|   +-- test_conductor.py             # 调度器 19 tests
|   +-- test_retry_cap.py             # 重试上限 5 tests
|   +-- test_hooks.py                 # 生命周期钩子 4 tests
|   +-- test_db_cleanup.py            # DB 清理 10 tests
|   +-- test_engine_process.py        # 进程管理 7 tests
|   +-- test_dashboard.py             # Dashboard 20 tests
|   +-- test_notify.py                # 通知 19 tests
|   +-- test_graph_engine.py          # DAG 引擎 21 tests
|   +-- test_agent_registry.py        # Agent 注册 12 tests
|   +-- test_pm_search.py             # PM 搜索 7 tests
|   +-- test_workflow_cli.py          # 工作流 CLI 20 tests
|   +-- test_topology.py              # 拓扑 19 tests
|   +-- test_conditions.py            # 条件引擎 11 tests
|
+-- docs/                             # 文档
|   +-- engine-architecture.md        # 架构文档
|   +-- phase3-report.md              # Phase 3 报告
|   +-- phase4-report.md              # Phase 4 报告
|   +-- phase5-report.md              # Phase 5 报告
|
+-- examples/devlog/                  # DevLog 验证项目
+-- pyproject.toml                    # 项目元数据
+-- CLAUDE.md                         # Claude Code 项目指南
+-- README.md                         # 本文件
```

---

## 开发与测试

### 运行测试

```bash
# 全部门禁测试（232 个测试用例）
source .venv/bin/activate
python gates/regression.py

# 单个模块
python -m pytest gates/test_db.py -v
python -m pytest gates/test_conductor.py -v
python -m pytest gates/test_retry_cap.py -v

# 所有测试（含新模块）
python -m pytest gates/ -v --tb=short

# 按标签运行
python -m pytest gates/ -v -k "conditions"
python -m pytest gates/ -v -k "topology"
```

### 代码风格

- 纯 Python dataclasses + type hints
- logging 模块统一日志
- 写操作全部经过 `threading.Lock()`
- SQLite WAL 模式 + `busy_timeout=5000`
- AST 安全条件解析，杜绝 `eval()`

### 贡献指南

1. Fork 仓库，创建 `feature/<desc>` 分支
2. 确保 `python gates/regression.py` 全部通过（220+ tests）
3. 提交 PR 到 `main` 分支
4. PR 需附测试用例或运行结果

---

## 版本历史

| Version | Phase | 核心交付 |
|---------|-------|---------|
| **v1.0.0** | **8b+8c** | 220+ tests, Condition Evaluator, Repository/Service 层, CLI 包, PM 验收标准, TDD 工作流, Swarms 模式, Architect Agent, 自动工作流选择, 基准测试, 3 种模式对比 |
| **v0.8.0** | **8a** | CLI 包重构, Service 层 (Role/Dashboard/Recovery/Discovery/Workflow), Persistence 层 (Task/Escalation/Metrics Repository), 组织架构清理, DashboardService 集成 |
| v0.7.0 | 7 | 3 层重试硬顶、步骤生命周期钩子、i18n 中文通知、Git 工作流强制、进程组管理、DB 自动清理、孤儿恢复、Dashboard 图表+DAG 可视化 |
| v0.6.0 | 5 | Daemon 生产化 (PID/日志/信号)、Dashboard、Discord Webhook 通知、PM 自动发现、Prompt 模板化、多任务并行 |
| v0.5.0 | 4 验证 | Prompt 修复、管道实战验证 |
| v0.3.0 | 4 | Conductor + 全链路自动化 (PM->Dev->Test)、升级表 |
| v0.2.0 | 3 | Engine 生产化、`multiagent run`/`metrics`、并行执行、心跳监控 |
| v0.1.0 | 1+2 | Dev+Test 双 Agent、PM Agent、Flask TODO 验证 |

---

## 技术决策

| 决策 | 理由 |
|------|------|
| SQLite 而非 PostgreSQL | 零依赖部署，单文件数据库 |
| `claude -p` 子进程而非 API | 直接使用 Claude Code 订阅，Agent 是独立进程 |
| CLI 而非 gRPC/REST | 简单可靠，符合 Unix 哲学 |
| ThreadPoolExecutor 而非 asyncio | blocking 工作负载，线程更简单 |
| YAML 而非 JSON 配置 | 可读性好，支持注释 |
| Discord 零 Token 通知 | 纯 HTTP POST，不消耗 LLM Token |
| Double-fork Daemon 而非 systemd | 平台无关，简单可控 |
| `start_new_session` + `os.killpg` | 确保 Agent 孙子进程全部清理 |
| AST 而非 eval() | 安全解析条件表达式，杜绝注入风险 |

---

## License

MIT License -- 详见 [LICENSE](LICENSE) 文件。

---

<p align="center">
  <b>AgentForge</b> -- 让 AI Agent 协作开发成为现实<br>
  <sub>Built with love by nanajiang + Claude</sub>
</p>
