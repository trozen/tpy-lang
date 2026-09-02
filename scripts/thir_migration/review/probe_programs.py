"""Run every distinct program embedded in the THIR unit tests through both codegen paths.

Answers, per program: does it ROUTE today (survives the cutover unchanged), BREAK at cutover
(AST emits, THIR falls back), or is it refused by the front end / codegen (a fixture that only
makes sense with extra lib dirs, or a deliberate reject pin). Output: JSON keyed by sha1.
"""
import ast, glob, json, sys, hashlib, collections, os
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, ".")
S = sys.argv[1]; JOBS = int(sys.argv[2]) if len(sys.argv) > 2 else 4

def collect():
    progs = {}
    for f in sorted(glob.glob("tpyc/thir/test_*.py")):
        tree = ast.parse(open(f).read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"): continue
            for n in ast.walk(node):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and "\n" in n.value and ("def " in n.value or "class " in n.value):
                    h = hashlib.sha1(n.value.encode()).hexdigest()[:16]
                    progs.setdefault(h, {"src": n.value, "uses": []})["uses"].append(f"{f}::{node.name}")
    return progs

def run_one(item):
    h, src = item
    import io, contextlib
    from tpyc.thir.testutil import _compile, _entry
    from tpyc.codegen_cpp.context import CodeGenOptions
    def go(thir):
        try:
            compiler, modules = _compile(src)
        except Exception as e:
            return ("FRONTEND", type(e).__name__, {})
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                compiler.generate_code_to_strings(_entry(modules), options=CodeGenOptions(emit_source_comments=True, comment_line_numbers=False, thir_codegen=thir))
        except Exception as e:
            return ("RAISES", type(e).__name__, dict(getattr(compiler, "_thir_fallback", {})))
        return ("EMITS", "", dict(getattr(compiler, "_thir_fallback", {})))
    try:
        a = go(False); t = go(True)
    except BaseException as e:
        return h, {"verdict": "HARNESS_ERROR", "detail": repr(e)[:200]}
    if a[0] == "FRONTEND": v = "FRONTEND_REFUSES"
    elif a[0] == "RAISES" and t[0] == "RAISES": v = "BOTH_REFUSE"
    elif a[0] == "EMITS" and t[0] == "EMITS" and not t[2]: v = "ROUTES"
    elif a[0] == "EMITS" and t[2]: v = "BREAKS_AT_CUTOVER"
    elif a[0] == "EMITS" and t[0] == "RAISES": v = "THIR_RAISES_PLAIN"
    elif a[0] == "RAISES" and t[0] == "EMITS": v = "THIR_ONLY_EMITS"
    else: v = "OTHER"
    return h, {"verdict": v, "ast": a[0], "ast_err": a[1], "thir": t[0], "thir_err": t[1], "fallback": t[2]}

if __name__ == "__main__":
    progs = collect(); print("distinct programs:", len(progs), flush=True)
    results = {}
    with ProcessPoolExecutor(JOBS) as ex:
        futs = {ex.submit(run_one, (h, p["src"])): h for h, p in progs.items()}
        for i, fut in enumerate(as_completed(futs)):
            h, r = fut.result(); r["uses"] = progs[h]["uses"]; results[h] = r
            if i % 500 == 0: print("done", i, flush=True)
    json.dump(results, open(f"{S}/program_verdicts.json", "w"), indent=0)
    c = collections.Counter(r["verdict"] for r in results.values()); print("verdicts:", dict(c))
    breaks = collections.Counter()
    for r in results.values():
        if r["verdict"] == "BREAKS_AT_CUTOVER":
            for u in r["uses"]: breaks[u.split("::")[0]] += 1
    print("files with BREAKS programs (top 15):", breaks.most_common(15))
