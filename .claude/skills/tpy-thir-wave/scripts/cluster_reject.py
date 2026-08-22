"""Cluster reject sites by SOURCE LINE across many cases.

probe_loc.py names the reason + line per case; this wraps it so a whole
candidate family can be read as a list of actual source statements -- the
"open the real blocking body" step the grind loop requires, done in bulk.

Usage (from the repo root):
    uv run python .claude/skills/tpy-thir-wave/scripts/cluster_reject.py \
        <reason-substring> [max_cases]

Reads /tmp/agents/thir-wave/blockers.json for the case list.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _case_entry import entry_src  # noqa: E402

BLOCKERS = Path("/tmp/agents/thir-wave/blockers.json")
LINE_RE = re.compile(r"^\s+(\d+)x line (\d+): (.*)$")
CASE_RE = re.compile(r"^=== (.*) ===$")


def main() -> None:
    needle = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    blockers = json.loads(BLOCKERS.read_text())
    cases = [k.removeprefix("cases/") for k, v in blockers.items()
             if any(needle in r for r in v.get("reasons", []))][:limit]
    proc = subprocess.run(
        ["uv", "run", "python",
         str(ROOT / ".claude/skills/tpy-thir-wave/scripts/probe_loc.py"),
         *cases],
        capture_output=True, text=True, cwd=ROOT)
    cur = None
    for line in proc.stdout.splitlines():
        m = CASE_RE.match(line)
        if m:
            cur = m.group(1)
            continue
        m = LINE_RE.match(line)
        if not m or needle not in m.group(3):
            continue
        src = entry_src(ROOT / "tests" / "cases" / cur)
        text = "<no entry source>"
        if src is not None:
            lines = src.read_text().splitlines()
            idx = int(m.group(2)) - 1
            if 0 <= idx < len(lines):
                text = lines[idx].strip()
        print(f"{cur}:{m.group(2)}  [{m.group(1)}x]  {text}")


if __name__ == "__main__":
    main()
