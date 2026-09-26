"""The one verdict for an INFERRED join of two value types.

An inferred join -- a ternary's arms, the operands of a value-position
`and` / `or`, the peers of a list / dict / set literal, the elements an
unannotated container learns from its uses, the bindings of one unannotated
local across statements -- has no declared type to convert into, so both
operands must settle on one type they already share. An integer meeting a
float has none: CPython keeps whichever value is picked (`a if c else 2.5`
is the int 3, `[1, 2.5]` keeps its int), so one float type for both would
silently print `3.0`; and a number has exactly one type, so the join never
becomes an `int32 | float` union either. A DECLARED float slot is not a
join: the annotation converts each value (the numeric-tower rule), so a
caller with such a slot does not ask here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Callable, Sequence

from ..parse.nodes import (TpyArrayLiteral, TpyDictLiteral, TpyExpr,
                           TpyFloatLiteral, TpyIntLiteral, TpyListRepeat,
                           TpyNamedExpr, TpySetLiteral, TpyTupleLiteral,
                           TpyUnaryOp)
from ..typesys import (BIGINT, NominalType, OptionalType, TpyType, TupleType,
                       is_any_float_type, is_any_int_type, is_float_type,
                       literal_peer_children, resolve_int_literals,
                       unwrap_readonly)
from ..type_def_registry import is_array, is_dict, is_list, is_set
from ..value_category import peel_coerce
from ..prescan import storage_spelling


class JoinOutcome(Enum):
    JOINED = auto()
    INT_FLOAT_MIX = auto()
    INCOMPATIBLE = auto()


@dataclass(frozen=True)
class InferredJoin:
    outcome: JoinOutcome
    joined: TpyType | None = None
    # The integer and float that met, which of the two operands held the
    # integer, and whether they met below the operands (a tuple or literal
    # element) -- a top-level mix can be fixed by wrapping one operand.
    int_side: TpyType | None = None
    float_side: TpyType | None = None
    int_first: bool = True
    nested: bool = False
    # The child indices (`literal_peer_children` / type-argument order) from
    # the operands down to the pair that met, so a diagnostic can find the
    # source nodes that hold the integer.
    path: tuple[int, ...] = ()


def descend(mix: InferredJoin, index: int) -> InferredJoin:
    """`mix`, found at child `index` of the pair that was asked."""
    return replace(mix, nested=True, path=(index,) + mix.path)


def flipped(mix: InferredJoin) -> InferredJoin:
    """`mix` as seen with the two operands asked the other way round."""
    return replace(mix, int_first=not mix.int_first)


def join_inferred_value_types(
    a: TpyType, b: TpyType,
    join: Callable[[TpyType, TpyType], TpyType | InferredJoin | None],
) -> InferredJoin:
    """Join two operand types of an inferred join. `join` is the caller's
    structural join (the select join, or the literal peer-unify), asked
    once no integer meets a float anywhere `literal_peer_children`
    descends; None means the two have no common type. A join that descends
    further itself (a pending container pinned to a concrete one) returns
    the verdict it reached there."""
    mix = _find_int_float_mix(a, b, nested=False)
    if mix is not None:
        return mix
    joined = join(a, b)
    if isinstance(joined, InferredJoin):
        return joined
    if joined is None:
        return InferredJoin(JoinOutcome.INCOMPATIBLE)
    return InferredJoin(JoinOutcome.JOINED, joined)


def find_int_float_mix(a: TpyType, b: TpyType) -> InferredJoin | None:
    """The int/float mix that keeps two operand types from sharing one
    type, or None; the pre-check `join_inferred_value_types` runs."""
    return _find_int_float_mix(a, b, nested=False)


def _find_int_float_mix(a: TpyType, b: TpyType,
                        nested: bool) -> InferredJoin | None:
    a, b = unwrap_readonly(a), unwrap_readonly(b)
    if is_any_int_type(a) and is_any_float_type(b):
        return InferredJoin(JoinOutcome.INT_FLOAT_MIX, int_side=a,
                            float_side=b, int_first=True, nested=nested)
    if is_any_float_type(a) and is_any_int_type(b):
        return InferredJoin(JoinOutcome.INT_FLOAT_MIX, int_side=b,
                            float_side=a, int_first=False, nested=nested)
    pairs = literal_peer_children(a, b)
    if pairs is not None:
        for k, (x, y) in enumerate(pairs):
            mix = _find_int_float_mix(x, y, nested=True)
            if mix is not None:
                return descend(mix, k)
        return None
    return _same_generic_mix(a, b)


def _same_generic_mix(a: TpyType, b: TpyType) -> InferredJoin | None:
    """The mix between two instantiations of one generic type (`list[int32]`
    and `list[float]`, two dict literals) whose type arguments differ only
    by an integer meeting a float: nothing converts a concrete container's
    elements, so that is the whole reason the two share no type."""
    if not (isinstance(a, NominalType) and isinstance(b, NominalType)
            and a.qualified_name() == b.qualified_name()):
        return None
    ia, ib = a.inner_types(), b.inner_types()
    if len(ia) != len(ib):
        return None
    found: InferredJoin | None = None
    for k, (x, y) in enumerate(zip(ia, ib)):
        if unwrap_readonly(x) == unwrap_readonly(y):
            continue
        mix = _find_int_float_mix(x, y, nested=True)
        if mix is None:
            return None
        found = found or descend(mix, k)
    return found


def literal_leaf_mix(expr: TpyExpr, target: TpyType,
                     leaf_type: Callable[[TpyExpr], TpyType | None],
                     ) -> InferredJoin | None:
    """The int/float mix between the leaves of a container literal, each at
    its own type (`leaf_type`), and the concrete container `target`'s
    element types at the same positions -- the leaf first. A literal
    analyzed under a hint records its elements at the hint's type; its
    leaves keep their own."""
    e = peel_coerce(expr)
    t = unwrap_readonly(target)
    # The picked value of an `Optional` slot is the inner type's.
    if isinstance(t, OptionalType):
        t = unwrap_readonly(t.inner)
    children = _literal_children(e)
    if children is None:
        leaf = leaf_type(e)
        return (_find_int_float_mix(unwrap_readonly(leaf), t, nested=False)
                if leaf is not None else None)
    if isinstance(e, TpyTupleLiteral):
        if not (isinstance(t, TupleType)
                and len(t.element_types) == len(e.elements)):
            return None
        targets = t.element_types
    elif isinstance(t, NominalType) and (
            (isinstance(e, TpyDictLiteral) and is_dict(t))
            or (isinstance(e, TpySetLiteral) and is_set(t))
            or (isinstance(e, (TpyArrayLiteral, TpyListRepeat))
                and (is_list(t) or is_array(t)))):
        targets = t.type_args
    else:
        return None
    for k, child in children:
        mix = literal_leaf_mix(child, targets[k], leaf_type)
        if mix is not None:
            return descend(mix, k)
    return None


def declared_float_slot(slot: TpyType | None) -> TpyType | None:
    """The float type a declared slot converts an int/float pair into, so
    no inferred join is left to refuse: a float slot, or the float of a
    `float | None` one (the picked value is never None there)."""
    t = unwrap_readonly(slot) if slot is not None else None
    if isinstance(t, OptionalType):
        t = unwrap_readonly(t.inner)
    return t if t is not None and is_float_type(t) else None


def python_type_name(t: TpyType | None) -> str:
    """How a mix message names a type: an unresolved int literal is the
    Python `int` it still is, not whatever default width it would later
    settle on; a typed value keeps its own type."""
    if t is None:
        return "None"
    return str(resolve_int_literals(unwrap_readonly(t), BIGINT))


def operand_spelling(e: TpyExpr) -> str | None:
    """The source spelling of a simple operand -- a name, a field path, a
    numeric literal -- for a rewrite hint; None for anything longer, whose
    exact text the parse tree does not keep."""
    if isinstance(e, TpyNamedExpr):
        # Spelling the target alone would drop the binding from the rewrite.
        return None
    if isinstance(e, TpyIntLiteral):
        return str(e.value)
    if isinstance(e, TpyFloatLiteral):
        return repr(e.value)
    return storage_spelling(e)


def int_literal_spellings(mix: InferredJoin,
                          int_nodes: Sequence[TpyExpr]) -> list[str] | None:
    """The source spellings of the int literals that hold the integer side
    of `mix`, found below `int_nodes` along `mix.path` -- or None unless
    every node there is an int literal written in the source (`1`, `-1`),
    since only those take the `1.0` spelling (a folded `2 + 3` does not)."""
    spellings: list[str] = []
    for node in int_nodes:
        leaves = _path_leaves(node, mix.path)
        if leaves is None:
            return None
        for leaf in leaves:
            s = _int_literal_spelling(leaf)
            if s is None:
                return None
            if s not in spellings:
                spellings.append(s)
    return spellings or None


def _literal_children(e: TpyExpr) -> list[tuple[int, TpyExpr]] | None:
    """The children of a container literal, each with its child index in the
    order `literal_peer_children` uses (a dict's keys 0, values 1); None for
    anything that is not a container literal."""
    if isinstance(e, (TpyArrayLiteral, TpySetLiteral, TpyListRepeat)):
        return [(0, x) for x in e.elements]
    if isinstance(e, TpyDictLiteral):
        return [(0, k) for k in e.keys] + [(1, v) for v in e.values]
    if isinstance(e, TpyTupleLiteral):
        return list(enumerate(e.elements))
    return None


def _path_leaves(e: TpyExpr, path: tuple[int, ...]) -> list[TpyExpr] | None:
    e = peel_coerce(e)
    if not path:
        return [e]
    k, rest = path[0], path[1:]
    children = [c for i, c in _literal_children(e) or [] if i == k]
    if not children:
        return None
    out: list[TpyExpr] = []
    for child in children:
        leaves = _path_leaves(child, rest)
        if leaves is None:
            return None
        out.extend(leaves)
    return out


def _int_literal_spelling(e: TpyExpr) -> str | None:
    e = peel_coerce(e)
    if isinstance(e, TpyIntLiteral):
        return str(e.value)
    if (isinstance(e, TpyUnaryOp) and e.op == "-"
            and isinstance(peel_coerce(e.operand), TpyIntLiteral)):
        return f"-{peel_coerce(e.operand).value}"
    return None


def _literal_fix(spellings: list[str]) -> str:
    """Spell int literals as the floats they have to be."""
    if len(spellings) > 3:
        return f"write the int literals as floats ({spellings[0]}.0, ...)"
    return (f"write {_words([s + '.0' for s in spellings])} instead of "
            f"{_words(spellings)}")


def _words(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def select_mix_message(mix: InferredJoin, construct: str,
                       rewrite: Callable[[str, str], str] | None,
                       left: TpyExpr, right: TpyExpr,
                       types: tuple[TpyType, TpyType],
                       name_target: bool) -> str:
    """The diagnostic for a select (ternary, `and`, `or`) whose operands mix
    an integer and a float. `rewrite(left, right)` spells the select back
    with the integer operand converted, when both operands are simple;
    `types` are the operands' (resolved) types; `name_target` says the
    select is the whole value of an unannotated first binding, whose name
    can be annotated instead.

    Int literals are respelled as floats. An integer container VARIABLE is
    redeclared rather than converted: converting its elements would copy it,
    losing the aliasing CPython keeps."""
    int_node, float_type = ((left, types[1]) if mix.int_first
                            else (right, types[0]))
    literals = int_literal_spellings(mix, [int_node])
    i = "int" if literals else python_type_name(mix.int_side)
    f = python_type_name(mix.float_side)
    what = " elements" if mix.nested else ""
    head = (f"this {construct} mixes {i} and {f}{what}, and CPython keeps "
            f"whichever value it picks; ")
    variable = storage_spelling(peel_coerce(int_node)) if mix.nested else None
    if literals:
        fix = _literal_fix(literals)
    elif variable is not None:
        fix = f"declare {variable} as {python_type_name(float_type)}"
    else:
        fix = "convert to one type: " + _select_conversion(
            mix, rewrite, operand_spelling(left), operand_spelling(right))
    # A nested mix converts at an annotated target only when the integers
    # are literals, which pin to it; a variable keeps its own type.
    annotation = (None if not name_target
                  else f if not mix.nested
                  else python_type_name(float_type) if literals
                  else None)
    if annotation is None:
        return head + fix
    return f"{head}{fix}, or annotate the target as {annotation}"


def _select_conversion(mix: InferredJoin,
                       rewrite: Callable[[str, str], str] | None,
                       ls: str | None, rs: str | None) -> str:
    i = python_type_name(mix.int_side)
    f = python_type_name(mix.float_side)
    if mix.nested or rewrite is None or ls is None or rs is None:
        where = "elements" if mix.nested else "operand"
        return f"{f}(...) on the {i} {where}"
    if mix.int_first:
        return rewrite(f"{f}({ls})", rs)
    return rewrite(ls, f"{f}({rs})")


def literal_mix_message(mix: InferredJoin, title: str, item: str,
                        index: int, new: TpyType, earlier: TpyType,
                        peers: Sequence[TpyExpr],
                        annotation: Callable[[str], str] | None) -> str:
    """The diagnostic for a list / dict / set literal whose peers mix an
    integer and a float: `title` heads it (`List literal has mixed types`),
    `item` names one peer (`element`, `key`, `value`), `peers` are the peer
    nodes up to and including the `index`-th, and `annotation(f)` spells the
    annotated target with the float type filled in -- None when the literal
    is not the whole value of an unannotated first binding."""
    i = python_type_name(mix.int_side)
    f = python_type_name(mix.float_side)
    int_nodes = peers[:-1] if mix.int_first else peers[-1:]
    literals = int_literal_spellings(mix, int_nodes)
    fix = (_literal_fix(literals) if literals
           else f"convert to one type: {f}(...) on the {i} {item}s")
    msg = (f"{title}: {item} {index} is {python_type_name(new)}, but earlier "
           f"{item}s are {python_type_name(earlier)}; CPython keeps each "
           f"value's own type, so {fix}")
    if mix.nested or annotation is None:
        return msg
    return f"{msg}, or annotate the target as {annotation(f)}"


def usage_mix_message(mix: InferredJoin, container: str,
                      annotation: str | None,
                      value: TpyExpr | None) -> str:
    """The diagnostic for an unannotated container whose element (or key,
    or value) type is learned from its uses, when a use adds a float where
    earlier ones added an integer or the other way round. `container` names
    it (`list 'xs'`); `annotation` spells the declaration that would give it
    the float type, when the mix is not nested inside an element; `value`
    is the node the new use adds, when the diagnostic has it."""
    i = python_type_name(mix.int_side)
    f = python_type_name(mix.float_side)
    # Only the new use's node is at hand: an earlier integer has no source.
    literals = (int_literal_spellings(mix, [value])
                if value is not None and not mix.int_first else None)
    fix = (_literal_fix(literals) if literals
           else f"convert to one type: {f}(...) on the {i} values")
    head = (f"{container} mixes {i} and {f} values, and CPython keeps each "
            f"value's own type; {fix}")
    if mix.nested or annotation is None:
        return head
    return f"{head}, or annotate the container, e.g. {annotation}"


def rebind_mix_message(mix: InferredJoin, name: str, new_value: TpyExpr | None,
                       earlier: Sequence[TpyExpr], earlier_line: int | None,
                       aug_op: str | None, annotatable: bool,
                       bound_elsewhere: bool = False) -> str:
    """The diagnostic for a local whose bindings mix an integer and a float
    across statements. `mix` was asked with the local's type so far first
    and the new binding's second; `new_value` is the node the new binding
    stores (None for an augmented assignment, whose result is no source
    node); `earlier` are the earlier bindings' nodes on the other side of
    the mix, the first of them written at `earlier_line`; `annotatable` says
    an annotation of the name here would declare it -- a loop variable or a
    `nonlocal` / `global` name is bound by something else; `bound_elsewhere`
    says it is a `nonlocal` / `global` name, whose owning binding an
    annotation converts."""
    int_nodes = (list(earlier) if mix.int_first
                 else [new_value] if new_value is not None else [])
    literals = int_literal_spellings(mix, int_nodes) if int_nodes else None
    i = "int" if literals else python_type_name(mix.int_side)
    f = python_type_name(mix.float_side)
    was, now = (i, f) if mix.int_first else (f, i)
    what = " elements" if mix.nested else ""
    here = (f"'{aug_op}=' makes it {now} here" if aug_op is not None
            else f"to {now}{what} here")
    if earlier_line is not None:
        head = f"'{name}' is bound to {was}{what} at line {earlier_line} and {here}"
    else:
        head = (f"'{name}' has type {was} and {here}" if aug_op is not None
                else f"'{name}' has type {was}{what} and is bound {here}")
    head += ", and CPython keeps each value's own type; "
    if not annotatable:
        fix = f"bind the {now} value to a new name"
        if bound_elsewhere:
            fix += f", or declare '{name}' as {f} where it is first bound"
        return head + fix
    if literals:
        fix = _literal_fix(literals)
    elif (not mix.nested and not mix.int_first and new_value is not None
            and (s := operand_spelling(new_value)) is not None):
        fix = f"convert to one type: {name} = {f}({s})"
    else:
        fix = f"convert to one type: {f}(...) on the {i} value"
    # A nested mix would need the whole tuple type spelled in the annotation.
    if mix.nested:
        return head + fix
    return f"{head}{fix}, or annotate {name}: {f}"
