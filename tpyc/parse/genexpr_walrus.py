"""Walrus targets of generator expressions that the binding scope uses.

PEP 572: a walrus inside a comprehension or generator expression binds its
target in the first enclosing function or module scope S, through any
nesting of comprehensions. TPy builds each generator expression as a
generator function of its own, so a walrus target there binds in that frame;
that is unobservable unless some occurrence of the name outside the
generator expression resolves to S. This pass finds those walruses, per
generator expression (the innermost one around the walrus), for sema to
refuse.

Python's symtable does all the scoping, in two runs over copies of the
module. Run 1 (the module as written) finds each generator expression's S.
Run 2 renames the target inside the generator expression to a name the
source never spells -- a consistent rename keeps that subtree's scoping --
and, for a function S, gives S a keyword-only parameter of the target's
name: that parameter keeps every other occurrence resolving as before, and
it is the one binding no source statement can produce, so any other flag on
S's symbol is an occurrence outside the generator expression.
"""
from __future__ import annotations

import ast
import copy
import io
import symtable
import tokenize
import unicodedata
from dataclasses import dataclass, field
from typing import Iterator

from ..identity_map import IdentityMap
from .module_settings import respelled_symtable

_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


@dataclass
class _Group:
    """The walruses of one target name whose innermost generator expression
    is `genexpr`."""
    genexpr: ast.GeneratorExp
    name: str  # as spelled
    mangled: str  # as the symtable stores it in S
    private: str | None  # the class whose name mangles it
    walruses: list[ast.NamedExpr] = field(default_factory=list)
    binds_in: ast.AST | None = None  # S: the module or a function / lambda
    write_only: bool = True
    refused: bool = False


def genexpr_walrus_used_outside(
    tree: ast.Module, source: str,
) -> IdentityMap[ast.GeneratorExp, list[ast.NamedExpr]]:
    """By generator expression: its walruses whose target has an occurrence
    outside it that resolves to the walrus's binding scope."""
    out: IdentityMap[ast.GeneratorExp, list[ast.NamedExpr]] = IdentityMap()
    if not any(isinstance(n, ast.NamedExpr)
               for gx in ast.walk(tree) if isinstance(gx, ast.GeneratorExp)
               for n in ast.walk(gx)):
        return out
    fresh = _FreshNames(source)
    groups = _collect_groups(tree)

    # Run 1 also reports the programs Python itself refuses.
    first = _Run(tree, source, fresh, IdentityMap())
    for g in groups:
        g.binds_in = first.binding_scope(g.genexpr)
        if not isinstance(g.binds_in, ast.Module):
            sym = first.symbol(g.binds_in, g.mangled)
            # A parameter or a global / nonlocal declaration of S is a use,
            # and run 2's injected parameter would clash with it.
            g.refused = sym.is_parameter() or sym.is_declared_global() or sym.is_nonlocal()
    renames: IdentityMap[ast.AST, str] = IdentityMap()
    params: list[tuple[ast.AST, str]] = []
    for g in groups:
        nested = [o.genexpr for o in groups if o is not g and o.mangled == g.mangled
                  and o.binds_in is g.binds_in]
        occurrences = list(_occurrences(g, nested, first))
        g.write_only = all(any(o is w.target for w in g.walruses) for o in occurrences)
        new = fresh.take()
        for o in occurrences:
            renames[o] = new
        if (isinstance(g.binds_in, _FUNCTIONS) and not g.refused
                and not any(s is g.binds_in and _mangle(n, g.private) == g.mangled
                            for s, n in params)):
            params.append((g.binds_in, g.name))
    second = _Run(tree, source, fresh, renames, params)

    for g in groups:
        if g.refused or any(o is not g and o.binds_in is g.binds_in
                            and o.mangled == g.mangled and not o.write_only
                            for o in groups):
            used = True
        elif isinstance(g.binds_in, ast.Module):
            used = _module_uses(second.top, g.name)
        else:
            assert g.binds_in is not None
            sym = second.symbol(g.binds_in, g.mangled)
            used = (sym.is_assigned() or sym.is_referenced() or sym.is_imported()
                    or sym.is_annotated() or second.is_cell(g.binds_in, g.mangled))
        if used:
            out.setdefault(g.genexpr, []).extend(g.walruses)
    return out


