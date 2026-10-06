"""How a method of the `list`, `dict` and `set` stubs uses the container's
parts.

A container literal whose numbers are not decided yet
(`tpyc/sema/pending_num.py`, the element cells) can go through a method with
them still open only when the signature never builds a type from them. That
is one question about the stub's signature, asked here once (`container_call`)
for every container: by the method call on such a container, by the element
inference of the lists without a cell, and by the test for a protocol that
says nothing about elements.

The signature says WHICH part an argument names: the stub's type parameters
map to the container's steps (`PendingContainerType.STEPS`: a list's or set's
`T` its element, a dict's `K` / `V` its key and value). What the method DOES
with it -- holds it from then on, or only compares it with what it holds --
is no fact of the signature (`update` and `intersection_update` are spelled
alike): it is the stub's own declaration, `@native(..., element_effect=...)`
(`FunctionInfo.native_element_effect`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from ..typesys import (FunctionInfo, MethodSignature, NominalType,
                       OptionalType, PendingContainerType, PendingListType,
                       RecordInfo, TpyType, TypeParamRef, UnionType, NoneType,
                       contains_type_param, is_protocol_type)

from .pending_num import bare_slot

if TYPE_CHECKING:
    from .context import SemanticContext


class Role(Enum):
    # The argument is the container's element, key or value itself.
    PART = auto()
    # The argument holds entries the method takes one by one: a container
    # of the same kind (`update(other)`), or a protocol of the element's
    # values (`extend(Iterable[Own[T]])`).
    SOURCE = auto()


class Effect(Enum):
    # The arguments are values the container holds from then on: each is
    # a store into its cells (an inserting key widens the key).
    INSERT = auto()
    # The arguments are only compared with what the container holds (a
    # lookup key, a `get` / `pop` default handed back): each must fit.
    LOOKUP = auto()


_EFFECTS = {"insert": Effect.INSERT, "lookup": Effect.LOOKUP}


@dataclass(frozen=True)
class ContainerCall:
    """A method of a container stub as a call on a container literal sees
    it: the overload, per argument its role and, for a PART, the step of
    the container's tree it is and whether the method returns it, whether
    a parameter, the result or a bound of its own builds a type from the
    parts, the method's declared effect, and whether what it returns
    follows the parts (a part, an optional part, a view) rather than
    building a type from them."""
    fn: FunctionInfo
    roles: tuple[tuple[Role, str] | None, ...]
    # Per argument: a PART the result is typed by (`get`'s and `pop`'s
    # default) rather than one the method only compares (their key).
    returned: tuple[bool, ...]
    builds: bool
    effect: Effect | None
    result_follows: bool
    # The result mentions no part at all.
    result_blind: bool
    # A SOURCE parameter is a protocol of the parts (`Iterable[Own[T]]`),
    # not a container of the receiver's kind: what it takes keeps its own
    # type, which the protocol admits.
    source_is_protocol: bool = False
    # Some parameter's type mentions the parts.
    params_mention: bool = False
    # The method bounds a part (`def remove[T: Equatable]` on `list[T]`):
    # a requirement on the receiver's part, which builds no type of it.
    bounds_parts: bool = False

    @property
    def touches(self) -> bool:
        return any(r is not None for r in self.roles)

    def compares(self, i: int) -> bool:
        """Whether argument `i` is a value a lookup only compares with what
        the container holds, rather than one it hands back."""
        return self.effect is Effect.LOOKUP and not self.returned[i]

    @property
    def names_parts(self) -> bool:
        """An argument or a bound of the method is about the parts: with no
        declared effect, the call decides them first."""
        return self.touches or self.bounds_parts

    @property
    def stores_elements(self) -> bool:
        """The method's one argument is entries the container then holds
        (`extend`, `update`, `|=`): each is a store into it."""
        return (self.effect is Effect.INSERT and not self.builds
                and self.roles == ((Role.SOURCE, ""),))

    @property
    def blind(self) -> bool:
        """The method never touches the parts."""
        return (not self.builds and not self.names_parts
                and self.result_blind)


def _qname(container: PendingContainerType | type) -> str:
    cls = container if isinstance(container, type) else type(container)
    return f"builtins.{cls.KIND}"


def _stub(ctx: 'SemanticContext', container: PendingContainerType | type,
          ) -> tuple[RecordInfo, dict[str, str]] | None:
    """The stub record of `container`'s kind and its type parameters
    mapped to the container's steps, or None."""
    cls = container if isinstance(container, type) else type(container)
    record = ctx.registry.get_builtin_record(_qname(cls))
    if record is None:
        return None
    return record, dict(zip(record.type_params, cls.STEPS))


