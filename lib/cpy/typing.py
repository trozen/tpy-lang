"""CPython typing shim: re-exports stdlib typing with a working @overload.

stdlib typing.overload discards function bodies and raises NotImplementedError
when called. This shim provides a runtime dispatcher that supports TPy's
mode (b) bodied @overload -- variants carry their own bodies and dispatch
is by arity first, then isinstance on type annotations.
"""

import sys as _sys
import inspect as _inspect

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
        # Skip complex annotations (unions, generics) -- isinstance can't
        # handle them and we'd rather fall through than crash.
        if not isinstance(ann, type):
            continue
        if not isinstance(arg, ann):
            return False
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
