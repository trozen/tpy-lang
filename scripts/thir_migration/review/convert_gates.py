"""Count what the one conversion boundary (`tpyc/thir/lower/convert.py`) has
to remove from a sink family -- the lowering consolidation's ratchet.

TODO.md "One conversion boundary for every value sink": every sink hands its
lowered source to ONE `convert(expr, slot, types)`; the sink keeps literal
construction and its target / receiver tests, and nothing else decides a
conversion. This script is the structural acceptance, a static AST walk that
runs in about five seconds (it parses the whole lowering package for the
audit):

  (a) constructions -- THIRFormConvert / THIRMove / THIROptionalPtrArg /
      THIROptViewArg / THIRCopy nodes and `lower_copy_construct` calls built
      inside a family's code (convert.py excluded). Target 0 per migrated
      family.
  (b) kind tests -- `isinstance(x, Tpy*)` / `isinstance(x, THIR*)` inside a
      family, split by what they decide: `literal` (literal construction,
      allowed), `target` (the write target / receiver, allowed), `rule` (a
      named language-rule refusal, allowed, listed with its reason) and
      `conversion` (everything else; target 0 per migrated family). A
      module constant holding a tuple of kinds (`_STORAGE_READS`) counts as
      the kind test it is.
  (c) convert.py itself -- kind tests, `SinkPos` reads and lowering-context
      parameters (named `lc`, or annotated `_LowerCtx`). Target 0. Its
      `types` parameter is the analyzer (type relations only, by contract,
      not by a check).
  binding-presence tests -- `binding is None` / `is not None` (and on a
      local bound from `.binding`): a NAME read is the only source with a
      binding, so the test is an expression-kind test in disguise. Counted
      in convert.py and both families, reported on their own line, gated.
  (d) decision audit -- the call graph from the family's entries, convert,
      the Source stamp and the Slot builder, bounded at the source-lowering
      entries; every function in it that branches on `SinkPos`, a family
      tag or a kind test is listed. Target: only the allow-listed functions
      (literal construction, target / receiver predicates, the source's own
      lowering), each with its reason.

  bindings -- the binding table's structure (`tpyc/thir/lower/bindings.py`
      is the home of the per-name representation facts it models): writes
      of a `_LowerCtx` set / dict attribute outside the planner, less an
      allow-list of ledgers / grants / policies (`LEDGER_ATTRS`) -- the
      per-name facts still outside the table, as a number;
      `_BRANCH_SCOPED_SETS` entries outside that allow-list; and the
      known binding classifiers that still exist (`BINDING_CLASSIFIERS`).

The RETURN family is measured the same way, each site attributed to the
row whose witness / reject key sits in the innermost `if` around it: a row
landing 1 converts (its kind tests choose the source's READ and are
counted as `read`), a row left for landing 2 (allowed, with its family), or
the arm's unkeyed scaffolding (counted). Its audit walks from the migrated
rows' call sites, `convert` and the return slot builder; a callee reached
only from a landing-2 row is listed with that reason.

    uv run python scripts/thir_migration/review/convert_gates.py [--json]
        [--check]

`--check` compares against `convert_gates.expected.json` and exits 1 when a
number moved the wrong way; the landing-2 counts of the return family are
gated too, so moving code under a landing-2 key cannot take it out of the
ratchet.
"""
import argparse
import ast
import json
import os
import sys

