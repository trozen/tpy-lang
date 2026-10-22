"""What an escaping by-value closure's capture copies, and whether the body
binds it again after the closure exists -- the facts behind the
closure-capture warnings.

A `Callable` lambda and an escaping nested def own their captures: the C++
closure copies each one where it is created. CPython's closure reads the
variable itself, so the copy shows wherever the program changes the object
the capture refers to after the closure exists, and wherever the body (a
later statement, or a later iteration of an enclosing loop) binds the name
again before a call reads it.

The copy verdict (`CaptureCopy`) is DEFINITE only on positive evidence that
the capture copies a reference-type object: the binding the closure reads
(`ReadBinding`, its captured read's `outer`) holds the object by value. A
binding whose C++ form sema does not settle -- a pointer-repr Optional, a
pointer-variant union, a rebound or hoisted local (a slot its binds
re-point), a tuple outside a storing container's element, a binding of an
`@error_return` call's result, an open `T` -- may share the object, so it
gets the hedged verdict. A pointer or a view held in the capture refers to
its object rather than holding it, so it is no evidence at all. Every
warned statement is then true of the generated C++, which this module never
changes.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from ..identity_map import IdentityMap
from ..parse.nodes import (TpyCall, TpyComprehensionGenerator, TpyExpr,
                           TpyForEach, TpyLambda, TpyMethodCall, TpyName,
                           TpyNestedDef, TpyStmt, TpyTupleUnpack, TpyWhile,
                           value_capture_lambdas, walk_body_stmts,
                           walk_expr_tree)
from ..prescan import collect_fact_kills, loop_bindings_of
from ..type_def_registry import has_view_param_form
from ..typesys import (FrameType, OptionalType, TpyType,
                       TupleType, UnionType, is_dyn_protocol,
                       is_ptr_variant_union, param_takes_ownership,
                       pointer_repr_optional, unwrap_qualifiers,
                       unwrap_ref_type, unwrap_send_sync)
from .binding_ownership import ReadBinding, ReadKind
from .own_copy import contains_reference_type, type_param_names

if TYPE_CHECKING:
    from .context import SemanticContext

Closure = TpyLambda | TpyNestedDef


class CaptureCopy(Enum):
    """What a by-value capture copies (module docstring)."""
    DEFINITE = "definite"  # a reference-type object, for certain
    HEDGED = "hedged"      # a reference type is involved; sharing not ruled out
    COST = "cost"          # no reference object, an expensive immutable value
    NONE = "none"


class CopyReason(Enum):
    """Why an escaping nested def copies a capture rather than moving it,
    as its move analysis found; the text completes the def's warning."""
    USED_AFTER = (" (used after closure definition, preventing move)."
                  " Reorder code so the closure is the last use, or use"
                  " copy() to make the copy explicit")
    ALIASES = (", which aliases storage it does not own; use copy() to"
               " make the copy explicit")


class ClosureReads:
    """What a closure reads of the names it captures: the binding each
    resolves to where the closure is written (`closure_bindings`), and the
    type every read of it has, when all agree (`read_types`)."""

    def __init__(self) -> None:
        self.bindings: dict[str, ReadBinding | None] = {}
        self.read_types: dict[str, TpyType | None] = {}


def closure_bindings(ctx: 'SemanticContext', closure: Closure) -> ClosureReads:
    """The binding each name the closure captures resolves to where the
    closure is written: its captured reads' `outer` (a def's own nested
    defs' reads included), followed past every enclosing lambda to the
    body that binds it. None for a name every read
    of which resolves inside the closure (a comprehension variable of its
    body shadowing it): the closure never reads the captured binding. A
    module-level name the module does not register as a global
    (`global_scope`) is a local of the module's init body -- a block's
    binding the C++ declares there, not namespace storage.

    `read_types`: per captured name, the type each of its reads has when
    they all have the same one -- a capture narrowed where the closure is
    written (the C++ captures the narrowed member); None when they differ (a
    closure narrowing its capture itself reads the whole value first)."""
    reads = ClosureReads()
    out = reads.bindings
    types = reads.read_types

    def visit(e: TpyExpr) -> bool:
        b = ctx.read_bindings.get(e) if isinstance(e, TpyName) else None
        if b is not None and b.kind is ReadKind.CAPTURED:
            t = ctx.get_expr_type(e)
            types[e.name] = (t if e.name not in types or types[e.name] == t
                             else None)
            if out.get(e.name) is None:
                while b.kind is ReadKind.CAPTURED and b.outer is not None:
                    b = b.outer
                out[e.name] = b
        elif b is not None and out.get(e.name) is None:
            out[e.name] = None
        if isinstance(e, TpyLambda):
            walk_expr_tree(e.body, visit)
            return False
        return True

    def walk(stmts: list[TpyStmt]) -> None:
        walk_body_stmts(stmts, lambda e: walk_expr_tree(e, visit),
                        lambda s: walk(s.func.body)
                        if isinstance(s, TpyNestedDef) else None)

    if isinstance(closure, TpyLambda):
        walk_expr_tree(closure.body, visit)
    else:
        walk(closure.func.body)
    return reads


