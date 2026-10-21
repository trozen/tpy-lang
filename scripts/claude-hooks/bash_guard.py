#!/usr/bin/env python3
"""PreToolUse hook for Bash: refuse the launch and wait patterns that have
left sessions idle for hours.

- hand-written waiters (`pgrep` / `until` / `while` with `sleep`): they have
  no upper bound. Wait for the harness notification, or use the Monitor tool.
- `codex exec` or `agy -p` run directly: it can linger after its turn or
  stall, and an untracked launch never notifies. Both go through
  scripts/crosscheck_run.py.
- scripts/crosscheck_run.py launched without `run_in_background: true`: the
  background launch is what makes the harness notify when it exits.

A guard against habit, not a sandbox: quoted text is ignored, so a commit
message or a grep pattern may name these commands. Exit 2 blocks the call
and hands stderr back to the agent.
"""
import json
import re
import sys

QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
CODEX_EXEC = re.compile(r"(^|[\s;&|(`/])codex\s+(exec|e)(\s|$)")
AGY_PRINT = re.compile(r"(^|[\s;&|(`/])agy\b[^;&|]*\s(-p|--print|--prompt)")
WRAPPER = re.compile(r"crosscheck_run\.py\s+(codex|gemini)\b")
WAITER = re.compile(r"\b(pgrep|until|while)\b")
SLEEP = re.compile(r"(^|[\s;&|(])sleep\s")


def verdict(command: str, background: bool) -> str | None:
    bare = QUOTED.sub("''", command)
    if WAITER.search(bare) and SLEEP.search(bare):
        return ("No sleep-based waiter loops. A background command notifies when it "
                "exits; to wait on a condition, use the Monitor tool.")
    if (CODEX_EXEC.search(bare) or AGY_PRINT.search(bare)) and "--help" not in bare:
        return ("Do not run `codex exec` or `agy -p` directly. Use "
                "`python3 scripts/crosscheck_run.py codex|gemini start|resume <workdir> <prompt-file>` "
                "with `run_in_background: true` (CLAUDE.md \"Orchestration\").")
    if WRAPPER.search(bare) and not background:
        return ("Run scripts/crosscheck_run.py with Bash `run_in_background: true`; "
                "the harness notifies when it exits, and it always exits.")
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    tool_input = data.get("tool_input") or {}
    reason = verdict(tool_input.get("command") or "", bool(tool_input.get("run_in_background")))
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
