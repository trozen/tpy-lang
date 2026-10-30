"""Every statement binding of a name takes the one binding record
(`statements.record_name_binding`): a walrus of a value type, an
assignment statement a macro or a desugar builds, and a statement unpack
target produce the record their plain-declaration twin does -- ownership,
loans, provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ..diagnostics import SemanticError
from ..parse.nodes import TpyBinOp
from ..value_category import peel_coerce
from ..thir.testutil import _compile
from . import statements
from .context import BindingSource
from .statements import BindingOwns


@dataclass(frozen=True)
class Record:
    """What one call of the binder recorded for `name`."""
    name: str
    owns: BindingOwns
    owned_local: bool
    rvalue: bool
    loans: frozenset[tuple[str, str, object]]
    source: BindingSource
    param_derived: bool
    safe_to_return: bool


_PRELUDE = """\
from tpy import int32, Own
class P:
    def __init__(self, n: int32) -> None:
        self.n = n
"""


@pytest.fixture
def records(monkeypatch: pytest.MonkeyPatch) -> list[Record]:
    out: list[Record] = []
    real = statements.record_name_binding

    def spy(an: Any, name: str, var_type: Any, init: Any,
            **kw: Any) -> BindingOwns:
        owns = real(an, name, var_type, init, **kw)
        func = an.ctx.func
        loans = frozenset(
            (str(key), info.kind.name, info.elem_index)
            for key, holders in func.borrow_tracker.loans.items()
            for holder, info in holders.items() if holder == name)
        out.append(Record(name, owns, name in func.owned_locals,
                          name in func.rvalue_vars, loans, init,
                          func.bp_is_param_derived(name),
                          func.bp_is_safe_to_return(name)))
        return owns

    monkeypatch.setattr(statements, "record_name_binding", spy)
    return out


def _of(records: list[Record], name: str) -> list[Record]:
    return [r for r in records if r.name == name]


def test_value_walrus_records_as_its_declaration(
        records: list[Record]) -> None:
    _compile(_PRELUDE + """\
def f(xs: list[int32]) -> int32:
    m = len(xs)
    if (n := len(xs)) > 2:
        return n + m
    return m
""")
    (decl,) = _of(records, "m")
    (walrus,) = _of(records, "n")
    # The walrus `n` gets exactly the record of the declaration `m`.
    assert (walrus.owns, walrus.owned_local, walrus.rvalue, walrus.loans) \
        == (decl.owns, decl.owned_local, decl.rvalue, decl.loans)
    assert walrus.owns is BindingOwns.FRESH


def test_value_walrus_rebind_records(records: list[Record]) -> None:
    _compile(_PRELUDE + """\
def f(xs: list[int32]) -> int32:
    n = 0
    while (n := n + len(xs)) < 10:
        pass
    return n
""")
    first, rebind = _of(records, "n")
    assert rebind.owns is first.owns is BindingOwns.FRESH


def test_desugared_assignment_takes_the_record(
        records: list[Record]) -> None:
    # The suspension desugar of a chained comparison with an `await` builds
    # `TpyAssign(result, cmp)` statements (the parser builds `TpyVarDecl`
    # for a written `x = v`); each takes the binding record.
    _compile(_PRELUDE + """\
async def g() -> int32:
    return 1
async def f(x: int32) -> bool:
    return x < (await g()) < 3
""")
    rebinds = [r for r in records
               if isinstance(peel_coerce(r.source.value), TpyBinOp)
               and r.name.startswith("__")]
    assert len(rebinds) >= 2
    assert all(r.owns is BindingOwns.FRESH for r in rebinds)


def test_walrus_of_owned_optional_call_owns(records: list[Record]) -> None:
    _compile(_PRELUDE + """def mk() -> Own[P] | None:
    return P(1)
def f() -> int32:
    if (w := mk()) is not None:
        return w.n
    return 0
""")
    (w,) = _of(records, "w")
    # The walrus owns the call's fresh result, as its declaration twin does.
    assert w.owns is BindingOwns.FRESH and w.owned_local


def test_unpack_target_records_as_its_element_read(
        records: list[Record]) -> None:
    _compile(_PRELUDE + """\
def f(pair: tuple[P, int32]) -> int32:
    a, k = pair
    b = pair[0]
    return a.n + b.n + k
""")
    (a,) = _of(records, "a")
    (b,) = _of(records, "b")
    assert a.source.path == (0,)
    # `a` borrows element 0 of `pair` as `b = pair[0]` does.
    assert a.owns is b.owns is BindingOwns.BORROW
    assert a.loans == b.loans
    assert ("pair", "ELEMENT", ("lit", 0)) in a.loans
    (k,) = _of(records, "k")
    assert k.loans == frozenset()


def test_borrowed_element_of_value_call_stays_borrowed(
        records: list[Record]) -> None:
    # `pick` returns a tuple by value whose element 0 it lends from `m`: the
    # target borrows that element, whatever the whole call's category.
    _compile(_PRELUDE + """\
class M:
    p: P
    def __init__(self) -> None:
        self.p = P(1)
def pick(m: M) -> tuple[P, int32]:
    return (m.p, 3)
def f(m: M) -> int32:
    a, k = pick(m)
    return a.n + k
