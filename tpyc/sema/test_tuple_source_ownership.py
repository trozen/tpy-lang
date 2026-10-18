"""Sema's per-element facts about a tuple SOURCE at an owning slot: which
element paths it lends (`tuple_borrowed_paths`), which elements it hands
over as objects of its own (`tuple_source_elem_owned`, derived from it) and
which elements the slot copies (`own_element_copies`, by index) -- and of
the slot: which element paths it owns (`owned_slot_paths`)."""

import pytest

from ..diagnostics import SemanticError
from ..parse import (TpyAssign, TpyCall, TpyCoerce, TpyExpr, TpyFieldAccess,
                     TpyIfExpr, TpyMethodCall, TpyName, TpyReturn, TpySubscript,
                     TpyVarDecl, TpyYield)
from ..sema.alias_rebind import (owned_slot_paths, tuple_borrowed_paths,
                                 tuple_source_elem_owned)
from ..thir.testutil import _compile, _entry

PRELUDE = """\
from tpy import int32, Own
class A:
    def __init__(self, x: int32) -> None:
        self.x = x
class H:
    p: tuple[A, int32]
    def __init__(self) -> None:
        self.p = (A(1), 2)
    def get(self, a: A) -> tuple[A, int32]:
        return (a, 2)
    def ref_pair(self) -> tuple[A, int32]:
        return self.p
def mk(a: A) -> tuple[A, int32]:
    return (a, 1)
def mixed(a: A) -> tuple[Own[A], A]:
    return (A(a.x), a)
def owning(a: A) -> Own[tuple[A, A]]:
    return (A(a.x), A(a.x))
def mk2(a: A) -> tuple[A, A]:
    return (a, a)
def nested_mark(a: A) -> tuple[A, tuple[Own[A], int32]]:
    return (a, (A(2), 1))
def nested_mixed(a: A) -> tuple[Own[tuple[A, int32]], A]:
    return ((A(2), 1), a)
"""


def _returned(source: str) -> dict[str, tuple[TpyExpr, object]]:
    """The returned expression of every function of `source` (after the
    prelude), coercions peeled, by function name, with the analyzer."""
    _compiler, modules = _compile(PRELUDE + source)
    entry = _entry(modules)
    out = {}
    for f in entry.ast.functions:
        for s in f.body:
            if isinstance(s, TpyReturn) and s.value is not None:
                v = s.value
                while isinstance(v, TpyCoerce):
                    v = v.expr
                out[f.name] = (v, entry.analyzer)
    return out


@pytest.mark.parametrize("fn, kind", [
    ("r_call", TpyCall),
    ("r_method", TpyMethodCall),
    ("r_field", TpyFieldAccess),
    ("r_subscript", TpySubscript),
    ("r_select_name", TpyIfExpr),
])
def test_copied_elements_recorded_for_every_source_kind(fn: str,
                                                        kind: type) -> None:
    returned = _returned("""\
def r_call(a: A) -> Own[tuple[A, int32]]:
    return mk(a)
def r_method(h: H, a: A) -> Own[tuple[A, int32]]:
    return h.get(a)
def r_field(h: H) -> Own[tuple[A, int32]]:
    return h.p
def r_subscript(xs: list[tuple[A, int32]]) -> Own[tuple[A, int32]]:
    return xs[0]
def r_select_name(a: A, c: bool) -> Own[tuple[A, int32]]:
    p = mk(a)
    return mk(a) if c else p
""")
    expr, analyzer = returned[fn]
    assert isinstance(expr, kind)
    # Element 0 is the borrowed reference the owning slot copies.
    assert analyzer.ctx.own_element_copies.get(expr) == frozenset({0})


def test_name_keeps_its_return_check_record() -> None:
    # A tuple NAME's copy is recorded by the one recorder, by index.
    expr, analyzer = _returned("""\
def r_name(a: A) -> Own[tuple[A, int32]]:
    t = mk(a)
    return t
""")["r_name"]
    assert isinstance(expr, TpyName)
    assert analyzer.ctx.own_element_copies.get(expr) == frozenset({0})