def _collect_groups(tree: ast.Module) -> list[_Group]:
    groups: list[_Group] = []
    by_genexpr: IdentityMap[ast.GeneratorExp, dict[str, _Group]] = IdentityMap()

    def visit(node: ast.AST, private: str | None, gx: ast.GeneratorExp | None) -> None:
        if isinstance(node, ast.NamedExpr) and gx is not None:
            name = node.target.id  # type: ignore[attr-defined]
            mangled = _mangle(name, private)
            of_gx = by_genexpr.setdefault(gx, {})
            if mangled not in of_gx:
                of_gx[mangled] = _Group(gx, name, mangled, private)
                groups.append(of_gx[mangled])
            of_gx[mangled].walruses.append(node)
        if isinstance(node, _COMPREHENSIONS):
            # The first iterable is evaluated by the enclosing scope.
            first = node.generators[0]
            visit(first.iter, private, gx)
            inner = node if isinstance(node, ast.GeneratorExp) else gx
            for child in _children_except(node, first):
                visit(child, private, inner)
            for child in _children_except(first, first.iter):
                visit(child, private, inner)
            return
        if isinstance(node, ast.Lambda):
            visit(node.args, private, gx)
            visit(node.body, private, None)
            return
        if isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                visit(child, node.name if any(child is s for s in node.body) else private,
                      None)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, private, gx)

    visit(tree, None, None)
    return groups


def _children_except(node: ast.AST, skip: ast.AST) -> list[ast.AST]:
    return [c for c in ast.iter_child_nodes(node) if c is not skip]


def _occurrences(g: _Group, nested: list[ast.GeneratorExp],
                 first: _Run) -> Iterator[ast.AST]:
    """The occurrences of `g`'s name that resolve to its binding scope from
    inside its generator expression: everything under it but its first
    iterable, the frames of `nested` generator expressions (same name, same
    binding scope) and the lambdas and comprehensions that bind the name
    themselves (by run 1's tables), save what those evaluate outside. An
    expression spells names only as `ast.Name` and lambda parameters."""
    gfirst = g.genexpr.generators[0]
    stack = _children_except(g.genexpr, gfirst) + _children_except(gfirst, gfirst.iter)
    while stack:
        node = stack.pop()
        spelled = node.id if isinstance(node, ast.Name) else \
            node.arg if isinstance(node, ast.arg) else None
        if spelled is not None and _mangle(spelled, g.private) == g.mangled:
            yield node
        if any(node is n for n in nested):
            stack.append(node.generators[0].iter)  # type: ignore[attr-defined]
        elif isinstance(node, (ast.Lambda,) + _COMPREHENSIONS) and first.binds(node, g.mangled):
            if isinstance(node, ast.Lambda):
                stack.extend(d for d in node.args.defaults + node.args.kw_defaults
                             if d is not None)
            else:
                stack.append(node.generators[0].iter)  # type: ignore[attr-defined]
        else:
            stack.extend(ast.iter_child_nodes(node))


def _mangle(name: str, private: str | None) -> str:
    """The spelling the symtable stores for `name` inside class `private`."""
    if private is None or not name.startswith("__") or name.endswith("__") or "." in name:
        return name
    stripped = private.lstrip("_")
    return f"_{stripped}{name}" if stripped else name


def _module_uses(top: symtable.SymbolTable, name: str) -> bool:
    """Whether anything but the renamed walruses reads, binds or declares
    module global `name`."""
    if name in top.get_identifiers():
        return True
    stack = list(top.get_children())
    while stack:
        table = stack.pop()
        stack.extend(table.get_children())
        if name not in table.get_identifiers():
            continue
        sym = table.lookup(name)
        if sym.is_global():
            return True
        # A class body reads a name it also binds from the globals until
        # its own binding runs (`class C: z = y; y = 20`).
        if table.get_type() == "class" and sym.is_referenced() and \
                (sym.is_assigned() or sym.is_imported()):
            return True
    return False


