"""CPython typing shim: re-exports stdlib typing with a working @overload.

stdlib typing.overload discards function bodies and raises NotImplementedError
when called. This shim provides a runtime dispatcher that supports TPy's
mode (b) bodied @overload -- variants carry their own bodies and dispatch
is by arity first, then isinstance on type annotations.
"""

import sys as _sys
import inspect as _inspect
import collections.abc as _collections_abc

# Import the REAL stdlib typing, bypassing our shadow.
# Temporarily remove our parent directory from sys.path so the import
# machinery finds the stdlib module, not this file.
import os.path as _osp
_our_dir = _osp.dirname(_osp.abspath(__file__))
_saved_path = list(_sys.path)
_sys.path = [p for p in _sys.path if p != _our_dir]
_self_entry = _sys.modules.pop("typing", None)
try:
    import typing as _real_typing
finally:
    _sys.path = _saved_path
    _sys.modules["typing"] = _self_entry  # restore our module


# Copy all public names so `from typing import X` works for everything
for _name in dir(_real_typing):
    if not _name.startswith('__'):
        globals()[_name] = getattr(_real_typing, _name)

if hasattr(_real_typing, '__all__'):
    __all__ = list(_real_typing.__all__)


def __getattr__(name):
    # stdlib typing serves deprecated aliases (Match, Pattern, ByteString, ...)
    # lazily via its own module __getattr__, so they aren't in dir() and the
    # eager copy above misses them. Delegate any unresolved name to real typing
    # so `from typing import <lazy-name>` keeps working -- e.g. 3.14's
    # importlib.metadata does `from typing import ... Match ...`.
    return getattr(_real_typing, name)


# ---------------------------------------------------------------------------
# Runtime @overload dispatcher
# ---------------------------------------------------------------------------

_overload_registry: dict[str, list] = {}


def _arity_of(func) -> tuple[int, int]:
    """Return (min_args, max_args) for a function."""
    sig = _inspect.signature(func)
    min_args = 0
    max_args = 0
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        max_args += 1
        if p.default is _inspect.Parameter.empty:
            min_args += 1
    return min_args, max_args


def _callable_arity_of_annotation(ann) -> int | None:
    """Parameter count of a `Callable[[T1, ...], R]` / `Fn[...]` annotation,
    or None when the annotation isn't a parameterised callable. Used to
    disambiguate Fn-shape overload variants by the supplied callable's
    arity -- the compiler-side check is arity-aware, so the runtime
    dispatcher must match.
    """
    origin = getattr(ann, "__origin__", None)
    # Python 3.9+ resolves `Callable[X, R].__origin__` to
    # `collections.abc.Callable` regardless of whether the source spelled
    # `typing.Callable` or `collections.abc.Callable`.
    if origin is not _collections_abc.Callable:
        return None
    args_attr = getattr(ann, "__args__", None)
    if not args_attr or len(args_attr) < 1:
        return None
    # Callable[..., R] (open arity) has args = (Ellipsis, R) -- not arity-checkable.
    if args_attr[0] is Ellipsis:
        return None
    return len(args_attr) - 1  # last element is the return type


def _callable_arity_of_value(value) -> int | None:
    """Positional parameter count of a callable value (lambda, def, bound
    method, callable instance), or None when `inspect.signature` can't
    introspect it. Companion of `_callable_arity_of_annotation` for
    arity-based overload dispatch.
    """
    try:
        sig = _inspect.signature(value)
    except (TypeError, ValueError):
        return None
    n = 0
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        n += 1
    return n


def _type_matches(func, args: tuple) -> bool:
    """Check if positional args match the function's type annotations."""
    try:
        hints = _real_typing.get_type_hints(func)
    except Exception:
        return True  # can't resolve hints -- accept on arity alone
    params = list(_inspect.signature(func).parameters.keys())
    for i, arg in enumerate(args):
        if i >= len(params):
            break
        pname = params[i]
        if pname == "self":
            continue
        if pname not in hints:
            continue
        ann = hints[pname]
        if isinstance(ann, type):
            if not isinstance(arg, ann):
                return False
            continue
        # Fn[[T1, ...], R] / Callable[[T1, ...], R]: disambiguate by
        # callable arity. Skip when the annotation has open arity
        # (Callable[..., R]) or the arg can't be inspected.
        expected_arity = _callable_arity_of_annotation(ann)
        if expected_arity is not None:
            actual_arity = _callable_arity_of_value(arg)
            if actual_arity is not None and actual_arity != expected_arity:
                return False
        # Other complex annotations (unions, generics): skip; isinstance
        # can't handle them and we'd rather fall through than crash.
    return True


def overload(func):
    """Runtime-dispatchable @overload for CPython.

    Accumulates variants by qualified name. Each @overload call adds the
    decorated function and returns a dispatcher that tries each variant
    in registration order: arity match first, then isinstance on annotated
    parameter types.
    """
    key = func.__qualname__
    if key not in _overload_registry:
        _overload_registry[key] = []
    _overload_registry[key].append(func)
    variants = _overload_registry[key]

    def dispatcher(*args, **kwargs):
        n = len(args) + len(kwargs)
        # Pass 1: arity + type match
        for variant in variants:
            lo, hi = _arity_of(variant)
            if lo <= n <= hi and _type_matches(variant, args):
                return variant(*args, **kwargs)
        # Pass 2: arity match only (fallback for complex annotations)
        for variant in variants:
            lo, hi = _arity_of(variant)
            if lo <= n <= hi:
                return variant(*args, **kwargs)
        raise TypeError(
            f"No matching overload for {key} with {n} argument(s)")

    # Preserve metadata for introspection
    dispatcher.__name__ = func.__name__
    dispatcher.__qualname__ = func.__qualname__
    dispatcher.__module__ = func.__module__
    dispatcher.__overloaded__ = True
    return dispatcher