# CONVERT_GATES_ROOT measures another checkout (a baseline export).
REPO = os.environ.get("CONVERT_GATES_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".."))
LOWER = os.path.join("tpyc", "thir", "lower")
EXPECTED = os.path.join(os.path.dirname(__file__), "convert_gates.expected.json")

CONVERSION_NODES = ("THIRFormConvert", "THIRMove", "THIROptionalPtrArg",
                    "THIROptViewArg", "THIRCopy")
CONVERSION_CALLS = ("lower_copy_construct",)

LITERAL_KINDS = frozenset({
    "TpyNoneLiteral", "TpyIntLiteral", "TpyFloatLiteral", "TpyBoolLiteral",
    "TpyStrLiteral", "TpyBytesLiteral", "TpyArrayLiteral", "TpyDictLiteral",
    "TpySetLiteral", "TpyListRepeat", "TpyTupleLiteral",
    "TpyListComprehension", "TpySetComprehension", "TpyDictComprehension",
    "CONTAINER_LITERAL_NODES", "THIRContainerLiteral", "THIRLiteral",
})

# --- the field-write family -------------------------------------------------

FIELD_WRITE_FILE = os.path.join(LOWER, "field_write.py")
# The checks.py predicates the field write calls for itself.
FIELD_WRITE_CHECKS = (
    "_field_write_receiver_ok", "_viewfam_field_write_receiver_ok",
    "_scalar_field_write_ok", "_user_deref_field_write_ok",
    "view_slot_shape_ok", "_str_field_write_ok", "_bytes_field_write_ok",
    "_class_const_write_target_ok", "_method_recv_field_write_ok",
    "_container_field_write_slot",
)
# Functions whose kind tests are about the write TARGET / receiver.
TARGET_FUNCS = frozenset({
    "_lower_field_write_target", "_btuple_elem_field_write_ok",
    "_class_const_write_target_ok", "_method_recv_field_write_ok",
    "_field_write_receiver_ok", "_viewfam_field_write_receiver_ok",
    "_container_field_write_slot",
})
# (function, kind) -> the language rule the test enforces. These refuse a
# source the language does not allow at the slot; they are not conversions.
RULE_TESTS = {
    ("_closure_field_fence", "TpyLambda"):
        "expr.lambda: a stored closure copies its captures "
        "(BUGS.md#callable-local-lambda-copies-capture-silently)",
    ("_classify_value", "TpyLambda"):
        "expr.lambda: a stored closure copies its captures "
        "(BUGS.md#callable-local-lambda-copies-capture-silently)",
    ("view_slot_shape_ok", "*"):
        "view-slot fence: which source spellings a VIEW field may point "
        "into (lifetime; the missing fact is sema's outlives-the-holder "
        "verdict on the source)",
    ("_str_field_write_ok", "TpyStrLiteral"):
        "view-slot fence (str): a literal / slice / call / name",
    ("_str_field_write_ok", "TpySubscript"):
        "view-slot fence (str): a literal / slice / call / name",
    ("_str_field_write_ok", "TpyCall"):
        "view-slot fence (str): a literal / slice / call / name",
    ("_str_field_write_ok", "TpyMethodCall"):
        "view-slot fence (str): a literal / slice / call / name",
    ("_str_field_write_ok", "TpyCoerce"):
        "view-slot fence (str): peels a same-family coerce before the "
        "spelling rows",
}

# --- the return family -------------------------------------------------------

STATEMENTS_FILE = os.path.join(LOWER, "statements.py")
RETURN_FUNCS = ("_lower_resumable_return_value", "_wrap_view_owned_return",
                "_return_plan", "_return_converted")
# A construction or kind test in the return arm belongs to the row whose
# witness / reject key sits in the innermost `if` around it (test + body).
# The rows landing 1 converts through `convert`; what is left in them is the
# READ -- which lowering the admitted source takes -- and is listed as such.
RETURN_MIGRATED = frozenset({
    "ret.record_storage", "ret.record_self", "ret.record_field",
    "ret.record_subscript", "ret.record_borrow", "ret.borrowed_copy",
    "ret.record_value_opt_name", "ret.self_move", "ret.storage_opt_rvalue",
    "ret.storage_opt_whole_rvalue", "ret.ptr_opt_field",
    "ret.ptr_opt_field_narrowed", "ret.ptr_opt_field_addr",
    "ret.ptr_opt_subscript", "ret.ptr_opt_self", "ret.ptr_opt_ptr_name",
    "ret.value_opt_name", "ret.value_opt_field", "ret.record_call_borrow",
    "ret.record_ref_call_storage", "ret.record_call_storage",
    "ret.ptr_opt_borrow_call", "ret.opt_field_ref",
    "return.open_tparam_param", "ret.live_name_copy",
})
# Rows left for landing 2, by key prefix -> the family they belong to.
RETURN_LANDING2 = (
    ("ret.btuple_", "tuple"), ("ret.tuple_", "tuple"),
    ("ret.own_tuple_", "tuple"), ("ret.own_storage_tuple", "tuple"),
    ("ret.wrapper_ref_tuple", "tuple"), ("ret.generic_tuple_", "tuple"),
    ("ret.value_opt_tuple_", "tuple"),
    ("return.tuple_source", "tuple"),
    ("return.wrapper_ref_tuple_source", "tuple"),
    ("return.generic_tuple_source", "tuple"),
    ("return.own_element_copy_source", "tuple"),
    ("ret.dyn_", "dyn"), ("return.dyn_", "dyn"),
    ("ret.union_", "union"), ("ret.own_union_", "union"),
    ("ret.narrowed_union_addr", "union"), ("return.union_", "union"),
    ("return.own_union_source", "union"),
    ("ret.own_wrapper_", "wrapper"), ("ret.wrapper_borrow", "wrapper"),
    ("ret.genrec_", "wrapper"), ("return.own_wrapper_source", "wrapper"),
    ("return.wrapper_borrow_source", "wrapper"),
    ("return.genrec_source", "wrapper"),
    ("ret.value_opt_view_", "value-Optional view"),
    ("return.opt_view_source", "value-Optional view"),
    ("ret.str_field", "view"), ("ret.viewfam_field_recv", "view"),
    ("return.str_field_form", "view"),
    ("ret.closure_", "callable"), ("ret.callable_name", "callable"),
    ("return.callable_source", "callable"),
    ("ret.container_literal", "container literal"),
    ("ret.container_repeat", "container literal"),
    ("ret.container_comp", "container literal"),
    ("ret.value_opt_none", "literal (None into the value optional)"),
    ("ret.ptr_opt_ternary", "ternary"), ("ret.record_ifexpr", "ternary"),
    ("ret.finally_deferred", "finally-deferred"),
    ("return.finally_deferred_capture", "finally-deferred"),
    ("er.", "@error_return"), ("error_return.", "@error_return"),
    ("ret.consuming_self_field", "consuming-self"),
    ("return.consuming_self_field", "consuming-self"),
    ("ret.container_borrow_global", "global"),
    ("ret.any_", "Any"), ("return.slot_type", "Any"),
    ("ret.record_ptr_opt_local", "pointer-bound local"),
    ("ret.record_ptr_local", "pointer-bound local"),
    ("ret.container_ptr_local", "pointer-bound local"),
    ("ret.value_ptr_opt_local", "pointer-bound local"),
    ("ret.tparam_ptr_local", "pointer-bound local"),
    ("return.ptr_opt_leaf_shape", "pointer-bound local"),
    ("ret.storage_opt_own_move", "move eligibility"),
    ("ret.owned_element_move", "move eligibility"),
    ("return.storage_opt_source", "pointer-bound local"),
    ("return.optval_coerced_param", "value Optional"),
    ("ret.record_methodcall", "rvalue at the owning slot"),
    ("ret.record_op_storage", "rvalue at the owning slot"),
    ("ret.record_deref_coerce", "deref coercion"),
    ("ret.copy_record", "explicit copy() spelling"),
    ("ret.void", "no value"),
    ("res.", "resumable payload"),
)


def landing2_reason(key):
    for prefix, family in RETURN_LANDING2:
        if key.startswith(prefix):
            return "landing 2: " + family
    return None

# --- the decision audit ------------------------------------------------------

AUDIT_ENTRIES = ("lower_field_write", "lower_member_init_value", "convert",
                 "plan_read", "stamp_source", "binding_of",
                 "field_slot", "field_slot_use")
# Where the walk stops: the source / literal lowering entries. What they
# decide is the source's own lowering, not the sink's conversion.
AUDIT_BOUNDARY = frozenset({
    "_lower_expr", "_lower_expr_impl", "_lower_tuple_literal",
    "_lower_ru_literal", "_lower_comprehension", "_lower_copy_record",
    "_flush_witness", "_slot_literal_retype",
})
# Entry points of a question the sink asks about its TARGET or a LITERAL it
# builds, or of a named language rule: listed with the reason, not walked
# into (their callees answer the same question).
AUDIT_STOP = {
    # target / receiver
    "_lower_field_write_target": "target",
    "_lower_class_const_write_target": "target (class constant lvalue)",
    "_btuple_elem_field_write_ok": "target (borrow-tuple element receiver)",
    "_class_const_write_target_ok": "target (class constant lvalue)",
    "_field_receiver_ok": "target / receiver ladder",
    "_field_receiver_or_unbound_self_ok": "target / receiver ladder",
    "_field_over_subscript_ok": "target / receiver ladder",
    "_field_over_container_subscript_ok": "target / receiver ladder",
    "_user_deref_field_recv_ok": "target / receiver ladder",
    "_field_write_receiver_ok": "target / receiver ladder",
    "_viewfam_field_write_receiver_ok": "target / receiver ladder",
    "_method_recv_field_write_ok": "target / receiver ladder",
    "_container_field_write_slot": "target (the declared slot of the field)",
    # literal construction
    "_storage_literal": "literal construction",
    "_storage_tuple_literal": "literal construction",
    "_self_described_brace": "literal construction",
    "_container_literal_shape_ok": "literal construction",
    "_container_comp_arg": "literal construction",
    "_ru_instance_literal_ok": "literal construction",
    "_tuple_literal_has_ref_elements": "literal construction",
    # language rules
    "_lambda_routable": "language rule expr.lambda",
    "view_slot_shape_ok": "language rule: the view-slot fence (which "
                          "spellings a VIEW field may point into); missing "
                          "fact: sema's outlives-the-holder verdict",
    # explicit copy spellings the caller peels before conversion
    "copy_call_arg": "explicit `copy()` spelling (peeled by the caller)",
    "copy_ptr_optional_peel": "explicit `copy()` spelling",
    "copy_ctor_rvalue_source": "explicit `copy()` spelling",
}
# (function, kind) -> why a kind test inside the audited graph is allowed.
AUDIT_ALLOW_TESTS = {
    ("_closure_field_fence", "TpyLambda"): "language rule expr.lambda",
    ("_classify_value", "TpyLambda"): "language rule expr.lambda",
    ("_str_field_write_ok", "TpyCoerce"): "view-slot fence (str)",
    ("_str_field_write_ok", "TpySubscript"): "view-slot fence (str)",
    ("_str_field_write_ok", "TpyCall"): "view-slot fence (str)",
    ("_str_field_write_ok", "TpyMethodCall"): "view-slot fence (str)",
    ("stamp_source", "*"): "the source's own lowering: per-kind facts are "
                           "what the stamp computes (the one writer)",
    ("binding_of", "*"): "the source's own facts: a NAME read names a "
                         "binding",
    ("_held_from_facts", "*"): "the Source stamp: per-kind facts decide "
                                "where the value lives",
    ("_name_held", "*"): "the Source stamp: a NAME read's record, unless "
                         "the arm renamed or spelled the read",
    ("_tuple_layout_elems", "*"): "the Source stamp: per-element facts of "
                                  "a tuple binding's layout",
    ("_dies", "*"): "the Source stamp: a member / element read off a "
                    "temporary dies with it",
    ("_temporary", "*"): "the Source stamp: a select's lifetime is its "
                         "lowering's decision; a node with no parse node "
                         "is durable only where its kind says so",
}


def _read(rel):
    with open(os.path.join(REPO, rel)) as fh:
        return ast.parse(fh.read())


def _funcs(tree):
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _kind_names(test_arg):
    """The class names an isinstance second argument names."""
    out = []
    nodes = (test_arg.elts
             if isinstance(test_arg, (ast.Tuple, ast.Set, ast.List))
             else [test_arg])
    for n in nodes:
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
    return out


def _is_kind(name):
    return (name.startswith("Tpy") or name.startswith("THIR")
            or name == "CONTAINER_LITERAL_NODES" or name in kind_consts())


_KIND_CONSTS = None


def kind_consts():
    """Module constants of the lowering package (and the THIR nodes) that
    hold a tuple of kinds: an isinstance against one is a kind test."""
    global _KIND_CONSTS
    if _KIND_CONSTS is None:
        _KIND_CONSTS = set()
        rels = [os.path.join(LOWER, f)
                for f in sorted(os.listdir(os.path.join(REPO, LOWER)))
                if f.endswith(".py")]
        rels.append(os.path.join("tpyc", "thir", "nodes.py"))
        for rel in rels:
            for n in _read(rel).body:
                if isinstance(n, ast.Assign):
                    targets, value = n.targets, n.value
                elif isinstance(n, ast.AnnAssign) and n.value is not None:
                    targets, value = [n.target], n.value
                else:
                    continue
                # `frozenset((A, B))` / `frozenset({A, B})` holds kinds too.
                if (isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id in ("frozenset", "set", "tuple")
                        and len(value.args) == 1):
                    value = value.args[0]
                if not isinstance(value, (ast.Tuple, ast.Set, ast.List)):
                    continue
                names = _kind_names(value)
                if any(k.startswith(("Tpy", "THIR")) and k != "TpyType"
                       for k in names):
                    _KIND_CONSTS.update(t.id for t in targets
                                        if isinstance(t, ast.Name))
    return _KIND_CONSTS


def kind_tests(node):
    """(kind, lineno) per kind named in an isinstance test under `node`.
    Only parse-node (Tpy*) and THIR-node (THIR*) classes count: a TYPE test
    (`OptionalType`) is a type relation, not an expression kind."""
    out = []
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "isinstance" and len(n.args) == 2):
            for k in _kind_names(n.args[1]):
                if _is_kind(k) and k not in ("TpyType",):
                    out.append((k, n.lineno))
    return out


