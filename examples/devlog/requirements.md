# DevLog — CLI 开发者日志系统

## 项目定位

面向开发者的命令行日志/笔记工具。用 SQLite 存储，支持 Markdown 渲染、全文搜索、标签分类、Git 版本历史。

## 核心功能

### 1. 日志 CRUD
- `devlog add "标题"` — 创建日志（打开 $EDITOR 编辑内容）
- `devlog list [--tag <tag>] [--limit N]` — 列出日志标题
- `devlog show <id>` — 查看单条日志（Markdown 渲染到终端）
- `devlog edit <id>` — 编辑已有日志
- `devlog delete <id>` — 删除（需确认）

### 2. 标签系统
- 每条日志支持多标签（如 `#bug #python #fix`）
- `devlog tags` — 列出所有标签及使用次数
- `devlog list --tag python` — 按标签筛选

### 3. 全文搜索
- `devlog search "关键词"` — SQLite FTS5 全文搜索
- 搜索结果高亮匹配内容

### 4. 导出功能
- `devlog export --format html --output journal.html` — 导出为 HTML
- `devlog export --format md --output journal.md` — 导出为 Markdown 合集

### 5. Git 集成（可选/later）
- `devlog git-init` — 初始化 Git 仓库追踪日志数据
- 每次 add/edit/delete 自动 commit

## 技术约束

- **语言**: Python 3.10+
- **存储**: SQLite（WAL 模式），数据目录 `~/.devlog/`
- **CLI 框架**: `argparse` 或 `click`
- **Markdown 渲染**: `rich` 库（终端渲染）
- **导出 HTML**: 使用 `markdown` 库转 HTML + 内嵌 CSS
- **全文搜索**: SQLite FTS5 扩展
- **测试**: pytest，要求覆盖率 > 70%

## 数据模型

```
logs:
  id INTEGER PRIMARY KEY
  title TEXT NOT NULL
  content TEXT (Markdown)
  created_at TEXT (ISO8601)
  updated_at TEXT (ISO8601)

tags:
  id INTEGER PRIMARY KEY
  name TEXT UNIQUE

log_tags:
  log_id INTEGER REFERENCES logs(id)
  tag_id INTEGER REFERENCES tags(id)
```

## 验收标准

1. 所有 CRUD 操作通过 pytest 测试
2. FTS5 搜索返回正确结果
3. 导出 HTML 可在浏览器正常显示
4. 标签筛选和统计正确
5. CLI 帮助信息完整（`devlog --help`）
6. 错误处理友好（数据库不存在时自动初始化）