def container_calls(ctx: 'SemanticContext',
                    container: PendingContainerType | type, name: str,
                    arity: int) -> list[ContainerCall]:
    """Every overload of the method `name` of `container`'s kind taking
    `arity` arguments, classified (`ContainerCall`). The mutable and const
    twins of an `@auto_readonly` method use the parts alike: one stands for
    both."""
    stub = _stub(ctx, container)
    if stub is None:
        return []
    record, steps = stub
    fns = [fn for fn in record.get_method_overloads(name) or []
           if len(fn.params) == arity]
    if len(fns) == 2 and sum(f.is_auto_readonly_mutable_clone
                             for f in fns) == 1:
        fns = [f for f in fns if not f.is_auto_readonly_mutable_clone]
    return [_classify(fn, steps, _qname(container)) for fn in fns]


def container_call(ctx: 'SemanticContext',
                   container: PendingContainerType | type, name: str,
                   arity: int) -> ContainerCall | None:
    """`container_calls` when there is exactly one such overload."""
    calls = container_calls(ctx, container, name, arity)
    return calls[0] if len(calls) == 1 else None


def _classify(fn: FunctionInfo, steps: dict[str, str],
              qname: str) -> ContainerCall:
    params = set(steps)
    roles: list[tuple[Role, str] | None] = []
    returned: list[bool] = []
    # A method type parameter named as a class one is that class parameter
    # under a bound (`bound_check`); only a fresh one is a type the call
    # builds.
    fresh = [tp for tp in fn.type_params or () if tp not in params]
    builds = bool(fresh)
    for p in fn.params:
        bare = bare_slot(p.type)
        if isinstance(bare, TypeParamRef) and bare.name in params:
            roles.append((Role.PART, steps[bare.name]))
            returned.append(contains_type_param(fn.return_type, {bare.name}))
        elif _is_source(bare, params, qname):
            roles.append((Role.SOURCE, ""))
            returned.append(False)
        else:
            roles.append(None)
            returned.append(False)
            builds = builds or contains_type_param(p.type, params)
    effect = _EFFECTS.get(fn.native_element_effect or "")
    protocol = any(r is not None and r[0] is Role.SOURCE
                   and is_protocol_type(bare_slot(p.type))
                   for r, p in zip(roles, fn.params))
    return ContainerCall(
        fn, tuple(roles), tuple(returned), builds, effect,
        _follows(fn.return_type, params) and not fresh,
        not contains_type_param(fn.return_type, params), protocol,
        any(contains_type_param(p.type, params) for p in fn.params),
        len(fresh) < len(fn.type_params or ()))


def _is_source(t: TpyType | None, params: set[str], qname: str) -> bool:
    """A container of the receiver's kind over its type parameters, in
    order, or a protocol of one of them (`Iterable[Own[T]]`)."""
    if not isinstance(t, NominalType):
        return False
    args = [bare_slot(a) for a in t.type_args]
    if is_protocol_type(t):
        return (len(args) == 1 and isinstance(args[0], TypeParamRef)
                and args[0].name in params)
    return (t.qualified_name() == qname and len(args) == len(params)
            and all(isinstance(a, TypeParamRef) and a.name in params
                    for a in args))


_VIEW_QNAMES = {"builtins.dict_keys", "builtins.dict_values",
                "builtins.dict_items"}


def _follows(t: TpyType | None, params: set[str]) -> bool:
    """Whether a result of type `t` follows the container's parts: no
    part at all, a part, an optional one, or a dict view over the parts."""
    if t is None or not contains_type_param(t, params):
        return True
    bare = bare_slot(t)
    if isinstance(bare, TypeParamRef):
        return bare.name in params
    if isinstance(bare, OptionalType):
        return _follows(bare.inner, params)
    if isinstance(bare, UnionType):
        return all(isinstance(m, NoneType) or _follows(m, params)
                   for m in bare.members)
    if isinstance(bare, NominalType) and bare.qualified_name() in _VIEW_QNAMES:
        return all(isinstance(bare_slot(a), TypeParamRef) for a in bare.type_args)
    return False


def protocol_is_elem_blind(ctx: 'SemanticContext',
                           methods: list[MethodSignature]) -> bool:
    """Whether a list conforms to a protocol with `methods` through
    overloads that never touch its element (`Sized`)."""
    if not methods:
        return False
    for sig in methods:
        calls = container_calls(ctx, PendingListType, sig.name,
                                len(sig.params))
        if not calls or not all(c.blind for c in calls):
            return False
    return True


def iterated_step(ctx: 'SemanticContext',
                  container: PendingContainerType | type) -> str | None:
    """The step of `container`'s tree its stub's `__iter__` yields (a
    list's element), or None."""
    stub = _stub(ctx, container)
    if stub is None:
        return None
    record, steps = stub
    for fn in record.get_method_overloads("__iter__") or []:
        ret = bare_slot(fn.return_type)
        if fn.params or not isinstance(ret, NominalType) \
                or len(ret.type_args) != 1:
            continue
        arg = bare_slot(ret.type_args[0])
        if isinstance(arg, TypeParamRef) and arg.name in steps:
            return steps[arg.name]
    return None
