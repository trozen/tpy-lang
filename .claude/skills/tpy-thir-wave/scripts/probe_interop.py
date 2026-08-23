"""Reject-location spy for tests/interop ext-exec cases -- the interop twin of
probe_loc.py.

WHY THIS EXISTS: every script in .claude/skills/tpy-thir-wave/scripts/ resolves a
case as ROOT/"tests"/"cases"/<case>/"src"/"main.py" (probe_loc, probe_one,
probe_corpus, probe_sites, sites_multi, and the shared _case_entry.entry_src).
An interop case is ROOT/"tests"/"interop"/<case>/"src"/<case>.py and is compiled
with no_main=is_ext_module_build(). The existing scripts therefore cannot address
one at all -- they report nothing rather than erroring, which reads as "nothing to
see".

Mirrors conftest.run_interop_thir_overlay exactly (same Compiler construction, same
local-module filter, same emit_source_comments=True / comment_line_numbers=False,
same no_main, both emits, byte-diff of .hpp + .cpp + the _ext.cpp glue) and adds
the ThirUnsupported spy.

Two fixes over probe_file.py, both load-bearing here:
  * no_main -- probe_file.py omits it, so its emit is not the one the harness
    compares for an `# tpy: ext_module`.
  * note_detail is patched in EVERY tpyc.thir.lower.* module. probe_file.py patches
    only expressions + statements, so a detail raised from checks.py (where the
    method/container/dict-key gates live) comes back as `[None]`. The decisive
    `method.set.add` detail is invisible without this.

Usage (from the repo root):
    uv run python probe_interop.py class_properties [more_cases ...]
    uv run python probe_interop.py --all
"""
from __future__ import annotations

import dataclasses
import difflib
import importlib
import inspect
import pkgutil
import sys
from pathlib import Path

def _find_root() -> Path:
    """Repo root. parents[4] is correct once this lands in
    .claude/skills/tpy-thir-wave/scripts/; until then, walk up from cwd."""
    here = Path(__file__).resolve()
    if len(here.parents) > 4 and (here.parents[4] / "tpyc").is_dir():
        return here.parents[4]
    for c in [Path.cwd(), *Path.cwd().parents]:
        if (c / "tpyc").is_dir() and (c / "tests").is_dir():
            return c
    raise SystemExit("run me from the repo root")


ROOT = _find_root()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import conftest as C  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp import CodeGenOptions  # noqa: E402
from tpyc.thir import fallback as FB  # noqa: E402
import tpyc.thir.lower as LOWER  # noqa: E402

SCRATCH = Path("/tmp/agents/thir-wave/__interop_probe__")
INTEROP = ROOT / "tests" / "interop"

_orig_init = FB.ThirUnsupported.__init__
_orig_detail = FB.note_detail


def entry_src(case_dir: Path) -> 'Path | None':
    """The ext_module source the interop harness compiles: src/<case>.py (the
    filename IS the import name, per tests/interop/README.md)."""
    cand = case_dir / "src" / f"{case_dir.name}.py"
    if cand.exists():
        return cand
    src = case_dir / "src"
    if not src.is_dir():
        return None
    mods = [p for p in src.glob("*.py") if p.name not in ("driver.py",
                                                          "ext_checks.py")]
    return mods[0] if len(mods) == 1 else None


def probe(case: str) -> None:
    case_dir = INTEROP / case
    mod_py = entry_src(case_dir)
    print(f"=== interop/{case} ===")
    if mod_py is None:
        print("  NO ENTRY SOURCE")
        return
    marked = (case_dir / "no_thir.txt").exists()

    compiler = Compiler(mod_py, lib_dirs=C.DEFAULT_LIB_DIRS)
    compiled = compiler.compile()
    entry = next(m for m in compiled if m.is_entry_point)
    src_dir = mod_py.parent.resolve()
    local = []
    for mod in compiled:
        try:
            mod.path.resolve().relative_to(src_dir)
        except ValueError:
            continue
        local.append(mod)

    base = dataclasses.replace(
        CodeGenOptions(emit_source_comments=True, comment_line_numbers=False),
        no_main=compiler.is_ext_module_build())

    captured: list = []
    last_detail: list = [None]

    def detail_spy(reason):
        last_detail[0] = reason
        return _orig_detail(reason)

    def spy(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        loc = site = None
        for fr in inspect.stack()[1:16]:
            if site is None and "/tpyc/" in fr.filename:
                site = fr.filename.split("/tpyc/")[-1] + ":" + str(fr.lineno)
            for var in ("stmt", "e", "expr"):
                nl = getattr(fr.frame.f_locals.get(var), "loc", None)
                if nl is not None and getattr(nl, "line", None):
                    loc = nl.line
                    break
            if loc:
                break
        captured.append((args[0] if args else "?", loc, site, last_detail[0]))
        last_detail[0] = None

    patched = [FB]
    for _, name, _ in pkgutil.iter_modules(LOWER.__path__):
        m = importlib.import_module(f"tpyc.thir.lower.{name}")
        if hasattr(m, "note_detail"):
            patched.append(m)
    FB.ThirUnsupported.__init__ = spy
    for m in patched:
        m.note_detail = detail_spy

    out = SCRATCH / case
    emitted: dict = {}
    try:
        for label, thir in (("ast", False), ("thir", True)):
            opts = dataclasses.replace(base, thir_codegen=thir)
            for mod in local:
                hpp, cpp = compiler.generate_code(
                    mod, out / label, entry_module_name=entry.name,
                    options=opts)
                f = emitted.setdefault(mod.name, {})
                f[f"{label}.hpp"], f[f"{label}.cpp"] = hpp, cpp
                if cpp is not None:
                    glue = Path(cpp).with_name(f"{Path(cpp).stem}_ext.cpp")
                    f[f"{label}.ext"] = glue if glue.exists() else None
    finally:
        FB.ThirUnsupported.__init__ = _orig_init
        for m in patched:
            m.note_detail = _orig_detail

    from tpyc.thir import fallback as thir_fallback
    total = thir_fallback.ratchet_total(compiler._thir_fallback)
    print(f"  marker={'yes' if marked else 'no'}  ratchet_total={total}")
    print(f"  fallback: {dict(compiler._thir_fallback)}")
    for name, files in sorted(emitted.items()):
        for kind in ("hpp", "cpp", "ext"):
            a, t = files.get(f"ast.{kind}"), files.get(f"thir.{kind}")
            if a is None and t is None:
                continue
            if a is None or t is None:
                print(f"  DIVERGENCE {name}.{kind}: emitted on one path only")
                continue
            at, tt = Path(a).read_text(), Path(t).read_text()
            if at == tt:
                continue
            print(f"  DIVERGENCE {name}.{kind}:")
            print("".join(list(difflib.unified_diff(
                at.splitlines(True), tt.splitlines(True), "ast", "thir"))[:40]))
    for r, l, s, d in captured:
        print(f"    line {l}: {r} [{d}]  @ {s}")
    if marked and total == 0:
        print("  FLIP CANDIDATE: routes clean -- delete no_thir.txt")


cases = sys.argv[1:]
if not cases or cases == ["--all"]:
    cases = sorted(p.name for p in INTEROP.iterdir()
                   if (p / "src" / "driver.py").exists())
for c in cases:
    probe(c)