def capture_type(closure: Closure, name: str,
                 binding: ReadBinding | None) -> TpyType | None:
    """The type of the binding capture `name` reads: a parameter's declared
    type, an enclosing lambda's parameter type, else the closure's frame
    slot for it (its type where the closure is written)."""
    if binding is not None and binding.kind is ReadKind.PARAM:
        return next((t for p, t in binding.node.params if p == name), None)
    if binding is not None and binding.kind is ReadKind.LAMBDA:
        lam = binding.node
        i = lam.param_names.index(name)
        types = list(lam.inferred_param_types)
        return types[i] if i < len(types) else None
    frame = closure.frame_type
    if isinstance(frame, FrameType):
        for slot in frame.slots:
            if slot.name == name:
                return slot.type
    return None


def classify_capture(ctx: 'SemanticContext', name: str,
                     binding: ReadBinding | None, ctype: TpyType | None,
                     read_type: TpyType | None = None) -> CaptureCopy:
    """The copy verdict of by-value capture `name` (module docstring), in
    the body whose function state `ctx.func` is. `read_type`: the type the
    closure's reads of it have (`ClosureReads.read_types`); a value capture
    narrowed to a member copies that member only. A reference capture keeps
    the binding's verdict: its narrowed member's form is not settled here."""
    if not isinstance(ctype, TpyType):
        return CaptureCopy.NONE
    typ = unwrap_send_sync(unwrap_ref_type(ctype))
    bare = unwrap_qualifiers(typ)
    bare = ctx.view_storage_verdict(bare) or bare
    if not contains_reference_type(bare, held_only=True):
        held = bare
        if isinstance(read_type, TpyType):
            held = unwrap_qualifiers(unwrap_send_sync(
                unwrap_ref_type(read_type)))
            held = ctx.view_storage_verdict(held) or held
        if (not _passed_as_view(ctx, binding, typ)
                and _copies_expensive(held)):
            return CaptureCopy.COST
        return CaptureCopy.NONE
    if (contains_reference_type(bare, open_params_as_values=True,
                                held_only=True)
            and pointer_repr_optional(typ) is None
            and not is_ptr_variant_union(typ)
            and not type_param_names(bare)
            and not is_dyn_protocol(bare)
            and _holds_by_value(ctx, name, binding, bare)):
        return CaptureCopy.DEFINITE
    return CaptureCopy.HEDGED


def _copies_expensive(t: TpyType) -> bool:
    """Whether a copy of value `t` allocates (`is_expensive_copy`), through
    the members of an Optional, a union and a tuple, which hold them in
    place. Not `is_expensive_copy` of the wrapper alone, which answers for
    its own shape (BUGS.md#optional-union-expensive-member-not-expensive)."""
    t = unwrap_qualifiers(t)
    if isinstance(t, (OptionalType, UnionType, TupleType)):
        return any(_copies_expensive(m) for m in t.inner_types())
    return t.is_expensive_copy()