def binding_tests(node):
    """Lines of a binding-presence test under `node`: `<x>.binding` /
    `binding` / a local bound from `.binding` or `binding_of(...)` / a
    `binding_of(...)` call, compared `is None` / `is not None` or read for
    its truth (`if s.binding:`, `b and b.pointer`, `not b`)."""
    bound = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            value = n.value
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if ((isinstance(value, ast.Attribute)
                    and value.attr == "binding")
                    or (isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "binding_of")):
                bound.update(t.id for t in targets
                             if isinstance(t, ast.Name))

    def is_binding(e):
        return ((isinstance(e, ast.Attribute) and e.attr == "binding")
                or (isinstance(e, ast.Name)
                    and (e.id == "binding" or e.id in bound))
                or (isinstance(e, ast.Call)
                    and isinstance(e.func, ast.Name)
                    and e.func.id == "binding_of"))

    out = set()
    for n in ast.walk(node):
        if (isinstance(n, ast.Compare) and len(n.ops) == 1
                and isinstance(n.ops[0], (ast.Is, ast.IsNot))
                and isinstance(n.comparators[0], ast.Constant)
                and n.comparators[0].value is None):
            if is_binding(n.left):
                out.add(n.lineno)
            continue
        truth = []
        if isinstance(n, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            truth.append(n.test)
        elif isinstance(n, ast.BoolOp):
            truth.extend(n.values)
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            truth.append(n.operand)
        for t in truth:
            if is_binding(t):
                out.add(t.lineno)
    return sorted(out)


def constructions(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = (f.id if isinstance(f, ast.Name)
                    else f.attr if isinstance(f, ast.Attribute) else None)
            if name in CONVERSION_NODES or name in CONVERSION_CALLS:
                out.append((name, n.lineno))
    return out


def classify_test(func, kind):
    if func in TARGET_FUNCS:
        return "target"
    if (func, kind) in RULE_TESTS or (func, "*") in RULE_TESTS:
        return "rule"
    if kind in LITERAL_KINDS:
        return "literal"
    return "conversion"


# Literal construction builds its node against the slot (a tuple literal's
# storage wrap); its constructions are counted apart from (a).
LITERAL_FUNCS = frozenset({"_storage_literal", "_storage_tuple_literal",
                           "_self_described_brace"})


def family_numbers(units):
    """`units`: [(label, ast node)]. (a) constructions outside literal
    construction, (b) kind tests by class, with the sites listed."""
    cons = []
    lit_cons = []
    tests = {"literal": 0, "target": 0, "rule": 0, "conversion": 0}
    conv_sites = []
    binding_sites = []
    for label, node in units:
        binding_sites += ["%s:%d" % (label, line)
                          for line in binding_tests(node)]
        for name, line in constructions(node):
            site = "%s:%d %s" % (label, line, name)
            if label.split(":")[-1] in LITERAL_FUNCS:
                lit_cons.append(site)
            else:
                cons.append(site)
        for kind, line in kind_tests(node):
            cls = classify_test(label.split(":")[-1], kind)
            tests[cls] += 1
            if cls == "conversion":
                conv_sites.append("%s:%d %s" % (label, line, kind))
    return {"constructions": len(cons), "construction_sites": cons,
            "literal_constructions": lit_cons,
            "kind_tests": tests, "conversion_test_sites": conv_sites,
            "binding_test_sites": binding_sites}


def field_write_units():
    units = []
    fw = _read(FIELD_WRITE_FILE)
    for name, fn in _funcs(fw).items():
        units.append(("field_write.py:" + name, fn))
    checks = _funcs(_read(os.path.join(LOWER, "checks.py")))
    for name in FIELD_WRITE_CHECKS:
        if name in checks:
            units.append(("checks.py:" + name, checks[name]))
    return units


def _return_arm(tree):
    """The `if isinstance(stmt, TpyReturn):` arm of `_lower_stmt_dispatch`
    that is the sync return (the resumable leaf arm earlier in the function
    is a different, shorter branch)."""
    fn = _funcs(tree)["_lower_stmt_dispatch"]
    best = None
    for n in ast.walk(fn):
        if (isinstance(n, ast.If) and isinstance(n.test, ast.Call)
                and isinstance(n.test.func, ast.Name)
                and n.test.func.id == "isinstance"
                and len(n.test.args) == 2
                and _kind_names(n.test.args[1]) == ["TpyReturn"]):
            size = (n.end_lineno or n.lineno) - n.lineno
            if best is None or size > (best.end_lineno - best.lineno):
                best = n
    return best


def return_units():
    st = _read(STATEMENTS_FILE)
    units = []
    arm = _return_arm(st)
    if arm is not None:
        units.append(("statements.py:return_arm", arm))
    funcs = _funcs(st)
    for name in RETURN_FUNCS:
        if name in funcs:
            units.append(("statements.py:" + name, funcs[name]))
    return units




def _row_keys(nodes):
    """(line, key) for each witness / reject key named under `nodes`."""
    keys = []
    for top in nodes:
        for n in ast.walk(top):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id in ("_witness", "note_detail")):
                for a in n.args:
                    for c in ast.walk(a):
                        if (isinstance(c, ast.Constant)
                                and isinstance(c.value, str)
                                and "." in c.value):
                            keys.append((n.lineno, c.value))
    return keys


def _row_of(unit, target_line):
    """The key of the innermost `if` (the unit itself excluded) whose test
    or body holds the line; where that `if` names several, the one named
    nearest the line. None for the unit's own scaffolding."""
    best = None
    for n in ast.walk(unit):
        if not isinstance(n, ast.If) or n is unit:
            continue
        region = [n.test] + list(n.body)
        lo = n.test.lineno
        hi = max((getattr(r, "end_lineno", lo) or lo) for r in region)
        if not lo <= target_line <= hi:
            continue
        keys = _row_keys(region)
        if keys and (best is None or hi - lo < best[0]):
            best = (hi - lo, keys)
    if best is None:
        return None
    return min(best[1], key=lambda lk: abs(lk[0] - target_line))[1]


def return_numbers():
    """(a) constructions and (b) kind tests of the return family, each
    attributed to its row: a migrated row (target: no construction; its
    kind tests choose the READ), a landing-2 row (allowed, with the
    family), a literal, or unattributed (counted)."""
    out = {"a_constructions": 0, "a_sites": [], "a_landing2": 0,
           "b_conversion": 0, "b_conversion_sites": [], "b_read": 0,
           "b_read_sites": [], "b_landing2": 0, "b_literal": 0,
           "landing2_rows": {}, "binding_test_sites": []}
    for label, node in return_units():
        out["binding_test_sites"] += ["%s:%d" % (label, line)
                                      for line in binding_tests(node)]
        # The resumable payload is landing 2's own checkpoint, whole.
        unit_key = ("res.return_value"
                    if label.endswith("_lower_resumable_return_value")
                    else None)
        for name, line in constructions(node):
            key = unit_key or _row_of(node, line)
            reason = landing2_reason(key) if key else None
            if reason is not None:
                out["a_landing2"] += 1
                out["landing2_rows"].setdefault(key, reason)
            else:
                out["a_constructions"] += 1
                out["a_sites"].append("%s:%d %s [%s]" % (label, line, name,
                                                         key))
        for kind, line in kind_tests(node):
            if kind in LITERAL_KINDS:
                out["b_literal"] += 1
                continue
            key = unit_key or _row_of(node, line)
            reason = landing2_reason(key) if key else None
            if reason is not None:
                out["b_landing2"] += 1
                out["landing2_rows"].setdefault(key, reason)
            elif key in RETURN_MIGRATED:
                out["b_read"] += 1
                out["b_read_sites"].append("%s:%d %s [%s]" % (label, line,
                                                              kind, key))
            else:
                out["b_conversion"] += 1
                out["b_conversion_sites"].append(
                    "%s:%d %s [%s]" % (label, line, kind, key))
    return out


def convert_numbers():
    rel = os.path.join(LOWER, "convert.py")
    if not os.path.exists(os.path.join(REPO, rel)):
        return {"present": False, "kind_tests": 0, "sinkpos_reads": 0,
                "lc_params": 0, "sites": [], "binding_test_sites": []}
    tree = _read(rel)
    sites = ["%d %s" % (line, k) for k, line in kind_tests(tree)]
    sinkpos = 0
    lc = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id in ("SinkPos", "SinkForm"):
            sinkpos += 1
            sites.append("%d %s" % (n.lineno, n.id))
        if isinstance(n, ast.arg) and (
                n.arg == "lc" or (n.annotation is not None
                                  and "_LowerCtx" in ast.unparse(n.annotation))):
            lc += 1
            sites.append("%d param %s" % (n.lineno, n.arg))
    return {"present": True, "kind_tests": len(kind_tests(tree)),
            "sinkpos_reads": sinkpos, "lc_params": lc, "sites": sites,
            "binding_test_sites": ["convert.py:%d" % line
                                   for line in binding_tests(tree)]}


# --- decision audit ----------------------------------------------------------

def _all_funcs():
    """name -> [(rel, def)] over the lowering package and tpyc/thir/source.py.
    A module-level table (`_FAMILIES = [(classify, lower, ...)]`) enters as a
    pseudo-function whose body is the table, so a function that reads the
    table reaches the functions it lists."""
    table = {}
    rels = [os.path.join(LOWER, f)
            for f in sorted(os.listdir(os.path.join(REPO, LOWER)))
            if f.endswith(".py")]
    src = os.path.join("tpyc", "thir", "source.py")
    if os.path.exists(os.path.join(REPO, src)):
        rels.append(src)
    for rel in rels:
        tree = _read(rel)
        for name, fn in _funcs(tree).items():
            table.setdefault(name, []).append((rel, fn))
        for n in tree.body:
            targets = (n.targets if isinstance(n, ast.Assign)
                       else [n.target] if isinstance(n, ast.AnnAssign)
                       and n.value is not None else [])
            for t in targets:
                if isinstance(t, ast.Name) and t.id.startswith("_FAM"):
                    table.setdefault(t.id, []).append((rel, n))
    return table


def _callees(fn, known):
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in known:
                out.add(f.id)
            elif isinstance(f, ast.Attribute) and f.attr in known and (
                    isinstance(f.value, ast.Name)
                    and f.value.id.startswith("_")):
                # A module-aliased call (`_statements._is_any_type`).
                out.add(f.attr)
        elif (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
              and n.id in known):
            # A function referenced as a value (a family table entry).
            out.add(n.id)
    return out


FAMILY_TAGS = ("SinkPos", "SinkForm", "_ValueRender")


def _branches(fn):
    """(kind-or-tag, line) for what a function branches on: kind tests and
    comparisons against a sink position / form or a family tag."""
    out = list(kind_tests(fn))
    for n in ast.walk(fn):
        if isinstance(n, ast.Compare):
            for side in [n.left] + list(n.comparators):
                if (isinstance(side, ast.Attribute)
                        and isinstance(side.value, ast.Name)
                        and side.value.id in FAMILY_TAGS):
                    out.append(("%s.%s" % (side.value.id, side.attr),
                                n.lineno))
    return out


def _allowed(func, kind):
    if kind in LITERAL_KINDS:
        return "literal construction"
    return (AUDIT_ALLOW_TESTS.get((func, kind))
            or AUDIT_ALLOW_TESTS.get((func, "*")))


def audit(entries=AUDIT_ENTRIES, stops=None):
    stops = dict(AUDIT_STOP, **(stops or {}))
    table = _all_funcs()
    known = set(table)
    seen = set()
    stopped = set()
    todo = [e for e in entries if e in known]
    while todo:
        name = todo.pop()
        if name in seen or name in AUDIT_BOUNDARY:
            continue
        seen.add(name)
        if name in stops:
            stopped.add(name)
            continue
        for _rel, fn in table[name]:
            todo.extend(_callees(fn, known) - seen)
    rows = []
    for name in sorted(seen):
        for rel, fn in table[name]:
            br = _branches(fn)
            if not br:
                continue
            label = "%s:%s" % (os.path.basename(rel), name)
            if name in stopped:
                rows.append({"function": label,
                             "branches": ["%s@%d" % b for b in br],
                             "allowed": stops[name]})
                continue
            bad = [b for b in br if not _allowed(name, b[0])]
            reasons = sorted({_allowed(name, b[0]) for b in br
                              if _allowed(name, b[0])})
            rows.append({"function": label,
                         "branches": ["%s@%d" % b for b in (bad or br)],
                         "allowed": None if bad else "; ".join(reasons)})
    return {"walked": len(seen),
            "unexplained": [r["function"] for r in rows if not r["allowed"]],
            "allowed": [r["function"] for r in rows if r["allowed"]],
            "rows": rows}


# Functions the migrated return rows call to choose how the admitted source
# is READ (the lowering it takes) or to test its receiver: listed, not
# walked. The missing fact behind every READ entry is the same -- one
# lowering of a stored object that every reference / owning sink can take
# -- and its home is the source lowering (`_lower_expr` under one use).
RETURN_AUDIT_STOP = {
    "_lower_field_source": "read: the field's storage-form read",
    "_container_record_elem_subscript": "read: the element-lvalue admission",
    "_container_elem_lvalue_subscript": "read: the element-lvalue admission",
    "_subscript_container_recv_type": "target / receiver ladder",
    "_record_rvalue_source_shape": "read: an rvalue the tail lowers",
    "_record_source_reject_detail": "reject detail (diagnostic only)",
    "_ptr_opt_borrow_call_ret": "read: a borrow call's passthrough",
    "_value_opt_binding_kind": "read: the binding read whole",
    "is_property_getter_read": "read: a getter call's own convention",
    "copy_construct_form": "explicit copy spelling (the copy row's read)",
    "_field_decl_type": "read: the storage a field read names (missing "
                        "fact: the declared type of an lvalue's storage, a "
                        "Source fact beside `binding`)",
    "_optional_ptr_borrow_name": "read: a pointer binding read whole "
                                 "(`Source.binding.pointer` already "
                                 "carries it; the row predates it)",
}
RETURN_AUDIT_ENTRIES = ("_return_plan", "_return_converted", "return_slot",
                        "_return_holds", "_wrap_view_owned_return")


def return_audit():
    """The decision audit from the return arm. A function the arm calls
    from a landing-2 row is listed with that reason and not walked."""
    st = _read(STATEMENTS_FILE)
    arm = _return_arm(st)
    table = _all_funcs()
    known = set(table)
    seeds = set(RETURN_AUDIT_ENTRIES)
    left = {}
    if arm is not None:
        for n in ast.walk(arm):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            name = (f.id if isinstance(f, ast.Name)
                    else f.attr if isinstance(f, ast.Attribute) else None)
            if name not in known:
                continue
            key = _row_of(arm, n.lineno)
            if key in RETURN_MIGRATED:
                seeds.add(name)
            else:
                left.setdefault(name, (
                    landing2_reason(key) if key else None)
                    or "landing 2: the arm's unkeyed scaffolding (the "
                       "statement prologue, the pointer-local arm, the "
                       "generic tail)")
    stops = dict(RETURN_AUDIT_STOP)
    for name, reason in left.items():
        if name not in seeds:
            stops[name] = reason
    return audit(entries=tuple(sorted(seeds)), stops=stops)


# --- the Source as the one authority ------------------------------------------

# The stamp: the only functions that may build or rewrite a `Source`
# (`_lower_expr` attaches what `stamp_source` returns; `_restamp_reads`
# keeps a rebuilt node's own read facts current).
STAMP_FUNCS = frozenset({"stamp_source", "stamped", "_tuple_layout_elems",
                         "_restamp_reads", "_lower_expr"})
# The stamp's helpers that decide `held`: they may not read `.form`.
HELD_FUNCS = ("stamp_source", "stamped", "_held_from_facts", "_name_held",
              "_held_of_form", "_viewfam_of", "_owned_buffer",
              "_tuple_layout_elems", "_dies", "_temporary",
              # The binding table's projection (bindings.py), which the
              # name arm's `form` reads too.
              "project_held", "_value_opt_kind", "_held_of")
# Every classifier of a FIELD slot type that has existed; one may remain.
FIELD_CLASSIFIERS = ("_field_holds", "_storage_slot", "field_slot_class")


def _lowering_files():
    rels = [os.path.join(LOWER, f)
            for f in sorted(os.listdir(os.path.join(REPO, LOWER)))
            if f.endswith(".py")]
    return rels + [os.path.join("tpyc", "thir", "nodes.py")]


def _enclosing(tree):
    """node -> the name of the top-level (or class-level) function holding
    it."""
    owner = {}
    for top in ast.walk(tree):
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(top):
                owner.setdefault(n, top.name)
    return owner


def source_numbers():
    """Writers of `Source` outside the stamp, `.form` reads in the `held`
    derivation, `form=` arguments on THIR node constructions and
    `replace(..., form=...)` rewrites outside the stamp, and the field
    classifiers left."""
    writers, form_kw, form_reads, classifiers = [], [], [], []
    for rel in _lowering_files():
        tree = _read(rel)
        owner = _enclosing(tree)
        base = os.path.basename(rel)
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and n.name in FIELD_CLASSIFIERS:
                classifiers.append("%s:%s" % (base, n.name))
            if not isinstance(n, ast.Call):
                continue
            fn = owner.get(n, "<module>")
            f = n.func
            name = (f.id if isinstance(f, ast.Name)
                    else f.attr if isinstance(f, ast.Attribute) else None)
            kws = {k.arg for k in n.keywords}
            if fn not in STAMP_FUNCS and (
                    name == "Source"
                    or ("source" in kws and name == "replace")):
                writers.append("%s:%d %s" % (base, n.lineno, fn))
            if "form" in kws and fn not in STAMP_FUNCS and (
                    name == "replace" or (name or "").startswith("THIR")):
                form_kw.append("%s:%d" % (base, n.lineno))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Attribute) and n.attr == "form"
                    and isinstance(n.ctx, ast.Load)
                    and owner.get(n) in HELD_FUNCS):
                form_reads.append("%s:%d %s" % (base, n.lineno, owner[n]))
    return {"source_writers": writers, "form_kwargs": len(form_kw),
            "held_form_reads": form_reads,
            "field_classifiers": classifiers}


