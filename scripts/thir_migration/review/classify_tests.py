"""Classify every THIR unit test by the CLAIM its assertions make, and tally duplicated programs.

Claim types (a test may carry several; the strongest wins for the headline):
  reject    -- pins that THIR REJECTS a shape (_assert_rejects_at / _raised_in_lowering / ThirUnsupported checks)
  routes    -- asserts every body lowered (_assert_routes_byte_identical / _lower_ctx_witnessed / _assert_no_fallback)
  identity  -- only _assert_byte_identical (satisfied by a fallback; vacuous post-cutover)
  render    -- substring asserts on emitted C++ (`"..." in cpp`)
  node      -- asserts on THIR node structure (isinstance(..., THIR*), node fields)
  fallback  -- asserts on the fallback dict / reason tags (_thir_fallback, note())
  other     -- none of the above
"""
import ast, glob, json, sys, collections, hashlib
ROUTES = {"_assert_routes_byte_identical", "_lower_ctx_witnessed", "_assert_no_fallback", "_thir_ctx_witnessed"}
IDENT = {"_assert_byte_identical"}
REJECT = {"_assert_rejects_at", "_raised_in_lowering", "_rejects_at"}
LOWERS = {"_lower", "_lower_ctx", "_thir_ctx", "_lower_ctor", "_top_level", "_constant_positions", "_constant_inits"}
FALLBACK_NAMES = {"_thir_fallback", "_thir_reject_reason", "_thir_reject_detail", "fallback"}
rows = []; programs = collections.Counter(); prog_files = collections.defaultdict(set)
for f in sorted(glob.glob("tpyc/thir/test_*.py")):
    tree = ast.parse(open(f).read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"): continue
        calls = set(); render = node_asserts = fallback = False; nprog = 0
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                fn = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if isinstance(n.func, ast.Attribute) else None)
                if fn: calls.add(fn)
                if fn == "isinstance" and len(n.args) == 2:
                    a = n.args[1]
                    names = [a.id] if isinstance(a, ast.Name) else [e.id for e in getattr(a, "elts", []) if isinstance(e, ast.Name)]
                    if any(x.startswith("THIR") for x in names): node_asserts = True
            if isinstance(n, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in n.ops):
                if isinstance(n.left, ast.Constant) and isinstance(n.left.value, str): render = True
            if isinstance(n, ast.Attribute) and n.attr in FALLBACK_NAMES: fallback = True
            if isinstance(n, ast.Name) and n.id in FALLBACK_NAMES: fallback = True
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "\n" in n.value and ("def " in n.value or "class " in n.value):
                nprog += 1; programs[n.value] += 1; prog_files[n.value].add(f)
        claims = []
        if calls & REJECT or "ThirUnsupported" in calls: claims.append("reject")
        if calls & ROUTES: claims.append("routes")
        if calls & LOWERS and not (calls & ROUTES): claims.append("lowers")
        if calls & IDENT and not (calls & ROUTES): claims.append("identity")
        if render: claims.append("render")
        if node_asserts: claims.append("node")
        if fallback: claims.append("fallback")
        if not claims: claims.append("other")
        rows.append({"file": f, "test": node.name, "line": node.lineno, "claims": claims, "programs": nprog,
                     "lines": node.end_lineno - node.lineno + 1})
out = sys.argv[1] if len(sys.argv) > 1 else None
if out: json.dump(rows, open(out, "w"), indent=0)
print("tests:", len(rows))
head = collections.Counter()
for r in rows:
    c = r["claims"]
    head["reject" if "reject" in c else "routes" if "routes" in c else "lowers" if "lowers" in c else "identity+render" if "identity" in c and "render" in c
         else "identity-only" if "identity" in c else "render/node-only" if ("render" in c or "node" in c) else "fallback-only" if "fallback" in c else "other"] += 1
print("headline claim:", dict(head.most_common()))
print("claim combos (top 12):", collections.Counter(tuple(r["claims"]) for r in rows).most_common(12))
dups = {k: v for k, v in programs.items() if v > 1}
print(f"programs {sum(programs.values())} distinct {len(programs)} duplicated {len(dups)} extra copies {sum(v-1 for v in dups.values())} cross-file {sum(1 for k in dups if len(prog_files[k])>1)}")
wave = [r for r in rows if "wave_" in r["file"]]; named = [r for r in rows if "wave_" not in r["file"]]
print("wave tests:", len(wave), "named tests:", len(named))
