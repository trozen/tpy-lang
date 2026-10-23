"""The `@dynamic` erasure at an `Own[@dynamic P]` slot: which conversion a
source takes into the erased `unique_ptr<P>` -- none (an already-erased
`Own[P]`, forwarded), `std::make_unique<U>` (a record inheriting P), the
owning `make_adapter<Base>` Adapter (a structural conformer, an async-def
factory call), or the bound coroutine handle's unwrap. Sema publishes the
conversion on the source as a coercion (`dyn_erasure_coercion`, the
`into_dyn_*` rows of the coercion registry) where the slot is a result;
the call-argument and binding sinks ask `classify_dyn_own_arg`
directly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import qnames
from ..parse.nodes import TpyCall, TpyCoerce, TpyMethodCall, TpyName
from ..coercions import (DYN_ERASURES, INTO_DYN_ADAPTER, INTO_DYN_FACTORY,
                         INTO_DYN_INHERIT, Coercion)
from ..type_def_registry import protocol_info_of, is_subtype
from ..value_category import is_rvalue_source
from ..typesys import (
    ConcreteCoroType, NominalType, OwnType, TpyType, TypeParamRef,
    is_dyn_protocol, is_protocol_type, unwrap_readonly, unwrap_ref_type,
    unwrap_send_sync,
)

if TYPE_CHECKING:
    from ..parse.nodes import TpyExpr


def record_inherits_dynamic(concrete_type: TpyType, protocol: NominalType,
                            registry) -> bool:
    """Whether concrete_type's EMITTED struct converts to the @dynamic
    `protocol` base (`declares_dynamic_base`), so no adapter wrapping is
    needed.

    @native records are excluded: their struct is hand-written, so the
    lowering routes them through the Adapter even where the binding
    declares the base (`::tpy::BaseException` inherits `::tpy::Throwable`,
    `runtime/cpp/include/tpy/core.hpp`).
    """
    if not isinstance(concrete_type, NominalType) or not concrete_type.is_user_record:
        return False
    record_info = registry.get_record_for_type(concrete_type)
    if record_info is None or record_info.is_native:
        return False
    return declares_dynamic_base(concrete_type, protocol, registry)


def declares_dynamic_base(concrete_type: TpyType, protocol: NominalType,
                          registry) -> bool:
    """Whether a record or one of its base classes implements a @dynamic
    protocol that is (or inherits) `protocol` -- for a generated record the
    C++ base its struct chain is emitted with, for a native one the base its
    binding declares. Match is by qualified name; type_args are ignored
    (sema rejects a cross-instantiation mismatch before this runs)."""
    if not isinstance(concrete_type, NominalType) or not concrete_type.is_user_record:
        return False
    record_info = registry.get_record_for_type(concrete_type)
    target = protocol_info_of(protocol)
    if record_info is None or target is None:
        return False
    if _implements_dynamic(record_info, target):
        return True
    for base in record_info.mro_ancestors:
        base_info = registry.get_record_for_type(base)
        if base_info is not None and _implements_dynamic(base_info, target):
            return True
    return False


def _implements_dynamic(record_info, target) -> bool:
    """Whether `record_info` declares a @dynamic protocol that is (or
    inherits) `target`.

    The ``is_dynamic`` filter is applied at the IMPLEMENTED-protocol level
    (chain root), not at the target. A non-@dynamic protocol contributes no
    C++ base, so a chain rooted in a non-@dynamic implemented protocol
    cannot make the record C++-inherit anything -- even if a @dynamic
    ancestor sits further up the chain. This differs from
    ``sema/type_ops._inherits_protocol`` (which filters on the target,
    not the root); the closure cached on ``RecordInfo.transitive_supertypes``
    encodes the unfiltered closure used by the latter, so this helper keeps
    an explicit per-element walk and uses ``is_subtype`` only for the
    per-protocol ancestor step."""
    for p in record_info.implemented_protocols:
        pi = protocol_info_of(p)
        if pi is None or not pi.is_dynamic:
            continue
        if (pi.qualified_name() == target.qualified_name()
                or is_subtype(pi, target)):
            return True
    return False


def dyn_forward_ok(source: TpyType, target: TpyType, analyzer) -> bool:
    """True if `source` (@dynamic protocol) can be forwarded as `target`
    (@dynamic protocol) without an Adapter wrap -- same protocol (joint
    qualified_name + type_args check, so generic instantiations like
    `Container[int]` and `Container[str]` stay distinct) or inheriting
    peer. Module-level (analyzer-pure) so every caller shares one
    verdict."""
    if not (isinstance(source, NominalType) and isinstance(target, NominalType)):
        return False
    if (source.qualified_name() == target.qualified_name()
            and source.type_args == target.type_args):
        return True
    return is_subtype(protocol_info_of(source), protocol_info_of(target))


def resolve_own_source_type(arg: 'TpyExpr', declared: 'TpyType | None',
                            analyzer) -> 'OwnType | None':
    """The source ownership type if `arg` is shaped as Own[X] in C++, with
    the declared-type lookup passed IN (`declared`: the caller's
    var_types/param/field
    resolution for a name/field arg, None otherwise), so the verdict is
    analyzer-pure and THIR lowering can thread its own scope dict."""
    declared = unwrap_send_sync(declared) if declared is not None else None
    if isinstance(declared, OwnType):
        return declared
    fi = getattr(arg, 'resolved_function_info', None)
    fi_return = unwrap_send_sync(fi.return_type) if (
        fi is not None and fi.return_type is not None) else None
    if not isinstance(fi_return, OwnType):
        return None
    inner = fi_return.wrapped
    if not isinstance(inner, TypeParamRef):
        return fi_return
    ta = getattr(arg, 'inferred_type_args', None)
    if ta and fi.type_params:
        sub = dict(zip(fi.type_params, ta)).get(inner.name)
        if sub is not None:
            return OwnType(sub)
    obj = getattr(arg, 'obj', None)
    if obj is not None:
        obj_type = analyzer.get_expr_type(obj)
        if isinstance(obj_type, NominalType) and obj_type.type_args:
            record_info = analyzer.registry.get_record_for_type(obj_type)
            if record_info and record_info.type_params:
                sub = dict(zip(record_info.type_params,
                               obj_type.type_args)).get(inner.name)
                if sub is not None:
                    return OwnType(sub)
    return None


def is_async_call_with_protocol_return(arg: 'TpyExpr',
                                       arg_type: 'TpyType | None') -> bool:
    """True if `arg` is a call to an `async def` whose sema return type is
    the structural Cancellable / Awaitable wrap (the C++ value is the
    concrete coro factory struct)."""
    if not isinstance(arg, (TpyCall, TpyMethodCall)):
        return False
    fi = arg.resolved_function_info
    if fi is None or not fi.is_async:
        return False
    if not isinstance(arg_type, NominalType):
        return False
    return arg_type.qualified_name() in (qnames.CANCELLABLE, qnames.AWAITABLE)


def classify_dyn_own_arg(arg: 'TpyExpr', protocol: TpyType,
                         declared: 'TpyType | None',
                         analyzer) -> str:
    """Classify one arg bound for an `Own[@dynamic P]` slot -- the single
    verdict behind the Own[@dynamic P] argument dispatch (and, negated on
    'forward', the wrap-needed test).
    `declared` is the caller's declared-type resolution
    for the arg (var_types/param/field), None for other shapes.

    Verdicts and their renders:
      'coro_handle'   -- Own[ConcreteCoroType] source: the bound-handle
                         erasure `make_adapter<Base>(std::move(*(x)))`.
      'forward'       -- already-erased `unique_ptr<P>` (same/inheriting
                         protocol): the bare forward, no wrap.
      'async_factory' -- an async-def call rvalue: the concrete coro frame
                         moves into `make_adapter<Base>(...)`.
      'inherit'       -- an inheritance conformer: `std::make_unique<U>(x)`
                         (unique_ptr<U> converts to unique_ptr<P>).
      'structural'    -- a structural conformer: the owning
                         `make_adapter<Base>(x)` Adapter wrap.
    """
    source_own_type = resolve_own_source_type(arg, declared, analyzer)
    if source_own_type is not None:
        inner = unwrap_readonly(source_own_type.wrapped)
        if isinstance(inner, ConcreteCoroType):
            return 'coro_handle'
        if is_protocol_type(inner) and dyn_forward_ok(inner, protocol,
                                                      analyzer):
            return 'forward'
    arg_type = analyzer.get_expr_type(arg)
    if isinstance(arg_type, OwnType):
        arg_type = arg_type.wrapped
    if is_async_call_with_protocol_return(arg, arg_type):
        return 'async_factory'
    if is_dyn_protocol(arg_type) and dyn_forward_ok(arg_type, protocol,
                                                    analyzer):
        return 'forward'
    if record_inherits_dynamic(arg_type, protocol, analyzer.registry):
        return 'inherit'
    return 'structural'


def conformer_source(a: 'TpyExpr', analyzer) -> 'NominalType | None':
    """The concrete record a conformer source of the erasure wraps, for
    the source shapes the wraps take: a user-record CTOR rvalue, a
    record-typed NAME, an `Own[record]`-returning METHOD rvalue
    (`wait_for(loop.sock_accept(srv), ..)`). None for any other shape."""
    if isinstance(a, TpyCall):
        fi = a.resolved_function_info
        if fi is None or not fi.is_constructor:
            return None
    elif isinstance(a, TpyMethodCall):
        if (a.resolved_function_info is None
                or not is_rvalue_source(analyzer, a)):
            return None
    elif not isinstance(a, TpyName):
        return None
    at = analyzer.get_expr_type(a)
    at_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
            if at is not None else None)
    if isinstance(at_u, OwnType):
        at_u = unwrap_readonly(at_u.wrapped)
    # A protocol-typed source can never take the conformer wraps
    # (Adapter<P, P> over abstract P has no sizeof).
    if not (isinstance(at_u, NominalType) and at_u.is_user_record
            and not at_u.is_protocol):
        return None
    return at_u


def dyn_erasure_coercion(arg: 'TpyExpr', expected: TpyType,
                         declared: 'TpyType | None',
                         analyzer) -> 'Coercion | None':
    """The erasing conversion a source takes into an `Own[@dynamic P]`
    slot (`expected`), or None: the slot is no such slot, the source is
    already the erased pointer (forwarded bare), or no wrap takes its shape
    (the bound coroutine handle, a conformer that is no ctor, name or
    owned method result)."""
    u = unwrap_send_sync(expected)
    if not isinstance(u, OwnType):
        return None
    # RAW wrapped: an `Own[readonly[P]]` slot never takes the adapter.
    proto = u.wrapped
    if not (isinstance(proto, NominalType) and is_dyn_protocol(proto)):
        return None
    verdict = classify_dyn_own_arg(arg, proto, declared, analyzer)
    if verdict == "async_factory":
        return INTO_DYN_FACTORY
    if conformer_source(arg, analyzer) is None:
        return None
    if verdict == "inherit":
        return INTO_DYN_INHERIT
    if verdict == "structural":
        return INTO_DYN_ADAPTER
    return None


def erased_source(expr: 'TpyExpr') -> 'TpyExpr':
    """The concrete source under the `@dynamic` erasure sema published on
    it (`dyn_erasure_coercion`), else `expr`: the owning slot's copy / move
    rules read the source the erasure takes, not the erased owner."""
    if isinstance(expr, TpyCoerce) and expr.coercion in DYN_ERASURES:
        return expr.expr
    return expr