# `_LowerCtx` set / dict attributes that are NOT a per-name representation
# fact, so writing them outside the planner is not a binding writer.
LEDGER_ATTRS = {
    "global_slot_assigned": "ledger: the global slots already emitted",
    "walrus_predeclared": "ledger: the walrus decls already emitted",
    "forbidden_reads": "policy: the reads a region refuses",
    "forbidden_writes": "policy: the writes a region refuses",
    "unhandled_hoists": "ledger: the function's hoists not lowered yet",
    "import_calls": "per-statement import chain, keyed by statement",
    "pre_decl_import_cpp": "an import's spelling, not a binding's",
    "literal_facts": "flow fact: a name's literal value",
    "overload_literal_facts": "flow fact: the stub's literal values",
    "overload_narrowing": "narrowing: the stub's parameter types",
    "inline_narrowed": "narrowing: one condition's extraction",
    "deref_view_spelled": "narrowing: a branch's deref-view spelling",
    "tparam_bounds": "type-parameter bounds, not a binding",
    "sema_movable_locals": "sema's verdict, an input of the planner",
}
_SET_WRITE_METHODS = frozenset({"add", "discard", "update", "remove", "pop",
                                "clear", "setdefault", "difference_update",
                                "intersection_update"})
# The functions that classify a name's binding. Only `classify_binding` is
# the table's; the other two are classifiers still outside it.
BINDING_CLASSIFIERS = (("bindings.py", "classify_binding"),
                       ("checks.py", "_local_binding_shape"),
                       ("resumable.py", "frame_local_class"))
