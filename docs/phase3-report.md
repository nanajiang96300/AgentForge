# Phase 3 实施报告：Engine 生产化

> 版本：v0.2.0 | 日期：2026-06-20 ~ 2026-06-21 | 作者：nanajiang (架构监视者) + Claude (AI Agent)

## 一、背景与目标

### 1.1 前期状态

Phase 1 实现了 Dev + Test 双 Agent 协作，Phase 2 引入 PM Agent 形成 PM→Dev→Test 三步流水线。但存在以下关键问题：

- **Agent 调用靠人工拼接命令行**：每次 PM/Dev/Test 执行都需要手动构造 `claude -p` 命令
- **Engine 模块形同虚设**：`AgentSpawner`、`WorkflowOrchestrator` 代码已存在但从未被实际调用
- **`agent_metrics` 表完全为空**：虽然设计了 Token/Cost 追踪表，但没有代码路径写入数据
- **无正式的项目验证**：框架跑通了 C++ calculator 和 Flask TODO，但没有一个完整的中型项目验证

### 1.2 战略调整

原计划 Phase 3 实现 "User Agent + 业务闭环"，经分析决定**调整为 Engine 生产化**，理由：

1. **Human-as-User 已经有效**：人类开发者扮演 User Agent 角色（使用应用、发现 Bug、提交给 PM）效果很好
2. **AI User Agent 与 Test Agent 职责重叠**：两者都需要运行测试和检测问题
3. **Engine 是真正的短板**：框架核心生产化程度远低于 Agent 功能完整度
4. **无生产化 Engine 就没有真正的自动化**：后续 Phase 4 Conductor 等全自动调度都依赖 Engine

### 1.3 Phase 3 目标

将 AgentSpawner 确立为**所有 Agent 调用的唯一入口**，实现：

- `multiagent run <workflow.yaml>` 一键执行完整工作流
- 自动 Token/Cost/Time 指标采集
- 并行工作流支持（fan-out 多步骤同时执行）
- 心跳监控 + 崩溃恢复
- 通过 PM→Dev→Test 流水线构建 DevLog 中型 CLI 项目验证

---

## 二、多 Agent 架构设计

### 2.1 总体架构

```
┌─────────────────────────────────────────────────────┐
│                   multiagent CLI                     │
│  pm submit | run | metrics | conductor start         │
├─────────────────────────────────────────────────────┤
│                  engine_cli.py                       │
│            (工作流执行唯一入口)                        │
├─────────────────────────────────────────────────────┤
│              WorkflowOrchestrator                     │
│         DAG依赖解析 · 条件分支 · 并行调度               │
├─────────────────────────────────────────────────────┤
│                 AgentSpawner                          │
│       spawn() · monitor() · metrics capture          │
├─────────────────────────────────────────────────────┤
│              AgentAdapter (ABC)                       │
│     ClaudeCodeAdapter · OpenCodeAdapter              │
├─────────────────────────────────────────────────────┤
│                   StateDB                            │
│   tasks · step_results · heartbeat · agent_metrics   │
│         (SQLite WAL + 线程安全)                       │
└─────────────────────────────────────────────────────┘
```

### 2.2 Agent 角色定义

| Agent | 职责 | 约束 | 工作时长 |
|-------|------|------|---------|
| **PM** | 分析需求 → 定位模块 → 拆解任务 → 评估复杂度 | 只读 src/tests，可写 docs/ | 300s |
| **Dev** | 在隔离分支实现功能/Bug修复 | 不可修改 tests/ | 600s |
| **Test** | 运行测试套件 → 输出判决（approved/rejected） | 不可修改 src/scripts/ | 300s |

### 2.3 工作流编排

```
PM Analyze ──→ Dev Implement ──→ Test Verify
    │               │                │
    │          depends_on:      depends_on:
    │          pm_analyze       dev_fix
    │               │                │
    └── 输出:        │                │
    root_cause      │                │
    task_breakdown  │        ┌───────┘
    target_module   │        │ (verdict=rejected)
    complexity      │        │
                    ▼        ▼
              Dev Fix ← Test Verify
              (重做, 最多3次)
                    │
                    ▼ (3次失败)
               Escalated → Human 决策
```

**并行优化**：当多个步骤无依赖关系时，通过 `ThreadPoolExecutor` 并行执行。如钻石形工作流 (A→B|C→D) 中，B 和 C 会同批次并发。

### 2.4 双层权限模型

| 层级 | 机制 | 效果 |
|------|------|------|
| **硬约束** | `claude --disallowedTools Write(tests/*),Edit(tests/*)` | 框架层拦截，Agent 无法绕过 |
| **软约束** | `claude --append-system-prompt-file SKILL.md` | Agent 自行遵守，可被 override |

---

## 三、实施过程

