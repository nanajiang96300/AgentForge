# Phase 5 实施报告：Conductor 生产化 + 多项目 + 通知

> 版本：v0.6.0 | 日期：2026-06-22 ~ 2026-06-23 | 作者：nanajiang (架构监视者) + Claude (AI Agent)

## 一、背景与目标

### 1.1 前期状态

Phase 4 完成了 Conductor 调度循环 + PM→Dev→Test 全链路自动化，通过 2 次实战验证了管道可行性。但存在以下生产化缺失：

| 问题 | 影响 |
|------|------|
| **PID 文件从不写入** | `cmd_stop` 读不到 PID，daemon 无法正常停止 |
| **DB stop 信号无效** | 写入 `workflow_state` 表但轮询循环从不读取 |
| **无日志系统** | 全部 `print()`，daemon 模式下输出丢失，异常静默吞噬 |
| **单任务顺序执行** | 多个 pending 任务时只能逐个处理 |
| **无进度监控** | 不知道每个任务执行到哪个阶段、消耗多少 Token |
| **无实时通知** | Escalation 后需主动 `conductor alerts` 查询 |
| **Agent 输出不稳定** | PM/Dev/Test 输出字段名与 schema 不匹配导致重试 |

### 1.2 Phase 5 目标

将 Conductor 从「开发预览」升级为「可长期运行的无人值守框架」，实现：

1. **Daemon 生产化**：PID 文件 + 日志轮转 + 优雅停止 + restart
2. **多任务并行**：ThreadPoolExecutor 同时执行多个 pending 任务
3. **实时进度监控**：进度条 + 子任务计数 + Token/Cost 追踪
4. **Discord 通知**：Webhook 推送 Pipeline 事件（零 Token 消耗）
5. **Web Dashboard**：暗色主题实时仪表盘
6. **PM 自动发现**：GitHub Issues → 自动提交任务
7. **Prompt 模板化**：Few-shot examples + 精确字段 schema

---

## 二、架构设计

### 2.1 Phase 5 新增模块

```
src/multiagent/
├── conductor.py          # [重写] PID 管理 + 日志 + 并行 + 进度 + 通知
├── conductor_cli.py      # [重写] start/stop/restart + --workers + --discord-webhook
├── notify.py             # [新建] Discord Webhook/Bot 通知
├── dashboard.py          # [新建] Flask Web 仪表盘
├── pm_discover.py        # [新建] GitHub Issues 自动发现
architectures/dev-test-loop/prompts/
├── pm.md                 # [新建] PM Agent 模板 + few-shot
├── dev.md                # [新建] Dev Agent 模板 + few-shot
└── test.md               # [新建] Test Agent 模板 + few-shot
```

### 2.2 通知架构（零 Token）

```
Conductor._notify(event, task_id, ...)
    │
    ├── DiscordNotifier (webhook URL)
    │   └── HTTP POST → Discord embed 消息
    │
    └── DiscordChannelNotifier (Bot Token)
        └── REST API → 频道消息
```

关键设计：通知走纯 HTTP，不经过 LLM，不消耗 Token。

### 2.3 并行执行架构

```
_monitor_loop() 每轮 poll:
    │
    ├── _check_escalations()     (所有项目)
    ├── _check_stop_signal()      (所有项目)
    ├── _discover_and_submit()    (PM 自动发现)
    └── process_all()
         │
         ├── project-A.task-1 → ThreadPoolExecutor
         ├── project-A.task-2 → ThreadPoolExecutor
         ├── project-B.task-1 → ThreadPoolExecutor
         └── ... (max_workers=3)
```

每个任务在独立线程中执行完整的 PM→Dev→Test 流水线。

### 2.4 进度计算模型

```
PM task_breakdown [N 个子任务]
         ↓
Dev sub_tasks_completed [M 个已完成]
         ↓
progress% = stage_weight + (M/N × phase_weight)

Stage:  PM(10%) → Dev(30-60%) → Test(80-95%) → Done(100%)
Bar:    [██░░░░░░░░░░░░░░] 25%
```

---

## 三、实施过程

### Step 1: Daemon 生产化

**文件：** `conductor.py`, `conductor_cli.py`

| 功能 | 实现 |
|------|------|
| PID 文件 | 启动时写入 `.conductor.pid`，停止后清理 |
| 重复启动检测 | 检查 PID 存活，拒绝重复启动 |
| 优雅停止 | SIGTERM → `self.state.running = False` → 清理退出 |
| DB 停止信号 | 轮询 `workflow_state` 表，收到 `stopped` 自行终止 |
| 强制停止 | SIGTERM 超时 5s 后 SIGKILL |
| restart 命令 | `conductor restart` = stop + start |
| 日志系统 | Python `logging` + `RotatingFileHandler` (10MB×5) |
| daemon 模式 | `os.fork()` + `os.setsid()` + stdio 重定向 null |