# The planner's own module: the one place a record is written.
PLANNER_FILE = "bindings.py"


def _lowerctx_container_attrs(tree):
    """The `_LowerCtx` attributes `__init__` creates as a set, frozenset or
    dict -- the shape a per-name fact outside the table takes."""
    attrs = set()
    for cls in ast.walk(tree):
        if not (isinstance(cls, ast.ClassDef) and cls.name == "_LowerCtx"):
            continue
        for fn in cls.body:
            if not (isinstance(fn, ast.FunctionDef)
                    and fn.name == "__init__"):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.AnnAssign):
                    targets, value = [n.target], n.value
                elif isinstance(n, ast.Assign):
                    targets, value = n.targets, n.value
                else:
                    continue
                container = (isinstance(value, (ast.Dict, ast.Set))
                             or (isinstance(value, ast.Call)
                                 and isinstance(value.func, ast.Name)
                                 and value.func.id in ("set", "frozenset",
                                                       "dict")))
                for t in targets:
                    if (container and isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Name)
                            and t.value.id == "self"):
                        attrs.add(t.attr)
    return attrs


def binding_numbers():
    """The binding table's structure: writers of a `_LowerCtx` set / dict
    attribute outside the planner (a mutating method call, an item write
    or delete, an augmented assignment, or an assignment of a built value
    to `lc.<attr>`), less the ledgers / grants / policies of
    `LEDGER_ATTRS`; `_BRANCH_SCOPED_SETS` entries outside that list; and
    the known binding classifiers that exist."""
    ctx_tree = _read(os.path.join(LOWER, "context.py"))
    attrs = _lowerctx_container_attrs(ctx_tree) - set(LEDGER_ATTRS)
    writers, scoped, classifiers = [], [], []

    def lc_attr(e):
        if isinstance(e, ast.Subscript):
            e = e.value
        if (isinstance(e, ast.Attribute) and e.attr in attrs
                and isinstance(e.value, ast.Name) and e.value.id == "lc"):
            return e.attr
        return None

    for rel in _lowering_files():
        base = os.path.basename(rel)
        tree = _read(rel)
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        classifiers += ["%s:%s" % (f, fn) for f, fn in BINDING_CLASSIFIERS
                        if f == base and fn in defined]
        if base == "context.py":
            for n in ast.walk(tree):
                if (isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name)
                                and t.id == "_BRANCH_SCOPED_SETS"
                                for t in n.targets)
                        and isinstance(n.value, ast.Tuple)):
                    scoped += [e.value for e in n.value.elts
                               if isinstance(e, ast.Constant)
                               and e.value not in LEDGER_ATTRS]
        if base == PLANNER_FILE:
            continue
        for n in ast.walk(tree):
            hit = None
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in _SET_WRITE_METHODS):
                a = lc_attr(n.func.value)
                if a is not None and not isinstance(n.func.value,
                                                    ast.Subscript):
                    hit = "%s.%s" % (a, n.func.attr)
            elif isinstance(n, ast.AugAssign):
                a = lc_attr(n.target)
                if a is not None:
                    hit = "%s op=" % a
            elif isinstance(n, ast.Delete):
                for t in n.targets:
                    if isinstance(t, ast.Subscript) and lc_attr(t):
                        hit = "del %s[]" % lc_attr(t)
            elif isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = (n.targets if isinstance(n, ast.Assign)
                           else [n.target])
                for t in targets:
                    a = lc_attr(t)
                    if a is None:
                        continue
                    if isinstance(t, ast.Subscript):
                        hit = "%s[]=" % a
                    elif not isinstance(n.value, ast.Name):
                        # A restore of a saved value is no new fact.
                        hit = "%s=" % a
            if hit is not None:
                writers.append("%s:%d %s" % (base, n.lineno, hit))
    return {"writers": writers, "branch_scoped": scoped,
            "classifiers": classifiers}