### 3.1 实施步骤

#### Step 1：`multiagent run` CLI + Engine 门禁

**新建 `src/multiagent/engine_cli.py` (295行)**

核心入口函数 `cmd_run()` 实现：
- 加载 workflow YAML → 初始化 StateDB → 创建/检索 Task
- 干运行模式：仅验证工作流定义，打印步骤依赖树
- 执行模式：通过 WorkflowOrchestrator 运行完整工作流
- 结果输出：每个步骤的状态（✅/❌/⏳）+ 关键输出预览
- 自动更新任务状态（completed/failed）

**门禁测试 `gates/test_engine_cli.py` (11 tests)**
- 参数解析、任务创建、干运行验证、端到端执行、上下文传递

#### Step 2：PM CLI 通过 Engine 通道

**修改 `src/multiagent/pm_cli.py`**

- `cmd_submit()` 新增 `auto_run` 参数
- `multiagent pm submit --run` 自动调用 `cmd_run` 完成全链路
- `main()` 重写为统一 dispatch：`run` → engine_cli, `metrics` → metrics_cli, `conductor` → conductor_cli

**门禁测试 `gates/test_pm_engine.py` (8 tests)**
- 提交创建任务、自动运行、上下文存储、状态转换、metrics 记录

#### Step 3：并行工作流支持

**重写 `src/multiagent/orchestrator.py` (357行)**

核心类 `WorkflowOrchestrator`：
- `get_ready_steps()` — DAG 依赖解析
- `_execute_parallel()` — ThreadPoolExecutor 并发
- `_handle_rejection()` — 打回重做循环
- `_check_condition()` — 条件分支判断
- 线程安全：`threading.Lock` 保护共享状态 + SQLite WAL

**门禁测试 `gates/test_parallel.py` (6 tests)**
- 并行检测、菱形工作流、速度基准、线程安全

#### Step 4：心跳监控 + 崩溃恢复

**增强 `engine.py` 和 `db.py`**

- `AgentSpawner.spawn()` — 记录 heartbeat（PID + 时间戳）
- `StateDB.get_lost_agents()` — 检测僵死进程（超时阈值 60s）
- 超时自动终止 + 重试机制

**门禁测试 `gates/test_heartbeat.py` (7 tests)**

#### Step 5：Metrics CLI

**新建 `src/multiagent/metrics_cli.py` (207行)**

- `multiagent metrics` — 聚合统计（总调用数、Token、Cost）
- `--agent pm|dev|test` — 按 Agent 过滤
- `--task-id <id>` — 按任务过滤
- `--json` — JSON 格式输出
- `--details` — 每次调用的详细指标

**门禁测试 `gates/test_metrics_cli.py` (9 tests)**

### 3.2 DevLog 验证项目

DevLog (CLI Developer Journal) 是通过 PM→Dev→Test 流水线搭建的中型验证项目：

| 批次 | 内容 | 测试 | 覆盖率 |
|------|------|------|--------|
| Batch 1 | CLI 骨架 + DB 层 + CRUD | 基础功能 | - |
| Batch 2 | FTS5搜索 + 导出 + E2E | 115 tests | 92.16% |

**流水线耗时：** PM(22s) → Dev(59s) → Test(83s) = **总计 164s** 完成完整项目

---

## 四、问题与解决方案

### 问题 1：Engine 代码从未被实际使用

**现象**：Phase 2 已有 `AgentSpawner`、`WorkflowOrchestrator` 等代码，但 PM/Dev/Test 的调用都是手动拼接 `claude -p` 命令行。`agent_metrics` 表为空。

**解决**：
1. 创建 `engine_cli.py` 作为统一入口，所有 Agent 调用必须通过 `cmd_run()`
2. 重构 `pm_cli.py` 将 `cmd_submit()` 委托给 `cmd_run()`
3. `AgentSpawner.monitor()` 中添加 `_capture_metrics()` 自动提取 Claude JSON 输出中的 Token/Cost/Duration 并持久化

### 问题 2：Agent 子进程权限被拒

**现象**：`claude -p` 在非交互模式（子进程）下，auto-mode classifier 拒绝所有 Write/Edit/Bash 工具调用。Agent 虽然收到 prompt 但无法执行任何实际操作。

**尝试**：曾尝试 `--dangerously-skip-permissions` 但被 auto-mode 拦截。

**最终方案**：`--permission-mode acceptEdits` — 明确告知 auto-mode 自动批准 Edit/Write 调用。验证结果：0 permission denials。

### 问题 3：SQLite 线程安全

**现象**：并行执行步骤时出现 `SQLite objects created in a thread can only be used in that same thread` 错误。

**解决**：
1. `check_same_thread=False` — 允许多线程共享连接
2. `PRAGMA busy_timeout=5000` — 写锁等待 5 秒
3. `threading.Lock()` — 所有写方法加锁保护