### Step 2: Discord Webhook 通知

**文件：** `notify.py`（新建，148 行）

- `DiscordNotifier`：HTTP POST embed 到 Webhook URL
- `DiscordChannelNotifier`：通过 Bot Token 发送频道消息
- 事件：started（蓝色）、completed（绿色）、failed（红色）、escalated（橙色）
- 自动检测 ClaudeClaw 配置，无需额外设置
- `--discord-webhook <URL>` CLI 参数

### Step 3: 多任务并行 + 进度监控

**文件：** `conductor.py`

| 功能 | 实现 |
|------|------|
| 并行执行 | `process_all()` + ThreadPoolExecutor |
| 并发控制 | `--workers/-w` (默认 3) |
| 进度条 | `_progress_bar()` — ASCII `██░░` 格式 |
| 子任务追踪 | PM `task_breakdown` N→Dev `subtasks_completed` M |
| 实时状态 | `conductor status` 显示每个 in-flight 任务详情 |
| Token 追踪 | 实时 input/output token、cost、duration |

### Step 4: Web Dashboard

**文件：** `dashboard.py`（新建，242 行）

- Flask 应用，暗色 GitHub 风格主题
- 统计卡片：Pending/Running/Completed/Escalated + Token/Cost
- In-Flight 任务表：进度条 + PM→Dev→Test 管道可视化
- 全部任务历史表
- `/api/state` JSON 端点
- 15 秒自动刷新
- `multiagent dashboard` CLI 命令

### Step 5: PM 自动发现

**文件：** `pm_discover.py`（新建，148 行）

- `discover_github_issues()`：通过 `gh` CLI 获取 Issues
- 标签类型映射：bug→bug, debug→debug, feature→feature, docs→docs 等
- 去重：`agentforge:submitted` 标签避免重复提交
- 集成到 Conductor 轮询周期
- `--pm-auto-discover` CLI 参数

### Step 6: Prompt 模板化

**文件：** `architectures/dev-test-loop/prompts/{pm,dev,test}.md`

| Agent | 模板内容 |
|-------|---------|
| PM | 5 字段 schema + few-shot（搜索命令示例） |
| Dev | 2 字段 schema + few-shot（stats 命令示例） |
| Test | verdict schema + approved/rejected 双示例 |

`engine.py` 新增 `_load_prompt_template()` 方法，自动加载模板并置于 prompt 最前。

---

## 四、问题与解决方案

### 问题 1：Conductor 死循环重试

**现象**：`process_one` 调用 `cmd_run` 但 `WorkflowOrchestrator` 从未被 import（import 语句在 `if dry_run:` 块内），导致 `UnboundLocalError`，任务保持 `pending` 状态，Conductor 每 3 秒重新执行一次。

**解决**：将 `from .orchestrator import WorkflowOrchestrator` 移到 `engine_cli.py` 顶层。

### 问题 2：Rejection Loop 大小写不匹配

**现象**：Test Agent 输出 `"verdict": "REJECTED"` (大写)，但代码检查 `"rejected"` (小写)，导致打回逻辑不触发。

**解决**：全部 3 处裁决检查统一加 `.lower()`。

### 问题 3：Dev 输出 JSON 字段名不匹配

**现象**：Dev Agent 用 `branch` 和 `files_created`，但 schema 要求 `branch_name` 和 `files_changed`，导致 schema 校验失败 → 重复重试。

**解决**：`_build_prompt` 现在明确告知 Agent 精确的 JSON 字段名 + few-shot 示例。

### 问题 4：ClaudeClaw 状态栏覆盖

**现象**：ClaudeClaw 插件通过 hook 设置 `node .claude/statusline.cjs` 作为 statusLine，覆盖 ClaudeHUD。

**解决**：settings.json 中 `statusLine.command` 设为 ClaudeHUD（bun 版），ClaudeClaw 仍在后台运行（Discord 通知）。

### 问题 5：通知未触发

**现象**：Conductor 未推送 Discord 通知。

**原因**：`create_notifier()` 无 webhook URL 时，自动检测 ClaudeClaw 配置尝试通过 Bot Token 发频道消息，但 `listenGuilds` 是 Guild ID 而非 Channel ID。

**解决**：通过 `--discord-webhook <URL>` 明确指定 Webhook URL，100% 可靠。

---

## 五、测试情况

### 5.1 门禁回归

```bash
python gates/regression.py
```

