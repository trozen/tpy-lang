"""Cutover gate: the structural skeleton must not grow new calls into the AST
body emitters.

Gate D3 fixed the migration's end state as bodies-only -- `codegen_cpp` keeps
the structural skeleton (module driver, headers, signatures, record/protocol/
enum drivers, the ctor member-init driver, the resumable and simple-generator
frames, type rendering) as the permanent printer layer, and the AST BODY
emitters (`expressions`, `statements`, `match`, `builtins`) are deleted in one
cutover commit. That deletion is a wholesale removal only while every skeleton
entry into those four modules is either a routing seam or an arm that the
routed path never takes; an entry that survives routing would leave a
permanent hybrid at expression granularity and turn the cutover into
archaeology.

The scan below is the mechanical form of that property. Each entry is frozen
with the disposition that makes it acceptable today:

  SEAM     the routing entry point itself -- it dispatches to THIR and only
           reaches the AST emitter when the body did not lower.
  AST_ARM  guarded by a routing check (`thir_* is None` / `leaf is None`);
           unreachable once bodies all route, deleted with the AST path.
  OPEN     fires on the ROUTED path too. These are the cutover gate: the
           gate holds when no OPEN entry remains.

An entry is keyed by (module, enclosing function, dotted call chain), so
several calls in one function collapse into one entry with a count > 1. When
those occurrences do not share a disposition the entry carries a tuple with
one disposition per occurrence, in source order -- otherwise a single guarded
arm next to an open one would read as wholly open (or wholly discharged).

Adding a call fails this test on purpose. Discharging one means deleting its
entry here in the same change.

KNOWN LIMIT -- ONE DIRECTION ONLY. The scan walks skeleton -> body-emitter
calls. It does not see the reverse dependency: THIR lowering IMPORTS from the
four deleted modules at production sites (predicates and constants pulled out
of `expressions` / `builtins`, and the `match` helpers), and nothing scans
that. An empty OPEN set therefore means "no skeleton call survives routing",
NOT "the four modules are unreferenced". Those imports have to be inventoried
and relocated separately before the deletion commit -- the shared home for
what both layers need is `emit_prims.py`, guarded by
`test_shared_prims_module_never_names_a_body_emitter` below.

Also out of scope here, and easy to under-count when scoping the cutover: the
deletion takes THREE cross-path detectors with it, not just the corpus
byte-diff. `move_audit.py` and `binding_audit.py` are dual-path joins whose
AST-SIDE recorder lives inside the emitter being deleted, so each loses one of
its two sides. `dualgen.py` has the same problem -- it needs two authors.
"""
from __future__ import annotations

import ast
from pathlib import Path

# The four modules that ARE the AST body emitter. A call from one of them into
# another is internal; only calls from the skeleton are the gate's business.
BODY_EMITTER_MODULES = ("expressions", "statements", "match", "builtins")

# The attribute names by which a skeleton object reaches those modules'
# generators (`self.expressions`, `self.statements`, `self.statements.match`,
# `self.statements.builtins`).
BODY_EMITTER_ATTRS = frozenset(BODY_EMITTER_MODULES)

SKELETON_MODULES = (
    "generator.py", "records.py", "functions.py", "protocols.py", "types.py",
    "gen_async.py", "gen_generators.py", "resumable_cfg.py", "context.py",
    "forms.py", "param_const.py", "int_literals.py", "variant_access.py",
    "string_dispatch.py", "type_resolution.py", "emit_prims.py",
)

# The shared home for the predicates, type decisions and fragment renders both
# the skeleton and the AST body emitter need. It survives the cutover, so it
# may not depend on anything that does not (see `test_emit_prims_*` below).
SHARED_PRIMS_MODULE = "emit_prims.py"

SEAM = "SEAM"
AST_ARM = "AST_ARM"
OPEN = "OPEN"

