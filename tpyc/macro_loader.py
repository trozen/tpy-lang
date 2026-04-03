"""
TurboPython Macro Module Loader

Discovers and loads ``# tpy: macro_module`` files via CPython during
compilation. Macro modules are NOT compiled to C++ -- they run at compile
time only.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
import sys
import typing
from pathlib import Path
from typing import Any, Callable

import builtins as _builtins_module

from . import macro_api as _macro_api


# ---------------------------------------------------------------------------
# Macro module import/builtin restrictions
# ---------------------------------------------------------------------------
# Soft restrictions to keep macro modules on the stable tpyc.macro_api surface.
# NOT a security sandbox -- Python cannot be fully sandboxed via __builtins__.
# The goal is forward compatibility: when the compiler is self-hosted, macro
# code will run in a VM where only the macro_api surface is available.

# Modules that macro code is allowed to import.
_ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "tpyc.macro_api",
})

# Builtins that are NOT available in macro modules.
_BLOCKED_BUILTINS: frozenset[str] = frozenset({
    "open", "exec", "eval", "compile",
    "input", "breakpoint", "exit", "quit",
    "memoryview",
})

_original_import = _builtins_module.__import__


def _make_restricted_import(
    module_name: str, registry: "MacroRegistry",
) -> Callable:
    """Create a restricted __import__ for a macro module."""
    def _restricted_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if not name:
            raise ImportError(
                f"Macro module '{module_name}' cannot use relative imports"
            )
        for allowed in _ALLOWED_IMPORTS:
            if name == allowed or name.startswith(allowed + "."):
                return _original_import(name, *args, **kwargs)
        # Try resolving as another macro module from lib search paths
        mod = registry._resolve_macro_import(name)
        if mod is not None:
            return mod
        raise ImportError(
            f"Macro module '{module_name}' cannot import '{name}' "
            f"-- only tpyc.macro_api and other macro modules are allowed"
        )
    return _restricted_import


def _make_restricted_builtins(
    module_name: str, registry: "MacroRegistry",
) -> dict[str, Any]:
    """Create a restricted __builtins__ dict for a macro module."""
    restricted = {
        k: v for k, v in _builtins_module.__dict__.items()
        if k not in _BLOCKED_BUILTINS
    }
    restricted["__import__"] = _make_restricted_import(module_name, registry)
    return restricted


_MACRO_MODULE_RE = re.compile(r'^\s*#\s*tpy:\s+macro_module\s*$')


def is_macro_module_source(source: str) -> bool:
    """Quick check whether source text contains ``# tpy: macro_module``."""
    # Only scan the preamble (before first non-comment, non-blank line)
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            break
        if _MACRO_MODULE_RE.match(line):
            return True
    return False


def call_macro_field_function(
    registry: Any,
    call: Any,
    loc: Any,
) -> Any | None:
    """Call a macro-module function from a field default and return the result.

    If the call's resolved_import points to a loaded macro module, looks up
    the function, extracts kwargs, and calls it. Returns None if not resolvable.
    """
    from .sema.diagnostics import SemanticError

    resolved = getattr(call, 'resolved_import', None)
    if resolved is None:
        return None
    mod_name, func_name = resolved
    func = registry.get_export(mod_name, func_name)
    if func is None:
        return None

    if call.args:
        raise SemanticError(
            f"{func_name}() does not accept positional arguments", loc)

    # Unwrap literal kwargs to plain Python values so macro field functions
    # see the same types in both compiler and CPython paths.
    from .parse import (
        TpyStrLiteral, TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyNoneLiteral,
    )
    kwargs = {}
    for k, v in call.kwargs.items():
        if isinstance(v, TpyBoolLiteral):
            kwargs[k] = v.value
        elif isinstance(v, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral)):
            kwargs[k] = v.value
        elif isinstance(v, TpyNoneLiteral):
            kwargs[k] = None
        else:
            kwargs[k] = v  # non-literal (e.g. TpyName for default_factory)
    try:
        return func(**kwargs)
    except TypeError as e:
        raise SemanticError(f"{func_name}(): {e}", loc) from e


