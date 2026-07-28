"""probe_site for an arbitrary scratch main.py."""
from __future__ import annotations
import dataclasses, inspect, sys
from collections import Counter
from pathlib import Path
ROOT = Path("/home/tommy/dev/turbo-python/tpy-m1")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
import conftest as C
from tpyc.compiler import Compiler
from tpyc.codegen_cpp import CodeGenOptions
from tpyc.thir import fallback as FB
_orig_init = FB.ThirUnsupported.__init__
_orig_detail = FB.note_detail
main_src = Path(sys.argv[1])
frontend_registry, extra_lib_dirs = C._frontend_registry_for(main_src)
lib_dirs = list(extra_lib_dirs) + list(C.DEFAULT_LIB_DIRS)
thir_opts = dataclasses.replace(
    CodeGenOptions(emit_source_comments=True, comment_line_numbers=False),
    thir_codegen=True)
compiler = Compiler(main_src, lib_dirs=lib_dirs, frontend_registry=frontend_registry)
mods = compiler.compile()
entry = next(m for m in mods if m.is_entry_point)
captured = []
last_detail = [None]
def detail_spy(reason):
    last_detail[0] = reason
    return _orig_detail(reason)
def spy(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    loc = None; site = None
    for fr in inspect.stack()[1:16]:
        if site is None and "/tpyc/" in fr.filename:
            site = fr.filename.split("/tpyc/")[-1] + ":" + str(fr.lineno)
        for var in ("stmt", "e", "expr"):
            node = fr.frame.f_locals.get(var)
            nl = getattr(node, "loc", None)
            if nl is not None and getattr(nl, "line", None):
                loc = nl.line; break
        if loc: break
    captured.append((args[0] if args else "?", loc, site, last_detail[0]))
    last_detail[0] = None
FB.ThirUnsupported.__init__ = spy
FB.note_detail = detail_spy
import tpyc.thir.lower.expressions as EX, tpyc.thir.lower.statements as ST
for m in (EX, ST):
    if hasattr(m, "note_detail"): m.note_detail = detail_spy
try:
    compiler.generate_code(entry, Path("/tmp/agents/instgate/__pf__"),
                           entry_module_name=entry.name, options=thir_opts)
finally:
    FB.ThirUnsupported.__init__ = _orig_init
    FB.note_detail = _orig_detail
    for m in (EX, ST):
        if hasattr(m, "note_detail"): m.note_detail = _orig_detail
print("fallback:", dict(compiler._thir_fallback))
for (r, l, s, d), n in Counter(captured).most_common(40):
    print(f"  {n}x line {l}: {r} [{d}]  @ {s}")
