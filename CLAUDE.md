# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MultiAgent (AgentForge) — multi-agent collaborative development framework. Orchestrates Claude Code instances as specialized agents (PM, Dev, Test) coordinated by a Workflow Engine + Conductor. Current: v0.7.0 (Phase 7 complete).

## Human's Role

nanajiang is the **architectural monitor**. Responsibilities:
- Fix Engine/framework bugs discovered during pipeline runs
- Design architecture improvements
- Do NOT manually implement project code — let the PM→Dev→Test pipeline handle it
- Run `multiagent conductor start` to auto-process tasks

## Key Commands

```bash
source .venv/bin/activate && pip install -e .
python gates/regression.py                    # Run all gates (9 modules)
multiagent conductor start                    # Start auto-poll loop
multiagent conductor status                   # Queue: pending/running/escalated
multiagent conductor alerts                   # Escalations needing human attention
multiagent pm submit <requirements.md>        # Submit new requirement
multiagent pm status <task_id>                # Task progress + token usage
multiagent metrics                            # Token/cost analysis
multiagent dashboard                          # Web UI at http://127.0.0.1:5001
multiagent agent list                         # Registered agent types
```

## Core Architecture

```
src/multiagent/          # Framework (pip install -e .)
  engine.py              # AgentSpawner: prompt build → spawn → monitor → metrics
  orchestrator.py        # WorkflowOrchestrator: PM→Dev→Test + rejection loop + parallel
  conductor.py           # Conductor daemon: poll pending tasks → trigger workflows
  db.py                  # StateDB (SQLite WAL): tasks, step_results, agent_metrics, escalations
  adapters/              # AgentAdapter ABC → ClaudeCodeAdapter, OpenCodeAdapter
  runtime/registry.py    # AgentRegistry: custom agent types from YAML
  core/graph_engine.py   # WorkflowGraph: DAG with conditional edges → YAML export
  config/loader.py       # Unified path discovery (find_state_db, find_workflow_yaml)
  services/              # PidManager, CheckpointManager
  notify.py              # Discord webhook/channel notifications (zero-token)
  dashboard.py           # Flask web app (dark theme, workflow designer)
architectures/dev-test-loop/  # PM+Dev+Test config + skills + workflows
gates/                   # 76 regression tests (9 modules)
```

## Critical Constraints

- **Git workflow**: Branch-per-feature (`feature/<desc>`), NEVER commit directly to main
- **Adapter pattern**: `AgentAdapter` ABC with dual-layer constraints (`--disallowedTools` hard + SKILL.md soft)
- **Agent spawn**: `claude -p <prompt> --output-format json --permission-mode acceptEdits --bare`
- **Retry caps (P0)**: `MAX_TOTAL_STEP_EXECUTIONS=50` hard global cap; per-step retry `max=3` in workflow YAML. Both enforced in `orchestrator.execute_step()` iterative loop — no recursive retries
- **DB thread safety**: SQLite WAL + `busy_timeout=5000` + `threading.Lock()` on writes
- **StateDB tables**: tasks, step_results, workflow_state, heartbeat, agent_metrics, escalations
- **Config files**: `.claude/claudeclaw/`, `gates/results/`, `runs/`, `*.db`, `.conductor.pid` are gitignored
- **DevLog**: Independent git repo at `examples/devlog/.git`

## Discord Integration

ClaudeClaw bot at `.claude/claudeclaw/settings.json`. Model: `claude-sonnet-4-6` (implementation), `claude-opus-4-8` (planning). Session auto-rotate enabled (max 30 messages). Timeout: 10min default. Bot inherits this CLAUDE.md as context.

## Version History → See docs/

| Version | Content |
|---------|---------|
| v0.5.0 | Phase 4: Conductor + full automation |
| v0.6.0 | Phase 5: Daemon, notifications, dashboard, parallel exec |
| v0.7.0 | Phase 7: Clean code, agent registry, graph engine, checkpoints |