| # | 模块 | 测试数 | 状态 |
|---|------|--------|:--:|
| 1 | DB: CRUD + Dedup | 5 | ✅ |
| 2 | Adapters: CLI + Config | 5 | ✅ |
| 3 | Orchestrator: Workflow Engine | 6 | ✅ |
| 4 | Engine CLI: run command | 11 | ✅ |
| 5 | PM Engine: submit via Engine | 8 | ✅ |
| 6 | Parallel: fan-out execution | 6 | ✅ |
| 7 | Heartbeat: crash recovery | 7 | ✅ |
| 8 | Metrics CLI: token/cost | 9 | ✅ |
| 9 | Conductor: auto-trigger | 19 | ✅ |

**总计：9/9 模块，76 tests 全部通过**

### 5.2 实战验证

| 测试 | 结果 |
|------|------|
| 单任务 Pipeline | PM→Dev→Test 全自动，4 分 23 秒完成，Test approved |
| 双任务并行 | stats + template 同时执行，进度条实时显示 |
| Discord 通知 | Webhook 推送 started/completed embed 消息成功 |
| Web Dashboard | 暗色仪表盘正常渲染，15s 自动刷新 |

### 5.3 任务类型扩充

| Type | GitHub Labels |
|------|--------------|
| `bug` | bug, fix |
| `debug` | debug |
| `feature` | feature (默认) |
| `enhancement` | enhancement, refactor, performance |
| `docs` | docs, documentation |
| `manual` | CLI 手动提交 |

---

## 六、与原计划的差异

### 6.1 任务书 vs 实际实施

| 任务书要求 | 实际实施 | 差异说明 |
|-----------|---------|---------|
| Conductor Daemon 生产化 | ✅ 完成 | 增加 PID 检查、DB 停止信号、强制停止 |
| 多项目并发监控 | ✅ 完成 | 增加 ProjectConfig + 顺序轮询 |
| Web Dashboard | ✅ 完成 | 增加暗色主题 + 进度条嵌入式渲染 |
| Discord 实时通知 | ✅ 完成 | Webhook + Bot Channel 双方案 |
| PM 自动发现 | ✅ 完成 | 增加标签类型映射 + issue 去重 |
| Prompt 模板化 | ✅ 完成 | 增加 few-shot examples |
| Discord 降级（原 Phase 4） | ✅ 实现在 Phase 5 | 从 Phase 4 推迟到本阶段 |

### 6.2 新增功能（超出原计划）

| 功能 | 说明 |
|------|------|
| 进度条 + 子任务追踪 | 基于 PM task_breakdown 的细粒度进度 |
| 任务类型扩展 | 从 feature/bug 2 种扩展到 6 种 |
| ClaudeClaw 集成 | Discord Bot 在线 + 上下文继承 |
| REST API 端点 | `/api/state` JSON 接口 |
| on_verdict_approved 分支 | Test 批准后的步骤衔接 |

---

## 七、版本历史

| Version | Commit | Content |
|---------|--------|---------|
| v0.1.0 | `733ef06` | Phase 1+2: Dev+Test, PM Agent, Flask TODO |
| v0.2.0 | `eeac214` | Phase 3: Engine 生产化, DevLog |
| v0.3.0 | `744998b` | Phase 4: Conductor + 全链路自动化 |
| v0.5.0 | `44d10ce` | Phase 4 生产验证: prompt 修复 + 管道实战 |
| **v0.6.0** | **`06328fb`** | **Phase 5: Conductor 生产化 + Dashboard + Discord** |

## 八、附录

### A. Phase 5 新增/修改文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/multiagent/conductor.py` | 702 | [重写] PID/日志/并行/进度/通知 |
| `src/multiagent/conductor_cli.py` | 424 | [重写] restart + workers + discord |
| `src/multiagent/notify.py` | 208 | [新建] Webhook + Bot 通知 |
| `src/multiagent/dashboard.py` | 242 | [新建] Web 仪表盘 |
| `src/multiagent/pm_discover.py` | 148 | [新建] GitHub Issues 发现 |
| `src/multiagent/engine.py` | 修改 | Prompt 模板加载 |
| `src/multiagent/orchestrator.py` | 修改 | 大小写修复 + verdict_approved |
| `architectures/dev-test-loop/prompts/pm.md` | 48 | PM 模板 + few-shot |
| `architectures/dev-test-loop/prompts/dev.md` | 36 | Dev 模板 + few-shot |
| `architectures/dev-test-loop/prompts/test.md` | 46 | Test 模板 + few-shot |

### B. Phase 5 CLI 命令一览

```bash
# Daemon 管理
multiagent conductor start [--foreground] [--workers N] [--pid-file PATH]
multiagent conductor stop
multiagent conductor restart
multiagent conductor status

# 通知
multiagent conductor start --discord-webhook <URL>

# PM 自动发现
multiagent conductor start --pm-auto-discover

# Web Dashboard
multiagent dashboard [--port PORT]

# 全开模式
multiagent conductor start --workers 5 --pm-auto-discover --discord-webhook <URL>
```
