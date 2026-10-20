"""Invariants over the AST node definitions themselves."""

import typing
from dataclasses import fields

from .. import symbol_binding, typesys
from . import nodes as _nodes
from .nodes import TpyFieldAccess, TpyMethodCall, TpyName


def _call_slots() -> list[str]:
    """Every `TpyFieldAccess` slot that holds a dispatched method call.

    Annotations are RESOLVED rather than string-matched: the node module
    spells some of them quoted and some not, and a slot whose spelling
    differs from the one this test expected would be silently skipped --
    which is exactly the hole the test exists to close.
    """
    ns = dict(vars(_nodes))
    ns.update(vars(typesys))
    ns.update(vars(symbol_binding))
    hints = typing.get_type_hints(TpyFieldAccess, ns, include_extras=False)
    return [f.name for f in fields(TpyFieldAccess)
            if _is_method_call_slot(hints.get(f.name))]


def _is_method_call_slot(hint) -> bool:
    args = typing.get_args(hint)
    return bool(args) and TpyMethodCall in args and type(None) in args


def test_hidden_call_covers_every_call_slot():
    """`hidden_call` is the ONE enumeration of the calls a field access
    dispatches to; a slot added to the node and left out of it silently
    un-vetoes whatever the aggregate feeds (duplication, reordering,
    storage-key vetoes)."""
    slots = _call_slots()
    assert slots, "no TpyMethodCall-typed slots found -- did the type spelling change?"
    for slot in slots:
        access = TpyFieldAccess(obj=TpyName(name="o"), field="f")
        call = TpyMethodCall(obj=access.obj, method="m", args=[])
        setattr(access, slot, call)
        assert access.hidden_call is call, (
            f"TpyFieldAccess.{slot} holds a method call that `hidden_call` "
            f"does not report")


def test_hidden_call_is_none_for_a_plain_field_access():
    assert TpyFieldAccess(obj=TpyName(name="o"), field="f").hidden_call is None


def _shared_nodes(roots: object) -> list[object]:
    """The expression and statement nodes, lists and dicts `roots` holds at
    more than one place (a call's indexed-callee alternative aside)."""
    from .nodes import TpyExpr, TpyStmt, rewrite_tree
    met: set[int] = set()
    shared: list[object] = []

    def look(v: object) -> object:
        if isinstance(v, (TpyExpr, TpyStmt, list, dict)):
            if id(v) in met:
                shared.append(v)
            met.add(id(v))
        return v

    rewrite_tree(roots, look, skip=frozenset(("subscript_callee",)))
    return shared


def test_unshare_copies_each_later_occurrence():
    from .nodes import TpyBinOp, TpyExprStmt, unshare
    x = TpyName(name="x")
    body = [TpyExprStmt(expr=TpyBinOp(x, "+", x)), TpyExprStmt(expr=x)]
    unshare(body)
    first = body[0].expr
    assert first.left is x
    assert first.right is not x and first.right.name == "x"
    assert body[1].expr is not x and body[1].expr is not first.right
    assert not _shared_nodes(body)


def test_unshare_keeps_the_indexed_callee_alternative():
    """`f[T](a)`'s alternative reading as indexing shares the receiver with
    the call on purpose: sema resolves one reading off the other."""
    from .nodes import TpySubscript, unshare
    obj = TpyName(name="o")
    alt = TpySubscript(obj=TpyFieldAccess(obj=obj, field="m"),
                       index=TpyName(name="T"))
    call = TpyMethodCall(obj=obj, method="m", args=[], subscript_callee=alt)
    unshare([call])
    assert call.obj is obj and call.subscript_callee.obj.obj is obj


