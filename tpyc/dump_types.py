"""
Dump documentation for builtin types in markdown format.

Walks the .py stubs under lib/tpy/ and prints a module-by-module summary
of public classes, protocols, functions, and type aliases. Uses Python's
ast module -- not tpyc's parser -- because the goal is to show what the
source says, not to resolve types through sema.
"""

from __future__ import annotations

import ast as pyast
import io
import re
from contextlib import redirect_stdout
from pathlib import Path

from . import get_lib_dir


def dump_builtin_types() -> None:
    """Print markdown documentation for all public stubs under lib/tpy/."""
    print("# TurboPython Builtin Types\n")
    print("Auto-generated from .py stubs under `lib/tpy/`.\n")

    # Collect rendered module sections first so we can emit a TOC ahead of
    # them, then print everything. Only modules with public content end up
    # in `sections`.
    lib_dir = get_lib_dir() / "tpy"
    sections: list[tuple[str, str]] = []   # (module_name, rendered_body)
    for py_file in sorted(lib_dir.rglob("*.py")):
        if _should_skip(py_file):
            continue
        rendered = _render_module(py_file, lib_dir)
        if rendered is not None:
            sections.append(rendered)

    if sections:
        print("## Modules\n")
        for module_name, _ in sections:
            anchor = _gfm_anchor(f"`{module_name}`")
            print(f"- [`{module_name}`](#{anchor})")
        print()

    for _, body in sections:
        print(body, end="")


# ---------------------------------------------------------------------------
# Module-level filtering
# ---------------------------------------------------------------------------

# Files that exist but are internal infrastructure, not user-facing API.
_SKIP_BASENAMES: frozenset[str] = frozenset({
    "_version.py",          # compile-time macro module (internal)
    "_macro_helpers.py",    # shared helpers for @dataclass-style macros
})


def _should_skip(path: Path) -> bool:
    if "__pycache__" in path.parts:
        return True
    if path.name in _SKIP_BASENAMES:
        return True
    return False


# ---------------------------------------------------------------------------
# Per-module dump
# ---------------------------------------------------------------------------


