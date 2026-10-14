"""The module's `__tpy__` settings dict: the one reader.

`__tpy__ = {"mod": ..., "cpp_namespace": ..., "includes": [...],
"links": [...]}` declares how tpyc treats a module. It is compile-time
metadata: the parser strips the assignment from the body (like `__all__`),
and every site that decides a module's kind -- compiler routing, the macro
loader, the test harness -- reads it through `read_module_settings`.
"""

from __future__ import annotations

import ast
import io
import re
import symtable
import tokenize
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeGuard

from ..toolchain import PLATFORMS
from .imports import NonLiteralAllError, read_module_all
from .nodes import ModuleDirectives, ParseError, SourceLocation

SETTINGS_NAME = "__tpy__"

# The retired `# tpy: <directive>` comments `__tpy__` replaced, in their old
# bare-name or call shape, so prose that merely starts with `# tpy:` passes.
_STALE_DIRECTIVE_RE = re.compile(
    r'^#\s*tpy:\s+(native_module|macro_module|ext_module|cpp_namespace|include|link)'
    r'\s*(\(|$)')

_REBIND_MSG = "__tpy__ must be assigned once, at module top level"
_READ_MSG = "__tpy__ holds compile-time module settings and cannot be read"


def is_future_import(node: ast.AST) -> TypeGuard[ast.ImportFrom]:
    return isinstance(node, ast.ImportFrom) and node.module == "__future__" and node.level == 0


def is_preamble_stmt(node: ast.stmt, index: int | None = None) -> bool:
    """A statement that is not "code" for ordering rules. Without `index`
    (imports must follow no other code): docstrings, `pass` and `from
    __future__` imports. With the statement's `index` in the module body,
    Python's own `from __future__` placement rule, which `__tpy__` follows:
    only the docstring (statement 0) and `from __future__` imports."""
    if is_future_import(node):
        return True
    is_string = (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                 and isinstance(node.value.value, str))
    if index is not None:
        return is_string and index == 0
    return is_string or isinstance(node, ast.Pass)


def is_settings_header(node: ast.AST) -> bool:
    """`__tpy__ = <value>` -- the one statement form that declares settings."""
    return (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == SETTINGS_NAME)


def find_settings_header(tree: ast.Module) -> ast.Assign | None:
    for node in tree.body:
        if is_settings_header(node):
            assert isinstance(node, ast.Assign)
            return node
    return None


def read_source_settings(source: str) -> tuple[ast.Module, ModuleDirectives]:
    """Parse `source` and read its settings: the entry for callers that hold
    only text."""
    tree = ast.parse(source)
    return tree, read_module_settings(tree, source)


def module_kind(path: Path) -> Literal["native", "macro", "ext"] | None:
    """The `__tpy__["mod"]` of the module at `path` (None: ordinary)."""
    return read_source_settings(path.read_text(encoding="utf-8"))[1].kind


def read_module_settings(tree: ast.Module, source: str) -> ModuleDirectives:
    """Validate the module's `__tpy__` and return its settings.

    Raises ParseError at the offending node for a malformed dict, a
    misplaced or rebound `__tpy__`, a read of it, or a leftover `# tpy:`
    directive comment."""
    header: ast.Assign | None = None
    first_code: ast.stmt | None = None
    for index, node in enumerate(tree.body):
        if is_settings_header(node):
            if header is not None:
                raise ParseError(_REBIND_MSG, node)
            if first_code is not None:
                raise ParseError(
                    "__tpy__ must come before any other code (only the module "
                    "docstring and `from __future__` imports may precede it)", node)
            assert isinstance(node, ast.Assign)
            header = node
        elif first_code is None and not is_preamble_stmt(node, index):
            first_code = node
    settings = _read_dict(header.value) if header is not None else ModuleDirectives()
    _check_stale_comments(source, first_code)
    # A macro module runs under CPython and may read its own settings.
    _check_other_uses(tree, source, header, reads_allowed=settings.kind == "macro")
    return settings


def _check_stale_comments(source: str, first_code: ast.stmt | None) -> None:
    """Comments before the first code that still spell a retired `# tpy:`
    directive -- comments inside a multi-line header included, a docstring's
    own text excluded."""
    end = _stmt_start_line(first_code) if first_code is not None else None
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if end is not None and tok.start[0] >= end:
            break
        if tok.type == tokenize.COMMENT and _STALE_DIRECTIVE_RE.match(tok.string):
            raise ParseError(
                "'# tpy:' directives were replaced by __tpy__; write "
                "__tpy__ = {...}", loc=SourceLocation(line=tok.start[0]))


def _stmt_start_line(node: ast.stmt) -> int:
    decorators = getattr(node, "decorator_list", None) or []
    return min([node.lineno] + [d.lineno for d in decorators])


# --- the dict ---------------------------------------------------------------
#
# The schema below is the one declaration of what `__tpy__` accepts; the
# validator is generic over it and writes every message.

@dataclass(frozen=True)
class _Str:
    choices: tuple[str, ...] | None = None
    pattern: re.Pattern[str] | None = None
    pattern_error: str = ""  # formatted with the value