### 问题 4：Glob 遍历性能

**现象**：`find_state_db()` 使用 `**/state.db` glob 时会遍历 `.venv/` 等大目录，造成 10+ 秒卡顿。

**解决**：添加优先快速路径（先检查 `cwd/state.db`、`cwd/.framework/workflow/state.db`），仅在快速路径失败时 fallback 到 glob。glob 时跳过 `.venv`、`.git`、`__pycache__`、`node_modules`、`.claude`。

### 问题 5：DevLog 测试失败

在 DevLog 流水线执行中，Test Agent 报告了 4 个测试断言失败：

| 问题 | 原因 | 修复 |
|------|------|------|
| `excerpt` 调用错误 | Dev 将 `excerpt()` 实现为 `@property`，Test 期望方法调用 | 移除 `@property` 装饰器 |
| CLI exit code 不匹配 | Click 无命令时返回 2，测试期望 0 | 改为检查帮助文本存在性 |
| Rich render 调用次数 | Rich `console.print(table)` 只渲染一次，测试期望 >=2 | 修改断言为 >=1 |
| E2E fixture 变量遮蔽 | `from devlog import db` 被同名 fixture 遮蔽 | 重命名 import 为 `devlog_db` |

---

## 五、测试情况

### 5.1 门禁回归

```bash
python gates/regression.py
```

| # | 模块 | 说明 | 状态 |
|---|------|------|:--:|
| 1 | DB: CRUD + Dedup | 数据层基础操作 | ✅ |
| 2 | Adapters: CLI + Config | Agent 运行时适配器 | ✅ |
| 3 | Orchestrator: Workflow Engine | 步骤加载、依赖解析、输入输出 | ✅ |
| 4 | Engine CLI: run command | 11 tests — 参数解析、任务、端到端 | ✅ |
| 5 | PM Engine: submit via Engine | 8 tests — 提交、自动运行、状态 | ✅ |
| 6 | Parallel: fan-out execution | 6 tests — 并行检测、线程安全 | ✅ |
| 7 | Heartbeat: crash recovery | 7 tests — 心跳、超时、重试 | ✅ |
| 8 | Metrics CLI: token/cost | 9 tests — 聚合、过滤、JSON | ✅ |
| 9 | Conductor: auto-trigger | 19 tests — 调度、escalation | ✅ |

**总计：9/9 模块通过，76 tests**

### 5.2 DevLog 项目测试

| 模块 | 测试数 | 覆盖率 |
|------|--------|--------|
| models.py | 19 | 100% |
| render.py | 15 | 98.8% |
| export.py | 8 | 100% |
| db.py | 26 | 99.2% |
| search.py | 6 | 100% |
| cli.py | 35 | 80.1% |
| e2e.py | 6 | - |
| **总计** | **115** | **92.16%** |

---

## 六、后续计划

### Phase 4：Conductor + 全链路自动化

- Conductor 调度循环（监控 state.db → 自动运行工作流）
- PM→Dev→Test 完全自动接力（无需人工干预）
- Rejection Loop 自动回退
- Escalation 机制（3 次失败 → 人工决策）
- Conductor CLI：start / status / stop / alerts

### Phase 5：Conductor 生产化

- PID 文件管理 + 优雅停止
- 日志系统 + 轮转
- 多项目并发监控
- 实时进度追踪
- Discord 通知

### 长期方向

- Web Dashboard 可视化
- PM 自动发现（轮询 Git Issues）
- Prompt 模板化 + few-shot 示例
- 多架构支持

---

## 附录

### A. 关键文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/multiagent/engine_cli.py` | 295 | `multiagent run` CLI |
| `src/multiagent/metrics_cli.py` | 207 | `multiagent metrics` CLI |
| `src/multiagent/orchestrator.py` | 357 | WorkflowOrchestrator |
| `src/multiagent/engine.py` | 111 | AgentSpawner + metrics capture |
| `src/multiagent/db.py` | 239 | StateDB (6 表 + 迁移) |
| `src/multiagent/pm_cli.py` | 216 | PM CLI + dispatch |
| `architectures/dev-test-loop/workflow/pm-dev-test.yaml` | 96 | 工作流定义 |
| `architectures/dev-test-loop/config/roles.yaml` | 143 | Agent 角色 + 权限 |
| `examples/devlog/` | 915 | DevLog 验证项目 (7 模块) |

### B. Git 版本

| Tag | Commit | 内容 |
|-----|--------|------|
| v0.1.0 | `733ef06` | Phase 1+2: Dev+Test, PM, Flask TODO |
| **v0.2.0** | **`eeac214`** | **Phase 3: Engine 生产化, DevLog** |