class _FreshNames:
    """Names no source identifier spells, and no mangled one (no `__`)."""

    def __init__(self, source: str) -> None:
        self._taken = {
            unicodedata.normalize("NFKC", tok.string)
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type == tokenize.NAME}
        self._next = 0

    def take(self) -> str:
        while True:
            name = f"_tpy_w{self._next}"
            self._next += 1
            if name not in self._taken:
                return name


class _Run:
    """The symtable of a copy of the module with `renames` applied and a
    keyword-only parameter added per `params` entry; every function, lambda
    and comprehension carries a marker name so its table is found."""

    def __init__(self, tree: ast.Module, source: str, fresh: _FreshNames,
                 renames: IdentityMap[ast.AST, str],
                 params: list[tuple[ast.AST, str]] | None = None) -> None:
        copied = copy.deepcopy(tree)
        counterpart: IdentityMap[ast.AST, ast.AST] = IdentityMap()
        for orig, node in zip(ast.walk(tree), ast.walk(copied)):
            counterpart[orig] = node
        for orig, new in renames.items():
            node = counterpart[orig]
            if isinstance(node, ast.Name):
                node.id = new
            else:
                node.arg = new  # type: ignore[attr-defined]
        for scope, name in params or ():
            args = counterpart[scope].args  # type: ignore[attr-defined]
            args.kwonlyargs.append(ast.arg(name))
            args.kw_defaults.append(None)
        markers: dict[str, ast.AST] = {}
        for orig in ast.walk(tree):
            if isinstance(orig, _FUNCTIONS + _COMPREHENSIONS):
                marker = fresh.take()
                markers[marker] = orig
                _add_marker(counterpart[orig], marker)
        self.top = respelled_symtable(copied, source)
        self._tables: IdentityMap[ast.AST, symtable.SymbolTable] = IdentityMap()
        self._parent: IdentityMap[ast.AST, ast.AST] = IdentityMap()
        stack: list[tuple[symtable.SymbolTable, ast.AST]] = [
            (child, tree) for child in self.top.get_children()]
        while stack:
            table, outer = stack.pop()
            owner = outer
            for ident in table.get_identifiers():
                if ident in markers and table.lookup(ident).is_referenced():
                    owner = markers[ident]
                    if owner in self._tables:
                        raise AssertionError(f"two symtable tables carry {ident}")
                    self._tables[owner] = table
                    self._parent[owner] = outer
            stack.extend((child, owner) for child in table.get_children())
        missing = [m for m, node in markers.items() if node not in self._tables]
        if missing:
            raise AssertionError(f"no symtable table carries {missing[0]}")
        self._tables[tree] = self.top

    def binding_scope(self, gx: ast.GeneratorExp) -> ast.AST:
        """The scope a walrus in `gx`'s frame binds in."""
        scope = self._parent[gx]
        while isinstance(scope, _COMPREHENSIONS):
            scope = self._parent[scope]
        return scope

    def symbol(self, scope: ast.AST, name: str) -> symtable.Symbol:
        try:
            return self._tables[scope].lookup(name)
        except KeyError:
            raise AssertionError(
                f"the binding scope's table has no symbol {name!r}") from None

    def binds(self, scope: ast.AST, name: str) -> bool:
        """Whether `scope`'s own table binds `name` rather than resolving it
        outward."""
        table = self._tables[scope]
        if name not in table.get_identifiers():
            return False
        sym = table.lookup(name)
        return not (sym.is_free() or sym.is_global())

    def is_cell(self, scope: ast.AST, name: str) -> bool:
        # No public accessor for the cell scope on 3.12 - 3.14.
        flags = self._tables[scope]._table.symbols[name]  # type: ignore[attr-defined]
        return (flags >> symtable.SCOPE_OFF) & symtable.SCOPE_MASK == symtable.CELL  # type: ignore[attr-defined]


def _add_marker(node: ast.AST, marker: str) -> None:
    ref = ast.Name(marker, ast.Load())
    if isinstance(node, _COMPREHENSIONS):
        node.generators[-1].ifs.append(ref)
    elif isinstance(node, ast.Lambda):
        node.body = ast.Subscript(ast.Tuple([ref, node.body], ast.Load()),
                                  ast.Constant(1), ast.Load())
    else:
        node.body.insert(0, ast.Expr(ref))  # type: ignore[attr-defined]