def test_owning_call_records_nothing() -> None:
    expr, analyzer = _returned("""\
def r_owning(a: A) -> Own[tuple[A, A]]:
    return owning(a)
""")["r_owning"]
    assert expr not in analyzer.ctx.own_element_copies


def _warnings(analyzer) -> list[str]:
    return [d.message for d in analyzer.ctx.diagnostics
            if d.level.value == "warning"]


def test_owning_call_nested_value_tuple_records_nothing() -> None:
    # `-> Own[tuple[...]]` hands over every element, a nested value tuple's
    # reference members included: the field store copies nothing. (The
    # `nested_owned` body itself is refused by the lowering, not by sema.)
    _compiler, modules = _compile(PRELUDE + """def nested_owned() -> Own[tuple[tuple[A, int32], int32]]:
    return ((A(1), 1), 2)
class N:
    q: tuple[tuple[A, int32], int32]
    def __init__(self) -> None:
        self.q = nested_owned()
""")
    entry = _entry(modules)
    init = next(m for r in entry.ast.all_records() if r.name == "N"
                for m in r.methods if m.name == "__init__")
    store = next(s for s in init.body if isinstance(s, TpyAssign))
    v = store.value
    while isinstance(v, TpyCoerce):
        v = v.expr
    assert isinstance(v, TpyCall)
    assert v not in entry.analyzer.ctx.own_element_copies
    assert not [w for w in _warnings(entry.analyzer) if "tuple element" in w]


def test_owned_element_yield_of_borrow_call_records_copy() -> None:
    # `yield mk(a)` at an `Own` element: the borrowed element 0 copies,
    # warned and declared (the C++ build fails there:
    # BUGS.md#yield-tuple-name-own-element).
    _compiler, modules = _compile("from typing import Iterator\n" + PRELUDE
                                  + """def gen(a: A) -> Iterator[tuple[Own[A], int32]]:
    yield mk(a)
""")
    entry = _entry(modules)
    gen = next(f for f in entry.ast.functions if f.name == "gen")
    v = next(s for s in gen.body if isinstance(s, TpyYield)).value
    while isinstance(v, TpyCoerce):
        v = v.expr
    assert isinstance(v, TpyCall)
    assert entry.analyzer.ctx.own_element_copies.get(v) == frozenset({0})
    assert any("copies A into owned storage (tuple element 0)" in w
               for w in _warnings(entry.analyzer))


def test_field_returning_method_owns_nothing() -> None:
    # A method handing back a field of `self` hands over no element.
    inits, analyzer = _initializers("r = h.ref_pair()\nprint(r[0].x)")
    expr = inits["r"]
    assert isinstance(expr, TpyMethodCall)
    slot = analyzer.get_expr_type(expr)
    assert tuple_source_elem_owned(analyzer.ctx, expr, slot) == (False, False)


def _initializers(body: str) -> tuple[dict[str, TpyExpr], object]:
    """The initializer of every local of `subject` (after the prelude),
    coercions peeled, by name, with the analyzer."""
    _compiler, modules = _compile(
        PRELUDE + "def subject(c: bool, a: A, h: H, t: tuple[A, A]) -> None:\n"
        + "".join("    " + line + "\n" for line in body.splitlines()))
    entry = _entry(modules)
    subject = next(f for f in entry.ast.functions if f.name == "subject")
    out = {}
    for st in subject.body:
        if isinstance(st, TpyVarDecl) and st.init is not None:
            v = st.init
            while isinstance(v, TpyCoerce):
                v = v.expr
            out[st.name] = v
    return out, entry.analyzer