# (module, enclosing function, dotted call chain)
#   -> (count, disposition | one disposition per occurrence in source order)
FROZEN_SITES: dict[tuple[str, str, str], tuple[int, str | tuple[str, ...]]] = {
    # -- records.py ------------------------------------------------------
    # Class-constant defaults now lower through `tpyc/thir/constants.py`;
    # this call is the arm taken only when the constant did not lower.
    ("records.py", "_gen_record_decl", "self.expressions.gen_expr"):
        (1, AST_ARM),
    # The ctor member-init extraction runs only when the ctor did not lower
    # (`_emit_ctor_tail` returns early on a THIRConstructor).
    ("records.py", "_extract_base_inits", "self.expressions.gen_expr"):
        (1, AST_ARM),
    ("records.py", "_extract_field_inits", "self.expressions.gen_expr"):
        (1, AST_ARM),
    ("records.py", "_extract_field_inits",
     "self.expressions._view_source_to_owned"): (1, AST_ARM),
    ("records.py", "_extract_field_inits",
     "self.expressions._is_last_use_movable"): (1, AST_ARM),

    # -- functions.py ----------------------------------------------------
    # `StatementGenerator.gen_body` IS the per-body routing seam: it emits
    # from THIR when the body lowered and only then falls through to the AST
    # walk. At cutover the dispatch collapses into a direct THIR emit.
    ("functions.py", "gen_function_def", "self.statements.gen_body"):
        (2, SEAM),
    ("functions.py", "_gen_literal_specialized_function",
     "self.statements.gen_body"): (1, SEAM),
    ("functions.py", "_gen_overload_specialized_function",
     "self.statements.gen_body"): (1, SEAM),
    ("functions.py", "_gen_method_overload", "self.statements.gen_body"):
        (1, SEAM),
    ("functions.py", "gen_body", "self.statements.gen_body"): (1, SEAM),
    # Final-global initializers lower through `tpyc/thir/constants.py`; this
    # call is the arm taken only when the constant did not lower.
    ("functions.py", "_gen_final_init_expr",
     "self.statements.expressions.gen_expr"): (1, AST_ARM),
    # The module-init body's AST arm (`thir_top_level is None`).
    ("functions.py", "gen_module_init", "self.statements._gen_buffered_body"):
        (1, AST_ARM),

    # -- gen_generators.py (simple-generator lambda peephole) -------------
    # Every leaf-delegation site is guarded by `leaf is None`.
    ("gen_generators.py", "_gen_simple_while_generator",
     "self.expressions.gen_truthy_expr"): (1, AST_ARM),
    ("gen_generators.py", "_gen_simple_while_generator",
     "self.statements.gen_stmt"): (2, AST_ARM),
    ("gen_generators.py", "_gen_simple_while_generator",
     "self.statements.gen_yield_value"): (1, AST_ARM),
    ("gen_generators.py", "_gen_simple_for_generator",
     "self.expressions.gen_expr"): (2, AST_ARM),
    ("gen_generators.py", "_gen_simple_for_generator",
     "self.statements.gen_stmt"): (4, AST_ARM),
    ("gen_generators.py", "_gen_simple_for_generator",
     "self.statements.gen_yield_value"): (2, AST_ARM),
    ("gen_generators.py", "_gen_simple_for_yield_body",
     "self.statements.gen_stmt"): (2, AST_ARM),
    ("gen_generators.py", "_gen_simple_for_yield_body",
     "self.statements.gen_yield_value"): (1, AST_ARM),
    ("gen_generators.py", "_setup_body_scope", "self.statements.gen_body"):
        (1, AST_ARM),

    # -- gen_async.py (resumable frame skeleton) --------------------------
    # Leaf-guarded renders: the routed body swaps these for the leaf emitter.
    ("gen_async.py", "gen_coro_finally_top_def", "self.statements.gen_stmt"):
        (1, AST_ARM),
    ("gen_async.py", "_walk_inline", "self.expressions.gen_truthy_expr"):
        (1, AST_ARM),
    ("gen_async.py", "_emit_match_dispatch", "self.statements.match.gen_match"):
        (1, AST_ARM),
    ("gen_async.py", "_emit_with_ctx_bind", "self.expressions.gen_expr"):
        (1, AST_ARM),
    ("gen_async.py", "_for_src_access", "self.expressions.render_for_iterable"):
        (1, AST_ARM),
    ("gen_async.py", "_emit_async_for_iter_setup", "self.expressions.gen_expr"):
        (1, AST_ARM),
    ("gen_async.py", "_emit_for_range_setup",
     "self.statements.builtins.gen_range_args"): (1, AST_ARM),
    ("gen_async.py", "_for_src_expr", "self.expressions.gen_expr"):
        (1, AST_ARM),
    ("gen_async.py", "_suspend_expr_cpp", "self.expressions.gen_expr"):
        (1, AST_ARM),
    ("gen_async.py", "_gen_coro_emplace_arg", "self.expressions.gen_expr"):
        (1, AST_ARM),
    ("gen_async.py", "_gen_coro_emplace_arg",
     "self.expressions._gen_optional_ptr_arg"): (1, AST_ARM),
    ("gen_async.py", "_gen_coro_emplace_arg",
     "self.expressions._gen_dynamic_protocol_arg"): (1, AST_ARM),
    ("gen_async.py", "_gen_coro_emplace_arg", "self.expressions._gen_union_arg"):
        (1, AST_ARM),
    ("gen_async.py", "_gen_coro_emplace_arg", "self.expressions.gen_call_arg"):
        (1, AST_ARM),
    ("gen_async.py", "_emit_generator_yield",
     "self.statements.gen_yield_value"): (1, AST_ARM),
    # A routed frame emits its nested-def member bodies through the leaf
    # emitter (`leaf.emit_nested_def_body`, lowered under the member scope
    # at frame lowering); this call is the fallback frame's arm.
    ("gen_async.py", "gen_coro_finally_top_def",
     "self.statements.gen_nested_def_body"): (1, AST_ARM),
    # Frame scaffolding that runs for routed bodies too. What is left after
    # the emit-primitive relocation is not primitive: `_walk_inline`'s
    # ReturnT `gen_stmt` dispatch walks a body,
    # `_extra_template_args_for_await` dispatches an arbitrary expression,
    # and `_make_async_return` reaches `gen_expr` through its deferred-return
    # recipe. Where each belongs post-cutover is an open decision, not a
    # relocation.
    ("gen_async.py", "_thir_resumable_leaf_emitter",
     "self.statements._make_async_return"): (1, OPEN),
    ("gen_async.py", "_thir_resumable_leaf_emitter",
     "self.statements._make_generator_resumable_return"): (1, OPEN),
    ("gen_async.py", "_extra_template_args_for_await",
     "self.expressions.gen_expr"): (1, OPEN),
    # Three occurrences, one disposition each: the BB-statement walk and the
    # RaiseT terminator both sit in the `else` of a `thir_resumable_leaf is
    # not None` check, so a routed body emits them through the leaf emitter.
    # Only the ReturnT terminator dispatches unconditionally -- gen_stmt is
    # what runs the active finally chain around the Poll<T>::ready.
    ("gen_async.py", "_walk_inline", "self.statements.gen_stmt"):
        (3, (AST_ARM, OPEN, AST_ARM)),
}


