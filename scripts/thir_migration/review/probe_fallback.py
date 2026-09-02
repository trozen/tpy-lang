"""Probe one TPy program through both codegen paths and report THIR fallback.

usage: uv run python probe_fallback.py FILE.py [--default-int Int32|Int64|BigInt]

Prints, per run: AST outcome (EMITS / REFUSES <error>), THIR outcome
(EMITS / RAISES <type: msg>), the fallback dict {component:reason -> count}
(empty == every body routed), and whether both outputs are byte-identical.
A program is a post-cutover BREAK candidate when AST EMITS and the fallback
dict is non-empty (THIR handed at least one body back to the AST).
"""
import sys, argparse, traceback
sys.path.insert(0, ".")
from tpyc.thir.testutil import _compile, _entry
from tpyc.codegen_cpp.context import CodeGenOptions

ap = argparse.ArgumentParser(); ap.add_argument("file"); ap.add_argument("--default-int", default="Int32")
a = ap.parse_args()
src = open(a.file).read()

def run(thir: bool):
    try:
        compiler, modules = _compile(src, default_int=a.default_int)
    except Exception as e:
        return ("FRONTEND_REFUSES", f"{type(e).__name__}: {e}", {}, None)
    entry = _entry(modules)
    try:
        out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True, comment_line_numbers=False, thir_codegen=thir))
    except Exception as e:
        return ("REFUSES" if not thir else "RAISES", f"{type(e).__name__}: {e}", dict(getattr(compiler, "_thir_fallback", {})), None)
    return ("EMITS", "", dict(getattr(compiler, "_thir_fallback", {})), out)

ast_status, ast_msg, _, ast_out = run(False)
thir_status, thir_msg, fb, thir_out = run(True)
print(f"AST : {ast_status} {ast_msg}")
print(f"THIR: {thir_status} {thir_msg}")
print(f"fallback: {fb}")
if ast_out is not None and thir_out is not None:
    print("byte_identical:", ast_out == thir_out)
verdict = "ROUTES" if ast_status == "EMITS" and thir_status == "EMITS" and not fb else \
          "BREAKS_AT_CUTOVER" if ast_status == "EMITS" and fb else \
          "BOTH_REFUSE" if ast_status != "EMITS" else "THIR_RAISES_PLAIN" if thir_status == "RAISES" else "OTHER"
print("verdict:", verdict)
