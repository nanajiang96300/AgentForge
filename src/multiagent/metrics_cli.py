"""
Metrics CLI — Phase 3 Token/Cost 统计命令

用法:
    multiagent metrics                       汇总所有 Agent
    multiagent metrics --agent pm             按 Agent 筛选
    multiagent metrics --task-id <id>         按任务筛选
    multiagent metrics --json                 JSON 输出
"""

import sys
import json
import argparse
from pathlib import Path

from .db import StateDB


def find_state_db():
    for p in [Path.cwd()] + list(Path.cwd().parents):
        for pat in ["**/state.db", ".framework/workflow/state.db"]:
            m = list(p.glob(pat))
            if m:
                return m[0]
    return Path.cwd() / "state.db"


def parse_metrics_args(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    # Strip 'metrics' subcommand if present
    if argv and argv[0] == "metrics":
        argv = argv[1:]

    parser = argparse.ArgumentParser(
        prog="multiagent metrics",
        description="View token/cost metrics from agent executions",
    )
    parser.add_argument("--agent", default=None, help="Filter by agent role (pm, dev, test)")
    parser.add_argument("--task-id", default=None, help="Filter by task ID")
    parser.add_argument("--json", action="store_true", default=False, help="JSON output format")
    parser.add_argument("--details", action="store_true", default=False, help="Show per-call details")

    args, _ = parser.parse_known_args(argv)
    return {
        "agent": args.agent,
        "task_id": args.task_id,
        "json": args.json,
        "details": args.details,
    }


def cmd_metrics(db=None, agent=None, task_id=None, json_output=False, details=False):
    """查询并展示 metrics"""
    _close_db = False
    if db is None:
        db = StateDB(find_state_db())
        db.connect()
        _close_db = True

    try:
        # Build query
        conditions = []
        params = []
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Summary query
        summary = db.conn.execute(
            f"""SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens),
                SUM(cost_usd), AVG(duration_ms), SUM(cache_read_tokens)
                FROM agent_metrics {where}""",
            params,
        ).fetchone()

        total_calls = summary[0] or 0

        if total_calls == 0:
            if json_output:
                print(json.dumps({"error": "No metrics found", "total_calls": 0}))
            else:
                print("No metrics recorded yet.")
                print("Run 'multiagent run <workflow.yaml>' to execute agents through Engine.")
            return 0

        result = {
            "total_calls": total_calls,
            "total_input_tokens": summary[1] or 0,
            "total_output_tokens": summary[2] or 0,
            "total_cost_usd": round(summary[3] or 0.0, 6),
            "avg_duration_ms": int(summary[4] or 0),
            "total_cache_read_tokens": summary[5] or 0,
        }

        if json_output:
            # Per-agent breakdown
            per_agent = db.conn.execute(
                f"""SELECT agent, COUNT(*), SUM(input_tokens), SUM(output_tokens),
                    SUM(cost_usd), AVG(duration_ms)
                    FROM agent_metrics {where}
                    GROUP BY agent ORDER BY SUM(cost_usd) DESC""",
                params,
            ).fetchall()
            result["per_agent"] = [
                {
                    "agent": r[0], "calls": r[1],
                    "input_tokens": r[2] or 0, "output_tokens": r[3] or 0,
                    "cost_usd": round(r[4] or 0.0, 6), "avg_duration_ms": int(r[5] or 0),
                }
                for r in per_agent
            ]

            if details:
                per_call = db.conn.execute(
                    f"""SELECT task_id, step_id, agent, model, input_tokens, output_tokens,
                        cost_usd, duration_ms, status, recorded_at
                        FROM agent_metrics {where}
                        ORDER BY recorded_at DESC LIMIT 50""",
                    params,
                ).fetchall()
                result["calls"] = [
                    {
                        "task_id": r[0], "step_id": r[1], "agent": r[2], "model": r[3],
                        "input_tokens": r[4], "output_tokens": r[5],
                        "cost_usd": round(r[6] or 0.0, 6), "duration_ms": r[7],
                        "status": r[8], "recorded_at": r[9],
                    }
                    for r in per_call
                ]

            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0

        # Table output
        print(f"\n{'='*65}")
        print(f"  Token / Cost Report")
        print(f"{'='*65}")
        if agent:
            print(f"  Agent: {agent}")
        if task_id:
            print(f"  Task:  {task_id}")
        print(f"  Total Calls:    {total_calls}")
        print(f"  Input Tokens:   {result['total_input_tokens']:,}")
        print(f"  Output Tokens:  {result['total_output_tokens']:,}")
        print(f"  Cache Read:     {result['total_cache_read_tokens']:,}")
        print(f"  Cost (USD):     ${result['total_cost_usd']:.4f}")
        print(f"  Avg Duration:   {result['avg_duration_ms']:,}ms")
        print(f"{'='*65}")

        # Per-agent breakdown
        per_agent = db.conn.execute(
            f"""SELECT agent, COUNT(*), SUM(input_tokens), SUM(output_tokens),
                SUM(cost_usd), AVG(duration_ms)
                FROM agent_metrics {where}
                GROUP BY agent ORDER BY SUM(cost_usd) DESC""",
            params,
        ).fetchall()

        if per_agent and len(per_agent) > 1:
            print(f"\n  Per-Agent Breakdown:")
            print(f"  {'Agent':<10} {'Calls':<8} {'Input':<12} {'Output':<12} {'Cost':<10} {'Avg Dur'}")
            print(f"  {'-'*60}")
            for r in per_agent:
                print(f"  {r[0]:<10} {r[1]:<8} {r[2] or 0:>10,}  {r[3] or 0:>10,}  ${r[4] or 0:>7.4f}  {int(r[5] or 0):>6,}ms")

        if details:
            per_call = db.conn.execute(
                f"""SELECT task_id, step_id, agent, input_tokens, output_tokens,
                    cost_usd, duration_ms, status
                    FROM agent_metrics {where}
                    ORDER BY recorded_at DESC LIMIT 20""",
                params,
            ).fetchall()
            if per_call:
                print(f"\n  Recent Calls:")
                print(f"  {'Task':<14} {'Step':<14} {'Agent':<8} {'In':<10} {'Out':<10} {'Cost':<10} {'Dur'}")
                print(f"  {'-'*75}")
                for r in per_call:
                    print(f"  {r[0]:<14} {r[1]:<14} {r[2]:<8} {r[3] or 0:>8,}  {r[4] or 0:>8,}  ${r[5] or 0:>7.4f}  {r[6] or 0:>5,}ms")

        print()
        return 0

    finally:
        if _close_db:
            db.close()


def main():
    args = parse_metrics_args()
    return cmd_metrics(
        agent=args["agent"],
        task_id=args["task_id"],
        json_output=args["json"],
        details=args["details"],
    )


if __name__ == "__main__":
    main()