def _chain(node: ast.AST) -> list[str] | None:
    """The dotted attribute chain of a Call's func, root first."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return list(reversed(parts))
    return None


def _owners(tree: ast.AST) -> dict[int, str]:
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                owner.setdefault(ln, node.name)
    return owner


def scan_skeleton() -> dict[tuple[str, str, str], int]:
    """(module, enclosing function, call chain) -> occurrences."""
    found: dict[tuple[str, str, str], int] = {}
    root = Path(__file__).parent
    for mod in SKELETON_MODULES:
        tree = ast.parse((root / mod).read_text())
        owner = _owners(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            chain = _chain(node.func)
            if chain is None or len(chain) < 3 or chain[0] != "self":
                continue
            if not (BODY_EMITTER_ATTRS & set(chain[1:-1])):
                continue
            key = (mod, owner.get(node.lineno, "<module>"), ".".join(chain))
            found[key] = found.get(key, 0) + 1
    return found


def test_no_new_skeleton_calls_into_body_emitters() -> None:
    """The skeleton's entries into the AST body emitters are frozen.

    A new one has to be classified (and, if it survives routing, discharged)
    before cutover -- that is what keeps the deletion commit wholesale.
    """
    found = scan_skeleton()
    expected = {k: n for k, (n, _d) in FROZEN_SITES.items()}
    added = {k: n for k, n in found.items() if k not in expected}
    removed = {k: n for k, n in expected.items() if k not in found}
    changed = {k: (expected[k], found[k]) for k in found.keys() & expected.keys()
               if found[k] != expected[k]}
    assert not added, f"new skeleton -> body-emitter call sites: {added}"
    assert not removed, (
        f"frozen skeleton -> body-emitter call sites are gone (delete their "
        f"FROZEN_SITES entries): {removed}")
    assert not changed, (
        f"occurrence counts changed (expected, found): {changed}")


def test_skeleton_never_aliases_a_body_emitter() -> None:
    """`self.expressions` / `self.statements` may only appear as the receiver
    of a call. Binding one to a local would route around the scan above."""
    root = Path(__file__).parent
    offenders: list[str] = []
    for mod in SKELETON_MODULES:
        tree = ast.parse((root / mod).read_text())
        owner = _owners(tree)
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                if (isinstance(child, ast.Attribute)
                        and child.attr in BODY_EMITTER_ATTRS
                        and isinstance(child.ctx, ast.Load)
                        and isinstance(child.value, ast.Name)
                        and child.value.id == "self"
                        and not isinstance(parent, ast.Attribute)
                        # `__init__` is the composition root's wiring: it
                        # hands the generators to each other, it does not
                        # emit through them.
                        and owner.get(child.lineno) != "__init__"):
                    offenders.append(
                        f"{mod}:{child.lineno} in "
                        f"{owner.get(child.lineno, '?')}")
    assert not offenders, (
        f"skeleton binds an AST body emitter to a name instead of calling "
        f"through it: {offenders}")


def test_skeleton_never_passes_a_body_emitter_method_as_a_value() -> None:
    """`self.statements.foo` may only appear as the func of a Call.

    Handing a bound body-emitter method to someone else (a callback, a
    dataclass field, a THIR seam argument) reaches the emitter on the routed
    path just as a direct call does, but `scan_skeleton` only walks Call
    nodes -- so such a reference is invisible to the freeze table. The
    resumable frame's `ast_finally_push` bridge was exactly that.
    """
    root = Path(__file__).parent
    offenders: list[str] = []
    for mod in SKELETON_MODULES:
        tree = ast.parse((root / mod).read_text())
        owner = _owners(tree)
        call_funcs = {id(n.func) for n in ast.walk(tree)
                      if isinstance(n, ast.Call)}
        outer_attr = {id(n.value) for n in ast.walk(tree)
                      if isinstance(n, ast.Attribute)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or id(node) in outer_attr:
                continue  # inner link of a longer chain -- judge the outermost
            chain = _chain(node)
            if chain is None or chain[0] != "self":
                continue
            if not (BODY_EMITTER_ATTRS & set(chain[1:-1])):
                continue
            if id(node) in call_funcs:
                continue
            offenders.append(
                f"{mod}:{node.lineno} in {owner.get(node.lineno, '?')}: "
                f"{'.'.join(chain)}")
    assert not offenders, (
        f"skeleton hands an AST body-emitter method to another layer instead "
        f"of calling it: {offenders}")


def test_shared_prims_module_never_names_a_body_emitter() -> None:
    """The shared emit-primitives module must not reach the four modules the
    cutover deletes -- not by import, and not through a collaborator either.

    This is what makes relocating a helper there an actual discharge rather
    than one more hop to the same place: a primitive that needed
    `expressions` would still be body emission wearing a different import.
    (A transitive import closure proves nothing here -- `context` is a hub
    that every module reaches -- so the check is on this module's own text:
    no import of the four, and no `.expressions` / `.statements` / `.match` /
    `.builtins` attribute anywhere, which is how a backdoor through `ctx`
    would have to spell itself.)
    """
    tree = ast.parse((Path(__file__).parent / SHARED_PRIMS_MODULE).read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = ({node.module.split(".")[0]} if node.module else set())
            names |= {a.name.split(".")[0] for a in node.names}
            for hit in sorted(names & set(BODY_EMITTER_MODULES)):
                offenders.append(f"line {node.lineno}: imports {hit}")
        elif isinstance(node, ast.Attribute) and node.attr in BODY_EMITTER_ATTRS:
            offenders.append(f"line {node.lineno}: reaches .{node.attr}")
    assert not offenders, (
        f"{SHARED_PRIMS_MODULE} reaches an AST body emitter: {offenders}")


def _dispositions(count: int, disp: str | tuple[str, ...]) -> tuple[str, ...]:
    return (disp,) * count if isinstance(disp, str) else disp


def test_frozen_site_dispositions_cover_every_occurrence() -> None:
    """A per-occurrence disposition tuple must have one entry per call."""
    bad = {k: (n, d) for k, (n, d) in FROZEN_SITES.items()
           if len(_dispositions(n, d)) != n}
    assert not bad, f"disposition tuple does not match the count: {bad}"


def test_cutover_gate_open_sites() -> None:
    """The cutover gate: zero skeleton calls into a body emitter that the
    ROUTED path still takes. Until that set is empty this test pins its exact
    membership, so discharging one is a visible, deliberate edit.
    """
    open_calls = sorted(
        (k, i) for k, (n, d) in FROZEN_SITES.items()
        for i, disp in enumerate(_dispositions(n, d)) if disp == OPEN)
    assert len(open_calls) == 4, (
        f"the cutover gate's OPEN set changed ({len(open_calls)} calls); "
        f"update the count when a site is discharged: {open_calls}")
