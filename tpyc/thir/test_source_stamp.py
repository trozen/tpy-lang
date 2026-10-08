"""Invariants of the read facts a lowered expression carries (`Source`):
the node's own pointer / id-expression answers stay equal to the stamped
ones whenever an arm rebuilds the node, a NAME's pointer-ness comes from its
binding, and a const method's receiver is const. Hand-built nodes; no arm's
render is pinned here."""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ..typesys import INT32, NominalType, TupleType
from .lower.context import Slot, SlotConstruct, SlotHolds
from .lower.convert import Refuse, convert
from .lower.expressions import _HELD_BY_KIND, _held_from_facts, stamped
from .nodes import (
    Form, THIRExpr, THIRFieldAccess, THIRLiteral, THIRName, THIRSelf,
    THIRSubscript,
)
from .source import Binding, Held, Source

_REC = NominalType("P")


def _binding(name: str, *, pointer: bool) -> Binding:
    return Binding(name=name, type=_REC, param_type=None, is_param=False,
                   pointer=pointer, global_slot=False)


def _stamped(node):
    """The node with a stamp whose read facts are deliberately wrong: the
    node must rewrite them to its own."""
    return replace(node, source=Source(
        held=Held.BORROWED, pointer_held=not node.reads_raw_pointer,
        id_read=not node.reads_binding, binding=(
            node.source.binding if node.source is not None else None)))


def _tuple_elem(*, elem_pointer: bool) -> THIRSubscript:
    recv = THIRName(result_type=TupleType((_REC, INT32)), name="t",
                    form=Form.STORAGE)
    return THIRSubscript(result_type=_REC, receiver=recv,
                         index=THIRLiteral(result_type=INT32, value=0),
                         tuple_index=0, form=Form.BORROW,
                         elem_pointer=elem_pointer)


_NODES = {
    "pointer_name": replace(
        THIRName(result_type=_REC, name="p", form=Form.BORROW),
        source=Source(held=Held.BORROWED,
                      binding=_binding("p", pointer=True))),
    "plain_name": replace(
        THIRName(result_type=_REC, name="q", form=Form.BORROW),
        source=Source(held=Held.BORROWED,
                      binding=_binding("q", pointer=False))),
    "self": THIRSelf(result_type=_REC, form=Form.BORROW, pointer=True),
    "frame_self": THIRSelf(result_type=_REC, form=Form.BORROW,
                           cpp="__self", pointer=False),
    "pointer_elem": _tuple_elem(elem_pointer=True),
    "by_value_elem": _tuple_elem(elem_pointer=False),
}


@pytest.mark.parametrize("name", sorted(_NODES))
@pytest.mark.parametrize("deref", [False, True])
def test_rebuilt_node_rewrites_its_read_facts(name: str, deref: bool) -> None:
    node = replace(_stamped(_NODES[name]), deref=deref)
    assert node.source.pointer_held == node.reads_raw_pointer
    assert node.source.id_read == node.reads_binding


def test_deref_is_never_a_raw_pointer() -> None:
    for node in _NODES.values():
        assert not replace(node, deref=True).reads_raw_pointer


def test_by_value_tuple_element_is_no_pointer() -> None:
    # A record element of a tuple holding it by value yields `T&`.
    assert not _tuple_elem(elem_pointer=False).reads_raw_pointer
    assert _tuple_elem(elem_pointer=True).reads_raw_pointer


def test_name_indirection_follows_its_binding() -> None:
    assert _NODES["pointer_name"].indirect
    assert _NODES["pointer_name"].reads_raw_pointer
    assert not _NODES["plain_name"].indirect
    other = replace(_NODES["pointer_name"], name="r")
    assert not other.indirect


def test_frame_receiver_is_no_pointer() -> None:
    assert _NODES["self"].reads_raw_pointer
    assert not _NODES["frame_self"].reads_raw_pointer


@pytest.mark.parametrize("held,temporary", [
    (Held.FRESH, False), (Held.VALUE, True), (Held.STORAGE, True),
    (Held.STORAGE, None), (Held.BORROWED, None)])
def test_reference_slot_refuses_a_dying_source(held: Held,
                                               temporary: bool) -> None:
    # Sema lets some temporaries reach a reference result; the conversion
    # must not bind one, nor a source whose lifetime nothing states.
    node = replace(THIRName(result_type=_REC, name="x"),
                   source=Source(held=held, temporary=temporary))
    slot = Slot(_REC, SlotConstruct.RETURN, holds=SlotHolds.BORROWS)
    assert convert(node, slot, None) == Refuse("borrow_source")
    lvalue = replace(node, source=Source(held=Held.VALUE))
    assert not isinstance(convert(lvalue, slot, None), Refuse)


def _lc(*, readonly: bool, nested: bool = False):
    return SimpleNamespace(
        record_name="P", analyzer=None,
        func=SimpleNamespace(is_readonly=readonly, is_nested_def=nested))


def test_readonly_receiver_is_const() -> None:
    # A bare receiver an arm builds beside `_lower_expr` is stamped by the
    # same stamp, and a member read off it inherits its const-ness.
    recv = THIRSelf(result_type=INT32, form=Form.BORROW, pointer=True)
    field = THIRFieldAccess(result_type=INT32, receiver=recv, field_cpp="v",
                            is_arrow=True)
    assert stamped(recv, _lc(readonly=True)).source.const
    assert stamped(field, _lc(readonly=True)).source.const
    assert not stamped(recv, _lc(readonly=False)).source.const
    # A nested def's `self` is a capture, not the const method's receiver.
    assert not stamped(recv, _lc(readonly=True, nested=True)).source.const


def test_rebuilt_name_repicks_its_held() -> None:
    # A value-optional name read whole is its storage and through its
    # narrowed unwrap a borrow; stripping the deref must re-pick it.
    node = replace(THIRName(result_type=_REC, name="o", deref=True),
                   source=Source(held=Held.BORROWED,
                                 held_by_deref=(Held.STORAGE, Held.BORROWED)))
    assert node.source.held is Held.BORROWED
    whole = replace(node, deref=False)
    assert whole.source.held is Held.STORAGE
    assert replace(whole, deref=True).source.held is Held.BORROWED


def _concrete(base: type) -> set[type]:
    out: set[type] = set()
    stack = [base]
    while stack:
        for sub in stack.pop().__subclasses__():
            stack.append(sub)
            out.add(sub)
    return out


def _isinstance_targets(fn) -> set[str]:
    """Class names `fn` tests with `isinstance(x, THIRFoo)` or a tuple of
    them -- parsed, so a merely imported class does not count."""
    tree = ast.parse(inspect.getsource(fn))
    names: set[str] = set()
    for call in ast.walk(tree):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "isinstance" and len(call.args) == 2):
            target = call.args[1]
            elts = target.elts if isinstance(target, ast.Tuple) else [target]
            names.update(e.id for e in elts if isinstance(e, ast.Name))
    return names


def test_every_expression_kind_is_classified() -> None:
    """A node kind the stamp cannot classify fails at its first program;
    this walk fails at test time instead, the moment the kind is added."""
    named = _isinstance_targets(_held_from_facts) | {
        k.__name__ for k in _HELD_BY_KIND}
    missing = sorted(
        cls.__name__ for cls in _concrete(THIRExpr)
        if not any(b.__name__ in named for b in cls.__mro__[:-1]))
    assert missing == [], f"unclassified THIR expression kinds: {missing}"