@pytest.mark.parametrize("name, expected", [
    # A borrow-returning call hands nothing over.
    ("borrow", (False, False)),
    # The Own positions of the declared return.
    ("mix", (True, False)),
    # Every reference element of `-> Own[tuple[...]]`.
    ("own", (True, True)),
    # A select owns an element only where every arm does.
    ("sel", (True, False)),
    ("sel_own", (True, True)),
    # A NAME arm owns nothing.
    ("sel_name", (False, False)),
    # A name, a field and a literal of borrowed members own nothing.
    ("alias", (False, False)),
    ("field", (False, False)),
    ("lit", (False, False)),
])
def test_rvalue_source_ownership_per_arm(name: str,
                                         expected: tuple[bool, ...] | None
                                         ) -> None:
    inits, analyzer = _initializers("""\
borrow = mk2(a)
mix = mixed(a)
own = owning(a)
sel = mixed(a) if c else mixed(a)
sel_own = owning(a) if c else owning(a)
sel_name = mixed(a) if c else mix
alias = t
field = h.p
lit = (a, a)
print(borrow[0].x, mix[0].x, own[0].x, sel[0].x, sel_own[0].x)
print(sel_name[0].x, alias[0].x, field[0].x, lit[0].x)
""")
    expr = inits[name]
    slot = analyzer.get_expr_type(expr)
    assert tuple_source_elem_owned(analyzer.ctx, expr, slot) == expected


@pytest.mark.parametrize("name, expected", [
    # A literal lends each member it does not capture by value.
    ("lit", {(0,), (1,)}),
    # A call: the plain reference elements of its declared return.
    ("borrow", {(0,), (1,)}),
    # An `Own` marker at any depth hands its subtree over.
    ("mark", {(0,)}),
    # A wholly-owned nested element lends nothing; its sibling does.
    ("nest_mix", {(1,)}),
    # `-> Own[tuple]` lends nothing.
    ("own", set()),
    # A select lends what either arm lends.
    ("sel", {(1,)}),
    ("sel_name", {(0,), (1,)}),
    # Every reference path of a name, a field, a subscript.
    ("alias", {(0,), (1,)}),
    ("field", {(0,)}),
    ("sub", {(0,)}),
    # A method handing back its own field lends it.
    ("ref", {(0,)}),
])
def test_borrowed_paths_per_source_kind(name: str,
                                        expected: set[tuple[int, ...]]
                                        ) -> None:
    inits, analyzer = _initializers("""\
lit = (a, a)
borrow = mk2(a)
mark = nested_mark(a)
nest_mix = nested_mixed(a)
own = owning(a)
sel = mixed(a) if c else mixed(a)
sel_name = mk2(a) if c else t
alias = t
field = h.p
xs = [h.p]
sub = xs[0]
ref = h.ref_pair()
print(lit[0].x, borrow[0].x, mark[0].x, nest_mix[1].x, own[0].x, sel[0].x)
print(sel_name[0].x, alias[0].x, field[0].x, sub[0].x, ref[0].x)
""")
    expr = inits[name]
    slot = analyzer.get_expr_type(expr)
    assert tuple_borrowed_paths(analyzer.ctx, expr, slot) == expected


def test_borrowed_paths_none_for_no_tuple() -> None:
    inits, analyzer = _initializers("n = a\nprint(n.x)")
    expr = inits["n"]
    assert tuple_borrowed_paths(analyzer.ctx, expr,
                                analyzer.get_expr_type(expr)) is None


def _records(source: str) -> tuple[dict[int, object], list[str]]:
    """Every `own_element_copies` record of `source` (after the prelude) by
    line, with the warnings."""
    _compiler, modules = _compile(PRELUDE + source)
    entry = _entry(modules)
    ctx = entry.analyzer.ctx
    out: dict[int, object] = {}
    for e in list(ctx.own_element_copies.keys()):
        if e.loc is not None:
            out[e.loc.line - PRELUDE.count("\n")] = ctx.own_element_copies[e]
    return out, _warnings(entry.analyzer)


