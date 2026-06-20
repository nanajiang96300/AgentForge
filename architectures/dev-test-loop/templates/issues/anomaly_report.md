---
name: "[自动化上报] 工具链异常"
about: 自动化流程中捕获的工具链崩溃
labels: bug, automated
---

### 🚨 异常元数据

| 字段 | 值 |
|------|-----|
| **触发模块** | `[如：core/compute]` |
| **错误类型** | `[如：Segmentation fault / IndexError]` |
| **输入参数** | `[如：input_size=1024]` |
| **配置文件** | `[如：test.json]` |

### 🛠️ 复现步骤

1. 运行命令：`./scripts/run.sh --config [配置]`
2. 异常位置：`[文件路径]:[行号]`

### 📜 核心报错堆栈

```text
[精简后的报错日志]
```

### 📊 环境信息

- **分支/Commit**: `[git rev-parse HEAD]`
- **操作系统**: `[uname -a]`
- **编译器版本**: `[如：gcc 13.2]`
