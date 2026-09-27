"""A type hint that knows which of its positions an inferred local decides.

A hint is DECLARED where the source spells the slot's type -- an annotation,
a parameter, a return type -- and converts what it receives there (an int
bound at a `float` slot becomes a float). It is INFERRED where the type is
only an unannotated local's own type, reached through a reassignment
(`x = 0.5; x = ...`) and, from there, through a call's return into the type
parameters it binds: such a position still types what the value leaves
open (a float literal's width, a lambda's parameters, a generic call's type
argument) but converts nothing, since CPython keeps each value's own type.

One hint can mix the two: `def f[T](p: tuple[T, float]) -> T` called under
an inferred `int32` hint gives its argument the hint `tuple[int32, float]`
whose first element is inferred and whose second is declared. So a hint is
the substituted `type` plus the unsubstituted `pattern` it came from; the
positions of `pattern` that are one of the `seeded` type params are the
inferred ones. A wholly inferred hint (the reassigned local's own type) is
the same shape with a placeholder type param as its whole pattern.

An overload candidate the inferred local's type alone would pick hands each
argument its declared parameter type as a FILL-ONLY hint (`SlotHint.fill`):
wholly inferred, and bound to the one argument node it types
(`fill_node`). It fills only what nothing else types there -- a literal's
or empty constructor's open positions, a generic call's or construction's
unbound type parameters -- and no node nested in the argument sees it
(`SemanticContext.slot_hint_at`).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Callable, TYPE_CHECKING

from ..typesys import (ANY, AnyType, CallableType, NominalType, OptionalType,
                       TpyType, TupleType, TypeParamRef, UnionType,
                       contains_type_param, type_param_names,
                       unwrap_qualifiers)

if TYPE_CHECKING:
    from ..parse import TpyExpr

# The type param a wholly inferred hint's pattern consists of.
_LOCAL = TypeParamRef("<inferred local>")


class HintKind(Enum):
    # Every position converts what it receives.
    DECLARED = auto()
    # The positions of `pattern` naming a `seeded` type param convert
    # nothing; the others are declared.
    INFERRED = auto()


@dataclass(frozen=True)
class SlotHint:
    type: TpyType
    # INFERRED only: the unsubstituted form of `type`.
    pattern: TpyType | None = None
    seeded: frozenset[str] = frozenset()
    kind: HintKind = HintKind.DECLARED
    # A fill-only hint: the argument node it types, and no other. Equality
    # ignores it on purpose -- nothing compares or caches hints by scope.
    fill_node: TpyExpr | None = field(default=None, compare=False)

    @staticmethod
    def declared(t: TpyType) -> SlotHint:
        return SlotHint(t)

    @staticmethod
    def inferred_local(t: TpyType) -> SlotHint:
        """`t` decided wholly by an inferred local: the reassigned
        unannotated local's own type."""
        return SlotHint(t, _LOCAL, frozenset({_LOCAL.name}), HintKind.INFERRED)

    @staticmethod
    def fill(t: TpyType, node: TpyExpr) -> SlotHint:
        """An overload's declared parameter type `t` for the argument
        `node`, typing it as an inferred local's type would, but only
        where nothing else does."""
        return replace(SlotHint.inferred_local(t), fill_node=node)

    @property
    def is_fill(self) -> bool:
        return self.fill_node is not None

    def retarget(self, node: TpyExpr) -> SlotHint:
        """This fill-only hint for `node` instead: an operand whose value
        is the value of the node it was for."""
        return replace(self, fill_node=node)

    def as_local(self) -> SlotHint:
        """The hint without its fill-only scope: what the argument node's
        own analysis types its open positions under."""
        return replace(self, fill_node=None)

    @staticmethod
    def partly_inferred(t: TpyType, pattern: TpyType,
                        seeded: frozenset[str]) -> SlotHint:
        return SlotHint(t, pattern, seeded, HintKind.INFERRED)

    @staticmethod
    def of(hint: SlotHint | TpyType | None) -> SlotHint | None:
        """A bare type is a declared hint."""
        if hint is None or isinstance(hint, SlotHint):
            return hint
        return SlotHint.declared(hint)

    @property
    def is_declared(self) -> bool:
        """Every position of the hint is declared."""
        return self.kind is HintKind.DECLARED or (
            self.kind is HintKind.INFERRED
            and not contains_type_param(self.pattern, set(self.seeded)))

    @property
    def inferred(self) -> bool:
        """The hint's top position is inferred as a whole."""
        return (self.kind is HintKind.INFERRED
                and isinstance(self.pattern, TypeParamRef)
                and self.pattern.name in self.seeded)

    def map(self, fn: Callable[[TpyType], TpyType | None]) -> SlotHint | None:
        """Project `type` and `pattern` together: `fn` takes the same part
        (an element, a type argument, a wrapper's inner type) of both, so
        the part keeps the provenance its position has, and answers None
        where a type has no such part. None when the TYPE has none."""
        t = fn(self.type)
        if t is None:
            return None
        if self.kind is not HintKind.INFERRED:
            return SlotHint(t, kind=self.kind)
        if self.is_declared:
            return SlotHint.declared(t)
        if self.inferred:
            return SlotHint.partly_inferred(t, self.pattern, self.seeded)
        p = fn(self.pattern)
        if p is None:
            # The pattern's shape does not follow the type's; an inferred
            # reading refuses a mix rather than converting it silently.
            return SlotHint.inferred_local(t)
        return SlotHint.partly_inferred(t, p, self.seeded)

    def inferred_view(self) -> TpyType | None:
        """`type` with every declared position replaced by `Any`, which no
        int/float mix is found against -- or None when nothing is inferred.
        Comparing a value with this view finds exactly the mixes at the
        inferred positions."""
        if self.kind is not HintKind.INFERRED or self.is_declared:
            return None
        return _view(self.pattern, self.type, set(self.seeded))


