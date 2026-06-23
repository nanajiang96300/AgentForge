# 🤖 AgentForge

**Multi-Agent Collaborative Development Framework** — 编排 Claude Code 实例作为专业化 Agent（PM、Dev、Test），通过 Workflow Engine + Conductor 实现从需求到代码的全自动流水线。

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.7.0-purple.svg)](https://github.com/nanajiang/MutiAgent)
[![Tests](https://img.shields.io/badge/tests-94%20passed-green.svg)](.)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

---

## 目录

- [概述](#概述)
- [架构设计](#架构设计)
- [核心功能](#核心功能)
- [快速开始](#快速开始)
- [CLI 命令参考](#cli-命令参考)
- [配置说明](#配置说明)
- [部署指南](#部署指南)
- [使用案例](#使用案例)
- [项目结构](#项目结构)
- [开发与测试](#开发与测试)
- [版本历史](#版本历史)

---

## 概述

AgentForge 是一个**项目无关**的多 Agent 协作开发框架。它编排 Claude Code 实例作为专业化 Agent，通过工作流引擎和调度守护进程实现从需求分析到代码实现再到测试验证的全自动闭环。

**核心设计理念：**

| 原则 | 实现 |
|------|------|
| **框架与项目分离** | `src/multiagent/` 纯 Python 框架；`architectures/` YAML + Markdown 配置 |
| **适配器模式** | `AgentAdapter` ABC → 支持 Claude Code / OpenCode 等多种运行时 |
| **双层约束** | 硬约束（`--disallowedTools`）+ 软约束（SKILL.md）|
| **零 Token 通知** | Discord Webhook 纯 HTTP POST，不经过 LLM |
| **自动指标采集** | 每次 Agent 调用自动记录 Token / Cost / Duration |

**工作流自动化：**

```
需求文档 → PM 分析 → Dev 实现 → Test 验证
              ↑                      │
              └──── 打回修复 ←───────┘ (最多 3 次)
                         │
                         └── 超过上限 → 人工介入
```

---

## 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                     CLI Layer                            │
│  multiagent pm | run | metrics | conductor | dashboard   │
├─────────────────────────────────────────────────────────┤
│                   Conductor (调度层)                     │
│  monitor_loop → process_all → Parallel Tasks             │
├─────────────────────────────────────────────────────────┤
│              WorkflowOrchestrator (编排层)               │
│  DAG 依赖解析 · 条件分支 · 并行 fan-out · rejection loop  │
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

### 数据流

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

### Agent 角色

| Agent | 模型 | 职责 | 权限 |
|-------|------|------|------|
| **PM** | deepseek-chat | 分析需求、拆解任务、评估复杂度 | 只写 docs/ |
| **Dev** | deepseek-chat | 在隔离分支实现代码、提交 commit | 写 src/，禁写 tests/ |
| **Test** | deepseek-chat | 拉取分支、运行测试、给出判决 | 写 tests/，禁写 src/ |
| **Conductor** | deepseek-chat | 人类唯一入口，翻译指令，分发汇总 | 读写配置和记忆 |
| **User Agent** | deepseek-chat | 业务闭环驱动，可自定义 | 项目专用 |

---

## 核心功能

### ✅ 已实现

#### 工作流引擎
- **DAG 依赖解析** — 步骤间依赖关系和条件分支
- **并行执行** — 独立步骤自动 fan-out 并行
- **Rejection Loop** — Test 打回 → Dev 重做（最多 3 次）
- **条件路由** — `on_verdict_approved` / `on_verdict_rejected` 分支
- **Schema 校验** — 强制 Agent 输出符合要求的 JSON 字段
- **Git 工作流强制** — 三层硬约束要求 commit + push

#### 调度守护进程 (Conductor)
- **自动轮询** — 监控 pending 任务，自动触发工作流
- **多任务并行** — ThreadPoolExecutor 同时执行多个任务
- **PID 管理** — 完整的 start / stop / restart 生命周期
- **孤儿任务恢复** — 重启后自动恢复中断的任务
- **优雅停止** — SIGTERM → 杀 Agent 进程树 → 清理 PID 文件
- **Double-fork Daemon** — 正确的 POSIX 守护进程化
- **进程组管理** — `os.killpg()` 确保 Agent 孙子进程全部清理

#### 通知系统
- **Discord Webhook** — 零 Token 消耗的实时推送
- **Discord Channel Bot** — 通过 Bot Token 发送频道消息
- **富文本 Embed** — 每种 Agent 的输出对应专属字段
- **中英文双语** — 默认中文，`AGENTFORGE_LANG=en` 切换英文
- **步骤生命周期钩子** — before_step / after_step / on_rejection / on_escalation
- **重试 + 退避** — 发送失败自动重试（1s / 4s / 10s）
- **打回消抖** — 30 秒冷却期防止 Discord 刷屏

#### Web Dashboard
- **实时仪表盘** — 任务队列、进度条、Token 统计
- **历史图表** — 7 天 Token 用量柱状图 + 任务通过率折线图
- **搜索/筛选** — 客户端实时过滤任务
- **Workflow DAG 可视化** — Mermaid.js 渲染工作流图
- **Workflow Designer** — SVG 拖拽设计工作流
- **Command Center** — Web 界面执行 CLI 命令
- **REST API** — `/api/state`、`/api/status`、`/api/timeseries`、`/api/workflow-dag`

#### 指标与监控
- **自动指标采集** — Token 输入/输出、Cost、Duration、Cache 命中
- **心跳监控** — Agent 进程存活检测
- **丢失 Agent 清理** — 僵尸进程自动 reaping
- **DB 自动清理** — 过期数据定期 pruning + VACUUM

#### 安全与可靠性
- **3 层重试硬顶** — `MAX_TOTAL_STEP_EXECUTIONS=50`、每步 `retry.max=3`、打回 `max_rejections=3`
- **双层权限模型** — `--disallowedTools`（框架层硬约束）+ SKILL.md（Agent 层软约束）
- **SQLite WAL** — 并发安全 + crash 恢复
- **线程安全** — `threading.Lock()` 保护写操作

#### 扩展性
- **Agent 注册表** — 动态注册自定义 Agent 类型（YAML 加载）
- **Workflow Graph Engine** — LangGraph 风格 DAG，YAML/JSON 双向序列化
- **多运行时适配** — Claude Code + OpenCode（骨架）
- **Checkpoint 管理** — 任务状态保存/恢复/列表/删除
- **多项目支持** — 一个 Conductor 监控多个项目

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
# MultiAgent CLI v0.7.0
# Commands:
#   multiagent run <workflow.yaml>           Run a workflow through Engine
#   multiagent metrics [--agent] [--json]     View token/cost metrics
#   multiagent conductor start|status|stop    Conductor monitoring loop
#   multiagent dashboard                       Web Dashboard
#   multiagent pm init                       Initialize .pm/ workspace
#   multiagent pm submit <requirements.md>    Submit requirements
#   multiagent pm list                        List all tasks
#   multiagent pm status <task_id>            Show task details
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

### roles.yaml — Agent 角色定义

位于 `architectures/dev-test-loop/config/roles.yaml`，定义每个 Agent 的模型、权限、技能文件、超时等。

```yaml
agents:
  pm:
    description: "项目经理，分析需求，拆解任务"
    model: "deepseek/deepseek-chat"
    permissions:
      write: ["docs/"]
      read: ["src/", "tests/"]
      deny: ["src/", "tests/"]
    skill: "architectures/dev-test-loop/skills/pm/SKILL.md"
    timeout: 300
```

### workflow YAML — 工作流定义

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
      on_success:
        to_state: "assigned"

    - id: "dev_fix"
      agent: "dev"
      depends_on: "pm_analyze"
      output:
        required:
          - "branch_name"
          - "files_changed"

    - id: "test_verify"
      agent: "test"
      depends_on: "dev_fix"
      on_verdict_rejected:
        next: "dev_fix"              # 打回重新实现
      on_verdict_approved:
        action: "mark_complete"      # 通过则完成

  error_policy:
    max_rejections: 3
```

**内置工作流：**
- `pm-dev-test.yaml` — PM → Dev → Test 标准三步骤
- `pm-dev-reviewer-test.yaml` — PM → Dev → Reviewer → Test 四步骤（带代码审查）
- `dev-test.yaml` — Dev → Test 两步骤（跳过硬编码 PM 分析）

### Prompt 模板

位于 `architectures/dev-test-loop/prompts/`，为每个 Agent 提供 few-shot 示例和输出格式指导：

- `pm.md` — 分析需求、任务拆解模板
- `dev.md` — 代码实现模板（含 Git 工作流要求）
- `test.md` — 测试验证模板（含判决格式）

### SKILL.md — Agent 软约束

位于 `architectures/dev-test-loop/skills/`，通过 `--append-system-prompt-file` 注入 Agent 上下文：

- `pm/SKILL.md` — PM 行为准则
- `dev/SKILL.md` — Dev Git 分支工作流要求
- `test/SKILL.md` — Test 测试编写规范
- `conductor/SKILL.md` — Conductor 调度规则

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AGENTFORGE_LANG` | 通知语言 (`zh` / `en`) | `zh` |
| `HOME` | 用于查找 `.claude/claudeclaw/settings.json` | 系统默认 |

### Discord 集成

Discord Bot 配置位于 `.claude/claudeclaw/settings.json`（gitignored）：

```json
{
  "discord": {
    "token": "<bot-token>",
    "listenChannels": ["<channel-id>"]
  },
  "language": "zh"
}
```

或通过 CLI 使用 Webhook：

```bash
multiagent conductor start --discord-webhook https://discord.com/api/webhooks/xxx/yyy
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

## 复现步骤
1. 发送 POST /api/login 携带有效凭据
2. 观察响应时间 > 30s
EOF

# 2. 提交并自动执行
multiagent pm submit .pm/inbox/bug-login-timeout.md --run

# 3. PM 会自动：
#    - 分析根因（可能是数据库连接池耗尽）
#    - 拆解为子任务
#    - 评估复杂度
#
# 4. Dev 会自动：
#    - 创建 feature/fix-login-timeout 分支
#    - 修改源码（添加连接超时配置、重试逻辑）
#    - Commit + Push
#
# 5. Test 会自动：
#    - 拉取分支
#    - 运行测试套件
#    - 输出 verdict: approved/rejected

# 6. 查看结果
multiagent pm status task-xxxxxxxx
```

### 案例 2：新功能开发

```bash
cat > /tmp/feature-search.md << 'EOF'
# Feature: 全文搜索

## 需求
在 DevLog Web 应用中添加全文搜索功能，支持按标题和内容搜索日志条目。

## 验收标准
- 搜索框在页面顶部
- 支持模糊匹配
- 搜索结果高亮关键词
- 响应时间 < 500ms
EOF

multiagent pm submit /tmp/feature-search.md --run
```

### 案例 3：使用 Workflow Designer 自定义工作流

```bash
# 1. 打开 Dashboard
multiagent dashboard

# 2. 浏览器访问 http://127.0.0.1:5001/designer
# 3. 拖拽创建自定义 Agent 工作流
# 4. 点击 "Export YAML" 导出配置

# 5. 使用自定义工作流
multiagent run /path/to/custom-workflow.yaml
```

### 案例 4：多项目并发监控

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

### 案例 5：Discord Bot 中文通知

```bash
# 默认中文
multiagent conductor start --foreground --discord-webhook <URL>

# English
AGENTFORGE_LANG=en multiagent conductor start --foreground --discord-webhook <URL>
```

---

## 项目结构

```
MutiAgent/
├── src/multiagent/                   # 框架源码 (~5,400 行)
│   ├── __init__.py
│   ├── db.py                         # StateDB — SQLite WAL + 6 表 + 线程安全
│   ├── engine.py                     # AgentSpawner — 进程管理 + metrics
│   ├── engine_cli.py                 # multiagent run CLI
│   ├── orchestrator.py               # WorkflowOrchestrator — DAG 编排
│   ├── conductor.py                  # Conductor — 调度守护进程
│   ├── conductor_cli.py              # Conductor CLI
│   ├── pm_cli.py                     # PM CLI + 统一命令分发
│   ├── metrics_cli.py                # Metrics CLI
│   ├── metrics.py                    # 指标聚合/导出
│   ├── notify.py                     # Discord 通知 + i18n
│   ├── dashboard.py                  # Flask Web 仪表盘
│   ├── pm_discover.py                # GitHub Issues 自动发现
│   ├── interfaces.py                 # ABC 抽象接口
│   ├── adapters/                     # Agent 适配器
│   │   ├── base.py                   # AgentAdapter ABC
│   │   ├── claude_code.py            # ClaudeCodeAdapter
│   │   └── opencode.py               # OpenCodeAdapter (骨架)
│   ├── config/                       # 配置发现
│   │   └── loader.py                 # 统一路径发现
│   ├── core/                         # 核心引擎
│   │   ├── graph_engine.py           # WorkflowGraph DAG
│   │   └── progress.py               # 进度计算
│   ├── runtime/                      # 运行时
│   │   └── registry.py               # AgentRegistry
│   ├── services/                     # 服务
│   │   ├── checkpoint.py             # 检查点管理
│   │   └── pid_manager.py            # PID 文件管理
│   └── persistence/                  # 持久化仓库
│       ├── task_repo.py              # TaskRepository
│       ├── escalation_repo.py        # EscalationRepository
│       └── metrics_repo.py           # MetricsRepository
│
├── architectures/dev-test-loop/      # Agent 配置
│   ├── config/roles.yaml             # Agent 角色 + 权限 + 模型
│   ├── workflow/                     # 工作流定义
│   │   ├── pm-dev-test.yaml          # PM→Dev→Test 标准
│   │   ├── pm-dev-reviewer-test.yaml # PM→Dev→Reviewer→Test
│   │   └── dev-test.yaml             # Dev→Test 精简
│   ├── skills/                       # Agent 软约束
│   │   ├── pm/SKILL.md
│   │   ├── dev/SKILL.md
│   │   └── test/SKILL.md
│   └── prompts/                      # Prompt 模板
│       ├── pm.md
│       ├── dev.md
│       └── test.md
│
├── gates/                            # 门禁测试 (94 tests, 11 模块)
│   ├── regression.py                 # 总入口
│   ├── test_db.py                    # 5 tests
│   ├── test_adapters.py              # 5 tests
│   ├── test_orchestrator.py          # 6 tests
│   ├── test_engine_cli.py            # 11 tests
│   ├── test_pm_engine.py             # 8 tests
│   ├── test_parallel.py              # 6 tests
│   ├── test_heartbeat.py             # 7 tests
│   ├── test_metrics_cli.py           # 9 tests
│   ├── test_conductor.py             # 19 tests
│   ├── test_retry_cap.py             # 5 tests
│   └── test_hooks.py                 # 4 tests
│
├── docs/                             # 文档
│   ├── engine-architecture.md        # 架构文档
│   ├── phase3-report.md              # Phase 3 报告
│   ├── phase4-report.md              # Phase 4 报告
│   └── phase5-report.md              # Phase 5 报告
│
├── examples/devlog/                  # DevLog 验证项目
├── pyproject.toml                    # 项目元数据
├── CLAUDE.md                         # Claude Code 项目指南
└── README.md                         # 本文件
```

---

## 开发与测试

### 运行测试

```bash
# 全部门禁测试
source .venv/bin/activate
python gates/regression.py

# 单个模块
python -m pytest gates/test_db.py -v
python -m pytest gates/test_conductor.py -v
python -m pytest gates/test_retry_cap.py -v

# 所有测试（含新模块）
python -m pytest gates/ -v --tb=short
```

### 代码风格

- 纯 Python dataclasses + type hints
- logging 模块统一日志
- 写操作全部经过 `threading.Lock()`
- SQLite WAL 模式 + `busy_timeout=5000`

### 贡献指南

1. Fork 仓库，创建 `feature/<desc>` 分支
2. 确保 `python gates/regression.py` 全部通过
3. 提交 PR 到 `main` 分支
4. PR 需附测试用例或运行结果

---

## 版本历史

| Version | Phase | 核心交付 |
|---------|-------|---------|
| **v0.7.0** | 7 | 3 层重试硬顶、步骤生命周期钩子、i18n 中文通知、Git 工作流强制、进程组管理、DB 自动清理、孤儿恢复、通知系统增强、Dashboard 图表+DAG 可视化 |
| v0.6.0 | 5 | Daemon 生产化 (PID/日志/信号)、Dashboard、Discord Webhook 通知、PM 自动发现、Prompt 模板化、多任务并行 |
| v0.5.0 | 4 验证 | Prompt 修复、管道实战验证 |
| v0.3.0 | 4 | Conductor + 全链路自动化 (PM→Dev→Test)、升级表 |
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

---

## License

MIT License — 详见 [LICENSE](LICENSE) 文件。

---

<p align="center">
  <b>AgentForge</b> — 让 AI Agent 协作开发成为现实<br>
  <sub>Built with ❤️ by nanajiang + Claude</sub>
</p>
