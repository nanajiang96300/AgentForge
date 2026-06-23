# Phase 7 实施报告：角色系统 + Dashboard 重写 + 生产化加固

> 版本：v0.7.5 | 日期：2026-06-23 | 作者：nanajiang (架构监视者) + Claude (AI Agent)

## 一、背景与目标

### 1.1 前期状态

Phase 5 完成后，AgentForge 具备 PM→Dev→Test 全自动流水线 + Conductor 调度 + Dashboard 基础监控。但在实战运行中暴露了以下问题：

| 问题 | 影响 |
|------|------|
| **仪表盘 HTML/CSS/JS 全嵌入 Python 字符串** | f-string `{{}}` 转义地狱，JS `//` 注释吞噬代码，维护不可持续 |
| **角色系统不完整** | 只能手写 roles.yaml，无 CLI 管理，model/memory/permissions.write 未传递到 adapter |
| **工作流无验证** | 依赖错误、循环拓扑只有在运行时才暴露 |
| **Designer 连线无类型** | 无法区分 depends_on 硬依赖 vs verdict 条件边 |
| **3 层重试硬顶缺失** | 无限循环烧 Token ($93 损失) |
| **进程泄漏** | Agent 孙子进程在超时/stop 后存活 |
| **DB 无限膨胀** | step_results/agent_metrics/heartbeat 只 INSERT 不 DELETE |

### 1.2 Phase 7 目标

1. **运行时健壮性**：进程组管理、DB 自动清理、孤儿任务恢复、信号处理
2. **通知系统增强**：重试 + 退避、截断提示、打回消抖、i18n 中文
3. **Dashboard 重写**：Jinja2 模板 + 静态 CSS/JS，抛弃内联字符串
4. **角色系统 CLI**：`multiagent role create/list/show/delete/clone/validate`
5. **工作流 CLI + 验证引擎**：模板创建、7 项验证检查、3-Color DFS 循环检测
6. **Designer V2**：边类型选择、颜色编码、localStorage 持久化
7. **多拓扑测试**：5 种 DAG 结构全量 dry-run 验证

---

## 二、架构变更

### 2.1 Phase 7 新增/重写模块

```
src/multiagent/
├── dashboard.py              # [重写] 650→395行 (-40%)，纯路由 + API
├── role_cli.py               # [新建] 角色管理 CLI (400行)
├── workflow_cli.py            # [新建] 工作流 CLI + 验证引擎 (350行)
├── templates/                # [新建] Jinja2 模板 (4 文件, 315行)
│   ├── base.html             # 基础布局 + 导航 + CDN
│   ├── index.html            # Pipeline Monitoring
│   ├── designer.html         # Workflow Designer V2
│   └── commands.html         # Command Center
├── static/                   # [新建] 静态资源 (3 文件, 768行)
│   ├── dashboard.css         # 暗色主题 CSS
│   ├── dashboard.js          # Charts + Search + DAG
│   └── designer.js           # Designer 拖拽 + 边类型
├── engine.py                 # [修改] start_new_session + killpg + reap
├── db.py                     # [修改] prune/vacuum + search_tasks + status guard
├── conductor.py              # [修改] 孤儿恢复 + in-flight 清理 + SIGHUP
├── conductor_cli.py          # [修改] double-fork daemon
├── notify.py                 # [重写] retry + truncate + debounce + i18n
├── adapters/claude_code.py   # [修改] model/memory/permissions 接入
└── runtime/registry.py       # [修改] personality 字段

architectures/dev-test-loop/
├── templates/                # [新建] 骨架模板
│   ├── SKILL.md.skeleton     # 角色 SKILL.md 骨架
│   └── prompt.md.skeleton    # 角色 prompt.md 骨架

gates/                        # [扩展] 9→20 模块, 76→~200 tests
├── test_retry_cap.py         # [新建] 5 tests
├── test_hooks.py             # [新建] 4 tests
├── test_dashboard.py         # [新建] 20 tests
├── test_notify.py            # [新建] 17 tests
├── test_db_cleanup.py        # [新建] 9 tests
├── test_engine_process.py    # [新建] 6 tests
├── test_graph_engine.py      # [新建] 21 tests
├── test_agent_registry.py    # [新建] 12 tests
├── test_pm_search.py         # [新建] 7 tests
├── test_workflow_cli.py      # [新建] 20 tests
├── test_topology.py          # [新建] 18 tests
└── workflows/                # [新建] 测试 YAML
    ├── test_linear.yaml
    ├── test_diamond.yaml
    ├── test_reviewer.yaml
    └── test_rejection_loop.yaml
```

