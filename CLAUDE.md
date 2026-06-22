# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MultiAgent (aka AgentForge) — project-agnostic multi-agent collaborative development framework. Orchestrates Claude Code instances as specialized agents (PM, Dev, Test) coordinated by a Workflow Engine + Conductor.

**Current version**: v0.5.0 (Phase 4 complete — Conductor + 全链路自动化 verified in live pipeline)

## Human's Role

nanajiang is the **architectural monitor** (上层监视者). Responsibilities:
- Fix Engine/framework bugs discovered during pipeline runs
- Design architecture improvements
- Do NOT manually implement project code — let the PM→Dev→Test pipeline handle it
- Run `multiagent conductor start` to let the framework auto-process tasks

## Directory Structure

```
src/multiagent/            # Framework source (pip install -e .)
  adapters/                # Agent runtime adapters (ClaudeCode, OpenCode)
  db.py                    # StateDB (SQLite WAL) — 6 tables: tasks, step_results, workflow_state, heartbeat, agent_metrics, escalations
  engine.py                # AgentSpawner + monitor + metrics capture + prompt builder
  engine_cli.py            # multiagent run <workflow.yaml>
  orchestrator.py          # WorkflowOrchestrator — PM→Dev→Test with rejection loop + parallel fan-out
  conductor.py             # Conductor — polling loop: auto-detect pending tasks → trigger workflow
  conductor_cli.py         # multiagent conductor start|status|stop|alerts|retry|reject
  pm_cli.py                # multiagent pm submit|list|status|init (CLI dispatcher)
  metrics_cli.py           # multiagent metrics — token/cost analysis

architectures/             # Pluggable agent architectures
  dev-test-loop/           # PM + Dev + Test architecture
    config/roles.yaml      # Agent roles with permissions + model config
    skills/pm/SKILL.md     # PM agent soft constraints (Git workflow, task branching)
    skills/dev/SKILL.md    # Dev agent soft constraints (branch-per-task, test-then-merge)
    skills/test/SKILL.md   # Test agent soft constraints (run tests, produce verdict)
    workflow/pm-dev-test.yaml  # 3-step workflow definition

examples/devlog/           # DevLog — CLI developer journal (独立 git repo)
  src/devlog/
    db.py                  # SQLite WAL + FTS5
    cli.py                 # Click CLI: add/list/show/edit/delete/tags/search/export/serve/git-init
    web_server.py          # Flask REST API (8 endpoints) + Web UI (5 pages with editor)
    models.py, render.py, search.py, export.py
  tests/                   # 115+ tests, 92%+ coverage

gates/                     # Framework regression tests (9 modules)
  regression.py            # Master test runner
  test_conductor.py        # 19 conductor tests
  test_parallel.py, test_heartbeat.py, test_engine_cli.py, etc.

.claude/claudeclaw/        # ClaudeClaw Discord bot config
  settings.json            # Discord token, user IDs, guild IDs
```

## Quick Start

```bash
source .venv/bin/activate
pip install -e .
python gates/regression.py           # Run all gates (9 modules)
python -m multiagent.metrics         # Token/cost analysis
```

## Full Automation Pipeline (Phase 4)

```
Human: multiagent pm submit <requirements.md>
         ↓
Conductor (auto-poll every 3s): detects pending task in state.db
         ↓
PM Analyze → Dev Implement → Test Verify (全自动)
         ↓ (rejection loop: Test打回 → Dev重做, max 3次)
Escalated → multiagent conductor alerts → Human决策
```

Commands:
```bash
multiagent conductor start              # Start monitoring loop
multiagent conductor status             # View queue: pending/running/escalated
multiagent conductor alerts             # View escalations needing human attention
multiagent conductor retry <task_id>    # Retry escalated task
multiagent pm submit <requirements.md>  # Submit new requirement
multiagent pm status <task_id>          # Check task progress with token usage
multiagent metrics                      # View all token/cost stats
```

## Key Architecture Decisions

- **Adapter pattern**: `AgentAdapter` ABC → `ClaudeCodeAdapter`, `OpenCodeAdapter`. `project_root` defaults to `cwd`.
- **Dual-layer constraints**: `--disallowedTools` (hard, blocks Write/Edit on forbidden paths) + SKILL.md (soft, model compliance)
- **Agent spawn**: `claude -p <prompt> --output-format json --permission-mode acceptEdits --bare --add-dir <root>`
- **Prompt fix (v0.5.0)**: `_build_prompt` now includes exact required JSON field names + example format. Critical for pipeline success.
- **Automatic metrics**: Every agent spawn records to `agent_metrics` table via `--output-format json`
- **Thread-safe DB**: SQLite WAL + `check_same_thread=False` + `busy_timeout=5000` + `threading.Lock` on writes
- **Git workflow**: Branch-per-feature (`phase<N>-<desc>`), merge to main only after gates pass, tag after merge

## Git Workflow (CRITICAL)

- Every change → new feature branch → test → merge to main
- NEVER commit directly to main
- `gates/results/`, `runs/`, `*.db` are gitignored
- Architecture configs in `architectures/` are version-controlled
- DevLog has its OWN independent git repo at `examples/devlog/.git`

## Version History

| Version | Content |
|---------|---------|
| v0.1.0 | Phase 1+2: Dev+Test Agents, PM Agent, Flask TODO |
| v0.2.0 | Phase 3: Engine生产化, DevLog (115 tests, 92% coverage) |
| v0.3.0 | Phase 4: Conductor + 全链路自动化 |
| v0.5.0 | Phase 4 生产验证: prompt修复, pipeline实战 (DevLog Web Server/Editor) |
| v0.6.0 | Phase 5 target: Conductor生产化 + 多项目 + Dashboard + Discord通知 |

## Phase 5 Plan (Next)

1. Conductor Daemon 生产化 (PID file, start/stop/restart, log rotation)
2. 多项目并发监控 (one conductor → multiple state.db)
3. Web Dashboard (real-time pipeline status, token charts)
4. Discord 实时通知 (escalation → Discord message via ClaudeClaw)
5. PM 自动发现 (poll Git issues → auto-submit tasks)
6. Prompt 模板化 (schema-driven prompts with few-shot examples)

## Discord Integration

ClaudeClaw is configured at `.claude/claudeclaw/settings.json`. Start with `/claudeclaw:start` in Claude Code. Bot will inherit this CLAUDE.md as context — all project knowledge is preserved across Discord messages.