def measure():
    fw = family_numbers(field_write_units())
    ret = return_numbers()
    ret_au = return_audit()
    au = audit()
    return {
        "field_write": {
            "a_constructions": fw["constructions"],
            "b_kind_tests": fw["kind_tests"],
            "a_sites": fw["construction_sites"],
            "a_literal_sites": fw["literal_constructions"],
            "b_conversion_sites": fw["conversion_test_sites"],
            "binding_test_sites": fw["binding_test_sites"],
        },
        "return": dict(ret, d_walked=ret_au["walked"],
                       d_unexplained=ret_au["unexplained"],
                       d_allowed=ret_au["allowed"]),
        "c_convert": convert_numbers(),
        "d_audit": {"walked": au["walked"],
                    "unexplained": au["unexplained"],
                    "allowed": au["allowed"]},
        "_audit_rows": au["rows"],
        "source": source_numbers(),
        "bindings": binding_numbers(),
    }


def headline(m):
    fw = m["field_write"]
    c = m["c_convert"]
    d = m["d_audit"]
    r = m["return"]
    return ("field_write: (a) %d constructions, (b) %d conversion kind tests "
            "(literal %d, target %d, rule %d); (c) convert.py %s: %d kind "
            "tests, %d SinkPos, %d lc params; (d) audit %d functions walked, "
            "%d unexplained\n"
            "return: (a) %d constructions (+%d in landing-2 rows), (b) %d "
            "conversion kind tests (+%d read, %d landing-2, %d literal); (d) "
            "audit %d functions walked, %d unexplained; %d landing-2 rows" % (
                fw["a_constructions"], fw["b_kind_tests"]["conversion"],
                fw["b_kind_tests"]["literal"], fw["b_kind_tests"]["target"],
                fw["b_kind_tests"]["rule"],
                "present" if c["present"] else "absent", c["kind_tests"],
                c["sinkpos_reads"], c["lc_params"], d["walked"],
                len(d["unexplained"]), r["a_constructions"], r["a_landing2"],
                r["b_conversion"], r["b_read"], r["b_landing2"],
                r["b_literal"], r["d_walked"], len(r["d_unexplained"]),
                len(r["landing2_rows"]))
            + "\nbinding-presence tests: convert.py %d, field_write %d, "
            "return %d" % (len(c["binding_test_sites"]),
                            len(fw["binding_test_sites"]),
                            len(r["binding_test_sites"]))
            + "\nsource: %d writers outside the stamp, %d form reads in the "
            "held derivation, %d form= arguments outside the stamp, %d "
            "field classifiers" % (
                len(m["source"]["source_writers"]),
                len(m["source"]["held_form_reads"]),
                m["source"]["form_kwargs"],
                len(m["source"]["field_classifiers"]))
            + "\nbindings: %d writers outside the planner, %d binding sets "
            "branch-scoped, %d name classifiers (%s)" % (
                len(m["bindings"]["writers"]),
                len(m["bindings"]["branch_scoped"]),
                len(m["bindings"]["classifiers"]),
                ", ".join(m["bindings"]["classifiers"])))