### 2.2 Dashboard 架构对比

```
❌ 旧架构 (v0.6.0)                      ✅ 新架构 (v0.7.5)
dashboard.py  650行                    dashboard.py  395行  Python routes + API
  ├── 内联 HTML f-string                 templates/
  ├── 内联 CSS (Python 字符串)            ├── base.html     Jinja2 继承
  ├── 内联 JS (Python 字符串拼接)         ├── index.html    模板逻辑
  ├── {{}} 转义地狱                       ├── designer.html 干净 HTML
  └── // 注释吞噬代码 Bug                 └── commands.html
                                       static/
                                         ├── dashboard.css  真实 CSS
                                         ├── dashboard.js   真实 JS
                                         └── designer.js    真实 JS
```

### 2.3 角色配置完整映射

```
roles.yaml / CLI 创建
       │
       ▼
AgentConfig (registry.py) — 12 个字段
       │
       ▼
AgentSpawner.spawn() (engine.py)
       │
       ├─→ agent_config["model"] → --model flag          ← [v0.7.5 修复]
       ├─→ agent_config["skill"] → --append-system-prompt-file
       ├─→ agent_config["memory"] → mkdir -p              ← [v0.7.5 修复]
       ├─→ agent_config["permissions"]["write"] → --allowedTools  ← [v0.7.5 修复]
       ├─→ agent_config["permissions"]["deny"] → --disallowedTools
       ├─→ agent_config["output_required"] → prompt JSON schema
       └─→ agent_config["timeout"] → orchestrator step timeout
```

---

## 三、实施过程

### Step 1: 运行时健壮性修复

**文件：** `engine.py`, `db.py`, `conductor.py`, `conductor_cli.py`

| 修复 | 实现 |
|------|------|
| Agent 进程泄漏 | `spawn()` 加 `start_new_session=True`；`_kill_process()` 重写为 `os.killpg()` 杀整棵进程树 (SIGTERM→5s→SIGKILL) |
| 僵尸进程清理 | `reap_lost_agents()` 查询心跳表清理僵尸 PID |
| DB 无限膨胀 | `prune_step_results(30d)`、`prune_agent_metrics(90d)`、`prune_heartbeat(7d)`、`cleanup_task_data()`、`vacuum()`；connect 时自动触发 |
| Conductor stop 不杀 Agent | `_kill_in_flight_agents()` 枚举心跳 PID → killpg |
| 孤儿任务恢复 | `_recover_orphaned_tasks()` 启动时查找 running 任务 → 清理 → 标记 failed |
| 信号处理 | `_handle_signal` 加 `self._lock`；注册 SIGHUP；double-fork daemon |
| 任务状态不一致 | `update_task_status` 拒绝 terminal→non-terminal 降级 |

### Step 2: 通知系统增强

**文件：** `notify.py`

| 功能 | 实现 |
|------|------|
| Discord 发送重试 | `_send_with_retry()` 3 次重试，退避 [1s, 4s, 10s]，429 速率限制加倍等待 |
| 截断提示 | `_truncate_field(text, limit)` 超限追加 `...[truncated]` |
| Embed 总量校验 | `_validate_embed_total()` 确保总字段值 ≤ 6000 字符 |
| 打回消抖 | `NotifierStepHook` 30 秒冷却期 |
| i18n 中英文 | `set_language('zh'|'en')`，`AGENTFORGE_LANG` 环境变量 |

### Step 3: Dashboard 重写

**文件：** `dashboard.py` (650→395 行), `templates/` (4 文件), `static/` (3 文件)

| 组件 | 旧方案 | 新方案 |
|------|--------|--------|
| HTML | Python f-string 内联 | Jinja2 `{% extends "base.html" %}` |
| CSS | Python 字符串 | `static/dashboard.css` (303 行) |
| JS | Python 字符串拼接 | `static/dashboard.js` + `static/designer.js` |
| 导航 | Pipeline Monitoring 不可点击 | 全部可点击链接 |
| 图表 | 无 | Chart.js 7 天趋势 + 错误处理 |
| DAG | 无 | Mermaid.js 工作流可视化 |
| 搜索 | 无 | 客户端实时过滤 |

### Step 4: 角色系统 CLI

**文件：** `role_cli.py` (400 行)

