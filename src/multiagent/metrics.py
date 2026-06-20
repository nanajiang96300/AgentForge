"""Agent Metrics 分析工具"""
import sys, csv
from datetime import datetime
from pathlib import Path
from .db import StateDB

def find_state_db(start=None):
    if start is None: start = Path.cwd()
    for p in [start] + list(start.parents):
        for pat in ["**/state.db", ".framework/workflow/state.db"]:
            m = list(p.glob(pat))
            if m: return m[0]
    return start / "state.db"

def summary(db):
    s = db.get_metrics_summary()
    print(f"\n📊 Summary: {s['total_calls']} calls, {s['total_input_tokens']:,} in tokens, ${s['total_cost_usd']:.4f}")

def by_agent(db):
    for a in ["dev","test","pm","conductor","user-agent"]:
        s = db.get_metrics_summary(a)
        if s['total_calls'] > 0:
            print(f"  {a:<12} {s['total_calls']:>4} calls  {s['total_input_tokens']:>10,} in  ${s['total_cost_usd']:>8.4f}")

def export_csv(db, path="metrics_export.csv"):
    rows = db.conn.execute("""SELECT task_id,step_id,agent,adapter,model,duration_ms,
        input_tokens,output_tokens,cost_usd,status,recorded_at FROM agent_metrics ORDER BY recorded_at""")
    with open(path,"w",newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_id","step_id","agent","adapter","model","duration_ms","input_tokens","output_tokens","cost_usd","status","recorded_at"])
        w.writerows(rows)
    print(f"Exported to {path}")

def main():
    db = StateDB(find_state_db()); db.connect()
    if "--export-csv" in sys.argv: export_csv(db); db.close(); return
    print(f"Metrics Report — {datetime.now().isoformat()}")
    summary(db)
    by_agent(db)
    db.close()

if __name__ == "__main__": main()