@dataclass(frozen=True)
class _Bool:
    pass


@dataclass(frozen=True)
class _Record:
    """A dict literal with string keys; `noun` names its keys in messages."""
    fields: dict[str, _Field]
    required: tuple[str, ...] = ()
    noun: str = "key"


@dataclass(frozen=True)
class _Entries:
    """A list or tuple of entries: a bare string is the entry `{shorthand: s}`."""
    entry: _Record
    shorthand: str


_Field = _Str | _Bool | _Record | _Entries

_SCHEMA = _Record(
    {
        "mod": _Str(choices=("native", "macro", "ext")),
        "cpp_namespace": _Str(
            pattern=re.compile(r'[A-Za-z_][A-Za-z0-9_]*(::[A-Za-z_][A-Za-z0-9_]*)*'),
            pattern_error="invalid C++ namespace {!r} (must look like 'foo' or 'foo::bar')"),
        "includes": _Entries(
            _Record({"header": _Str(), "platform": _Str(choices=tuple(PLATFORMS))},
                    required=("header",)),
            shorthand="header"),
        "links": _Entries(
            # managed=True routes through the third-party registry
            # (tpyc/build/third_party.py); otherwise a raw `-l<lib>`.
            _Record({"lib": _Str(), "managed": _Bool(),
                     "platform": _Str(choices=tuple(PLATFORMS))},
                    required=("lib",)),
            shorthand="lib"),
    },
    noun="setting")

_Value = str | bool | list[dict[str, Any]] | dict[str, Any]


def _read_dict(value: ast.expr) -> ModuleDirectives:
    settings = _validate(value, _SCHEMA, "__tpy__")
    assert isinstance(settings, dict)
    directives = ModuleDirectives(
        kind=settings.get("mod"), cpp_namespace=settings.get("cpp_namespace"))
    for entry in settings.get("includes", []):
        directives.includes.append((entry["header"], entry.get("platform")))
    for entry in settings.get("links", []):
        target = directives.third_party_deps if entry.get("managed") else directives.link_libs
        target.append((entry["lib"], entry.get("platform")))
    return directives


def _validate(node: ast.expr, field: _Field, where: str) -> _Value:
    if isinstance(field, _Str):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            raise _not_literal(node, where) or ParseError(f"{where} must be a string", node)
        if field.choices is not None and node.value not in field.choices:
            raise ParseError(
                f"{where} must be one of " + ", ".join(f'"{c}"' for c in field.choices), node)
        if field.pattern is not None and not field.pattern.fullmatch(node.value):
            raise ParseError(f"{where}: " + field.pattern_error.format(node.value), node)
        return node.value
    if isinstance(field, _Bool):
        if not (isinstance(node, ast.Constant) and type(node.value) is bool):
            raise _not_literal(node, where) or ParseError(f"{where} must be True or False", node)
        return node.value
    if isinstance(field, _Entries):
        if not isinstance(node, (ast.List, ast.Tuple)):
            raise _not_literal(node, where) or ParseError(f"{where} must be a list or tuple", node)
        entries: list[dict[str, Any]] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                entries.append({field.shorthand: elt.value})
            elif isinstance(elt, ast.Dict):
                entry = _validate(elt, field.entry, f"{where} entry")
                assert isinstance(entry, dict)
                entries.append(entry)
            else:
                raise _not_literal(elt, where) or ParseError(
                    f"{where} entries must be a string or a dict", elt)
        return entries
    if not isinstance(node, ast.Dict):
        raise ParseError(f"{where} must be a dict literal", node)
    values: dict[str, Any] = {}
    for key, val in zip(node.keys, node.values):
        if key is None:
            raise ParseError(f"{where}: `**` unpacking is not allowed; spell every key", val)
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            raise ParseError(f"{where}: keys must be string literals", key)
        name = key.value
        if name in values:
            raise ParseError(f"{where}: duplicate key {name!r}", key)
        sub_field = field.fields.get(name)
        if sub_field is None:
            raise ParseError(
                f"{where}: unknown {field.noun} {name!r} "
                f"(known: {', '.join(field.fields)})", key)
        sub_where = f'{where}["{name}"]' if field is _SCHEMA else f'{where} "{name}"'
        values[name] = _validate(val, sub_field, sub_where)
    for name in field.required:
        if name not in values:
            raise ParseError(f'{where} is missing "{name}"', node)
    return values


def _not_literal(node: ast.expr, where: str) -> ParseError | None:
    """The error for a value that is no literal at all, else None."""
    if isinstance(node, (ast.Constant, ast.List, ast.Tuple, ast.Dict)):
        return None
    return ParseError(
        f"{where} must be a literal (a string, bool, list, tuple or dict)", node)


# --- other uses of the name -------------------------------------------------