def test_select_with_name_arm_records_at_every_sink() -> None:
    # A borrowing call or a name, at a return, a whole `Own` argument, an
    # `Own` element argument and an append: element 0 copies at each.
    records, warnings = _records("""\
def take(p: Own[tuple[A, int32]]) -> int32:
    return p[0].x
def take_elem(p: tuple[Own[A], int32]) -> int32:
    return p[0].x
def at_return(a: A, c: bool) -> Own[tuple[A, int32]]:
    p = mk(a)
    return mk(a) if c else p
def at_param(a: A, c: bool) -> int32:
    p = mk(a)
    return take(mk(a) if c else p)
def at_element_param(a: A, c: bool) -> int32:
    p = mk(a)
    return take_elem(mk(a) if c else p)
def at_append(a: A, c: bool) -> None:
    p = mk(a)
    xs: list[tuple[A, int32]] = []
    xs.append(mk(a) if c else p)
""")
    assert records == {7: frozenset({0}), 10: frozenset({0}),
                       13: frozenset({0}), 17: frozenset({0})}
    assert sum("copies A into owned storage (tuple element 0)" in w
               for w in warnings) == 4


def test_nested_mixed_records_only_the_borrowed_element() -> None:
    # Element 0 is a wholly-owned nested tuple; only element 1 copies.
    records, warnings = _records("""\
class N:
    p: tuple[tuple[A, int32], A]
    def __init__(self, a: A) -> None:
        self.p = nested_mixed(a)
""")
    assert records == {4: frozenset({1})}
    assert [w for w in warnings if "tuple element" in w] == [
        "copies A into field (tuple element 1); use copy() to make this "
        "explicit"]


def test_nested_own_markers_record_nothing() -> None:
    # `tuple[Own[A], tuple[Own[A], int32]]` into a field: every reference
    # element is handed over.
    records, warnings = _records("""\
def mixn(a: A) -> tuple[Own[A], tuple[Own[A], int32]]:
    return (A(1), (A(2), 1))
class N:
    r: tuple[A, tuple[A, int32]]
    def __init__(self, a: A) -> None:
        self.r = mixn(a)
""")
    assert records == {}
    assert not [w for w in warnings if "tuple element" in w]


def test_equal_type_select_of_owning_names_records_copy() -> None:
    # A select of two `tuple[Own[A], int32]` names has the parameter's very
    # type, yet a select arm never moves: element 0 copies. (The C++ build
    # of the program fails: the select is an lvalue at a `&&` parameter.)
    records, warnings = _records("""\
def fresh() -> tuple[Own[A], int32]:
    return (A(1), 1)
def take(p: tuple[Own[A], int32]) -> int32:
    return p[0].x
def names(c: bool) -> int32:
    p = fresh()
    q = fresh()
    r = take(p if c else q)
    return r + p[0].x + q[0].x
def call_name(c: bool) -> int32:
    p = fresh()
    r = take(fresh() if c else p)
    return r + p[0].x
""")
    assert records == {8: frozenset({0}), 12: frozenset({0})}
    assert warnings.count("copies A into owned storage (tuple element 0); "
                          "use copy() to make this explicit") == 2


NESTED = """\
class N:
    p: tuple[tuple[A, int32], int32]
    def __init__(self) -> None:
        self.p = ((A(1), 1), 2)
"""


def test_nested_slot_marker_records_its_element() -> None:
    # An `Own` marker nested in the slot owns path 0.0: the field source
    # lends it, so it copies, warned by its path and recorded by element 0.
    records, warnings = _records(NESTED + """\
def take(p: tuple[tuple[Own[A], int32], int32]) -> int32:
    return 1
def ret(n: N) -> tuple[tuple[Own[A], int32], int32]:
    return n.p
def run(n: N) -> int32:
    return take(n.p)
""")
    assert records == {8: frozenset({0}), 10: frozenset({0})}
    assert warnings == ["copies A into owned storage (tuple element 0.0); "
                        "use copy() to make this explicit"] * 2