```bash
multiagent role create <name> [--model ...] [--write-paths ...] [...]
  → 生成 SKILL.md 骨架 + prompt.md 骨架 + 注册到 roles.yaml

multiagent role list          # 列出所有角色
multiagent role show <name>   # 查看详情（12 字段）
multiagent role clone <src> --name <new>  # 克隆角色
multiagent role validate <name>  # 验证完整性（7 项检查）
multiagent role delete <name> [--files]  # 删除
```

**SKILL.md 生成策略：模板 + AI 辅助**
1. CLI 生成骨架（占位符填充）
2. 用户复制给 AI 填充完整内容
3. `role validate` 验证完整性

### Step 5: 工作流 CLI + 验证引擎

**文件：** `workflow_cli.py` (350 行)

```bash
multiagent workflow list                    # 4 个模板
multiagent workflow create pm-dev-test -o wf.yaml
multiagent workflow validate wf.yaml        # 7 项验证
multiagent workflow graph wf.yaml           # ASCII 可视化
```

**验证引擎 7 项检查：**

| 检查项 | 类型 |
|--------|------|
| 缺失 agent 分配 | ⚠️ warning |
| 依赖引用不存在 | ❌ issue |
| 重复 step id | ❌ issue |
| on_verdict_rejected.next 不存在 | ❌ issue |
| Rejection loop | ⚠️ warning (由 max_rejections 控制) |
| 硬循环（非 rejection） | ❌ issue (3-Color DFS) |
| Agent 未注册 | ⚠️ warning |

### Step 6: Designer V2

**文件：** `templates/designer.html`, `static/designer.js`

| 功能 | 说明 |
|------|------|
| 边类型选择 | depends_on (蓝实线) / verdict_rejected (红虚线 ✗) / verdict_approved (绿虚线 ✓) |
| 颜色编码 | 3 种边类型独立颜色 + 线型 + 标签 |
| 图例 | 侧边栏 legend |
| localStorage | 刷新后自动恢复图形 |
| 节点去重 | 同名 agent 自动编号 (DEV 1, DEV 2) |
| 富文本导出 | YAML 包含完整边类型信息 |
| Bug 修复 | `var AGENTS = []` 覆盖模板数据 (JS hoisting) |

### Step 7: 多拓扑测试

**文件：** `gates/workflows/`, `gates/test_topology.py`

| 拓扑 | Steps | 验证 |
|------|-------|------|
| D1 Linear | PM→Dev→Test | 依赖链、数据流 |
| D2 Diamond | PM→[Dev1,Dev2]→Test | `_can_parallelize`、多点汇聚 |
| D3 Reviewer | PM→Dev→Reviewer→Test | verdict 路由、回边 |
| D4 Rejection | PM→Dev→Test (max 3) | escalation 触发 |
| D5 Complex | PM→[Dev×3]→Test | fan-out→fan-in |

---

## 四、问题与解决方案

### 问题 1：JS `//` 注释吞噬 fetch() 调用

**现象**：7-Day Trends 和 Workflow DAG 不显示

**根因**：Python 字符串拼接把 `// comment` 和 `fetch(...)` 连在同一行，JS 行注释吞噬代码

**解决**：Dashboard 重写 — JS 代码移到独立 `.js` 文件，不再有 Python 字符串拼接

### 问题 2：`var AGENTS = []` 覆盖模板数据

**现象**：Designer 中 pm/dev/test 按钮点击无反应

**根因**：`designer.js` 中 `var AGENTS = []` 由于 JS 变量提升，覆盖了 HTML 模板中 `AGENTS = [{...}]` 设置的数据

**解决**：删除 `var AGENTS = []; var TEMPLATES = {}` 声明，使用模板初始化的全局变量

### 问题 3：Model/Memory/Permissions 未传递

**现象**：roles.yaml 中配置的 model 字段不会传给 Claude Code CLI

**根因**：`ClaudeCodeAdapter.build_command()` 从未读取 `agent_config["model"]`

**解决**：重写 `build_command()` 完整读取 12 个字段，生成正确 CLI 参数

### 问题 4：Task 状态 completed→assigned 降级

**现象**：`task-767c9b94` 完成但状态为 `assigned`

**根因**：`pm_analyze.on_success.to_state = "assigned"` 在 `test_verify.mark_complete` 之后覆盖

**解决**：`update_task_status` 增加 terminal status guard

---

## 五、测试情况