def _check_other_uses(
    tree: ast.Module, source: str, header: ast.Assign | None, *, reads_allowed: bool,
) -> None:
    """Reject every module-scope binding of `__tpy__` but the header and,
    unless `reads_allowed`, every read of it. Whether any occurrence names
    the module global is the scoping fact symtable computes; the error
    points at the first occurrence after the header."""
    if not reads_allowed:
        try:
            names = read_module_all(tree)
        except NonLiteralAllError:
            names = None  # the parser reports the bad `__all__`
        if names is not None and SETTINGS_NAME in names[0]:
            raise ParseError(_READ_MSG, loc=names[1])
    # Identifiers are NFKC-normalized, so a non-ASCII source can spell the
    # name without containing it.
    text = source if source.isascii() else unicodedata.normalize("NFKC", source)
    if text.count(SETTINGS_NAME) <= (1 if header is not None else 0):
        return
    table = _scope_table(tree, source, header, _annotations_unevaluated(tree))
    binds, reads = _module_uses(table)
    if binds or (reads and not reads_allowed):
        raise ParseError(_REBIND_MSG if binds else _READ_MSG,
                         loc=_first_occurrence(source, header))


def _scope_table(
    tree: ast.Module, source: str, header: ast.Assign | None, annotations_unevaluated: bool,
) -> symtable.SymbolTable:
    """The symtable of the module without its header, re-spelled so the
    verdict does not depend on the Python version (see _ScopeRespelling)."""
    scoped = ast.parse(source)
    if header is not None:
        scoped.body[tree.body.index(header)] = ast.Pass()
    scoped = _ScopeRespelling(annotations_unevaluated).visit(scoped)
    try:
        return symtable.symtable(ast.unparse(scoped), "<module>", "exec")
    except SyntaxError as exc:
        # Re-run on the real text for a line that points into the user's file.
        try:
            symtable.symtable(source, "<module>", "exec")
        except SyntaxError as real:
            exc = real
        raise ParseError(exc.msg, loc=SourceLocation(line=exc.lineno or 0)) from None


class _ScopeRespelling(ast.NodeTransformer):
    """List / set / dict comprehensions become generator expressions -- the
    same scoping -- because CPython inlines them into the enclosing table
    and drops a comprehension's own use of a name the enclosing scope
    already has, while a genexpr keeps its table. A function-body
    annotation is also evaluated as an expression: tpyc resolves it as a
    type, while CPython records its names only up to 3.13."""

    def __init__(self, annotations_unevaluated: bool) -> None:
        self._annotations_unevaluated = annotations_unevaluated
        self._in_function = False

    def _scope(self, node: ast.AST, in_function: bool) -> ast.AST:
        outer, self._in_function = self._in_function, in_function
        self.generic_visit(node)
        self._in_function = outer
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._scope(node, True)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._scope(node, True)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        return self._scope(node, False)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST | list[ast.stmt]:
        self.generic_visit(node)
        if not self._in_function or self._annotations_unevaluated:
            return node
        # The annotation moves out (one table per lambda in it); the stub
        # keeps the statement's scoping effect on its target.
        return [ast.Expr(node.annotation),
                ast.AnnAssign(node.target, ast.Constant(None), node.value, node.simple)]

    def visit_ListComp(self, node: ast.ListComp) -> ast.GeneratorExp:
        self.generic_visit(node)
        return ast.GeneratorExp(node.elt, node.generators)

    def visit_SetComp(self, node: ast.SetComp) -> ast.GeneratorExp:
        self.generic_visit(node)
        return ast.GeneratorExp(node.elt, node.generators)

    def visit_DictComp(self, node: ast.DictComp) -> ast.GeneratorExp:
        self.generic_visit(node)
        return ast.GeneratorExp(ast.Tuple([node.key, node.value], ast.Load()), node.generators)


def _module_uses(table: symtable.SymbolTable) -> tuple[bool, bool]:
    """Whether `table` or a scope inside it binds / reads the MODULE's
    `__tpy__`."""
    binds = reads = False
    if SETTINGS_NAME in table.get_identifiers():
        sym = table.lookup(SETTINGS_NAME)
        if table.get_type() == "module":
            # A child scope's `global __tpy__` marks the module symbol
            # declared-global too; that child's own table says whether it binds.
            bind = sym.is_assigned() or sym.is_imported()
            read = sym.is_referenced()
        else:
            bind = sym.is_declared_global() and (sym.is_assigned() or sym.is_imported())
            read = sym.is_global() and sym.is_referenced()
        binds, reads = bind, read
    for child in table.get_children():
        child_binds, child_reads = _module_uses(child)
        binds, reads = binds or child_binds, reads or child_reads
    return binds, reads


def _first_occurrence(source: str, header: ast.Assign | None) -> SourceLocation:
    """The first `__tpy__` token after the header's own target."""
    skip_line = header.lineno if header is not None else None
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if (tok.type == tokenize.NAME
                and unicodedata.normalize("NFKC", tok.string) == SETTINGS_NAME):
            if tok.start[0] == skip_line:
                skip_line = None
                continue
            return SourceLocation(line=tok.start[0])
    return SourceLocation(line=header.lineno if header is not None else 0)


def _annotations_unevaluated(tree: ast.Module) -> bool:
    """`from __future__ import annotations`: symtable then skips every
    annotation, on every Python version."""
    return any(is_future_import(node)
               and any(alias.name == "annotations" for alias in node.names)
               for node in tree.body)

