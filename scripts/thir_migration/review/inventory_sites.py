"""Inventory every `ThirUnsupported(...)` construction site: file, line, enclosing def chain, reason expr."""
import ast, glob, json, sys
sites = []
for f in sorted(glob.glob("tpyc/thir/**/*.py", recursive=True)):
    if "/test_" in f or f.endswith("testutil.py"): continue
    src = open(f).read(); tree = ast.parse(src)
    parents = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n): parents[c] = n
    def chain(n):
        out = []
        while n in parents:
            n = parents[n]
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): out.append(n.name)
        return ".".join(reversed(out))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and ((isinstance(n.func, ast.Name) and n.func.id == "ThirUnsupported") or (isinstance(n.func, ast.Attribute) and n.func.attr == "ThirUnsupported")):
            reason = ast.get_source_segment(src, n.args[0]) if n.args else "?"
            sites.append({"file": f, "line": n.lineno, "func": chain(n), "reason": reason[:160]})
json.dump(sites, open(sys.argv[1], "w"), indent=1)
import collections
print("sites:", len(sites)); print(collections.Counter(s["file"] for s in sites).most_common())
funcs = collections.Counter(s["func"] for s in sites)
print("distinct enclosing funcs:", len(funcs)); print("top funcs:", funcs.most_common(8))
