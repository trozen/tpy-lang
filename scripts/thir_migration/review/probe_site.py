"""Like probe_fallback.py, but records the source line of every
`raise ThirUnsupported` executed during the THIR run (via a wrapped
__init__ that captures the constructing frame). Prints unique
(file:line, reason) pairs so a bare landmark tag can be pinned to a site."""
import sys, argparse, traceback, collections
sys.path.insert(0, ".")
from tpyc.thir.testutil import _compile, _entry
from tpyc.codegen_cpp.context import CodeGenOptions
from tpyc.thir import fallback as fb

ap = argparse.ArgumentParser(); ap.add_argument("file"); ap.add_argument("--default-int", default="Int32")
ap.add_argument("--tb", action="store_true")
a = ap.parse_args()
src = open(a.file).read()

sites = collections.Counter()
_orig_init = fb.ThirUnsupported.__init__
def _init(self, reason, *, detail=False):
    _orig_init(self, reason, detail=detail)
    st = traceback.extract_stack(limit=3)
    fr = st[-2]
    sites[(fr.filename.split("tpyc/")[-1], fr.lineno, reason)] += 1
fb.ThirUnsupported.__init__ = _init

compiler, modules = _compile(src, default_int=a.default_int)
entry = _entry(modules)
try:
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=True, comment_line_numbers=False, thir_codegen=True))
    print("THIR: EMITS")
except Exception as e:
    print(f"THIR: RAISES {type(e).__name__}: {e}")
    if a.tb:
        traceback.print_exc()
print("fallback:", dict(getattr(compiler, "_thir_fallback", {})))
for (f, l, r), n in sorted(sites.items(), key=lambda kv: (kv[0][0], kv[0][1])):
    print(f"  site {f}:{l}  x{n}  {r!r}")
