# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MultiAgent — project-agnostic multi-agent collaborative development framework. Orchestrates Claude Code / OpenCode instances as specialized agents coordinated by a Workflow Engine.

## Directory Structure

```
src/multiagent/            # Framework source (pip install -e .)
  adapters/                # Agent runtime adapters (ClaudeCode, OpenCode)
  db.py                    # StateDB (SQLite WAL) + data models
  engine.py                # AgentSpawner + monitor + metrics capture
  metrics.py               # Token/cost analysis CLI

architectures/             # Pluggable agent architectures (config + skills)
  dev-test-loop/           # PM + Dev + Test architecture (Phase 1-2 example)
    config/                # roles.yaml, state-machine.yaml, discord-bots.yaml
    skills/                # Per-agent SKILL.md (soft constraints)
    templates/             # A2A Issue/PR templates
    protocols/             # A2A spec + YAML schema
    workflow/              # Workflow YAML definitions

examples/                  # Example projects using the framework
  cpp-calculator/          # C++ calculator (Phase 1 test project)

gates/                     # Framework quality gates (regression tests)
  regression.py            # Master test runner
  results/                 # Test results (gitignored)

runs/                      # Runtime logs (gitignored)
```

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python gates/regression.py           # Run all gates (no API)
python gates/regression.py --live    # Include live API tests
python -m multiagent.metrics         # Token/cost analysis
```

## Key Architecture Decisions

See `memory/` files for detailed records. Key points:
- **Adapter pattern**: Engine uses `AgentAdapter` ABC; `ClaudeCodeAdapter` implemented, `OpenCodeAdapter` skeleton
- **Dual-layer constraints**: `--disallowedTools` (hard, framework-level tool blocking) + SKILL.md (soft, model compliance)
- **Temporary agent model**: Dev/Test/PM spawn as `claude -p` (non-interactive one-shot), no session persistence
- **Automatic metrics**: Every agent spawn records token usage/cost/duration to `agent_metrics` table via `--output-format json`
- **Architecture separation**: Framework code (`src/multiagent/`) is pure Python; agent configurations (`architectures/`) are YAML + Markdown

## Development

```bash
source .venv/bin/activate
pip install -e .            # Editable install
python gates/regression.py  # Run gates before committing
```

## Git Workflow

- Each important change → new branch → test → merge to main
- `gates/results/`, `runs/`, `*.db` are gitignored
- Architecture configs in `architectures/` are version-controlled (they ARE the product)