def test_nested_slot_marker_at_owned_yield() -> None:
    _compiler, modules = _compile("from typing import Iterator\n" + PRELUDE
                                  + NESTED + """\
def gen(n: N) -> Iterator[tuple[tuple[Own[A], int32], int32]]:
    yield n.p
""")
    entry = _entry(modules)
    gen = next(f for f in entry.ast.functions if f.name == "gen")
    v = next(s for s in gen.body if isinstance(s, TpyYield)).value
    while isinstance(v, TpyCoerce):
        v = v.expr
    assert isinstance(v, TpyFieldAccess)
    assert entry.analyzer.ctx.own_element_copies.get(v) == frozenset({0})
    assert [w for w in _warnings(entry.analyzer) if "tuple" in w] == [
        "copies A into owned storage (tuple element 0.0); use copy() to "
        "make this explicit"]


def test_returned_nested_field_records_its_element() -> None:
    # `return n.p` copies element 0.0 into the owned return slot.
    records, warnings = _records(NESTED + """\
def direct(n: N) -> Own[tuple[tuple[A, int32], int32]]:
    return n.p
""")
    assert records == {6: frozenset({0})}
    assert warnings == ["copies A into owned storage (tuple element 0.0); "
                        "use copy() to make this explicit"]


def test_returned_nested_param_warns_like_its_append() -> None:
    # A nested tuple PARAMETER returned into `Own[tuple[...]]` copies path
    # 0.0, warned as the append of the same parameter warns it.
    records, warnings = _records("""\
def ret(x: tuple[tuple[A, int32], int32]) -> Own[tuple[tuple[A, int32], int32]]:
    return x
def app(x: tuple[tuple[A, int32], int32]) -> None:
    ys: list[tuple[tuple[A, int32], int32]] = []
    ys.append(x)
""")
    assert records[2] == frozenset({0})
    assert warnings == ["copies A into owned storage (tuple element 0.0); "
                        "use copy() to make this explicit"] * 2


def test_mixed_local_field_store_of_nocopy_element_is_an_error() -> None:
    with pytest.raises(SemanticError, match=r"cannot copy non-copyable type "
                       r"'G' into field \(tuple element 0\)"):
        _compile(PRELUDE + """\
from tpy import nocopy
@nocopy
class G:
    def __init__(self, x: int32) -> None:
        self.x = x
class F:
    gp: tuple[G, A]
    def __init__(self) -> None:
        self.gp = (G(0), A(0))
    def literal(self, a: A) -> None:
        t = (G(7), a)
        self.gp = t
""")


@pytest.mark.parametrize("slot, expected", [
    ("tuple[A, int32]", set()),
    ("tuple[Own[A], A]", {(0,)}),
    ("Own[tuple[A, tuple[A, int32]]]", {(0,), (1, 0)}),
    ("tuple[tuple[Own[A], int32], int32]", {(0, 0)}),
    ("tuple[Own[tuple[A, int32]], A]", {(0, 0)}),
])
def test_owned_slot_paths(slot: str, expected: set[tuple[int, ...]]) -> None:
    _compiler, modules = _compile(PRELUDE + f"def f(p: {slot}) -> None:\n"
                                  "    pass\n")
    entry = _entry(modules)
    f = next(fn for fn in entry.ast.functions if fn.name == "f")
    assert owned_slot_paths(f.params[0][1]) == expected


def test_generic_element_call_keeps_the_whole_tuple_hedge() -> None:
    # An open `T` at an `Own` element slot is not decided per element: the
    # call's copy is hedged for the whole tuple and declared as such.
    records, warnings = _records("""\
def gmk[T](a: T) -> tuple[T, int32]:
    return (a, 1)
def gtake[T](p: tuple[Own[T], int32]) -> int32:
    return p[1]
def run[T](a: T) -> int32:
    return gtake(gmk(a))
""")
    assert records == {6: None}
    assert ("may copy tuple[T, int32] into owned storage if not a value "
            "type; use copy() to make this explicit") in warnings
