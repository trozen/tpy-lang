"""Every statement binding of a name takes the one binding record
(`statements.record_name_binding`): a walrus of a value type and an
assignment statement a macro or a desugar builds produce the record their
plain-declaration twin does -- ownership, loans, provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ..parse.nodes import TpyBinOp
from ..value_category import peel_coerce
from ..thir.testutil import _compile
from . import statements
from .statements import BindingOwns


@dataclass(frozen=True)
class Record:
    """What one call of the binder recorded for `name`."""
    name: str
    owns: BindingOwns
    owned_local: bool
    rvalue: bool
    loans: frozenset[tuple[str, str, object]]
    source: object


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
                          name in func.rvalue_vars, loans, init))
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
               if isinstance(peel_coerce(r.source), TpyBinOp)
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
