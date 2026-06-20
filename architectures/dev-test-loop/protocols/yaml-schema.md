# YAML Frontmatter Schema 定义

> 所有 A2A 通信的必填字段规范。格式校验在 CI 前强制执行。

---

## 通用 Schema（所有通信必须包含）

```yaml
a2a_version:
  type: string
  required: true
  pattern: "^1\\.0$"
  description: "A2A 协议版本"

message_id:
  type: string
  required: true
  format: uuid
  description: "消息唯一标识"

timestamp:
  type: string
  required: true
  format: date-time
  description: "ISO 8601 时间戳"

sender:
  type: object
  required: true
  properties:
    role:
      type: string
      required: true
      enum: ["conductor", "pm", "dev", "test", "user-agent"]
    agent_id:
      type: string
      required: true

receiver:
  type: object
  required: true
  properties:
    role:
      type: string
      required: true
      enum: ["conductor", "pm", "dev", "test", "user-agent"]
```

---

## 异常上报 Schema（User Agent → PM）

```yaml
anomaly_info:
  type: object
  required: true
  properties:
    trigger_module:
      type: string
      required: true
    error_type:
      type: string
      required: true
    environment:
      type: object
      required: true
    reproduction:
      type: object
      required: true
      properties:
        command:
          type: string
          required: true
        failure_location:
          type: string
          required: true
          pattern: "^[^:]+:\\d+$"
    stack_trace:
      type: string
      required: true
```

---

## Task Assignment Schema（PM → Dev）

```yaml
task_info:
  type: object
  required: true
  properties:
    root_cause:
      type: string
      required: true
    target_module:
      type: string
      required: true
    complexity:
      type: string
      required: true
      enum: ["trivial", "simple", "medium", "complex"]
    estimated_files:
      type: array
      required: true
      items:
        type: string
    assigned_dev:
      type: string
      required: true
```

---

## Rejection Schema（Test → Dev）

```yaml
rejection_info:
  type: object
  required: true
  properties:
    test_id:
      type: string
      required: true
      pattern: "^T-\\d{3,}$"
    input_params:
      type: string
      required: true
    expected:
      type: string
      required: true
    actual:
      type: string
      required: true
    failure_location:
      type: string
      required: true
      pattern: "^[^:]+:\\d+$"
    stack_trace:
      type: string
      required: true
    rejection_count:
      type: integer
      required: true
      minimum: 1
      maximum: 3
```

---

## 校验失败处理

1. **CI 前校验**: Test Agent 在运行测试前先校验 PR 描述
2. **缺字段**: 立即打回，附带缺失字段清单
3. **格式错误**: 立即打回，附带期望格式
4. **Schema 版本不匹配**: 升级 Conductor，要求所有 Agent 更新协议版本
