#!/usr/bin/env python3
"""AST-emit-arm RESIDENCY metric for the THIR migration.

Measures how much of tpyc/codegen_cpp/ still fires ONLY because THIR
falls back to the AST emit path -- i.e. the AST emit code left to delete.

Method (A-minus-U subtraction):
  U (baseline) : codegen_cpp coverage under --thir-codegen, UNMARKED cases only
                 (ratchet => zero fallback => pure THIR shared-helper usage).
  A (full)     : codegen_cpp coverage under --thir-codegen, ALL cases
                 (shared helpers + fallback AST emit arms).
  D (denom)    : codegen_cpp coverage under --no-thir, ALL cases
                 (every AST emit arm the corpus exercises).
  RESIDENCY = A - U  = AST emit functions that fire only via fallback.

Usage:
  1. Produce three coverage JSONs (see run_all() / the shell recipe printed
     by --howto). Each is `pytest ... --cov=tpyc/codegen_cpp --cov-report=json:PATH`.
  2. python ast_arm_residency.py --u U.json --a A.json --d D.json \
         --src <repo>/tpyc/codegen_cpp --thir <repo>/tpyc/thir
"""
import argparse
import ast
import json
import os
import sys


def func_body_ranges(path):
    """Return list of (qualname, body_start_line, end_line) for every
    def/async def in `path`. body_start excludes the signature/def line so
    the range reflects code that runs only when the function is CALLED
    (the def line itself executes at import)."""
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src, filename=path)
    out = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qn = f"{prefix}{child.name}"
                body_start = child.body[0].lineno if child.body else child.lineno
                out.append((qn, body_start, child.end_lineno))
                walk(child, qn + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def hit_functions(cov_json, src_dir):
    """Map executed lines -> innermost enclosing function body.
    Returns dict: relpath -> set(qualname) of functions with >=1 body line run,
    plus a registry relpath -> {qualname: (start,end)}."""
    data = json.load(open(cov_json))
    hits = {}
    registry = {}
    for fpath, fentry in data["files"].items():
        ap = os.path.abspath(fpath)
        if os.path.abspath(src_dir) not in ap:
            continue
        rel = os.path.basename(ap)
        ranges = func_body_ranges(ap)
        registry[rel] = {qn: (s, e) for qn, s, e in ranges}
        executed = set(fentry["executed_lines"])
        fn_hits = set()
        for line in executed:
            # innermost = smallest range containing the line in its body
            best = None
            best_span = None
            for qn, s, e in ranges:
                if s <= line <= e:
                    span = e - s
                    if best is None or span < best_span:
                        best, best_span = qn, span
            if best is not None:
                fn_hits.add(best)
        hits[rel] = fn_hits
    return hits, registry


def flatten(hits):
    return {(rel, qn) for rel, s in hits.items() for qn in s}


def thir_references(thir_dir):
    """Set of bare function/method names referenced anywhere under thir/.
    Used to flag residency funcs that are ALSO called by THIR lowering
    (shared-helper contamination of A-U rather than a true fallback arm)."""
    names = set()
    for root, _dirs, files in os.walk(thir_dir):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            try:
                tree = ast.parse(open(os.path.join(root, fn)).read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.Name):
                    names.add(node.id)
    return names


def bare(qn):
    return qn.split(".")[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--u", required=True)
    ap.add_argument("--a", required=True)
    ap.add_argument("--d", required=True)
    ap.add_argument("--src", required=True, help="tpyc/codegen_cpp dir")
    ap.add_argument("--thir", required=True, help="tpyc/thir dir")
    args = ap.parse_args()

    U, reg = hit_functions(args.u, args.src)
    A, _ = hit_functions(args.a, args.src)
    D, _ = hit_functions(args.d, args.src)

    Uf, Af, Df = flatten(U), flatten(A), flatten(D)
    residency = Af - Uf                      # fires only via fallback
    all_funcs = {(rel, qn) for rel, d in reg.items() for qn in d}

    thir_names = thir_references(args.thir)

    print("=" * 72)
    print("THIR AST-EMIT-ARM RESIDENCY")
    print("=" * 72)
    print(f"codegen_cpp functions defined (all)     : {len(all_funcs)}")
    print(f"  hit under D (--no-thir, all cases)     : {len(Df)}   [denominator]")
    print(f"  hit under U (--thir, unmarked only)    : {len(Uf)}   [THIR shared helpers]")
    print(f"  hit under A (--thir, all cases)        : {len(Af)}")
    print(f"RESIDENCY  A - U                          : {len(residency)}   [fallback-only AST emit fns]")
    denom = residency & Df
    print(f"  of which also in D (real emit arms)     : {len(denom)}")
    print(f"  of which NOT in D (fallback-only paths) : {len(residency - Df)}")
    if Df:
        print(f"RESIDENCY / D                            : {len(residency)}/{len(Df)} "
              f"= {100.0*len(residency)/len(Df):.1f}%")

    # line estimate: sum body spans of residency funcs
    def span(rel, qn):
        s, e = reg[rel][qn]
        return e - s + 1
    lines = sum(span(rel, qn) for rel, qn in residency if qn in reg.get(rel, {}))
    print(f"~lines in residency funcs (body spans)    : {lines}")
    print()

    print("-" * 72)
    print("RESIDENCY LIST (fallback-only AST emit functions), by file:")
    print("  [thir] = bare name also referenced under tpyc/thir/ (possible")
    print("           shared-helper contaminant, not a pure fallback arm)")
    print("-" * 72)
    by_file = {}
    for rel, qn in residency:
        by_file.setdefault(rel, []).append(qn)
    for rel in sorted(by_file):
        print(f"\n### {rel}")
        for qn in sorted(by_file[rel], key=lambda q: -span(rel, q) if q in reg.get(rel, {}) else 0):
            s, e = reg[rel].get(qn, (0, 0))
            flag = " [thir]" if bare(qn) in thir_names else ""
            ind = "" if (rel, qn) in Df else " [NOT-in-D]"
            print(f"  {e-s+1:4d}L  L{s}-{e}  {qn}{flag}{ind}")

    print()
    print("-" * 72)
    print("CORPUS-COVERAGE GAPS: emit funcs in source NOT covered by D")
    print("(residency=0 there is untrustworthy -- corpus never exercises them)")
    print("-" * 72)
    gaps = all_funcs - Df
    by_file = {}
    for rel, qn in gaps:
        by_file.setdefault(rel, []).append(qn)
    for rel in sorted(by_file):
        print(f"\n### {rel}  ({len(by_file[rel])} uncovered)")
        for qn in sorted(by_file[rel]):
            print(f"    {qn}")


if __name__ == "__main__":
    main()
