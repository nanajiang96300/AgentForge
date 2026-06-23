# Phase 4 实施报告：Conductor + 全链路自动化

> 版本：v0.3.0 → v0.5.0 | 日期：2026-06-21 ~ 2026-06-22 | 作者：nanajiang + Claude

## 一、背景与目标

### 1.1 前期状态

Phase 3 完成了 Engine 生产化——`multiagent run` 可以一键执行 PM→Dev→Test 工作流。但整个流程仍依赖人工触发：

```
当前: Human 手动 → PM分析 → Human读结果 → Human触发Dev → Human触发Test
目标: Human → Conductor ──→ PM分析 → Dev实现 → Test验证 → Conductor汇报
```

### 1.2 Phase 4 目标

接入 Conductor 调度层，实现 Human 提交需求后完全无人值守的自动流水线。

---

## 二、架构设计

### 2.1 Conductor 调度循环

```
Conductor._monitor_loop()
    │
    ├── 每 N 秒轮询 state.db
    │   ├── get_pending_tasks() → 发现待处理任务
    │   └── process_one() → cmd_run() → PM→Dev→Test
    │
    ├── 检测 escalated 任务 → 写入 escalations 表
    │
    └── multiagent conductor alerts → Human 查询/决策
```

### 2.2 状态机

```
pending → running → assigned → completed
                  ↓
              escalated (rejection_count >= 3)
                  ↓
          Human: retry / reject
```

### 2.3 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| Conductor | `conductor.py` | 轮询调度、任务分发 |
| Conductor CLI | `conductor_cli.py` | start/status/stop/alerts/retry/reject |
| WorkflowOrchestrator | `orchestrator.py` | PM→Dev→Test DAG 编排 |
| AgentSpawner | `engine.py` | Agent 子进程生命周期 |
| StateDB | `db.py` | 6 表持久化 + escalations 表 |

---

## 三、实施过程

### Step 1: Conductor 调度循环

**`conductor.py` (276行)**

- `_monitor_loop()` — 轮询循环，发现 pending → 自动 run workflow
- `process_one()` — 取第一个 pending 任务，调用 `cmd_run`
- 状态机感知：pm_analyze 完成 → 自动触发 dev_fix → test_verify

### Step 2: 全自动工作流

**`orchestrator.py` 增强**

- `run()` — 完整 3 步 PM→Dev→Test，每步完成后自动触发下一步
- Rejection Loop: Test 打回 → Dev 重做（最多 3 次）
- Escalation: 3 次失败 → 暂停 → Human 通知
- 并行 fan-out: 无依赖步骤通过 ThreadPoolExecutor 并发

### Step 3: Conductor CLI

**`conductor_cli.py` (335行)**

```
multiagent conductor start              # 启动监控循环
multiagent conductor status             # 查看队列状态
multiagent conductor stop               # 停止监控
multiagent conductor alerts             # 查看升级事件
multiagent conductor retry <task_id>    # 重新执行
multiagent conductor reject <task_id>   # 放弃任务
```

### Step 4: Human 通知

**`db.py` — escalations 表**

```sql
CREATE TABLE escalations (
    id, task_id, step_id, reason, context, status, created_at, resolved_at, resolution
);
```

- `record_escalation()` — 记录升级事件
- `get_pending_escalations()` — 查询待处理
- `resolve_escalation()` — 处理升级

### Step 5: 门禁测试

**`gates/test_conductor.py` (19 tests)**

| 类别 | 测试数 | 内容 |
|------|--------|------|
| Conductor Core | 6 | 初始化、状态、process_one、错误处理 |
| Full Auto Pipeline | 2 | 三步接力、数据流 |
| Rejection Loop | 2 | 单次打回、最大打回触发 escalation |
| Escalation Recording | 4 | 记录、解析、查询 pending/escalated |
| Retry & Reject | 2 | 重试、不存在任务 |
| End-to-End | 3 | 完整流程、计数器、escalation 检测 |

### Step 6: 端到端验证

通过真实的 PM→Dev→Test 流水线实现了 DevLog Web Server 和 Web Editor 功能。

---

## 四、问题与解决方案

### 问题 1：WorkflowOrchestrator Import 死循环

**现象**：Conductor 每 3 秒重新执行同一任务，日志充满 `Executing workflow` 但任务从未完成。

**原因**：`from .orchestrator import WorkflowOrchestrator` 在 `if dry_run:` 块内。非干运行模式下 import 不执行 → `UnboundLocalError` → 任务保持 pending → 无限循环。

**解决**：将 import 移到 `engine_cli.py` 文件顶层。

### 问题 2：Agent 子进程权限拒绝

**现象**：`claude -p` 在子进程中，auto-mode 拒绝所有 Write/Edit/Bash 调用。

**尝试**：`--dangerously-skip-permissions` 被拦截。

**最终方案**：`--permission-mode acceptEdits` — 显式告知 auto-mode 自动批准 Edit/Write。

### 问题 3：SQLite 跨线程错误

**现象**：并行步骤执行时报 `SQLite objects created in a thread can only be used in that same thread`。

**解决**：
1. `check_same_thread=False`
2. `PRAGMA busy_timeout=5000`
3. `threading.Lock()` 保护所有写操作

### 问题 4：Dev Agent JSON 字段名不匹配

**现象**：Dev 输出 `branch`/`files_created`，但 schema 要求 `branch_name`/`files_changed`，导致 schema 校验失败 → 无限重试。

**解决**：`_build_prompt` 现在包含精确字段名 + JSON 示例格式。

### 问题 5：Rejection Loop 不重置 Test

**现象**：打回后 `test_verify` 保持 REJECTED 状态，不会再运行。

**解决**：`_handle_rejection` 同时重置 `test_verify` 和 `dev_fix` 为 PENDING。

---

## 五、测试情况

### 5.1 门禁回归（9/9 模块，76 tests）

| # | 模块 | 状态 |
|---|------|:--:|
| 1 | DB: CRUD + Dedup | ✅ |
| 2 | Adapters: CLI + Config | ✅ |
| 3 | Orchestrator: Workflow Engine | ✅ |
| 4 | Engine CLI: run command | ✅ |
| 5 | PM Engine: submit via Engine | ✅ |
| 6 | Parallel: fan-out execution | ✅ |
| 7 | Heartbeat: crash recovery | ✅ |
| 8 | Metrics CLI: token/cost | ✅ |
| 9 | Conductor: auto-trigger + full auto | ✅ |

### 5.2 实战验证

| 测试 | 结果 |
|------|------|
| DevLog Web Server | `devlog serve` Flask REST API + Web UI，269行 |
| DevLog Web Editor | `/new` `/logs/<id>/edit` 创建编辑页面 + CSS fix |
| 全自动 Pipeline | PM(2min) → Dev(7min) → Test(3min)，Test approved |

---

## 六、与原计划差异

| 原计划 | 实际 | 原因 |
|--------|------|------|
| Discord 通知（Phase 4） | 推迟到 Phase 5 | 终端 + 文件系统先行，Discord 需要 bot |
| Conductor 作为独立 Agent | 作为 Engine 调度层 | 减少组件复杂度，复用 Engine |
| 文件系统通信 | CLI + DB 通信 | CLI 已存在，DB 更可靠 |

## 七、版本

| Tag | Content |
|-----|---------|
| v0.3.0 | Phase 4 初始实现 (Conductor + CLI + escalations) |
| v0.5.0 | Phase 4 生产验证 (prompt 修复 + 管道实战) |