def _passed_as_view(ctx: 'SemanticContext', binding: ReadBinding | None,
                    typ: TpyType) -> bool:
    """A parameter whose passing form is a view of the caller's buffer
    (`has_view_param_form`: `str`, `bytes`, an Optional of one) and that
    takes no ownership: its capture copies the view. Not one the body
    rebinds, which then holds a value of its own."""
    if (binding is None or param_takes_ownership(typ)
            or not has_view_param_form(typ)):
        return False
    if binding.kind is ReadKind.LAMBDA:
        return True
    if binding.kind is not ReadKind.PARAM:
        return False
    fs = ctx.func
    # An enclosing function's parameter: its rebinds are not in this state,
    # so it reads as passed.
    return (binding.node is not fs.current_function
            or not (binding.name in fs.current_rebound_params
                    or _slot_bound(fs, binding.name)))


def _holds_by_value(ctx: 'SemanticContext', name: str,
                    binding: ReadBinding | None, bare: TpyType) -> bool:
    """Whether the binding holds its object by value, so the capture
    copies it: a local bound once, never hoisted, from no `@error_return`
    call; a parameter never rebound; an iteration step or comprehension
    variable; an enclosing lambda's parameter. A tuple holds its reference
    elements by value only as an element of a container that stores them
    (an iteration step, a comprehension variable); any other tuple may be
    the borrow form, a tuple of pointers."""
    if binding is None:
        return False
    fs = ctx.func
    kind = binding.kind
    is_tuple = isinstance(bare, TupleType)
    if kind is ReadKind.LAMBDA:
        return not is_tuple
    if kind in (ReadKind.LOCAL, ReadKind.PARAM):
        if (is_tuple or binding.node is not fs.current_function
                or _slot_bound(fs, name)):
            return False
        if kind is ReadKind.PARAM:
            return name not in fs.current_rebound_params
        decl = fs.var_decl_by_name.get(name)
        return not _calls_error_return(decl.init if decl is not None
                                       else None)
    if kind is ReadKind.SITE:
        node = binding.node
        if isinstance(node, TpyComprehensionGenerator):
            return not is_tuple or _stores_elements(ctx, node.iterable)
        if isinstance(node, TpyForEach):
            return not _slot_bound(fs, name) and (
                not is_tuple or _stores_elements(ctx, node.iterable))
        if isinstance(node, TpyTupleUnpack) and node.is_loop_head:
            return not _slot_bound(fs, name) and not is_tuple
    return False


def _stores_elements(ctx: 'SemanticContext', container: TpyExpr) -> bool:
    """Whether `container` holds its elements in storage of its own that an
    iteration step refers to, so a tuple element is the storage form;
    another iterator may hand out a tuple of borrows."""
    t = ctx.get_expr_type(container)
    if not isinstance(t, TpyType):
        return False
    bare = unwrap_qualifiers(t)
    return not isinstance(bare, TupleType) and bare.subscript_borrows()


def _slot_bound(fs: object, name: str) -> bool:
    """A local held in a slot its binds re-point (rebound or hoisted): its
    capture may copy the pointer. Read off the function state, since the
    ownership table is written after the closure pass."""
    return (name in fs.current_reassigned_vars or name in fs.hoisted_vars
            or name in fs.escape_hoisted_vars)


def _calls_error_return(init: TpyExpr | None) -> bool:
    """Whether a binding's value comes through an `@error_return` call,
    whose result the binding may refer to rather than hold."""
    if init is None:
        return False
    found = False

    def visit(e: TpyExpr) -> bool:
        nonlocal found
        if isinstance(e, (TpyCall, TpyMethodCall)):
            fi = getattr(e, "resolved_function_info", None)
            found = found or (fi is not None and bool(fi.error_return_type))
        return not found

    walk_expr_tree(init, visit)
    return found


# --- The warnings ------------------------------------------------------

def closure_noun(closure: Closure) -> str:
    """How a warning names the closure: a lambda by its line, a nested
    def by its name."""
    if isinstance(closure, TpyNestedDef):
        return f"nested function '{closure.func.name}'"
    line = closure.loc.line if closure.loc is not None else "?"
    return f"lambda at line {line}"


