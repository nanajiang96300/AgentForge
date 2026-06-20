# A2A 结构化通信协议规范

> 版本: v1.0  
> 定位: 框架通用，定义 Agent 间通信的强制规范

---

## 一、设计原则

1. **零自然语言歧义**: Agent 之间通过 Git Issue/PR + YAML Frontmatter 通信，不依赖自然语言理解
2. **强制结构化**: 每条通信必须包含必填字段，缺失则被格式校验拦截
3. **单向隔离**: Dev 看不到 Test 的功能验收测试用例，Test 改不了 Dev 的源码
4. **可追溯**: 所有通信落盘为 Git Issue/PR 评论，永久可审计

---

## 二、通信矩阵

| 发送方 | 接收方 | 通道 | 载体 | 触发条件 |
|--------|--------|------|------|----------|
| Human | Conductor | Discord | 自然语言 + 图片 | 随时 |
| Conductor | Human | Discord | 自然语言 + Mermaid 图片 | 状态汇报/异常升级 |
| Conductor | PM | Git Issue | `task_assignment` 模板 | Human 发起基建任务 |
| Conductor | User Agent | 文件系统 JSON | `business_directive.json` | Human 发起业务指令 |
| User Agent | Conductor | Git Issue | `anomaly_report` 模板 | 工具链异常 |
| PM | Dev | Git Issue @提及 | `task_assignment` 模板 | 分析完毕指派任务 |
| Dev | Test | Git PR | `bugfix_pr` / `feature_pr` 模板 | 修复完成 |
| Test | Dev | PR Review Comment | `rejection_info` YAML | CI 失败打回 |
| Test | Conductor | PR Review Comment (CC) | `approval_review` 模板 | CI 全绿 |
| PM | Conductor | Git Issue 状态变更 | Webhook 通知 | Issue 状态变化 |

---

## 三、YAML Frontmatter 强制规范

### 3.1 通用必填字段

```yaml
---
a2a_version: "1.0"
message_id: "<uuid>"
timestamp: "<ISO 8601>"
sender:
  role: "<conductor|pm|dev|test|user-agent>"
  agent_id: "<agent identifier>"
receiver:
  role: "<conductor|pm|dev|test|user-agent>"
---
```

### 3.2 异常上报专用字段 (User Agent → PM)

```yaml
---
anomaly_info:
  trigger_module: "<模块名>"
  error_type: "<错误类型>"
  environment:
    input_size: "<参数>"
    config_file: "<路径>"
  reproduction:
    command: "<复现命令>"
    failure_location: "<文件:行号>"
  stack_trace: |
    <精简堆栈>
---
```

### 3.3 打回专用字段 (Test → Dev)

```yaml
---
rejection_info:
  test_id: "T-<NNN>"
  input_params: "<参数>"
  expected: "<预期值>"
  actual: "<实际值>"
  failure_location: "<文件:行号>"
  stack_trace: |
    <完整堆栈>
  rejection_count: <N>
  max_rejections: 3
---
```

---

## 四、通信契约的强制校验

1. **GitHub Issue Template**: `.github/ISSUE_TEMPLATE/` 下模板自动强制使用
2. **PR Template**: `.github/PULL_REQUEST_TEMPLATE/` 下模板创建时自动填充
3. **格式校验**: Test Agent 在 CI 前校验 PR 描述是否符合 YAML 规范
4. **Execution Guard**: 操作系统级别拦截越权文件写入
