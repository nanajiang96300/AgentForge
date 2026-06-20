"""
Claude Code 适配器 — --output-format json + 双层约束。

硬约束: --disallowedTools（框架层拦截工具调用，模型无法绕过）
软约束: --append-system-prompt-file（SKILL.md，模型自行遵守）
"""
import json, re
from .base import AgentAdapter

def _extract_json_block(text):
    m = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL):
        try: return json.loads(m.group())
        except: continue
    return None

class ClaudeCodeAdapter(AgentAdapter):
    def name(self): return "claude-code"

    def build_command(self, agent_config, task_prompt, step):
        cmd = ["claude", "-p", task_prompt, "--output-format", "json"]
        skill_rel = agent_config.get("skill", "")
        if skill_rel:
            sp = self.project_root / skill_rel
            if sp.exists(): cmd.extend(["--append-system-prompt-file", str(sp)])
        _, disallowed = self.get_tool_restriction_flags(agent_config.get("permissions", {}))
        if disallowed: cmd.extend(["--disallowedTools", ",".join(disallowed)])
        cmd.append("--bare"); cmd.extend(["--add-dir", str(self.project_root)])
        return cmd

    def parse_output(self, stdout, stderr):
        from ..engine import StepResult, StepStatus
        result = StepResult(step_id="", agent="", status=StepStatus.COMPLETED, output={})
        raw = None
        try: raw = json.loads(stdout.strip())
        except: pass
        if raw:
            if raw.get("is_error"): result.status = StepStatus.CRASHED; result.error = raw.get("result","")[:1000]
            text = raw.get("result",""); result.output["response"] = text
            jb = _extract_json_block(text)
            if jb: result.output.update(jb)
        else:
            result.output["response"] = stdout.strip()
            jb = _extract_json_block(stdout)
            if jb: result.output.update(jb)
        if stderr and ("error" in stderr.lower() or "traceback" in stderr.lower()):
            result.status = StepStatus.CRASHED; result.error = stderr[:1000]
        return result, raw

    def _paths_to_tool_patterns(self, deny_paths, write_paths):
        return [], [f"Write({p.rstrip('/')}/*)" for p in deny_paths] + [f"Edit({p.rstrip('/')}/*)" for p in deny_paths]
