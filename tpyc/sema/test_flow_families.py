"""Every FlowFacts family and every function-state field is classified for
the scope a comprehension or a lambda opens (`InitTracker.hidden`), so a new
one cannot skip it.

The classification is checked against what each field's annotation says its
key (a mapping's) or element (a collection's) identifies: a field is "not
keyed by a variable's name" only if that names none of the name-derived keys
(`NAME_DERIVED_KEYS`: `str`, `StorageKey` and the name-keyed composites),
directly or as a field of a record it names (`OwnSlot`'s parameter name),
but for the records of `RECORDS_NOT_NAMING_VARIABLES` and the fields of
`NAMES_NOT_VARIABLES`, whose `str` is no variable's; every name-keyed field
(masked, unmasked or whole-body) names one."""

import ast
import builtins
import dataclasses
import sys
from collections import Counter
from dataclasses import fields
from typing import Any

from ..prescan import LiteralLocals
from . import context
from .context import FunctionTrackingState
from .flow_facts import FlowFacts
from .init_tracker import (HIDDEN_FIELDS, NAME_DERIVED_KEYS,
                           NAME_KEYED_FAMILIES, NAMES_NOT_VARIABLES,
                           NOT_NAME_KEYED_FIELDS,
                           RECORDS_NOT_NAMING_VARIABLES, UNKEYED_FAMILIES,
                           UNMASKED_NAME_FIELDS, WHOLE_BODY_NAME_FIELDS)

_MAPPINGS = {"dict", "Mapping", "IdentityMap"}


def _spelling(ann: Any) -> str:
    if isinstance(ann, str):
        return ann
    return getattr(ann, "__forward_arg__", None) or repr(ann)


def _resolve(name: str, ns: dict[str, Any]) -> Any:
    """The class `name` spells in module namespace `ns`, else the one class
    of that name a loaded compiler module defines (a `TYPE_CHECKING`
    import)."""
    if name in ns:
        return ns[name]
    if hasattr(builtins, name):
        return getattr(builtins, name)
    found = {getattr(m, name) for k, m in list(sys.modules.items())
             if k.startswith("tpyc.") and m is not None
             and getattr(getattr(m, name, None), "__module__", None) == k}
    assert len(found) == 1, f"cannot resolve {name!r}"
    return found.pop()


def _record_fields(cls: Any) -> list[tuple[str, Any]] | None:
    if dataclasses.is_dataclass(cls):
        return [(f.name, f.type) for f in fields(cls)]
    if (isinstance(cls, type) and issubclass(cls, tuple)
            and hasattr(cls, "_fields")):
        return list(cls.__annotations__.items())
    return None


def _identified(node: ast.expr):
    """The type names a key or an element of annotation `node` spells."""
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Attribute):
        yield node.attr
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield from _identified(ast.parse(node.value, mode="eval").body)
    elif isinstance(node, ast.BinOp):
        yield from _identified(node.left)
        yield from _identified(node.right)
    elif isinstance(node, ast.Subscript):
        base = node.value.id if isinstance(node.value, ast.Name) else None
        if base == "Callable":
            return
        args = (node.slice.elts if isinstance(node.slice, ast.Tuple)
                else [node.slice])
        for a in args[:1] if base in _MAPPINGS else args:
            yield from _identified(a)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            yield from _identified(e)


def _name_keys(ann: Any, ns: dict[str, Any],
               seen: frozenset[str] = frozenset()) -> list[str]:
    """The name-derived keys annotation `ann` identifies by, as paths
    through the records holding them."""
    found = []
    for name in _identified(ast.parse(_spelling(ann), mode="eval").body):
        if name in NAME_DERIVED_KEYS:
            found.append(name)
            continue
        cls = _resolve(name, ns)
        if (name in seen or any(c.__name__ in RECORDS_NOT_NAMING_VARIABLES
                                for c in getattr(cls, "__mro__", ()))):
            continue
        inner = vars(sys.modules[getattr(cls, "__module__", "builtins")])
        found += [f"{name}.{member}"
                  for member, member_ann in _record_fields(cls) or ()
                  if _name_keys(member_ann, inner, seen | {name})]
    return found


def _keys() -> dict[str, list[str]]:
    return {f.name: _name_keys(f.type, vars(context))
            for f in fields(FunctionTrackingState)}


def test_every_flow_family_is_classified() -> None:
    families = [f.name for f in fields(FlowFacts)]
    classified = list(NAME_KEYED_FAMILIES) + list(UNKEYED_FAMILIES)
    assert sorted(classified) == sorted(families)


def test_every_function_state_field_is_classified_once() -> None:
    actual = {f.name for f in fields(FunctionTrackingState)}
    counts = Counter(HIDDEN_FIELDS + NOT_NAME_KEYED_FIELDS
                     + WHOLE_BODY_NAME_FIELDS + tuple(UNMASKED_NAME_FIELDS))
    assert not [f for f, n in counts.items() if n > 1], (
        "classified more than once")
    assert counts.keys() - actual == set(), "classified but not a field"
    assert actual - counts.keys() == set(), (
        "unclassified: name it in HIDDEN_FIELDS (masked inside a "
        "comprehension or lambda) or one of the other lists "
        "(tpyc/sema/init_tracker.py)")


def test_not_name_keyed_fields_name_no_name_key() -> None:
    keys = _keys()
    wrong = {f: keys[f] for f in NOT_NAME_KEYED_FIELDS
             if keys[f] and f not in NAMES_NOT_VARIABLES}
    assert wrong == {}, (
        "keyed by a name-derived key: mask it (HIDDEN_FIELDS) or "
        "list why not (UNMASKED_NAME_FIELDS, NAMES_NOT_VARIABLES)")
    assert set(NAMES_NOT_VARIABLES) <= set(NOT_NAME_KEYED_FIELDS)


def test_name_keyed_fields_name_a_name_key() -> None:
    keys = _keys()
    named = (HIDDEN_FIELDS + WHOLE_BODY_NAME_FIELDS
             + tuple(UNMASKED_NAME_FIELDS))
    assert [f for f in named if f in keys and not keys[f]] == []


def test_record_keys_are_seen() -> None:
    """The record walk reaches a name held in a record, so a list of them
    is name-keyed."""
    keys = _keys()
    assert keys["current_consumed_own_params"] == ["OwnSlot.param"]
    assert keys["comp_targets"] == ["CompTarget.name"]


def test_literal_locals_without_covers_every_field() -> None:
    """`LiteralLocals.without`, the mask of a composite field, drops the
    names from every one of its members."""
    full = LiteralLocals(**{
        f.name: ({"n": ()} if f.name in ("literals", "single_stores")
                 else frozenset({"n"}))
        for f in fields(LiteralLocals)})
    masked = full.without(frozenset({"n"}))
    assert all(not getattr(masked, f.name) for f in fields(LiteralLocals))
