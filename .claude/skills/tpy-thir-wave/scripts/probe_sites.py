"""Sub-shape histogram for ONE blocker tag: for every case whose SOLE blocker
is that tag, record WHERE in tpyc/thir/lower/ the rejection was raised.

This is the measurement that makes the ELEPHANTS legible. `expr.call` and
`expr.method_call` carry no note_detail, so the residual dump shows each as one
opaque blob -- which is exactly why they read as "fragmenting" and get skipped
wave after wave. They are not fragmenting: on the 2026-07-27 tree `expr.call`'s
67 sole-blocker cases concentrate into FOUR raise sites covering 50 of them
(arg rows 16, generic arg gate 11, instantiation shape 14, result gate 9).

NEVER reject a family by sampling a handful of its cases -- the sites are
interleaved, so a 4-case sample reads as 4 unrelated shapes. Run this instead;
it costs ~4 minutes.

Usage (from the repo root, after probe_corpus.py):
    uv run python .claude/skills/tpy-thir-wave/scripts/probe_sites.py \
        "body:expr.call"
"""
import collections
import dataclasses
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import conftest as C  # noqa: E402
from _case_entry import entry_src  # noqa: E402
import tpyc.thir.fallback as F  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp import CodeGenOptions  # noqa: E402

TARGET = sys.argv[1]
OUT_DIR = Path("/tmp/agents/thir-wave")
blockers = json.load(open(OUT_DIR / "blockers.json"))
names = sorted(n.replace("cases/", "", 1)
               for n, r in blockers.items()
               if r.get("reasons") == [TARGET])
reason = TARGET.split(":", 1)[1]

sites = collections.Counter()
per_case = {}
_orig = F.ThirUnsupported.__init__


def patched(self, r, *a, **kw):
    if r == reason:
        st = traceback.extract_stack()[:-1]
        frame = next((f for f in reversed(st)
                      if "/thir/lower/" in f.filename), None)
        if frame is not None:
            key = f"{Path(frame.filename).name}:{frame.lineno}"
            patched.last = key
    _orig(self, r, *a, **kw)


F.ThirUnsupported.__init__ = patched
opts = dataclasses.replace(
    CodeGenOptions(emit_source_comments=True, comment_line_numbers=False),
    thir_codegen=True)

for case in names:
    cd = ROOT / "tests/cases" / case
    src = entry_src(cd)
    if src is None:
        continue
    patched.last = None
    seen = set()
    try:
        fr, extra = C._frontend_registry_for(src)
        comp = Compiler(src, default_int=C.get_case_default_int(cd),
                        lib_dirs=list(extra) + list(C.DEFAULT_LIB_DIRS),
                        frontend_registry=fr)
        mods = comp.compile()
        entry = next(m for m in mods if m.is_entry_point)
        sd = src.parent.resolve()
        for mod in mods:
            try:
                mod.path.resolve().relative_to(sd)
            except ValueError:
                continue
            comp.generate_code(mod, cd / "__tpyc_spy__",
                               entry_module_name=entry.name, options=opts)
    except Exception:
        pass
    finally:
        import shutil
        shutil.rmtree(cd / "__tpyc_spy__", ignore_errors=True)
    if patched.last:
        sites[patched.last] += 1
        per_case[case] = patched.last

print(f"{TARGET}: {len(names)} sole-blocker cases, "
      f"{len(per_case)} attributed to a raise site\n")
cum = 0
for site, n in sites.most_common():
    cum += n
    ex = [c for c, s in per_case.items() if s == site][:3]
    print(f"{n:4d}  cum {cum:4d}  {site:<28} e.g. {', '.join(ex)}")
json.dump(per_case, open(OUT_DIR / f"sites_{reason}.json", "w"),
          indent=0)