def test_class_macro_output_shares_no_node():
    """`@total_ordering` and `@dataclass(order=True)` derive their methods
    from one set of nodes; each generated method gets its own."""
    from ..thir.testutil import _compile, _entry
    source = (
        "from dataclasses import dataclass\n"
        "from functools import total_ordering\n"
        "@total_ordering\n"
        "class V:\n"
        "    def __init__(self, n: int) -> None:\n"
        "        self.n = n\n"
        "    def __eq__(self, other: 'V') -> bool:\n"
        "        return self.n == other.n\n"
        "    def __lt__(self, other: 'V') -> bool:\n"
        "        return self.n < other.n\n"
        "@dataclass(order=True)\n"
        "class P:\n"
        "    a: int\n"
        "    b: int = 2\n"
        "print(V(1) <= V(2), P(1) < P(1, 3))\n")
    _, modules = _compile(source)
    for record in _entry(modules).ast.records:
        defaults = [f.default_expr for f in getattr(record, "fields", [])]
        assert not _shared_nodes([defaults, record.methods]), record.name


def test_unshare_copies_a_container_held_twice():
    """Two methods given one `body` list: unsharing only the statements in
    it would leave both methods holding the same copies."""
    from .nodes import TpyFunction, TpyReturn, unshare
    from ..typesys import STR
    body = [TpyReturn(value=TpyName(name="x"))]
    a = TpyFunction(name="a", params=[], return_type=STR, body=body)
    b = TpyFunction(name="b", params=[], return_type=STR, body=body)
    unshare([a, b])
    assert a.body is not b.body
    assert a.body[0] is not b.body[0]
    assert a.body[0].value is not b.body[0].value
    assert not _shared_nodes([a, b])


# A builder whose terminal emits a record with two methods over one body
# and a function reading one node twice, and a function macro that puts
# the first statement's argument in the second statement too.
_SHARING_MACROS = """\
__tpy__ = {"mod": "macro"}
from tpyc.macro_api import (
    builder_macro, builder_terminal, function_macro, BuilderContext,
    FunctionMacroContext, MacroArgs, TypeInfo, types, ast,
)


@builder_macro
class Pair:
    def __init__(self, ctx: BuilderContext, args: MacroArgs) -> None:
        pass

    @builder_terminal
    def build(self, ctx: BuilderContext, args: MacroArgs) -> TypeInfo:
        body = [ast.return_(ast.field_access(ast.name("self"), "v"))]
        one = ast.function("one", [], types.int32, body, is_method=True)
        two = ast.function("two", [], types.int32, body, is_method=True)
        name = ctx.fresh_module_name("pair")
        rec = ctx.emit_record(name, [("v", types.int32)], methods=[one, two])
        n = ast.int_lit(3)
        fn = ctx.fresh_module_name("build_pair")
        ctx.emit_function(fn, [], rec.raw_type,
                          [ast.return_(ast.call(name, [ast.binop(n, "+", n)]))])
        ctx.replace_call(fn, MacroArgs())
        return rec


@function_macro
def share(ctx: FunctionMacroContext) -> None:
    first, second = ctx.body
    ctx.replace_expr(second.expr.args[0], first.expr.args[0])
"""

_SHARING_MAIN = """\
from shmacros import Pair, share

@share
def twice() -> None:
    print(1)
    print(2)

def main() -> None:
    p = Pair()
    r = p.build()
    print(r.one(), r.two())
    twice()

main()
"""


def _compile_sharing(tmp_path):
    from ..thir.testutil import _compile, _entry
    (tmp_path / "shmacros.py").write_text(_SHARING_MACROS)
    _, modules = _compile(_SHARING_MAIN, extra_lib_dirs=[tmp_path])
    return _entry(modules).ast


def test_builder_record_methods_share_no_node(tmp_path):
    ast = _compile_sharing(tmp_path)
    (record,) = [r for r in ast.records if "pair" in r.name]
    assert not _shared_nodes(record.methods)


def test_builder_function_body_shares_no_node(tmp_path):
    ast = _compile_sharing(tmp_path)
    (fn,) = [f for f in ast.functions if "build_pair" in f.name]
    assert not _shared_nodes(fn.body)


def test_function_macro_body_shares_no_node(tmp_path):
    ast = _compile_sharing(tmp_path)
    (fn,) = [f for f in ast.functions if f.name == "twice"]
    assert not _shared_nodes(fn.body)
