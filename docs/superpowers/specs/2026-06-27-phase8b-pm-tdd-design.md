# Phase 8b: PM 强化 + TDD 工作流 — 设计规格

> 版本：v0.9.0 | 日期：2026-06-27 | 作者：nanajiang + Claude

## 一、背景与目标

### 1.1 问题诊断

| 问题 | 影响 |
|------|------|
| PM 输出缺少验收标准 | Dev 凭感觉实现，Test 不知道测什么 |
| PM 复杂度判定主观 | 无法准确路由到合适 Agent |
| 无 TDD 流程 | Test 事后验证，打回率高达 3 次，烧 Token |
| 角色 Skill 太基础 | PM/Dev/Test 均缺少方法论指导 |
| PM 兼任架构师 | 缺少架构分析工具支持 |

### 1.2 目标

1. PM 输出增强：验收标准 + 复杂度矩阵
2. 新建 TDD 工作流：PM→Test写→Dev最小实现→Test验
3. 集成社区架构 Skill：architect + architecture-introspector
4. 为各角色注入 Superpowers 方法论 Skill

---

## 二、PM 强化

### 2.1 输出变更

```
当前:                                    强化后:
├── root_cause                           ├── root_cause
├── target_module                        ├── target_module
├── complexity (low/med/high)            ├── complexity (impact × effort 矩阵)
├── task_breakdown                       ├── task_breakdown (每项含验收标准)
├── estimated_files                      ├── estimated_files
                                         └── acceptance_criteria [新增]
                                            (2-5 条 Given/When/Then)
```

### 2.2 复杂度矩阵

```
                影响面 (用户/文件/模块数)
            低              中              高
实现  低    low             medium          high
难度  中    medium          high            critical
      高    high            critical        critical
```

### 2.3 PM SKILL.md 重写要点

- 结构化需求分析流程（问题→影响→根因→矩阵）
- 强制输出 Given/When/Then 验收标准
- 架构分析引导：现有代码调用 architecture-introspector，新模块调用 architect
- 复杂度矩阵指导 Dev 选择策略

---

## 三、角色 Skill 补充方案

### 3.1 角色 Skill 对照表

| 角色 | 主 Skill | 辅助 Skill (Superpowers/社区) |
|------|---------|------------------------------|
| PM | pm/SKILL.md (重写) | architect (jschulte), architecture-introspector (mahidalhan) |
| Dev | dev/SKILL.md (重写) | test-driven-development, systematic-debugging, using-git-worktrees |
| Test | test/SKILL.md (重写) | verification-before-completion, requesting-code-review |

### 3.2 Superpowers 子 Agent 适配性

| Skill | 子 Agent | 原因 |
|-------|:---:|------|
| test-driven-development | ✅ | 纯方法论，RED-GREEN 循环 |
| systematic-debugging | ✅ | 4 阶段流程，自主执行 |
| using-git-worktrees | ✅ | 纯工具操作 |
| verification-before-completion | ✅ | 自检清单 |
| requesting-code-review | ✅ | 格式化输出 |
| brainstorming | ❌ | 需人类逐轮交互 |
| writing-plans | ❌ | 需人类审批 |

### 3.3 安装

```bash
# 社区架构 Skill (已安装到 architectures/dev-test-loop/skills/)
# - architect: jschulte/claude-plugins
# - architecture-introspector: mahidalhan/claude-hacks

# Superpowers (已安装在 ~/.claude/skills/)
# 通过 adapter 的 --append-system-prompt-file 注入
```

---

## 四、TDD 工作流

### 4.1 新建 `pm-testfirst-dev-test.yaml`

```
PM → Test(写失败测试) → Dev(最小实现) → Test(验证通过)

Step 1: pm_analyze (agent: pm)
  输出: + acceptance_criteria (Given/When/Then)
  
Step 2: test_write (agent: test)
  depends_on: pm_analyze
  输入: acceptance_criteria, target_module
  输出: test_file_path, test_cases, expected_failures
  职责: 基于验收标准写一定会失败的测试 (RED)

Step 3: dev_implement (agent: dev)
  depends_on: test_write
  输入: acceptance_criteria, test_file_path
  输出: branch_name, files_changed
  约束: YAGNI — 最小实现让测试变绿 (GREEN)

Step 4: test_verify (agent: test)
  depends_on: dev_implement
  输入: branch_name, test_file_path
  输出: verdict (approved/rejected), test_summary
  on_verdict_rejected → dev_implement (max 3)
  on_verdict_approved → mark_complete
```

### 4.2 与现有 workflow 对比

| 维度 | pm-dev-test | pm-testfirst-dev-test |
|------|------------|----------------------|
| 步骤数 | 3 | 4 |
| Dev 输入 | PM task_breakdown | acceptance_criteria + 失败测试 |
| Dev 约束 | "实现功能" | "最小实现让测试变绿" |
| Test 职责 | 仅事后验证 | 先写测试 + 后验证 |

---

## 五、执行步骤

```
Step 1: 重写 PM SKILL.md (+验收标准 + 复杂度矩阵 + 架构引导)
Step 2: 重写 Dev SKILL.md (+TDD + 最小实现 + 调试方法论)
Step 3: 重写 Test SKILL.md (+测试先行 + 严格验证)
Step 4: 更新 roles.yaml (output_required 加 acceptance_criteria)
Step 5: 更新 PM prompt 模板 (pm.md)
Step 6: 创建 pm-testfirst-dev-test.yaml 工作流
Step 7: 配置 adapter 注入 Superpowers skill 到子 Agent
Step 8: 回归测试 + 验证
```

---

## 六、成功标准

- [ ] PM 输出包含 acceptance_criteria (Given/When/Then 格式)
- [ ] 复杂度使用 impact × effort 矩阵判定
- [ ] TDD 工作流可 dry-run 通过
- [ ] PM/Dev/Test SKILL.md 包含方法论指导
- [ ] 架构 Skill 可在 PM 流程中调用
- [ ] 回归测试全部通过
- [ ] 实际任务 TDD 流程跑通
