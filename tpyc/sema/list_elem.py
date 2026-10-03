"""How a signature of the `list` stub uses the list's element.

A list literal whose element is not decided yet (`tpyc/sema/pending_num.py`,
the element cells) can go through a method or meet a protocol with the
element still open only when the signature never builds a type from it.
That is one question about the stub's signature, asked here once: by the
method call on such a list, by the element inference of the lists without a
cell, and by the test for a protocol that says nothing about elements.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from .. import qnames
from ..typesys import (FunctionInfo, MethodSignature, OwnType, TpyType,
                       TypeParamRef, contains_type_param, unwrap_ref_type)

if TYPE_CHECKING:
    from .context import SemanticContext


class ElemUse(Enum):
    # The type does not mention the element.
    NONE = auto()
    # The type is the element itself, as a value taken or handed out.
    VALUE = auto()
    # The element sits inside another type (`Iterable[T]`, `list[T]`).
    INSIDE = auto()


def elem_use(t: TpyType | None, elem_params: set[str]) -> ElemUse:
    """How type `t` of a list-stub signature uses the list's element."""
    if t is None:
        return ElemUse.NONE
    bare = unwrap_ref_type(t)
    if isinstance(bare, OwnType):
        bare = bare.wrapped
    if isinstance(bare, TypeParamRef) and bare.name in elem_params:
        return ElemUse.VALUE
    return (ElemUse.INSIDE if contains_type_param(t, elem_params)
            else ElemUse.NONE)


@dataclass(frozen=True)
class ElemSignature:
    """A list-stub method's use of the element: per parameter, in its
    result, and whether the method puts a bound of its own on it."""
    params: tuple[ElemUse, ...]
    result: ElemUse
    bounded: bool

    @property
    def builds_types(self) -> bool:
        """The method needs the element's type: it builds a type from it
        or checks a bound on it."""
        return (self.bounded or self.result is ElemUse.INSIDE
                or ElemUse.INSIDE in self.params)

    @property
    def blind(self) -> bool:
        """The method never touches the element."""
        return (not self.bounded and self.result is ElemUse.NONE
                and all(p is ElemUse.NONE for p in self.params))


def elem_signature(fn: FunctionInfo, elem_params: set[str]) -> ElemSignature:
    return ElemSignature(
        params=tuple(elem_use(p.type, elem_params) for p in fn.params),
        result=elem_use(fn.return_type, elem_params),
        bounded=bool(fn.type_params))


def list_overloads(ctx: 'SemanticContext', name: str, arity: int,
                   ) -> list[tuple[FunctionInfo, ElemSignature]]:
    """The overloads of list method `name` taking `arity` arguments, each
    with its use of the element."""
    record = ctx.registry.get_builtin_record(qnames.LIST)
    if record is None:
        return []
    params = set(record.type_params)
    return [(fn, elem_signature(fn, params))
            for fn in record.get_method_overloads(name) or []
            if len(fn.params) == arity]


def protocol_is_elem_blind(ctx: 'SemanticContext',
                           methods: list[MethodSignature]) -> bool:
    """Whether a list conforms to a protocol with `methods` through
    overloads that never touch its element (`Sized`)."""
    if not methods:
        return False
    for sig in methods:
        conforming = list_overloads(ctx, sig.name, len(sig.params))
        if not conforming or not all(s.blind for _fn, s in conforming):
            return False
    return True
