---
name: "[任务指派] {Issue 标题}"
about: PM 分析完毕后向 Dev 指派修复任务
labels: assigned
---

### 📋 任务分析

| 字段 | 值 |
|------|-----|
| **根因定位** | `[根本原因分析]` |
| **目标模块** | `[如：src/core/compute.cpp]` |
| **复杂度** | `[trivial / simple / medium / complex]` |
| **预计修改文件数** | `[N]` |

### 🎯 指派任务

@Dev_Agent 请修复以下问题：

1. **修改目标**: `[文件路径]`
2. **修改策略**: `[建议的修复方向]`
3. **参考文档**: `docs/architecture/[相关文档].md`

### ⚠️ 约束

- ❌ 禁止修改 `tests/` 目录
- ❌ 禁止修改 `docs/` 目录
- ✅ 请在隔离分支 `bugfix/{issue编号}` 上工作
- ✅ 修复完成后提交 PR，使用 `bugfix_pr.md` 模板

### 📎 关联

- 关联 Issue: #[Issue编号]
- 分析时间: `[ISO 8601]`
