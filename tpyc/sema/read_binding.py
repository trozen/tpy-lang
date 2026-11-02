"""The binding a name read resolves to (`ReadBinding`), recorded once
per read where it is analyzed (`SemanticContext.record_read_binding`) and
asked passively by the ownership checks: the scope binding the name is
found by `Scope.lookup_owned`, the one scope lookup, and this module only
classifies it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Collection

from ..namespace import Blocked
from ..parse.nodes import TpyFunction

if TYPE_CHECKING:
    from ..diagnostics import Scope
    from .context import SemanticContext


class ReadKind(Enum):
    """What a name read resolves to (`ReadBinding`)."""
    PARAM = "param"
    RECEIVER = "receiver"
    LAMBDA = "lambda"
    LOCAL = "local"
    SITE = "site"          # a binding site's target
    CAPTURED = "captured"  # an enclosing body's binding, read in place
    MODULE = "module"      # module storage


@dataclass(frozen=True)
class ReadBinding:
    """The binding a name read resolves to, recorded where the read is
    analyzed (`resolve_read`): the binding never changes after the read,
    so a verdict built from it later is order-free. `node` is the function
    of a parameter, the receiver or a local, the lambda of a lambda
    parameter, the node of a binding site (`Scope.binders`). A captured
    read's `outer` is the binding the closure reads, as a read where the
    closure is written resolves it (itself captured past a further root)."""
    kind: ReadKind
    name: str
    node: object = None
    outer: 'ReadBinding | None' = None


def resolve_read(ctx: 'SemanticContext', name: str) -> ReadBinding:
    """The binding a read of `name` here resolves to: the innermost scope
    binding it -- a binding site's target (`Scope.binders`), a
    lambda's parameter, a name an enclosing body binds past a nested def's
    or a lambda's root (captured), module storage -- else the function's
    parameter, its receiver or its local."""
    fs = ctx.func
    return _resolve_from(ctx, name, fs.current_scope, fs.current_function,
                         fs.global_declarations, fs.body_bound_names)


def _resolve_from(ctx: 'SemanticContext', name: str, scope: 'Scope | None',
                  func: object, globals_: 'Collection[str]',
                  bound: 'Collection[str]') -> ReadBinding:
    """`resolve_read` from `scope`, a scope of function `func`'s body,
    which declares `globals_` global and binds `bound`; a binding past a
    nested def's root resolves against the enclosing function's
    (`Scope.outer_function`)."""
    found, owner = (scope.lookup_owned(name) if scope is not None
                    else (None, None))
    assert not isinstance(found, Blocked), (
        f"read of '{name}' resolved while its function's barrier blocks it")
    # The first nested def's or lambda's root crossed below the owner, and
    # the outermost scope reached (the module's for a name nothing binds).
    root: 'Scope | None' = None
    top = scope
    while top is not None and top is not owner:
        if root is None and (top.function_root
                             or top.lambda_node is not None):
            root = top
        if top.parent is None:
            break
        top = top.parent
    at_module = top is ctx.global_scope
    if owner is not None and not at_module:
        if root is not None:
            if root.function_root:
                func, globals_, bound = (root.outer_function,
                                         root.outer_globals,
                                         root.outer_bound)
            return ReadBinding(ReadKind.CAPTURED, name, outer=_resolve_from(
                ctx, name, root.parent, func, globals_, bound))
        binder = owner.binders.get(name)
        if binder is not None:
            return ReadBinding(ReadKind.SITE, name, binder)
        if owner.lambda_node is not None:
            return ReadBinding(ReadKind.LAMBDA, name, owner.lambda_node)
    if (not isinstance(func, TpyFunction)
            or ctx.binds_module_slot(name, func)
            or at_module or name in globals_):
        return ReadBinding(ReadKind.MODULE, name)
    if any(p == name for p, _t in func.params):
        return ReadBinding(ReadKind.PARAM, name, func)
    # A `self` a non-consuming method binds is a local of its own.
    if (name == "self" and func.is_method and not func.is_staticmethod
            and not func.is_classmethod
            and (func.is_consuming or name not in bound)):
        return ReadBinding(ReadKind.RECEIVER, name, func)
    return ReadBinding(ReadKind.LOCAL, name, func)