def validate_and_call_macro(
    macro_fn: Callable,
    cls_info: Any,
    kwargs: dict[str, Any],
    macro_name: str,
    loc: Any,
) -> None:
    """Validate kwargs against macro signature and invoke the macro.

    Inspects the macro function's signature to check for unknown kwargs,
    missing required kwargs, and type mismatches. Wraps errors as
    SemanticError with the decorator's source location.
    """
    # Deferred: sema.registration imports macro_loader, so importing
    # diagnostics at module level would create a circular import.
    from .sema.diagnostics import SemanticError

    sig = inspect.signature(macro_fn)
    params = sig.parameters

    # Skip the first parameter (cls: ClassInfo)
    param_list = list(params.values())
    if not param_list:
        raise SemanticError(
            f"@{macro_name}: macro function must accept a ClassInfo parameter",
            loc,
        )
    _SKIP_KINDS = (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
    macro_params = {p.name: p for p in param_list[1:] if p.kind not in _SKIP_KINDS}

    # Check for **kwargs -- if present, skip unknown-kwarg validation
    has_var_keyword = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in param_list[1:]
    )

    # Reject unknown kwargs
    if not has_var_keyword:
        for key in kwargs:
            if key not in macro_params:
                if macro_params:
                    known = "(supported: " + ", ".join(sorted(macro_params)) + ")"
                else:
                    known = "(takes no keyword arguments)"
                raise SemanticError(
                    f"@{macro_name}: unknown keyword argument '{key}' {known}",
                    loc,
                )

    # Check for missing required kwargs (no default value)
    for name, param in macro_params.items():
        if param.default is inspect.Parameter.empty and name not in kwargs:
            raise SemanticError(
                f"@{macro_name}: missing required keyword argument '{name}'",
                loc,
            )

    # Resolve annotations (handles `from __future__ import annotations`
    # which turns annotations into strings at runtime)
    try:
        hints = typing.get_type_hints(macro_fn)
    except Exception:
        hints = {}

    # Type-check values against annotations
    for key, value in kwargs.items():
        if key not in macro_params:
            continue
        ann = hints.get(key)
        if ann is None:
            continue
        if not isinstance(ann, type):
            continue
        if not isinstance(value, ann):
            raise SemanticError(
                f"@{macro_name}: '{key}' must be {ann.__name__}, "
                f"got {type(value).__name__} ({value!r})",
                loc,
            )

    # Call the macro, wrapping unexpected exceptions
    from .macro_api import MacroError, ast as _ast_builder

    _ast_builder.reset_tmp_counter()
    try:
        macro_fn(cls_info, **kwargs)
    except MacroError as e:
        raise SemanticError(str(e), e.loc or loc) from e
    except SemanticError:
        raise
    except Exception as e:
        raise SemanticError(
            f"@{macro_name}: macro raised {type(e).__name__}: {e}",
            loc,
        ) from e


_EXTRACTABLE_TYPES = (str, int, float, bool)


def _extract_macro_arg_value(macro_arg: Any, expected_type: type, param_name: str,
                              macro_name: str, loc: Any) -> Any:
    """Extract a Python value from a MacroArg for simple-typed parameters."""
    from .sema.diagnostics import SemanticError
    from .parse import TpyStrLiteral, TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral

    expr = macro_arg.expr
    _LITERAL_MAP = {
        str: (TpyStrLiteral, "a string literal"),
        int: (TpyIntLiteral, "an integer literal"),
        float: (TpyFloatLiteral, "a float literal"),
        bool: (TpyBoolLiteral, "a boolean literal"),
    }
    literal_type, desc = _LITERAL_MAP[expected_type]
    if not isinstance(expr, literal_type):
        raise SemanticError(
            f"{macro_name}(): '{param_name}' must be {desc}", loc)
    return expr.value


def expand_call_macro(
    macro_fn: Callable,
    ctx: Any,
    macro_args: list,
    macro_kwargs: dict,
    macro_name: str,
    loc: Any,
) -> Any:
    """Validate args and call a call-site macro, returning the replacement TpyExpr.

    Kwargs annotated with simple types (str, int, float, bool) are extracted
    from the MacroArg automatically. Wraps unexpected exceptions as SemanticError.
    """
    # Deferred: sema.registration imports macro_loader
    from .sema.diagnostics import SemanticError
    from .macro_api import MacroError, ast as _ast_builder

    _ast_builder.reset_tmp_counter()

    # Inspect signature: extract plain values for simple-typed kwargs
    sig = inspect.signature(macro_fn)
    params = list(sig.parameters.values())
    resolved_kwargs: dict[str, Any] = {}
    try:
        hints = typing.get_type_hints(macro_fn)
    except Exception:
        hints = {}
    for key, macro_arg in macro_kwargs.items():
        ann = hints.get(key)
        if isinstance(ann, type) and ann in _EXTRACTABLE_TYPES:
            resolved_kwargs[key] = _extract_macro_arg_value(
                macro_arg, ann, key, macro_name, loc)
        else:
            resolved_kwargs[key] = macro_arg

    try:
        result = macro_fn(ctx, *macro_args, **resolved_kwargs)
    except MacroError as e:
        raise SemanticError(str(e), e.loc or loc) from e
    except SemanticError:
        raise
    except Exception as e:
        raise SemanticError(
            f"{macro_name}(): macro raised {type(e).__name__}: {e}",
            loc,
        ) from e

    from .parse import TpyExpr
    if not isinstance(result, TpyExpr):
        raise SemanticError(
            f"{macro_name}(): call macro must return a TpyExpr, "
            f"got {type(result).__name__}",
            loc,
        )
    return result


