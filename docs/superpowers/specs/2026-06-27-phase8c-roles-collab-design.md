# Phase 8c: 角色模板系统 + 灵活协作模式 — 设计规格

> 版本：v0.10.0 | 日期：2026-06-27 | 作者：nanajiang + Claude

## 一、目标

1. 角色模板系统：模板驱动的角色创建，用户选模板 + 微调参数
2. Architect 预设角色：独立的架构设计师 Agent
3. Swarms 协作模式：多个同质 Agent 并行抢任务

## 二、角色模板系统

### 2.1 模板格式

每个模板是一个 YAML 文件，存放在 `architectures/dev-test-loop/templates/roles/`:

```yaml
# templates/roles/architect.yaml
name: architect
description: 架构设计师 — 设计系统架构、生成 ADR、产出架构文档
model: deepseek-v4-pro
personality: visionary, systematic, principled
skill: architectures/dev-test-loop/skills/architect/SKILL.md
output_required:
  - architecture_doc
  - adrs
  - component_diagram
  - tech_stack
  - tradeoffs
permissions:
  read: [src/, docs/, architectures/]
  write: [docs/architecture/, .agents/memory/architect/]
  deny: [src/, tests/]
timeout: 600
session: per-issue
```

### 2.2 内建模板

| 模板名 | 用途 | 模型 |
|--------|------|------|
| `architect` | 架构设计 (C4 图 + ADR) | pro |
| `security-auditor` | 安全审计 | pro |
| `code-reviewer` | 代码审查 | flash |
| `performance-optimizer` | 性能优化 | pro |

### 2.3 CLI

```bash
multiagent role create --from-template architect --name my-architect
multiagent role create --from-template security-auditor --name sec-bot --model deepseek-v4-flash
multiagent role list-templates
multiagent role show-template architect
```

## 三、Architect 预设角色

注册为内建 Agent，可在 workflow 中使用:
- 注册到 AgentRegistry + roles.yaml
- 拥有 architect skill 作为主 Skill
- 输出: architecture_doc, adrs, component_diagram, tech_stack, tradeoffs

## 四、Swarms 协作模式

新增 workflow 模板 `swarm-dev.yaml`:
- 多个 dev Agent 从共享任务队列抢任务
- 不需要 PM 逐个指派
- 适用场景: 多个独立的同类型子任务

## 五、执行步骤

1. 实现 RoleTemplateLoader + 内建模板
2. 注册 Architect 预设角色
3. 创建 Swarms workflow 模板
4. CLI 集成 (role create --from-template, role list-templates)
5. 回归测试 + 实际任务验证
