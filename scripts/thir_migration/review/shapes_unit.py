"""Shape-fingerprint every routing program from program_verdicts.json (the unit-test side of the coverage question)."""
import json, sys, collections
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, ".")
S = sys.argv[1]; JOBS = int(sys.argv[2])
def one(item):
    h, src = item
    import io, contextlib
    from tpyc.thir.testutil import _compile, _entry
    from tpyc.thir.shape import body_shape_signature
    from tpyc.compilation_context import activate_compiler
    try:
        compiler, modules = _compile(src); entry = _entry(modules)
        sigs = set()
        with activate_compiler(compiler):
            for f in entry.ast.functions: sigs.add(body_shape_signature(f, "body"))
            for r in entry.ast.records:
                for m in r.methods: sigs.add(body_shape_signature(m, "body"))
        return h, sorted(sigs)
    except Exception as e:
        return h, None
if __name__ == "__main__":
    v = json.load(open(f"{S}/program_verdicts.json"))
    progs = {h: r for h, r in v.items() if r["verdict"] == "ROUTES"}
    import ast, glob, hashlib
    srcs = {}
    for f in sorted(glob.glob("tpyc/thir/test_*.py")):
        for n in ast.walk(ast.parse(open(f).read())):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "\n" in n.value and ("def " in n.value or "class " in n.value):
                srcs[hashlib.sha1(n.value.encode()).hexdigest()[:16]] = n.value
    with ProcessPoolExecutor(JOBS) as ex:
        res = dict(ex.map(one, [(h, srcs[h]) for h in progs]))
    json.dump(res, open(f"{S}/shapes_unit.json", "w"))
    allsigs = collections.Counter(s for v in res.values() if v for s in v)
    print("programs:", len(res), "failed:", sum(1 for v in res.values() if v is None), "distinct body shapes:", len(allsigs))
