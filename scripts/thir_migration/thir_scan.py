import sys, dataclasses
from pathlib import Path

REPO = Path("/home/tommy/dev/turbo-python/tpy-t1")
sys.path.insert(0, str(REPO))

import dataclasses as _dc
from tests.conftest import (TEST_CODEGEN_OPTIONS as _BASE_OPTS, DEFAULT_LIB_DIRS,
                            _frontend_registry_for, _thir_case_mode)
TEST_CODEGEN_OPTIONS = _dc.replace(_BASE_OPTS, thir_codegen=True)
from tpyc.cli import get_module_name
from tpyc.compiler import Compiler
from tpyc.compilation_context import activate_compiler
import tpyc.thir.lower as _lowmod

captured = []  # (component, name, reason)

_orig_fn = _lowmod.lower_function
_orig_ct = _lowmod.lower_constructor
_orig_sg = _lowmod.lower_simple_generator

def wrap_fn(func, analyzer, *a, **k):
    from tpyc.compilation_context import get_current_compiler
    r = _orig_fn(func, analyzer, *a, **k)
    if r is None:
        c = get_current_compiler()
        captured.append(("body", func.name, c._thir_reject_reason if c else None))
    return r

def wrap_ct(record, init, analyzer, *a, **k):
    from tpyc.compilation_context import get_current_compiler
    r = _orig_ct(record, init, analyzer, *a, **k)
    if r is None:
        c = get_current_compiler()
        captured.append(("ctor", getattr(record,'name','?')+".__init__", c._thir_reject_reason if c else None))
    return r

def wrap_sg(func, analyzer, *a, **k):
    from tpyc.compilation_context import get_current_compiler
    r = _orig_sg(func, analyzer, *a, **k)
    if r is None:
        c = get_current_compiler()
        captured.append(("sgen", func.name, c._thir_reject_reason if c else None))
    return r

_lowmod.lower_function = wrap_fn
_lowmod.lower_constructor = wrap_ct
_lowmod.lower_simple_generator = wrap_sg

def scan(case_dir: Path):
    global captured
    captured = []
    src = case_dir / "src" / "main.py"
    if not src.exists():
        # try group-level layout
        cands = list((case_dir / "src").glob("*.py")) if (case_dir/"src").exists() else []
        if not cands: return None
        src = cands[0]
    reg, extra = _frontend_registry_for(src)
    lib_dirs = list(extra) + list(DEFAULT_LIB_DIRS)
    import json
    di = "Int32"
    # walk options.json for default_int
    p = case_dir
    stops = REPO / "tests" / "cases"
    chain = []
    cur = case_dir
    while True:
        chain.append(cur)
        if cur == stops: break
        if cur.parent == cur: break
        cur = cur.parent
    for d in reversed(chain):
        oj = d / "options.json"
        if oj.exists():
            try:
                o = json.loads(oj.read_text())
                if "default_int" in o: di = o["default_int"]
            except Exception: pass
    try:
        compiler = Compiler(src, default_int=di, lib_dirs=lib_dirs, frontend_registry=reg)
        mods = compiler.compile()
    except Exception as e:
        return ("COMPILE_ERR", str(e)[:120])
    with activate_compiler(compiler):
        entry = next(m for m in mods if m.is_entry_point)
        src_dir = src.parent.resolve()
        import tempfile
        out = Path(tempfile.mkdtemp())
        local = []
        for m in mods:
            try:
                m.path.resolve().relative_to(src_dir); local.append(m)
            except ValueError: pass
        for m in local:
            try:
                compiler.generate_code(m, out, entry_module_name=entry.name,
                                       options=TEST_CODEGEN_OPTIONS)
            except Exception as e:
                return ("CODEGEN_ERR", str(e)[:120])
    return dict(compiler._thir_fallback), list(captured)

if __name__ == "__main__":
    for arg in sys.argv[1:]:
        cd = Path(arg)
        if not cd.is_absolute(): cd = REPO / "tests" / "cases" / arg
        res = scan(cd)
        print("===", cd.relative_to(REPO/'tests'/'cases') if str(cd).startswith(str(REPO/'tests'/'cases')) else cd)
        if res is None:
            print("  (no src)"); continue
        if isinstance(res, tuple) and res[0] in ("COMPILE_ERR","CODEGEN_ERR"):
            print("  ", res); continue
        fb, cap = res
        for k,v in sorted(fb.items(), key=lambda x:-x[1]):
            print(f"  {v:3d}  {k}")
        for comp,name,reason in cap:
            print(f"     - {comp} {name}: {reason}")