def copy_message(closure: Closure, name: str, verdict: CaptureCopy,
                 reason: CopyReason | None = None) -> str:
    """The copy warning of capture `name`, worded for `verdict`; a nested
    def's capture its move analysis gave `reason` keeps that wording."""
    if reason is not None and isinstance(closure, TpyNestedDef):
        fname = closure.func.name
        if verdict is CaptureCopy.HEDGED:
            return (f"Escaping closure '{fname}' may keep its own copy of"
                    f" local '{name}'{reason.value}")
        return f"Escaping closure '{fname}' copies local '{name}'{reason.value}"
    subject = closure_noun(closure)
    if verdict is CaptureCopy.DEFINITE:
        return (f"{subject} copies the object '{name}' refers to when it is"
                f" created; changes to that object after this are not seen"
                f" by it (a Python closure sees them)")
    if verdict is CaptureCopy.HEDGED:
        return (f"{subject} may keep its own copy of '{name}'; changes made"
                f" after it is created may not be seen")
    return f"{subject} copies '{name}' when it is created"


def loop_message(closure: Closure, name: str, definite: bool) -> str:
    """The loop-rebinding warning of capture `name`; `definite` when the
    closure certainly keeps its own iteration's value
    (`_keeps_own_iteration`)."""
    noun = ("lambda" if isinstance(closure, TpyLambda)
            else closure_noun(closure))
    if definite:
        return (f"'{name}' is bound again on each iteration; the {noun}"
                f" keeps the value from its own iteration, while a Python"
                f" closure called after the loop sees the last one")
    return (f"'{name}' may be bound again on each iteration; the {noun} may"
            f" keep the value from its own iteration, while a Python closure"
            f" called after the loop sees the last one")


def rebound_after_message(name: str) -> str:
    return (f"Escaping closure captures local '{name}' by value, but"
            f" '{name}' is reassigned after the closure is created; the"
            f" closure keeps the value from capture time (CPython would"
            f" observe the later value). Capture a fresh local that is"
            f" not reassigned (e.g. `snap = {name}`), or use copy() to"
            f" make the snapshot explicit.")


def by_value_captures(closure: Closure,
                      bindings: dict[str, ReadBinding | None]) -> list[str]:
    """The names `closure` copies and reads: a by-value lambda's captures,
    an escaping def's captures it neither moves, references nor declares
    nonlocal. Not a method's receiver, which the closure captures as
    itself, nor a name the closure's body only shadows
    (`closure_bindings`), nor module storage, which the closure reads in
    place as a Python closure does."""
    if isinstance(closure, TpyLambda):
        names = (list(closure.captured_names) if closure.captures_by_value
                 else [])
    elif closure.escapes:
        names = [n for n in closure.captured_names
                 if n not in closure.ref_captures
                 and n not in closure.move_captures
                 and n not in closure.nonlocal_names]
    else:
        names = []
    return [n for n in names
            if not (n in bindings and (bindings[n] is None
                                       or bindings[n].kind
                                       in (ReadKind.RECEIVER,
                                           ReadKind.MODULE)))]


def warn_once(ctx: 'SemanticContext', message: str, node: Closure) -> None:
    """Emit a capture warning once per source position: the two bodies of
    an `@auto_readonly` pair are one source occurrence."""
    loc = node.loc
    key = (loc.line, loc.column, message) if loc is not None else None
    if key is not None:
        if key in ctx.capture_warnings:
            return
        ctx.capture_warnings.add(key)
    ctx.warning(message, node)


def warn_closure_captures(ctx: 'SemanticContext', body: list[TpyStmt],
                          def_reasons: IdentityMap | None = None) -> None:
    """Every closure-capture warning of the by-value closures `body`
    creates (nested lambdas included; a nested def's own body is its own):
    what each capture copies, a capture the body binds again at a later
    top-level statement, and one a loop around the closure binds again
    each iteration. `def_reasons`: per escaping nested def, the
    `CopyReason` of each capture its move analysis copies."""
    defs: list[TpyNestedDef] = []
    loops: list[TpyStmt] = []

    def on_stmt(s: TpyStmt) -> None:
        if isinstance(s, TpyNestedDef):
            defs.append(s)
        elif isinstance(s, (TpyForEach, TpyWhile)):
            loops.append(s)

    walk_body_stmts(body, lambda e: None, on_stmt)
    closures: list[Closure] = [*defs, *(lam for lam, _ in
                                        value_capture_lambdas(body,
                                                              nested=True))]
    captures: IdentityMap = IdentityMap()
    for closure in closures:
        reads = closure_bindings(ctx, closure)
        bindings = reads.bindings
        names = by_value_captures(closure, bindings)
        captures[closure] = (names, bindings)
        reasons = (def_reasons.get(closure) if def_reasons is not None
                   else None) or {}
        for name in names:
            binding = bindings.get(name)
            verdict = classify_capture(ctx, name, binding,
                                       capture_type(closure, name, binding),
                                       reads.read_types.get(name))
            if verdict is not CaptureCopy.NONE:
                warn_once(ctx, copy_message(closure, name, verdict,
                                            reasons.get(name)), closure)
    for closure in closures:
        names, bindings = captures[closure]
        for name in names:
            binding = bindings.get(name)
            if (binding is not None and binding.kind is ReadKind.SITE
                    and isinstance(binding.node, TpyComprehensionGenerator)):
                warn_once(ctx, loop_message(closure, name, True), closure)
    _warn_rebound_after(ctx, body, captures)
    for loop in loops:
        _warn_loop_rebinds(ctx, loop, captures)


