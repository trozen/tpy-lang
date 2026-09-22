"""Count the lowering's builtin-container gates -- the container-family
consolidation's ratchet.

The consolidation (TODO.md "[thir] ARCHITECTURAL: the lowering's
builtin-container family is accidental") replaces per-site enumerations of
`list`/`dict`/`set`/`bytearray`/`Array` with one reference-type axis. This
script is the number that has to go down: it AST-counts, per lowering file
plus `emit.py`, every call of the eight container predicates, the record and
reference admission helpers, `is_value_type`, the family-enumeration
disjunctions, and the pending-literal resolver.

    uv run python scripts/thir_migration/review/container_gates.py [--json]

Counts come from `ast`, so docstring and comment mentions do not inflate them
(a raw `grep -c` reads ~5% high). The two counts that should stay put are
`resolve_pending_container` and the literal-construction sites: pending
resolution and literal construction are the two places a lowering gate may
legitimately name a builtin container.
"""
import argparse
import ast
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
LOWER = os.path.join(REPO, "tpyc", "thir", "lower")

CONTAINER_PREDS = ("is_list", "is_dict", "is_set", "is_array", "is_span",
                   "is_varargs", "is_dict_view", "is_bytearray_type")
AXIS_PREDS = ("_f1_record", "record_like", "_f1_container_ref",
              "_bytes_family_ref", "is_value_type")
OTHER = ("resolve_pending_container",)
CORE = {"is_list", "is_dict", "is_set"}


def files():
    names = sorted(f for f in os.listdir(LOWER) if f.endswith(".py"))
    return [os.path.join("tpyc", "thir", "lower", f) for f in names] + [
        os.path.join("tpyc", "thir", "emit.py")]


def call_name(node):
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def measure(rel):
    with open(os.path.join(REPO, rel)) as fh:
        tree = ast.parse(fh.read())
    counts = {k: 0 for k in CONTAINER_PREDS + AXIS_PREDS + OTHER}
    for node in ast.walk(tree):
        name = call_name(node)
        if name in counts:
            counts[name] += 1
    # Family enumerations: an `or` naming >= 2 of list/dict/set, or one of
    # those plus another container predicate. Nested Or nodes starting on the
    # same line are one expression.
    seen = set()
    enums = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        named = {call_name(v) for v in ast.walk(node)} & set(CONTAINER_PREDS)
        if len(named & CORE) >= 2 or (len(named) >= 2 and named & CORE):
            if node.lineno not in seen:
                seen.add(node.lineno)
                enums += 1
    counts["family_enumerations"] = enums
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = {rel: measure(rel) for rel in files()}
    keys = list(CONTAINER_PREDS) + ["family_enumerations"] + \
        list(AXIS_PREDS) + list(OTHER)
    totals = {k: sum(r[k] for r in rows.values()) for k in keys}

    if args.json:
        json.dump({"per_file": rows, "totals": totals}, sys.stdout, indent=1)
        print()
        return 0

    width = max(len(k) for k in keys)
    names = [os.path.basename(rel) for rel in rows]
    print("%-*s  %s  %5s" % (width, "gate", "  ".join(
        "%6s" % n[:6] for n in names), "TOTAL"))
    for k in keys:
        cells = "  ".join("%6d" % rows[rel][k] for rel in rows)
        print("%-*s  %s  %5d" % (width, k, cells, totals[k]))
    container_total = sum(totals[k] for k in CONTAINER_PREDS)
    print()
    print("container-predicate calls: %d" % container_total)
    print("family enumerations:       %d" % totals["family_enumerations"])
    print("axis calls: %s" % "  ".join(
        "%s %d" % (k, totals[k]) for k in AXIS_PREDS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