# Projections for `SlotHint.map`: the part of a type, or None.

def type_arg(k: int) -> Callable[[TpyType], TpyType | None]:
    """The `k`-th type argument of a generic (a list's or set's element, a
    dict's key and value)."""
    def project(t: TpyType) -> TpyType | None:
        if not isinstance(t, NominalType) or k >= len(t.type_args):
            return None
        a = t.type_args[k]
        return a if isinstance(a, TpyType) else None
    return project


def element(k: int) -> Callable[[TpyType], TpyType | None]:
    """The `k`-th element of a tuple."""
    def project(t: TpyType) -> TpyType | None:
        return (t.element_types[k] if isinstance(t, TupleType)
                and k < len(t.element_types) else None)
    return project


def callable_return(t: TpyType) -> TpyType | None:
    return t.return_type if isinstance(t, CallableType) else None


def optional_inner(t: TpyType) -> TpyType | None:
    return t.inner if isinstance(t, OptionalType) else None


def aligned_children(p: TpyType, t: TpyType,
                     ) -> list[tuple[TpyType, TpyType]] | None:
    """The children of `p` and `t` paired by position, when both have the
    same shape; None otherwise. A union is never paired: substitution can
    merge or reorder its members."""
    if isinstance(p, UnionType) or isinstance(t, UnionType):
        return None
    pi, ti = p.inner_types(), t.inner_types()
    if not (type(p) is type(t) and pi and len(pi) == len(ti)
            and (not isinstance(p, NominalType)
                 or p.qualified_name() == t.qualified_name())):
        return None
    return list(zip(pi, ti))


def _view(p: TpyType, t: TpyType, seeded: set[str]) -> TpyType:
    if isinstance(p, TypeParamRef):
        return t if p.name in seeded else ANY
    if not contains_type_param(p, seeded):
        return ANY
    pairs = aligned_children(p, t)
    if pairs is None:
        # A shape the substitution did not keep: read it as inferred.
        return t
    return t.with_inner_types(tuple(_view(a, b, seeded) for a, b in pairs))


def params_at_inferred(pattern: TpyType, hint: SlotHint | None,
                       ) -> frozenset[str]:
    """The type params of `pattern` (a call's return type) that a match
    against `hint` binds at a position an inferred local decides."""
    view = hint.inferred_view() if hint is not None else None
    if view is None:
        return frozenset()
    out: set[str] = set()
    _collect_at_inferred(pattern, view, out)
    return frozenset(out)


def _collect_at_inferred(p: TpyType, v: TpyType, out: set[str]) -> None:
    if isinstance(v, AnyType):
        return
    p, v = unwrap_qualifiers(p), unwrap_qualifiers(v)
    if isinstance(p, TypeParamRef):
        out.add(p.name)
        return
    if isinstance(p, OptionalType) and not isinstance(v, OptionalType):
        # The matcher binds an Optional parameter's inner type to a bare
        # value; follow it the same way.
        _collect_at_inferred(p.inner, v, out)
        return
    pairs = aligned_children(p, v)
    if pairs is None:
        out.update(type_param_names(p))
        return
    for a, b in pairs:
        _collect_at_inferred(a, b, out)