class MacroRegistry:
    """Registry of loaded class macros.

    Maps ``(module_name, decorator_name)`` to the Python callable that
    implements the macro.
    """

    def __init__(self, search_dirs: list[Path] | None = None) -> None:
        self._macros: dict[tuple[str, str], Callable] = {}
        self._call_macros: dict[tuple[str, str], Callable] = {}
        self._modules: dict[str, Any] = {}
        self._loaded_modules: set[str] = set()
        # module_name -> {dep_module: names_or_None}
        # None means all exports, list means specific names only.
        self._macro_deps: dict[str, dict[str, list[str] | None]] = {}
        # Lib search dirs for resolving macro-to-macro imports
        self._search_dirs: list[Path] = list(search_dirs or [])
        # Guard against circular macro imports
        self._loading: set[str] = set()

    def register(self, module: str, name: str, func: Callable) -> None:
        self._macros[(module, name)] = func

    def get_macro(self, module: str, name: str) -> Callable | None:
        return self._macros.get((module, name))

    def get_call_macro(self, module: str, name: str) -> Callable | None:
        return self._call_macros.get((module, name))

    def get_export(self, module: str, name: str) -> Any | None:
        """Look up any exported name from a loaded macro module."""
        mod = self._modules.get(module)
        if mod is None:
            return None
        return getattr(mod, name, None)

    def get_deps(self, module_name: str) -> dict[str, list[str] | None]:
        """Return MACRO_DEPS for a loaded macro module.

        Returns dict mapping dep module name to list of specific names (or None
        for all exports). Empty dict if no deps declared.
        """
        return self._macro_deps.get(module_name, {})

    def _resolve_macro_import(self, name: str) -> Any | None:
        """Resolve an import to another macro module from lib search paths.

        Returns the loaded module object, or None if not found.
        """
        if name in self._modules:
            return self._modules[name]
        if name in self._loading:
            raise ImportError(f"Circular macro module import: '{name}'")
        for d in self._search_dirs:
            path = d / Path(name.replace(".", "/")).with_suffix(".py")
            if path.is_file():
                source = path.read_text()
                if is_macro_module_source(source):
                    self.load_module(name, path)
                    return self._modules.get(name)
        return None

    def is_loaded(self, module_name: str) -> bool:
        return module_name in self._loaded_modules

    def load_module(self, module_name: str, file_path: Path) -> None:
        """Load a macro module via CPython and register its exports.

        Functions decorated with ``@class_macro`` are registered automatically.
        """
        if module_name in self._loaded_modules:
            return

        # Ensure tpyc is importable (macro modules import from tpyc.macro_api)
        tpyc_parent = str(Path(__file__).resolve().parent.parent)
        if tpyc_parent not in sys.path:
            sys.path.insert(0, tpyc_parent)

        spec = importlib.util.spec_from_file_location(
            f"_tpy_macro_{module_name}", str(file_path)
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(
                f"Cannot load macro module '{module_name}' from {file_path}"
            )

        mod = importlib.util.module_from_spec(spec)
        mod.__builtins__ = _make_restricted_builtins(module_name, self)
        self._loading.add(module_name)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            _macro_api._pending_macro_deps = None
            raise RuntimeError(
                f"Error executing macro module '{module_name}' ({file_path}): {e}"
            ) from e
        finally:
            self._loading.discard(module_name)

        self._modules[module_name] = mod

        # Read deps registered via macro_deps() during module execution
        if _macro_api._pending_macro_deps is not None:
            self._macro_deps[module_name] = _macro_api._pending_macro_deps
            _macro_api._pending_macro_deps = None

        # Scan for @class_macro and @call_macro decorated functions
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if callable(obj):
                if getattr(obj, '_is_class_macro', False):
                    self.register(module_name, attr_name, obj)
                if getattr(obj, '_is_call_macro', False):
                    self._call_macros[(module_name, attr_name)] = obj

        self._loaded_modules.add(module_name)