""")
    (a,) = _of(records, "a")
    assert not a.source.handover and a.source.lvalue
    assert a.owns is BindingOwns.BORROW
    assert not a.owned_local


def test_unpack_target_takes_over_a_handed_element(
        records: list[Record]) -> None:
    _compile(_PRELUDE + """\
def mk() -> tuple[Own[P], int32]:
    return (P(1), 2)
def f() -> int32:
    a, k = mk()
    return a.n + k
""")
    (a,) = _of(records, "a")
    assert a.owns is BindingOwns.FRESH
    assert a.owned_local and a.rvalue


def test_unpack_target_provenance_is_its_element_reads(
        records: list[Record]) -> None:
    _compile(_PRELUDE + """\
def f(t: tuple[P, int32]) -> P:
    a, k = t
    b = t[0]
    return a
""")
    (a,) = _of(records, "a")
    (b,) = _of(records, "b")
    # A borrowed element lives where its element read does: in the param.
    assert (a.param_derived, a.safe_to_return) \
        == (b.param_derived, b.safe_to_return) == (True, True)


def test_handed_over_unpack_target_is_a_fresh_local(
        records: list[Record]) -> None:
    _compile(_PRELUDE + """\
def f(t: tuple[Own[P], int32]) -> int32:
    a, k = t
    return a.n + k
""")
    (a,) = _of(records, "a")
    # `t`'s last use hands element 0 over: `a` owns it, so a reference
    # into it may not leave the function, whatever `t` derives from.
    assert a.source.handover and a.owned_local
    assert (a.param_derived, a.safe_to_return) == (False, False)


def test_loop_head_unpack_keeps_its_own_path(records: list[Record]) -> None:
    _compile(_PRELUDE + """\
def f(pairs: list[tuple[P, int32]]) -> int32:
    t = 0
    for a, k in pairs:
        t += a.n + k
    return t
""")
    # A loop head's targets file their element loans outside the binder.
    assert _of(records, "a") == [] and _of(records, "k") == []


@pytest.mark.parametrize("view, owned, lit", [("StrView", "str", '"x"'),
                                              ("BytesView", "bytes", 'b"x"')])
def test_view_target_rebound_from_a_temporary_is_refused(
        view: str, owned: str, lit: str) -> None:
    # The binder's temporary-view check reads the target's declared view
    # type, so an unpack target refuses the dying source as its twin does.
    with pytest.raises(SemanticError,
                       match=f"Cannot bind {view} 'a' to a temporary view"):
        _compile(f"""\
from tpy import int32, {view}
def mk(n: int32) -> tuple[{owned}, int32]:
    return ({lit} * n, n)
def f() -> int32:
    a: {view} = {lit}
    k = 0
    a, k = mk(2)
    return len(a) + k
""")


@pytest.mark.parametrize("bind", ["b, k = (mk(i + 1), 3)",
                                  "b = (mk(i + 1), 3)[0]"])
def test_view_target_of_a_literal_unpack_temporary_is_refused(
        bind: str) -> None:
    # A literal unpack binds its target to the parser's temp; the check
    # asks the element the temp holds, as the element read twin does.
    with pytest.raises(SemanticError,
                       match="Cannot bind StrView 'b' to a temporary view"):
        _compile(f"""\
from tpy import int32, StrView
def mk(n: int32) -> str:
    return "long string materialization " * n
def f() -> int32:
    b: StrView = "x"
    k = 0
    for i in range(2):
        {bind}
    return len(b) + k
""")


@pytest.mark.parametrize("view, bind", [
    ("StrView", "b, k = (mk(i + 1), 3)"),
    ("StrView", "b = (mk(i + 1), 3)[0]"),
    ("StrView", 'b, k = (p + "x", 1)'),
    ("StrView", 'b = p + "x"'),
    ("StrView", "b, k = (p + p, 1)"),
    ("StrView", "b = p + p"),
    ("BytesView", 'b, k = (q + b"x", 1)'),
    ("BytesView", 'b = q + b"x"'),
])
def test_view_target_of_a_coerced_literal_unpack_temporary_is_refused(
        view: str, bind: str) -> None:
    # An owning element (a `String`, a concatenation) reaches the view
    # target through a coercion around the temp's name; the check still
    # asks the element the temp holds, as the twin does.
    with pytest.raises(SemanticError,
                       match=f"Cannot bind {view} 'b' to a temporary view"):
        _compile(f"""\
from tpy import int32, StrView, BytesView, String
def mk(n: int32) -> String:
    return String("long string materialization " * n)
def f(p: str, q: bytes) -> int32:
    b: {view} = {'"x"' if view == "StrView" else 'b"x"'}
    k = 0
    for i in range(2):
        {bind}
    return len(b) + k
""")


def test_view_walrus_of_a_temporary_is_refused() -> None:
    # The walrus takes the binder's view check: refused in sema.
    with pytest.raises(SemanticError,
                       match="Cannot bind StrView 'a' to a temporary view"):
        _compile("""\
from tpy import int32, StrView
def mk(n: int32) -> str:
    return "long string materialization " * n
def f() -> int32:
    a: StrView = "x"
    if (a := mk(2)):
        return len(a)
    return 0
""")