### 5.1 门禁回归

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
| 10 | Retry Caps: hard limits | 5 | ✅ |
| 11 | Step Hooks: lifecycle events | 4 | ✅ |
| 12 | Dashboard: page rendering + APIs | 20 | ✅ |
| 13 | Notify: i18n + retry + debounce | 17 | ✅ |
| 14 | DB Cleanup: prune + vacuum | 9 | ✅ |
| 15 | Engine Process: killpg + reap | 6 | ✅ |
| 16 | Graph Engine: DAG + serialization | 21 | ✅ |
| 17 | Agent Registry: CRUD + YAML | 12 | ✅ |
| 18 | PM Search: keyword + status | 7 | ✅ |
| 19 | Workflow CLI: templates + validation | 20 | ✅ |
| 20 | Topology: DAG structures | 18 | ✅ |

**总计：20/20 模块，~200 tests 全部通过**

### 5.2 验证引擎 7 项全通过

```
✅ Missing agent → warning
✅ Bad depends_on → issue
✅ Duplicate id → issue
✅ Bad verdict next → issue
✅ Rejection loop → warning
✅ Hard cycle → issue
✅ Unknown agent → warning
```

---

## 六、与原计划的差异

| 原计划 | 实际实施 | 差异说明 |
|--------|---------|---------|
| Phase 6: 多架构/WebSocket/Systemd | Phase 7: 角色系统/Dashboard 重写 | 优先修复架构债和实战问题 |
| Designer 简单连线 | Designer V2 边类型 + 颜色编码 | 超出计划 |
| — | SKILL.md 骨架模板 + AI 填充策略 | 新增 |
| — | Workflow CLI 验证引擎 (7 项) | 新增 |
| — | 3-Color DFS 循环检测 | 新增 |

---

## 七、版本历史

| Version | Commit | Content |
|---------|--------|---------|
| v0.1.0 | `733ef06` | Phase 1+2: Dev+Test, PM Agent, Flask TODO |
| v0.2.0 | `eeac214` | Phase 3: Engine 生产化, DevLog |
| v0.3.0 | `744998b` | Phase 4: Conductor + 全链路自动化 |
| v0.5.0 | `44d10ce` | Phase 4 验证: prompt 修复 + 管道实战 |
| v0.6.0 | `06328fb` | Phase 5: Daemon 生产化 + Dashboard + Discord |
| v0.7.0 | `4225cf2` | Phase 7: 重试硬顶 + 钩子 + i18n + Git 工作流 |
| **v0.7.5** | **`b08f396`** | **Phase 7: 角色系统 + Dashboard 重写 + Designer V2 + 验证引擎** |

## 八、Phase 7 新增/修改文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/multiagent/dashboard.py` | 395 | [重写] 纯路由 + API, -40% 代码量 |
| `src/multiagent/role_cli.py` | 400 | [新建] 角色管理 CLI |
| `src/multiagent/workflow_cli.py` | 350 | [新建] 工作流 CLI + 验证引擎 |
| `src/multiagent/templates/*.html` | 315 | [新建] Jinja2 模板 (4 文件) |
| `src/multiagent/static/*.{css,js}` | 768 | [新建] 静态资源 (3 文件) |
| `src/multiagent/engine.py` | +90 | [修改] killpg + reap |
| `src/multiagent/db.py` | +107 | [修改] prune + vacuum + search_tasks + status guard |
| `src/multiagent/conductor.py` | +91 | [修改] 孤儿恢复 + 信号处理 |
| `src/multiagent/conductor_cli.py` | +18 | [修改] double-fork |
| `src/multiagent/notify.py` | +158 | [重写] retry + truncate + debounce |
| `src/multiagent/adapters/claude_code.py` | +30 | [修改] model/memory/permissions 接入 |
| `src/multiagent/runtime/registry.py` | +5 | [修改] personality 字段 |
| `architectures/dev-test-loop/templates/` | 2 文件 | [新建] 骨架模板 |
| `gates/` | +12 文件 | [扩展] 11 个新测试模块 + 4 个测试 YAML |

### Phase 7 CLI 命令一览

```bash
# 角色管理 (新增)
multiagent role create <name> [--model ...] [--write-paths ...]
multiagent role list
multiagent role show <name>
multiagent role clone <src> --name <new>
multiagent role validate <name>
multiagent role delete <name> [--files]

# 工作流管理 (新增)
multiagent workflow list
multiagent workflow create <template> -o <output.yaml>
multiagent workflow validate <path>
multiagent workflow graph <path>

# Daemon 管理
multiagent conductor start [--foreground] [--workers N]
multiagent conductor stop / restart / status / alerts

# 监控
multiagent dashboard
multiagent metrics [--agent] [--json]
```