def _gated(m):
    """The numbers `--check` ratchets: none may grow."""
    return {
        "field_write_a": m["field_write"]["a_constructions"],
        "field_write_b": m["field_write"]["b_kind_tests"]["conversion"],
        "convert_c": (m["c_convert"]["kind_tests"]
                      + m["c_convert"]["sinkpos_reads"]
                      + m["c_convert"]["lc_params"]),
        "audit_d": len(m["d_audit"]["unexplained"]),
        "return_a": m["return"]["a_constructions"],
        "return_b": m["return"]["b_conversion"],
        "return_b_read": m["return"]["b_read"],
        "return_d": len(m["return"]["d_unexplained"]),
        "return_a_landing2": m["return"]["a_landing2"],
        "return_b_landing2": m["return"]["b_landing2"],
        "binding_convert": len(m["c_convert"]["binding_test_sites"]),
        "binding_field_write": len(
            m["field_write"]["binding_test_sites"]),
        "binding_return": len(m["return"]["binding_test_sites"]),
        "source_writers": len(m["source"]["source_writers"]),
        "held_form_reads": len(m["source"]["held_form_reads"]),
        "form_kwargs": m["source"]["form_kwargs"],
        "field_classifiers": len(m["source"]["field_classifiers"]),
        "binding_writers": len(m["bindings"]["writers"]),
        "binding_sets_branch_scoped": len(m["bindings"]["branch_scoped"]),
        "binding_classifiers": len(m["bindings"]["classifiers"]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--audit", action="store_true",
                    help="print every audited function that branches")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--update", action="store_true")
    args = ap.parse_args()
    m = measure()
    if args.update:
        with open(EXPECTED, "w") as fh:
            json.dump(_gated(m), fh, indent=1, sort_keys=True)
            fh.write("\n")
    if args.json:
        out = dict(m)
        if not args.audit:
            out.pop("_audit_rows")
        json.dump(out, sys.stdout, indent=1)
        print()
    if args.audit:
        for r in m["_audit_rows"]:
            print("%-60s %-12s %s" % (r["function"],
                                      "ok" if r["allowed"] else "UNEXPLAINED",
                                      " ".join(r["branches"][:6])))
    print(headline(m))
    if args.check:
        with open(EXPECTED) as fh:
            want = json.load(fh)
        got = _gated(m)
        worse = {k: (want[k], got[k]) for k in want if got.get(k, 0) > want[k]}
        if worse:
            print("GREW: %s" % worse)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