def _render_module(path: Path, lib_dir: Path) -> tuple[str, str] | None:
    """Render a single module to markdown. Returns (module_name, body) or None
    if the module has no public content worth documenting."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = pyast.parse(source)
    except SyntaxError:
        return None

    allow = _extract_all_whitelist(tree)
    classes = [n for n in tree.body if isinstance(n, pyast.ClassDef)]
    functions = [n for n in tree.body
                 if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef))]
    type_aliases = [n for n in tree.body if isinstance(n, pyast.TypeAlias)]
    # Typed module-level constants: e.g. `__version__: str = ...` in
    # tpy/version.py. User-visible, surface them like type aliases.
    ann_constants = [n for n in tree.body
                     if isinstance(n, pyast.AnnAssign)
                     and isinstance(n.target, pyast.Name)]

    classes = [c for c in classes if _is_public(c.name, allow)]
    functions = [f for f in functions if _is_public(f.name, allow)]
    type_aliases = [t for t in type_aliases
                    if isinstance(t.name, pyast.Name) and _is_public(t.name.id, allow)]
    ann_constants = [a for a in ann_constants
                     if _is_public(a.target.id, allow)]

    if not (classes or functions or type_aliases or ann_constants):
        return None

    rel = path.relative_to(lib_dir).as_posix()
    module_name = _path_to_module(rel)

    buf = io.StringIO()
    with redirect_stdout(buf):
        print(f"## `{module_name}`\n")
        module_doc = _get_docstring(tree)
        if module_doc:
            print(module_doc + "\n")
        for alias in type_aliases:
            _print_type_alias(alias)
        for const in ann_constants:
            _print_ann_constant(const)
        for cls in classes:
            _print_class(cls)
        for fn in functions:
            _print_function(fn)

    return module_name, buf.getvalue()


def _get_docstring(node: pyast.AST) -> str | None:
    """Return the node's docstring, or None.

    Applies to modules, classes, and functions -- anything
    pyast.get_docstring accepts. `clean=True` applies inspect.cleandoc
    (dedents + strips leading/trailing blank lines), so we don't need
    further normalisation here.
    """
    if not isinstance(node, (pyast.Module, pyast.ClassDef,
                              pyast.FunctionDef, pyast.AsyncFunctionDef)):
        return None
    raw = pyast.get_docstring(node, clean=True)
    return raw or None


def _indent(text: str, prefix: str) -> str:
    """Prefix every line of text; used to render docstrings under list items."""
    return "\n".join(prefix + line if line else line for line in text.splitlines())


# GFM-style anchor derivation: lowercase, strip characters that aren't
# letters/digits/spaces/hyphens, replace spaces with hyphens. Matches what
# GitHub / glow / most markdown renderers produce for a header.
_ANCHOR_STRIP = re.compile(r"[^\w\s-]", flags=re.UNICODE)


def _gfm_anchor(header_text: str) -> str:
    text = header_text.lower()
    text = _ANCHOR_STRIP.sub("", text)
    return text.replace(" ", "-")


def _extract_all_whitelist(tree: pyast.Module) -> frozenset[str] | None:
    """Return the set of names in __all__, or None if __all__ is not defined."""
    for stmt in tree.body:
        if (isinstance(stmt, pyast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], pyast.Name)
                and stmt.targets[0].id == "__all__"
                and isinstance(stmt.value, (pyast.List, pyast.Tuple))):
            names: list[str] = []
            for elt in stmt.value.elts:
                if isinstance(elt, pyast.Constant) and isinstance(elt.value, str):
                    names.append(elt.value)
            return frozenset(names)
    return None


def _is_public(name: str, allow: frozenset[str] | None) -> bool:
    if allow is not None:
        return name in allow
    # Dunder names (__foo__) are public-by-convention module metadata
    # (e.g. __version__, __all__). Treat them as public here.
    if name.startswith("__") and name.endswith("__"):
        return True
    return not name.startswith("_")


def _path_to_module(rel: str) -> str:
    if rel.endswith("/__init__.py"):
        rel = rel[: -len("/__init__.py")]
    elif rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


# ---------------------------------------------------------------------------
# Printers
# ---------------------------------------------------------------------------


def _print_type_alias(alias: pyast.TypeAlias) -> None:
    if not isinstance(alias.name, pyast.Name):
        return
    name = alias.name.id
    value = _unparse(alias.value)
    print(f"- type `{name}` = `{value}`\n")


def _print_ann_constant(const: pyast.AnnAssign) -> None:
    """Render a module-level typed constant (e.g. `__version__: str = ...`)."""
    if not isinstance(const.target, pyast.Name):
        return
    name = const.target.id
    annotation = _unparse(const.annotation)
    print(f"- `{name}: {annotation}`\n")


def _print_class(cls: pyast.ClassDef) -> None:
    name = cls.name
    bases = [_unparse(b) for b in cls.bases]
    is_protocol = any("Protocol" in b for b in bases)
    decorators = [_unparse(d) for d in cls.decorator_list]
    suffix = " (protocol)" if is_protocol else ""

    header_prefix = " ".join(f"@{d}" for d in decorators)
    header_prefix = f"{header_prefix} " if header_prefix else ""
    print(f"### {header_prefix}{name}{suffix}\n")

    if bases:
        print(f"Extends: `{', '.join(bases)}`\n")

    doc = _get_docstring(cls)
    if doc:
        print(doc + "\n")

    methods: list[pyast.FunctionDef | pyast.AsyncFunctionDef] = [
        n for n in cls.body
        if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef))
    ]
    # All `__init__` overloads go under Constructor; everything else
    # visible goes under Methods.
    inits = [m for m in methods if m.name == "__init__"]
    other_methods = [m for m in methods
                     if m.name != "__init__" and _method_is_visible(m)]

    if inits:
        print("**Constructor:**\n")
        for init in inits:
            _print_callable_item(name, init, is_method=True, skip_self=True)
        print()

    if other_methods:
        print("**Methods:**\n")
        for m in other_methods:
            _print_callable_item(m.name, m, is_method=True)
        print()


def _method_is_visible(m: pyast.FunctionDef | pyast.AsyncFunctionDef) -> bool:
    # Show dunder methods (__len__, __iter__, __str__, ...) but hide private
    # helpers (_foo) and the __init__ already printed as Constructor.
    if m.name.startswith("__") and m.name.endswith("__"):
        return True
    return not m.name.startswith("_")


def _print_function(fn: pyast.FunctionDef | pyast.AsyncFunctionDef) -> None:
    decorators = [_unparse(d) for d in fn.decorator_list]
    prefix = " ".join(f"@{d}" for d in decorators)
    prefix = f"{prefix} " if prefix else ""
    print(f"### {prefix}{fn.name}()\n")
    _print_callable_item(fn.name, fn)
    print()


def _print_callable_item(
    name: str,
    fn: pyast.FunctionDef | pyast.AsyncFunctionDef,
    *,
    is_method: bool = False,
    skip_self: bool = False,
) -> None:
    """Render one list-item line for a callable, with docstring below when
    present. Docstring is indented two spaces so markdown renderers keep it
    as a continuation of the list item. Trailing blank line ensures the
    next list item isn't parsed as a continuation paragraph of this one
    (CommonMark "loose list" requirement once any item has block content).
    """
    sig = _format_function_signature(name, fn, is_method=is_method, skip_self=skip_self)
    print(f"- `{sig}`")
    doc = _get_docstring(fn)
    if doc:
        print()
        print(_indent(doc, "  "))
        print()


def _format_function_signature(
    name: str,
    fn: pyast.FunctionDef | pyast.AsyncFunctionDef,
    *,
    is_method: bool = False,
    skip_self: bool = False,
) -> str:
    parts: list[str] = []
    args = fn.args
    positional = list(args.posonlyargs) + list(args.args)
    for i, arg in enumerate(positional):
        if skip_self and is_method and i == 0 and arg.arg in ("self", "cls"):
            continue
        parts.append(_format_arg(arg))
    if args.vararg is not None:
        parts.append("*" + _format_arg(args.vararg))
    elif args.kwonlyargs:
        # Bare `*` separator marks the start of keyword-only parameters
        # when there's no *args to absorb positional overflow. Required
        # for correct rendering of e.g. dataclasses.field(*, default, ...).
        parts.append("*")
    for arg in args.kwonlyargs:
        parts.append(_format_arg(arg))
    if args.kwarg is not None:
        parts.append("**" + _format_arg(args.kwarg))

    ret = _unparse(fn.returns) if fn.returns is not None else "..."
    return f"{name}({', '.join(parts)}) -> {ret}"


def _format_arg(arg: pyast.arg) -> str:
    if arg.annotation is not None:
        return f"{arg.arg}: {_unparse(arg.annotation)}"
    return arg.arg


def _unparse(node: pyast.AST | None) -> str:
    if node is None:
        return "..."
    try:
        return pyast.unparse(node)
    except Exception:
        return "<?>"