def _warn_rebound_after(ctx: 'SemanticContext', body: list[TpyStmt],
                        captures: IdentityMap) -> None:
    """A closure at a top-level statement of `body` whose capture a later
    top-level statement binds again: the closure keeps the value it
    captured. A nested def bound deeper has no position here and is
    skipped."""
    later: set[str] = set()
    found: list[tuple[Closure, str]] = []
    for stmt in reversed(body):
        if isinstance(stmt, TpyNestedDef):
            at: list[Closure] = [stmt]
        else:
            at = [lam for lam, _ in value_capture_lambdas([stmt])]
        for closure in reversed(at):
            names, _bindings = captures.get(closure) or ([], {})
            stale = [n for n in names if n in later]
            if stale:
                found.append((closure, stale[0]))
        later |= collect_fact_kills([stmt]).names
    for closure, name in reversed(found):
        warn_once(ctx, rebound_after_message(name), closure)


def _warn_loop_rebinds(ctx: 'SemanticContext', loop: TpyStmt,
                       captures: IdentityMap) -> None:
    """A closure in `loop`'s body whose capture a pass of the loop binds
    again (`LoopBindings`): each closure may keep its own iteration's
    value. Not an escape-hoisted local, which every closure reads through
    one slot, as a Python cell does."""
    facts = loop_bindings_of(ctx.loop_bindings, loop, ctx.write_summaries)
    rebound = facts.body | facts.target
    fs = ctx.func
    inside: list[Closure] = [lam for lam, _ in
                             value_capture_lambdas(loop.body, nested=True)]
    walk_body_stmts(loop.body, lambda e: None,
                    lambda s: inside.append(s)
                    if isinstance(s, TpyNestedDef) else None)
    for closure in inside:
        names, bindings = captures.get(closure) or ([], {})
        for name in names:
            binding = bindings.get(name)
            if (binding is None or name not in rebound
                    or name in fs.escape_hoisted_vars):
                continue
            if (binding.kind is ReadKind.SITE
                    or (binding.kind in (ReadKind.LOCAL, ReadKind.PARAM)
                        and binding.node is fs.current_function)):
                definite = _keeps_own_iteration(ctx, closure, name, binding,
                                                facts.target)
                warn_once(ctx, loop_message(closure, name, definite),
                          closure)


def _keeps_own_iteration(ctx: 'SemanticContext', closure: Closure,
                         name: str, binding: ReadBinding,
                         targets: frozenset[str]) -> bool:
    """Whether each closure certainly keeps its own iteration's value of
    rebound capture `name`: a loop's own variable, a value local the loop
    binds again, or an object the closure certainly copies
    (`CaptureCopy.DEFINITE`). Any other reference or Optional local may be
    held in one slot each pass writes in place, which every closure then
    reads as a Python cell is read; sema does not settle which."""
    if binding.kind is ReadKind.SITE or name in targets:
        return True
    t = capture_type(closure, name, binding)
    if not isinstance(t, TpyType):
        return False
    bare = unwrap_qualifiers(unwrap_send_sync(unwrap_ref_type(t)))
    if (not isinstance(bare, OptionalType)
            and not contains_reference_type(bare, held_only=True)):
        return True
    return classify_capture(ctx, name, binding, t) is CaptureCopy.DEFINITE
