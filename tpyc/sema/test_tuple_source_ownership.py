"""Sema's per-element facts about a tuple SOURCE at an owning slot: which
element paths it lends (`tuple_borrowed_paths`), which elements it hands
over as objects of its own (`tuple_source_elem_owned`, derived from it),
which elements the slot copies (`own_element_copies`, by index) and the
per-leaf record built for every owning sink (`tuple_records`) -- and of the
slot: which element paths it owns (`owned_slot_paths`). Every program this
file compiles also checks that the records of its sources -- names and
walrus reads included -- agree with the copy warnings, per leaf path
(`_compile`)."""

import re

import pytest

from ..diagnostics import SemanticError
from ..parse import (TpyAssign, TpyCall, TpyCoerce, TpyExpr, TpyFieldAccess,
                     TpyIfExpr, TpyMethodCall, TpyName, TpyNamedExpr,
                     TpyReturn, TpySubscript, TpyTupleLiteral, TpyVarDecl,
                     TpyYield)
from ..parse.nodes import SourceLocation
from ..sema import alias_rebind
from ..sema.alias_rebind import (bare_tuple, direct_elements, owned_slot_paths,
                                 tuple_borrowed_paths,
                                 tuple_source_elem_owned)
from ..sema.compatibility import TupleSink, TypeCompatibility
from ..sema.context import NonOwningBind, SemanticContext
from ..sema.tuple_records import (LeafPermission, TupleSourceRecord,
                                  tuple_source_record)
from ..thir.testutil import _compile as _compile_program, _entry
from ..typesys import TpyType
from ..value_category import peel_value_wrappers

_LEAF_IN_WARNING = re.compile(r"(?:copies|may copy) .*\(.*tuple element "
                              r"([0-9.]+)\)")
# A name read's copy of the whole tuple: it speaks for every leaf the
# record copies.
_WHOLE_IN_WARNING = re.compile(r"^(?:copies|may copy) tuple\[")

# Lines whose record and warnings are known to disagree, keyed by a snippet
# the program must contain (which program, and which parametrization of it)
# and the exact stripped line, with the entry that tracks each.
_KNOWN_DISAGREEMENTS: dict[tuple[str, str], str] = {
    # The nested member warns a copy its slot does not make.
    ("def nested_arg(a: A) -> int32:",
     "return take_nested((A(0), (a, 1)))"):
        "BUGS.md#own-element-arg-nested-tuple-false-copy-warning",
    ("def elem_nested(a: A) -> None:", "print(take((A(0), (a, 1))))"):
        "BUGS.md#own-element-arg-nested-tuple-false-copy-warning",
    # A union-leaf tuple name records WARNED with no warning.
    ("class H:\n    p: tuple[A | B, int32]\n", "xs.append(t)"):
        "BUGS.md#tuple-name-union-leaf-copy-silent",
    ("class H:\n    p: tuple[A | B, int32]\n", "print(own_arg(t))"):
        "BUGS.md#tuple-name-union-leaf-copy-silent",
    # A `Send[T]`-leaf tuple name records WARNED with no warning or hedge.
    ("class H[T]:\n    p: tuple[Send[T], int32]\n", "xs.append(t)"):
        "BUGS.md#send-own-tuple-leaf-copy-unwarned",
    ("class H[T]:\n    p: tuple[Send[T], int32]\n", "print(own_arg(t))"):
        "BUGS.md#send-own-tuple-leaf-copy-unwarned",
}


def _span(loc: SourceLocation) -> tuple[tuple[int, int], tuple[int, int]]:
    end = (loc.end_line if loc.end_line is not None else loc.line,
           loc.end_column if loc.end_column is not None else 10**9)
    return (loc.line, loc.column), end


def _source_of(loc: SourceLocation,
               sources: list[TpyExpr]) -> list[TpyExpr]:
    """The registered sources a diagnostic at `loc` speaks for: the
    innermost one its location falls in (a literal member's warning), else
    the outermost ones its node spans (a statement's or a walrus's)."""
    at = (loc.line, loc.column)
    inside = [e for e in sources
              if _span(e.loc)[0] <= at < _span(e.loc)[1]]
    if inside:
        return [max(inside, key=lambda e: _span(e.loc)[0])]
    lo, hi = _span(loc)
    within = [e for e in sources
              if lo <= _span(e.loc)[0] and _span(e.loc)[1] <= hi]
    return [e for e in within
            if not any(o is not e and _span(o.loc)[0] <= _span(e.loc)[0]
                       and _span(e.loc)[1] <= _span(o.loc)[1]
                       for o in within)]


def _assert_records_agree_with_warnings(
        entry: object, registered: list[TpyExpr], source: str) -> None:
    """Every registered owning tuple sink has a record. For each source --
    a name and a walrus read included -- the leaves its record says the
    sink copies (WARNED / HEDGED) are exactly the leaf paths its own
    warnings name, a whole-tuple copy warning naming every leaf the record
    copies (and requiring one, or an undecided leaf), an explicit `copy()` leaf is named by none,
    and a warning may name a leaf only the record leaves undecided (a
    lending call's; a nested leaf under a name,
    BUGS.md#tuple-name-facts-top-level-only). Lines matching a
    `_KNOWN_DISAGREEMENTS` key are skipped."""
    ctx = entry.analyzer.ctx
    lines = source.splitlines()
    known = {text for marker, text in _KNOWN_DISAGREEMENTS
             if marker in source}

    def excluded(line: int) -> bool:
        text = lines[line - 1] if 0 < line <= len(lines) else ""
        return text.strip() in known

    for e in registered:
        if e.loc is not None and not excluded(e.loc.line):
            assert tuple_source_record(ctx, e) is not None, (
                e.loc.line, type(e).__name__)
    sources = [e for e in ctx.tuple_records.keys() if e.loc is not None]
    named: dict[int, set[tuple[int, ...]]] = {id(e): set() for e in sources}
    whole: set[int] = set()
    for d in ctx.diagnostics:
        if not (d.level.value == "warning" and d.loc is not None):
            continue
        m = _LEAF_IN_WARNING.search(d.message)
        if not (m or _WHOLE_IN_WARNING.search(d.message)):
            continue
        if excluded(d.loc.line):
            continue
        # A warning outside every source is a sink's that keeps no record
        # (a local binding's member copy).
        for e in _source_of(d.loc, sources):
            if m:
                named[id(e)].add(
                    tuple(int(i) for i in m.group(1).split(".")))
            else:
                whole.add(id(e))
    for e in sources:
        if excluded(e.loc.line):
            continue
        rec = ctx.tuple_records[e]
        perms = {p: leaf.permission for p, leaf in rec.leaves.items()}
        copied = {p for p, perm in perms.items() if perm.copies}
        undecided = {p for p, perm in perms.items()
                     if perm is LeafPermission.UNDECIDED}
        explicit = {p for p, perm in perms.items()
                    if perm is LeafPermission.EXPLICIT}
        mine = named[id(e)]
        where = (e.loc.line, type(e).__name__)
        if id(e) in whole:
            assert copied | undecided, (where, perms)
            mine = mine | copied
        assert copied <= mine, (where, copied, mine)
        assert mine <= copied | undecided, (where, copied, mine)
        assert not (explicit & mine), (where, explicit, mine)


def _compile(source: str) -> tuple[object, list]:
    """Compile `source` and check its records against its warnings
    (`_assert_records_agree_with_warnings`), over every source an owning
    tuple sink registered."""
    registered: list[TpyExpr] = []
    register = TypeCompatibility.register_tuple_sink

    def recording(self: TypeCompatibility, src: TpyExpr | None,
                  slot: TpyType | None, *args: object,
                  **kwargs: object) -> None:
        if src is not None and bare_tuple(slot) is not None:
            registered.append(src)
        register(self, src, slot, *args, **kwargs)

    TypeCompatibility.register_tuple_sink = recording
    try:
        compiler, modules = _compile_program(source)
    finally:
        TypeCompatibility.register_tuple_sink = register
    _assert_records_agree_with_warnings(_entry(modules), registered, source)
    return compiler, modules

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


def test_generic_element_call_hedges_per_leaf() -> None:
    # An open `T` at an `Own` element slot is decided per leaf: the call's
    # copy is hedged for element 0 and declared as such.
    records, warnings = _records("""\
def gmk[T](a: T) -> tuple[T, int32]:
    return (a, 1)
def gtake[T](p: tuple[Own[T], int32]) -> int32:
    return p[1]
def run[T](a: T) -> int32:
    return gtake(gmk(a))
""")
    assert records == {6: frozenset({0})}
    assert ("may copy T into owned storage (tuple element 0) if not a value "
            "type; use copy() to make this explicit") in warnings


# -- the per-leaf record (`tuple_records`) ------------------------------------

LEAF_PRELUDE = """\
from tpy import int32, Own, copy, nocopy
from typing import Callable
class A:
    def __init__(self, v: int32) -> None:
        self.v = v
class B:
    def __init__(self, w: int32) -> None:
        self.w = w
@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v
class U:
    t: tuple[A | B, int32]
    def __init__(self, t: tuple[A | B, int32]) -> None:
        self.t = t
def mk[T](x: T) -> tuple[T, int32]:
    return (x, 1)
def mk2[T](x: T) -> tuple[tuple[T, int32], int32]:
    return ((x, 1), 2)
def pair(a: A, b: A) -> tuple[A, A]:
    return (a, b)
def opair(a: A) -> tuple[Own[A], A]:
    return (A(a.v), a)
def take(t: tuple[Own[A], int32]) -> int32:
    return t[0].v
"""
_LEAF_LINES = LEAF_PRELUDE.count("\n")


def _leaf_records(source: str) -> tuple[
        dict[tuple[int, str], TupleSourceRecord], list[str], SemanticContext]:
    """Every tuple-source record of `source` (after the leaf prelude) by
    (line, source node kind), with the warnings and the context."""
    _compiler, modules = _compile(LEAF_PRELUDE + source)
    return _records_of(_entry(modules).analyzer.ctx)


def _records_of(ctx: SemanticContext) -> tuple[
        dict[tuple[int, str], TupleSourceRecord], list[str], SemanticContext]:
    out = {}
    for e, rec in ctx.tuple_records.items():
        line = e.loc.line - _LEAF_LINES if e.loc is not None else None
        if line is not None and line > 0:
            out[(line, type(e).__name__)] = rec
    warnings = [d.message for d in ctx.diagnostics
                if d.level.value == "warning" and d.loc is not None
                and d.loc.line > _LEAF_LINES]
    return out, warnings, ctx


def _perms(rec: TupleSourceRecord) -> dict[tuple[int, ...], LeafPermission]:
    return {p: leaf.permission for p, leaf in rec.leaves.items()}


P = LeafPermission


def test_generic_results_hedge_per_leaf() -> None:
    records, warnings, _ctx = _leaf_records("""\
def r_call[T](x: T) -> Own[tuple[T, int32]]:
    return mk(x)
def r_select[T](x: T, y: T, c: bool) -> Own[tuple[T, int32]]:
    return mk(x) if c else mk(y)
def r_nested[T](x: T) -> Own[tuple[tuple[T, int32], int32]]:
    return mk2(x)
""")
    assert _perms(records[(2, "TpyCall")]) == {(0,): P.HEDGED}
    sel = records[(4, "TpyIfExpr")]
    assert _perms(sel) == {(0,): P.HEDGED}
    assert [_perms(a) for a in sel.arms] == [{(0,): P.HEDGED}] * 2
    assert _perms(records[(6, "TpyCall")]) == {(0, 0): P.HEDGED}
    # One hedge per leaf, none for the whole tuple.
    assert warnings == [
        "may copy T into owned storage (tuple element 0) if not a value "
        "type; use copy() to make this explicit"] * 2 + [
        "may copy T into owned storage (tuple element 0.0) if not a value "
        "type; use copy() to make this explicit"]


def test_union_leaf_copies_like_its_scalar_twin() -> None:
    records, warnings, ctx = _leaf_records("""\
def r_select(x: tuple[A | B, int32], y: tuple[A | B, int32],
             c: bool) -> Own[tuple[A | B, int32]]:
    return x if c else y
def r_field(u: U) -> Own[tuple[A | B, int32]]:
    return u.t
""")
    sel = records[(3, "TpyIfExpr")]
    assert _perms(sel) == {(0,): P.WARNED}
    assert [_perms(a) for a in sel.arms] == [{(0,): P.WARNED}] * 2
    assert _perms(records[(5, "TpyFieldAccess")]) == {(0,): P.WARNED}
    assert warnings.count("copies A | B into owned storage (tuple element "
                          "0); use copy() to make this explicit") == 2
    # A NAME keeps its own rule: the prelude ctor's `self.t = t` stays
    # silent (BUGS.md#tuple-name-union-leaf-copy-silent).
    assert not [d for d in ctx.diagnostics if "into field" in d.message]


def test_tuple_name_union_leaf_keeps_the_name_rule() -> None:
    records, warnings, _ctx = _leaf_records("""\
def f(t: tuple[A | B, int32]) -> int32:
    xs: list[tuple[A | B, int32]] = [t]
    return t[1]
class W:
    t: tuple[A | B, int32]
    def __init__(self, t: tuple[A | B, int32]) -> None:
        self.t = (A(0), 0)
    def store(self, t: tuple[A | B, int32]) -> None:
        self.t = t
""")
    # No warning (BUGS.md#tuple-name-union-leaf-copy-silent); no verdict.
    assert not warnings
    assert _perms(records[(2, "TpyName")]) == {(0,): P.UNDECIDED}
    assert _perms(records[(9, "TpyName")]) == {(0,): P.UNDECIDED}


def test_union_leaf_with_non_copyable_alternative_is_rejected() -> None:
    with pytest.raises(SemanticError, match=r"cannot copy non-copyable type "
                       r"'(N \| B|B \| N)' into owned storage \(tuple "
                       r"element 0\)"):
        _compile(LEAF_PRELUDE + """\
def r(x: tuple[N | B, int32], y: tuple[N | B, int32],
      c: bool) -> Own[tuple[N | B, int32]]:
    return x if c else y
""")


def test_select_of_fresh_or_copied_arms_copies_nothing_implicitly() -> None:
    records, warnings, ctx = _leaf_records("""\
def fresh(c: bool) -> Own[tuple[A, int32]]:
    return (A(1), 2) if c else (A(2), 3)
def copied(x: A, y: A, c: bool) -> Own[tuple[A, int32]]:
    return (copy(x), 1) if c else (copy(y), 2)
def mixed_arms(a: A, b: A, c: bool) -> Own[tuple[A, A]]:
    return opair(a) if c else opair(b)
""")
    fresh = records[(2, "TpyIfExpr")]
    assert _perms(fresh) == {(0,): P.MOVE}
    assert [_perms(a) for a in fresh.arms] == [{(0,): P.MOVE}] * 2
    explicit = records[(4, "TpyIfExpr")]
    assert _perms(explicit) == {(0,): P.EXPLICIT}
    assert [_perms(a) for a in explicit.arms] == [{(0,): P.EXPLICIT}] * 2
    # The negative control: each arm lends its element 1.
    assert _perms(records[(6, "TpyIfExpr")]) == {(0,): P.MOVE,
                                                 (1,): P.WARNED}
    assert warnings == ["copies A into owned storage (tuple element 1); "
                        "use copy() to make this explicit"]
    # The declared copies agree: nothing for the fresh and explicit
    # selects, element 1 for the control.
    declared = {e.loc.line - _LEAF_LINES: v
                for e, v in ctx.own_element_copies.items()}
    assert declared == {6: frozenset({1})}


def test_walrus_is_its_target_names_read() -> None:
    # The literal is captured into `t`, which stays live: its leaf copies,
    # with the argument coercion's warning, never a move.
    records, warnings, ctx = _leaf_records("""\
def f() -> int32:
    r = take((t := (A(2), 1)))
    t[0].v = 5
    return r + t[0].v
def own(x: Own[tuple[A, int32]]) -> int32:
    return x[1]
def g() -> int32:
    r = own((t := (A(2), 1)))
    return r + t[0].v
""")
    assert _perms(records[(2, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(8, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert warnings == [
        "copies tuple[A, IntLiteral(1)] into owned storage; use copy() to "
        "make this explicit",
        "copies A into owned storage (tuple element 0); use copy() to make "
        "this explicit"]
    walrus = {e.loc.line - _LEAF_LINES: v
              for e, v in ctx.own_element_copies.items()
              if isinstance(e, TpyNamedExpr)}
    assert walrus == {2: None}


def test_mixed_slot_passes_the_borrowed_leaf_it_does_not_own() -> None:
    records, warnings, _ctx = _leaf_records("""\
def relay(a: A, b: A) -> tuple[Own[A], A]:
    return pair(a, b)
""")
    rec = records[(2, "TpyCall")]
    assert {p: (leaf.borrowed, leaf.slot_owns, leaf.permission)
            for p, leaf in rec.leaves.items()} == {
        (0,): (True, True, P.WARNED), (1,): (True, False, P.PASS)}
    assert warnings == ["copies A into owned storage (tuple element 0); "
                        "use copy() to make this explicit"]


def test_captured_tuple_names_copy_every_owned_leaf() -> None:
    records, warnings, _ctx = _leaf_records("""\
def inline() -> int32:
    t = (A(1), 3)
    def inner() -> tuple[Own[A], int32]:
        return t
    return inner()[1]
def param(t: tuple[Own[A], int32]) -> int32:
    def inner() -> tuple[Own[A], int32]:
        return t
    return inner()[1]
def lam() -> int32:
    t = (A(1), 3)
    f: Callable[[], Own[tuple[A, int32]]] = lambda: t
    return f()[1]
""")
    # A declared `Own` element is no ownership of the closure's.
    for line in (4, 8, 12):
        assert _perms(records[(line, "TpyName")]) == {(0,): P.WARNED}
    assert warnings.count("copies A into owned storage (tuple element 0); "
                          "use copy() to make this explicit") == 3


def test_captured_non_copyable_leaf_is_rejected_without_move_advice() -> None:
    with pytest.raises(SemanticError) as e:
        _compile(LEAF_PRELUDE + """\
def f() -> int32:
    t = (N(1), 3)
    def inner() -> Own[tuple[N, int32]]:
        return t
    return inner()[1]
""")
    msg = e.value.message
    assert msg.startswith("cannot copy non-copyable type 'N' into owned "
                          "storage (tuple element 0)")
    assert "auto-move" not in msg and "copy()" not in msg


def test_name_and_lending_call_leaves_stay_undecided() -> None:
    records, _warnings_, _ctx = _leaf_records("""\
def nested(t: tuple[tuple[A, int32], int32]) -> Own[tuple[tuple[A, int32], int32]]:
    return t
def lending[T](a: tuple[T, int32], b: tuple[T, int32]) -> Own[tuple[T, int32]]:
    return min(a, b, key=lambda p: p[1])
""")
    # A name's facts are top-level: its nested leaf waits for them.
    assert _perms(records[(2, "TpyName")]) == {(0, 0): P.UNDECIDED}
    # A native lending call's result paths are not placed yet.
    assert _perms(records[(4, "TpyCall")]) == {(0,): P.UNDECIDED}


def test_name_record_is_settled_by_a_loop_back_edge() -> None:
    records, warnings, _ctx = _leaf_records("""\
def back(a: A, n: int32) -> tuple[Own[A], int32]:
    t = (A(1), 1)
    i = 0
    while i < n:
        if i == 1:
            return t
        t = (a, 2)
        i += 1
    return (A(0), 0)
def moved() -> tuple[Own[A], int32]:
    t = (A(1), 1)
    return t
""")
    # The later borrowing rebind turns the return's move into a copy.
    assert _perms(records[(6, "TpyName")]) == {(0,): P.WARNED}
    assert _perms(records[(12, "TpyName")]) == {(0,): P.MOVE}


def test_literal_member_move_settled_into_a_copy() -> None:
    records, warnings, _ctx = _leaf_records("""\
def back(a: A, n: int32) -> tuple[Own[A], int32]:
    t = A(1)
    i = 0
    while i < n:
        if i == 1:
            return (t, 1)
        t = a
        i += 1
    return (A(0), 0)
""")
    # The back edge's borrowing rebind turns the member's move into the
    # copy the settled warning names.
    assert _perms(records[(6, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert records[(6, "TpyTupleLiteral")].leaves[(0,)].borrowed
    assert _perms(records[(9, "TpyTupleLiteral")]) == {(0,): P.MOVE}
    assert len(warnings) == 1 and "bound to existing storage" in warnings[0]


def test_owning_yield_and_lambda_parameter_names_publish_records() -> None:
    records, warnings, _ctx = _leaf_records("""\
from typing import Iterator
def fresh() -> Iterator[tuple[Own[A], int32]]:
    t = (A(3), 1)
    yield t
def live() -> Iterator[tuple[Own[A], int32]]:
    t = (A(3), 1)
    yield t
    print(t[0].v)
def lam() -> int32:
    g: Callable[[tuple[Own[A], int32]], tuple[Own[A], int32]] = lambda p: p
    return g((A(3), 1))[1]
""")
    # A last-use yield moves the element the local owns; one read later
    # copies, under the yield's whole-tuple warning.
    assert _perms(records[(4, "TpyName")]) == {(0,): P.MOVE}
    assert _perms(records[(7, "TpyName")]) == {(0,): P.WARNED}
    assert [w for w in warnings if "copies" in w] == [
        "copies tuple[A, int32] into owned storage; use copy() to make this "
        "explicit"]
    # A returned parameter hands over what its declared type owns.
    assert _perms(records[(10, "TpyName")]) == {(0,): P.MOVE}


def test_nested_tuple_members_get_per_leaf_verdicts() -> None:
    records, warnings, _ctx = _leaf_records("""\
class H:
    p: tuple[A, int32]
    def __init__(self) -> None:
        self.p = (A(1), 2)
def fresh() -> tuple[Own[tuple[A, int32]], int32]:
    return ((A(1), 2), 3)
def outer(p: tuple[Own[tuple[A, int32]], int32]) -> int32:
    return p[1]
def arg() -> int32:
    return outer(((A(1), 2), 3))
def mk(a: A) -> tuple[Own[A], int32]:
    return (A(a.v), 2)
def call(a: A) -> tuple[tuple[Own[A], int32], int32]:
    return (mk(a), 3)
def field(h: H) -> tuple[tuple[Own[A], int32], int32]:
    return (h.p, 3)
""")
    # An `Own[tuple[...]]` member owns every leaf it holds.
    assert _perms(records[(6, "TpyTupleLiteral")]) == {(0, 0): P.MOVE}
    assert _perms(records[(10, "TpyTupleLiteral")]) == {(0, 0): P.MOVE}
    # A nested source other than a name or a lending call takes the owned
    # tuple sink's decision: a fresh element moves, a field's copies.
    assert _perms(records[(14, "TpyTupleLiteral")]) == {(0, 0): P.MOVE}
    assert _perms(records[(16, "TpyTupleLiteral")]) == {(0, 0): P.WARNED}
    assert warnings == ["copies A into owned storage (tuple element 0.0); "
                        "use copy() to make this explicit"]


def test_select_of_explicit_copies_is_explicit() -> None:
    records, warnings, _ctx = _leaf_records("""\
def whole(a: A, b: A, c: bool) -> Own[tuple[A, int32]]:
    x = (a, 1)
    y = (b, 2)
    return copy(x) if c else copy(y)
def nested(a: A, b: A, c: bool, d: bool) -> Own[tuple[A, int32]]:
    return ((copy(a), 1) if c else (copy(b), 2)) if d else (copy(a), 3)
""")
    sel = records[(4, "TpyIfExpr")]
    assert _perms(sel) == {(0,): P.EXPLICIT}
    assert [_perms(a) for a in sel.arms] == [{(0,): P.EXPLICIT}] * 2
    outer = records[(6, "TpyIfExpr")]
    assert _perms(outer) == {(0,): P.EXPLICIT}
    assert _perms(outer.arms[0]) == {(0,): P.EXPLICIT}
    assert [_perms(a) for a in outer.arms[0].arms] == [{(0,): P.EXPLICIT}] * 2
    assert not warnings


def test_select_arms_lend_what_their_members_bring() -> None:
    records, warnings, ctx = _leaf_records("""\
def f(a: A, c: bool) -> Own[tuple[A, int32]]:
    return (a, 1) if c else (A(2), 2)
""")
    sel = next(e for e in ctx.tuple_records.keys()
               if isinstance(e, TpyIfExpr))
    arms = [peel_value_wrappers(a) for a in (sel.then_expr, sel.else_expr)]
    assert [type(a).__name__ for a in arms] == ["TpyTupleLiteral"] * 2
    # An arm is a plain operand of the select: it keeps no capture of its
    # own, and lends what its members do as they arrive.
    assert [a.elem_capture for a in arms] == [[], []]
    rec = records[(2, "TpyIfExpr")]
    assert _perms(rec) == {(0,): P.WARNED}
    assert [_perms(a) for a in rec.arms] == [{(0,): P.WARNED},
                                             {(0,): P.MOVE}]
    assert warnings == ["copies A into owned storage (tuple element 0); "
                        "use copy() to make this explicit"]


def _probe(source: str) -> tuple[
        dict[tuple[int, str], TupleSourceRecord], list[str], object]:
    """The records (by line and node kind), the warnings and the entry
    module of `source` after the leaf prelude."""
    _compiler, modules = _compile(LEAF_PRELUDE + source)
    entry = _entry(modules)
    records, warnings, _ctx = _records_of(entry.analyzer.ctx)
    return records, warnings, entry


def _leaves(rec: TupleSourceRecord) -> dict[tuple[int, ...], tuple]:
    return {p: (leaf.borrowed, leaf.slot_owns, leaf.permission)
            for p, leaf in rec.leaves.items()}


def test_value_capture_keeps_a_members_incoming_borrow() -> None:
    # Each arm's nested call lends `a` / `b` at (0, 0), although the arm
    # holds that member by value: the select copies it, warned once.
    records, warnings, entry = _probe("""\
def p1(a: A) -> tuple[A, int32]:
    return (a, 1)
def selected(a: A, b: A, c: bool) -> Own[tuple[tuple[A, int32], int32]]:
    return (p1(a), 1) if c else (p1(b), 2)
def stored(a: A) -> int32:
    t = (p1(a), 1)
    return t[1]
""")
    sel = records[(4, "TpyIfExpr")]
    assert _leaves(sel) == {(0, 0): (True, True, P.WARNED)}
    assert [_leaves(a) for a in sel.arms] == [
        {(0, 0): (True, True, P.WARNED)}] * 2
    # The select's copy, and the local's: it stores the nested tuple by
    # value.
    assert warnings == ["copies A into owned storage (tuple element 0.0); "
                        "use copy() to make this explicit"] * 2
    # The two questions of one literal: what arrives borrowed (the sink's)
    # and what a binding of it holds as its own (the binding mask's, which
    # holds the nested tuple by value).
    ctx = entry.analyzer.ctx
    stored = next(f for f in entry.ast.functions if f.name == "stored")
    init = next(st.init for st in stored.body if isinstance(st, TpyVarDecl))
    slot = ctx.get_expr_type(init)
    assert entry.analyzer.compat.tuple_lent_paths(init, slot) == {(0, 0)}
    assert tuple_borrowed_paths(ctx, init, slot) == frozenset()
    assert tuple_source_elem_owned(ctx, init, slot) == (True, False)


def test_owned_tuple_member_decides_each_leaf() -> None:
    # An `Own[tuple[...]]` member: a call's leaves by its declared return,
    # a literal's per member; a nested NAME waits for path-complete facts.
    records, warnings, _a = _probe("""\
def own_first(a: A) -> tuple[Own[A], A]:
    return (A(1), a)
def call(a: A) -> tuple[Own[tuple[A, A]], int32]:
    return (own_first(a), 3)
def name(a: A) -> tuple[Own[tuple[A, A]], int32]:
    t = own_first(a)
    return (t, 3)
def explicit(a: A) -> tuple[Own[tuple[A, A]], int32]:
    return (copy(own_first(a)), 3)
def lit(a: A) -> tuple[Own[tuple[A, A]], int32]:
    return ((a, A(2)), 3)
def whole(a: A) -> Own[tuple[tuple[A, int32], int32]]:
    return ((a, 1), 1)
""")
    assert _leaves(records[(4, "TpyTupleLiteral")]) == {
        (0, 0): (False, True, P.MOVE), (0, 1): (True, True, P.WARNED)}
    assert _perms(records[(7, "TpyTupleLiteral")]) == {
        (0, 0): P.UNDECIDED, (0, 1): P.UNDECIDED}
    assert _perms(records[(9, "TpyTupleLiteral")]) == {
        (0, 0): P.EXPLICIT, (0, 1): P.EXPLICIT}
    assert _leaves(records[(11, "TpyTupleLiteral")]) == {
        (0, 0): (True, True, P.WARNED), (0, 1): (False, True, P.MOVE)}
    # A whole `Own[tuple[...]]` slot owns the nested literal's leaf too.
    assert _leaves(records[(13, "TpyTupleLiteral")]) == {
        (0, 0): (True, True, P.WARNED)}
    assert warnings == [
        "copies A into owned storage (tuple element 0.1); use copy() to "
        "make this explicit"] + [
        "copies A into owned storage (tuple element 0.0); use copy() to "
        "make this explicit"] * 2


def test_optional_select_arms_keep_their_source_ownership() -> None:
    records, warnings, _a = _probe("""\
def mk() -> Own[A | None]:
    return A(1)
def fresh(c: bool) -> Own[tuple[A | None, int32]]:
    return (mk(), 1) if c else (mk(), 2)
def copied(a: A | None, c: bool) -> Own[tuple[A | None, int32]]:
    return (copy(a), 1) if c else (copy(a), 2)
""")
    fresh = records[(4, "TpyIfExpr")]
    assert _leaves(fresh) == {(0,): (False, True, P.MOVE)}
    assert [_perms(a) for a in fresh.arms] == [{(0,): P.MOVE}] * 2
    copied = records[(6, "TpyIfExpr")]
    assert _perms(copied) == {(0,): P.EXPLICIT}
    assert [_perms(a) for a in copied.arms] == [{(0,): P.EXPLICIT}] * 2
    assert not warnings


def test_yielded_name_is_settled_by_a_loop_back_edge() -> None:
    records, warnings, entry = _probe("""\
from typing import Iterator
def g(a: A, n: int32) -> Iterator[tuple[Own[A], int32]]:
    t = (A(1), 2)
    i = 0
    while i < n:
        if i == 1:
            yield t
        t = (a, 2)
        i += 1
""")
    # The yield is an owning read the settlement sees: the back edge's
    # borrowing rebind turns its move into the copy it warns.
    rec = records[(7, "TpyName")]
    assert _leaves(rec) == {(0,): (True, True, P.WARNED)}
    ctx = entry.analyzer.ctx
    yielded = next(e for e in ctx.tuple_records.keys()
                   if isinstance(e, TpyName)
                   and e.loc.line - _LEAF_LINES == 7)
    assert yielded in ctx.declared_tuple_reads
    assert ctx.own_element_copies.get(yielded) == frozenset({0})
    assert [w for w in warnings if "tuple element" in w] == [
        "copies A into owned storage (tuple element 0); use copy() to make "
        "this explicit ('t' is bound to existing storage at line "
        f"{_LEAF_LINES + 8})"]


def test_walrus_and_lambda_parameter_reads_keep_their_context() -> None:
    records, warnings, _a = _probe("""\
from typing import Iterator
def ret() -> tuple[Own[A], int32]:
    return (t := (A(1), 2))
def gen() -> Iterator[tuple[Own[A], int32]]:
    yield (t := (A(1), 2))
    print(t[1])
def lam() -> int32:
    g: Callable[[tuple[Own[A], int32]], tuple[Own[A], int32]] = lambda p: p
    h: Callable[[tuple[A, int32]], Own[tuple[A, int32]]] = lambda q: q
    return g((A(3), 1))[1]
""")
    # A walrus is its target name's read: the return's record follows the
    # name's rule (no verdict for a read nothing declared), the live
    # yield's copies the leaf under the whole-tuple warning.
    assert _perms(records[(3, "TpyTupleLiteral")]) == {(0,): P.UNDECIDED}
    assert _perms(records[(5, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    # A lambda parameter hands over what its declared type owns.
    assert _leaves(records[(8, "TpyName")]) == {(0,): (False, True, P.MOVE)}
    # The borrowed parameter's copy is not warned yet
    # (BUGS.md#lambda-own-result-borrowed-param-copies-unwarned).
    assert _perms(records[(9, "TpyName")]) == {(0,): P.UNDECIDED}
    assert [w for w in warnings if "tuple element" in w] == []


def test_generic_captured_return_hedges_once_per_leaf() -> None:
    records, warnings, _a = _probe("""\
def gp[T](t: tuple[T, int32]) -> None:
    def inner() -> Own[tuple[T, int32]]:
        return t
    r = inner()
    print(r[1])
""")
    assert _perms(records[(3, "TpyName")]) == {(0,): P.HEDGED}
    assert warnings == ["may copy T into owned storage (tuple element 0) if "
                        "not a value type; use copy() to make this explicit"]


def test_captured_local_at_whole_own_tuple_result_warns_per_leaf() -> None:
    # Sema only: the lowering refuses this return after the warning
    # (BUGS.md#captured-tuple-own-tuple-return-rejects), so no runtime case
    # can pin it yet.
    records, warnings, _e = _probe("""\
def f() -> int32:
    t = (A(1), 3)
    def inner() -> Own[tuple[A, int32]]:
        return t
    return inner()[1]
""")
    assert _leaves(records[(4, "TpyName")]) == {(0,): (True, True, P.WARNED)}
    assert warnings == ["copies A into owned storage (tuple element 0); use "
                        "copy() to make this explicit"]


def test_consuming_field_member_moves_out_of_self() -> None:
    records, warnings, _e = _probe("""\
from typing import Self
class H:
    a: A
    def __init__(self) -> None:
        self.a = A(1)
    def take(self: Own[Self]) -> tuple[Own[A], int32]:
        return (self.a, 2)
""")
    # The consuming method's own field moves out, as its scalar read does.
    assert _leaves(records[(7, "TpyTupleLiteral")]) == {
        (0,): (False, True, P.MOVE)}
    assert [w for w in warnings if "tuple element" in w] == []


def test_captured_nested_tuple_copies_every_nested_leaf() -> None:
    records, warnings, _e = _probe("""\
def c(a: A) -> None:
    t = ((A(1), a), 1)
    def inner() -> Own[tuple[tuple[A, A], int32]]:
        return t
    inner()
""")
    # The capture is read in place: each nested leaf the slot owns copies,
    # warned per leaf.
    assert _leaves(records[(4, "TpyName")]) == {
        (0, 0): (True, True, P.WARNED), (0, 1): (True, True, P.WARNED)}
    assert [w for w in warnings if "tuple element 0." in w] == [
        "copies A into owned storage (tuple element 0.1); use copy() to "
        "make this explicit",
        "copies A into owned storage (tuple element 0.0); use copy() to "
        "make this explicit",
        "copies A into owned storage (tuple element 0.1); use copy() to "
        "make this explicit"]


def _registrations(source: str) -> list[tuple[int, str, object, str]]:
    """Every owning tuple sink registration of `source` (after the leaf
    prelude) that reached the record builder: (line, source node kind,
    sink kind, slot)."""
    kept: list[tuple[int, str, object, str]] = []
    build = TypeCompatibility.build_tuple_records

    def collecting(self: TypeCompatibility) -> None:
        for r in self.ctx.func.tuple_sinks.values():
            line = r.source.loc.line - _LEAF_LINES
            if line > 0:
                kept.append((line, type(r.source).__name__, r.sink,
                             str(r.slot)))
        build(self)

    TypeCompatibility.build_tuple_records = collecting
    try:
        _compile(LEAF_PRELUDE + source)
    finally:
        TypeCompatibility.build_tuple_records = build
    return kept


def test_one_registration_per_source() -> None:
    kept = _registrations("""\
def own(x: Own[tuple[A, int32]]) -> int32:
    return x[1]
def by_name() -> int32:
    t = (A(1), 2)
    r = own(t)
    return r + t[1]
def by_walrus() -> int32:
    r = own((t := (A(2), 1)))
    return r + t[1]
def keep(x: Own[tuple[Own[A], int32]]) -> int32:
    return x[1]
def fresh(a: A) -> tuple[Own[A], int32]:
    return (A(a.v), 1)
def nested_slot(a: A) -> int32:
    return keep(fresh(a))
""")
    # The argument's own check stands over its coercion's `Own`-slot check,
    # which runs on the same source after it; a coercion that recurses into
    # the element slots keeps its outermost slot.
    assert [k for k in kept if k[0] in (5, 8, 15)] == [
        (5, "TpyName", TupleSink.ARG, "tuple[Own[A], int32]"),
        (8, "TpyNamedExpr", TupleSink.ARG, "Own[tuple[A, int32]]"),
        (15, "TpyCall", None, "Own[tuple[Own[A], int32]]")]


def test_walrus_store_warns_its_name_read_once() -> None:
    records, warnings, _e = _probe("""\
class S:
    p: tuple[A, int32]
    def __init__(self) -> None:
        self.p = (A(0), 0)
def stores(b: A) -> None:
    h = S()
    h.p = (u := (b, 2))
    xs = [(A(0), 0)]
    xs[0] = (w := (b, 3))
    print(u[1], w[1])
""")
    # The store copies the walrus's name read; its literal's members took
    # the local's rule where the walrus bound them.
    assert _perms(records[(7, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(9, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert [w for w in warnings if "tuple element" in w] == [
        "copies A into field (tuple element 0); use copy() to make this "
        "explicit",
        "copies A into container (tuple element 0); use copy() to make "
        "this explicit"]


def test_records_agree_with_warnings_per_leaf_path() -> None:
    # Per-leaf shapes at every source kind -- nested arguments, `copy()`
    # selects, open `T` storage, nested fields and calls, settled moves,
    # lambda bodies, nested selects, mixed slots, consuming fields,
    # captured nested names, walrus stores -- compiled with the agreement
    # check (`_compile`); together they copy enough leaves to bite.
    entry_count = 0
    for source in _PROBE_SHAPES:
        _compiler, modules = _compile(LEAF_PRELUDE + source)
        ctx = _entry(modules).analyzer.ctx
        entry_count += sum(leaf.permission.copies
                           for rec in ctx.tuple_records.values()
                           for leaf in rec.leaves.values())
    assert entry_count >= 10


_PROBE_SHAPES = ["""\
def take2(p: tuple[Own[tuple[A, int32]], int32]) -> int32:
    return p[1]
def nested_arg() -> int32:
    return take2(((A(1), 2), 3))
def explicit_tuple(a: A, b: A, c: bool) -> Own[tuple[A, int32]]:
    x = (a, 1)
    y = (b, 2)
    return copy(x) if c else copy(y)
def explicit_nested(a: A, b: A, c: bool, d: bool) -> Own[tuple[A, int32]]:
    return ((copy(a), 1) if c else (copy(b), 2)) if d else (copy(a), 3)
def store(a: A, b: A, c: bool) -> int32:
    x = (a, 1)
    y = (b, 2)
    out = [copy(x) if c else copy(y)]
    return out[0][0].v
""", """\
def push[T](x: T) -> int32:
    xs: list[tuple[T, int32]] = []
    xs.append((x, 1))
    return xs[0][1]
def box[T](x: T) -> Own[list[tuple[T, int32]]]:
    return [(x, 1)]
def direct[T](x: T) -> tuple[Own[T], int32]:
    return (x, 1)
""", """\
class H:
    p: tuple[A, int32]
    def __init__(self) -> None:
        self.p = (A(1), 2)
def field(h: H) -> tuple[tuple[Own[A], int32], int32]:
    return (h.p, 3)
def mk1(a: A) -> tuple[Own[A], int32]:
    return (A(a.v), 2)
def call(a: A) -> tuple[tuple[Own[A], int32], int32]:
    return (mk1(a), 3)
def fresh() -> tuple[Own[tuple[A, int32]], int32]:
    return ((A(1), 2), 3)
def borrower(a: A) -> Own[tuple[A, int32]]:
    return (a, 2)
""", """\
def back(a: A, n: int32) -> tuple[Own[A], int32]:
    t = A(1)
    i = 0
    while i < n:
        if i == 1:
            return (t, 1)
        t = a
        i += 1
    return (A(0), 0)
""", """\
def make() -> tuple[Own[A], int32]:
    return (A(1), 1)
def take3(t: tuple[Own[A], int32]) -> int32:
    return t[1]
def take4(t: Own[tuple[A, int32]]) -> int32:
    return t[1]
def relay() -> tuple[Own[A], int32]:
    return make()
def f() -> int32:
    t = make()
    return take3(make()) + take4(make()) + take3(t)
def lit(a: A) -> int32:
    g: Callable[[], Own[tuple[A, int32]]] = lambda: (a, 1)
    return g()[0].v
def lit_fresh() -> int32:
    g: Callable[[], Own[tuple[A, int32]]] = lambda: (A(3), 1)
    return g()[0].v
def nested_lit(a: A) -> int32:
    return take5(((a, 1), 2)) + take5(((A(3), 1), 2))
def take5(p: tuple[tuple[Own[A], int32], int32]) -> int32:
    return 7
""", """\
def p1(a: A) -> tuple[A, int32]:
    return (a, 1)
def selected(a: A, b: A, c: bool) -> Own[tuple[tuple[A, int32], int32]]:
    return (p1(a), 1) if c else (p1(b), 2)
def stored(a: A, b: A, c: bool) -> None:
    xs: list[tuple[tuple[A, int32], int32]] = []
    xs.append((p1(a), 1) if c else (p1(b), 2))
def own_first(a: A) -> tuple[Own[A], A]:
    return (A(1), a)
def call(a: A) -> tuple[Own[tuple[A, A]], int32]:
    return (own_first(a), 3)
def lit(a: A) -> tuple[Own[tuple[A, A]], int32]:
    return ((a, A(2)), 3)
def whole(a: A) -> Own[tuple[tuple[A, int32], int32]]:
    return ((a, 1), 1)
def mixed_slot(a: A) -> tuple[Own[tuple[A, int32]], int32]:
    return ((a, 1), 1)
""", """\
from typing import Self
def take_nested(p: tuple[Own[A], tuple[A, int32]]) -> int32:
    return p[1][1]
def nested_arg(a: A) -> int32:
    return take_nested((A(0), (a, 1)))
def lam_fresh() -> int32:
    g: Callable[[], tuple[Own[A], int32]] = lambda: (A(1), 2)
    return g()[1]
class C:
    a: A
    p: tuple[A, int32]
    def __init__(self) -> None:
        self.a = A(1)
        self.p = (A(0), 0)
    def take(self: Own[Self]) -> tuple[Own[A], int32]:
        return (self.a, 2)
def captured(a: A) -> None:
    t = ((A(1), a), 1)
    def inner() -> Own[tuple[tuple[A, A], int32]]:
        return t
    inner()
def walrus_store(b: A, c: C) -> None:
    c.p = (u := (b, 2))
    xs = [(A(0), 0)]
    xs[0] = (w := (b, 3))
    print(u[1], w[1], take((A(1), 2)), take((v := (b, 2))), v[1])
"""]


def test_hedged_leaf_is_an_open_type_parameter() -> None:
    records, warnings, _ctx = _leaf_records("""\
def lt[T](xs: list[T], x: T) -> Own[tuple[list[T], T]]:
    return (xs, x)
""")
    # `list[T]` copies whatever T is; a bare `T` leaf only may.
    assert _perms(records[(2, "TpyTupleLiteral")]) == {(0,): P.WARNED,
                                                      (1,): P.HEDGED}
    assert len(warnings) == 2


def test_global_and_member_init_sinks_publish_the_record() -> None:
    records, warnings, _ctx = _leaf_records("""\
G = (A(1), 2)
def ret() -> Own[tuple[A, int32]]:
    return G
def app() -> None:
    xs: list[tuple[A, int32]] = []
    xs.append(G)
class M:
    p: tuple[A, int32]
    q: tuple[A, A]
    def __init__(self, a: A) -> None:
        self.p = (a, 1)
        self.q = pair(a, a)
""")
    # A global read in a function lends what the global holds.
    assert _perms(records[(3, "TpyName")]) == {(0,): P.WARNED}
    assert _perms(records[(6, "TpyName")]) == {(0,): P.WARNED}
    # Constructor member-init stores: a literal and a borrowing call.
    assert _perms(records[(11, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(12, "TpyCall")]) == {(0,): P.WARNED,
                                               (1,): P.WARNED}
    assert len(warnings) == 5


def test_lookup_peels_the_wrappers_the_record_was_keyed_past() -> None:
    _records_, _w, ctx = _leaf_records("""\
def f() -> int32:
    r = take((t := (A(2), 1)))
    return r + t[1]
""")
    lit = next(e for e in ctx.tuple_records.keys()
               if isinstance(e, TpyTupleLiteral))
    walrus = TpyNamedExpr(target="u", value=lit)
    assert tuple_source_record(ctx, walrus) is tuple_source_record(ctx, lit)
    assert tuple_source_record(ctx, walrus) is not None
    assert tuple_source_record(ctx, TpyName("nothing")) is None


def test_non_name_record_agrees_with_the_declared_copies() -> None:
    _records_, _w, ctx = _leaf_records("""\
def a(x: A, y: A, c: bool) -> Own[tuple[A, A]]:
    return pair(x, y) if c else pair(y, x)
def b[T](x: T) -> Own[tuple[T, int32]]:
    return mk(x)
def c(xs: list[tuple[A | B, int32]]) -> None:
    ys: list[tuple[A | B, int32]] = []
    ys.append(xs[0])
def d(a: A, b: A) -> tuple[Own[A], A]:
    return pair(a, b)
""")
    checked = 0
    for e, rec in ctx.tuple_records.items():
        if isinstance(e, (TpyName, TpyTupleLiteral)):
            continue
        copied = rec.copied_top_level()
        assert ctx.own_element_copies.get(e, frozenset()) == copied
        checked += bool(copied)
    assert checked == 4


def test_every_position_publishes_the_record() -> None:
    # One borrowing call source (`pair(a, a)`, element 0 copies) at each
    # position's owning sink.
    records, _warnings_, _ctx = _leaf_records("""\
from typing import Iterator
from tpy import error_return, ReturnException
class E(Exception, ReturnException):
    pass
class M:
    def meth(self, a: A) -> tuple[Own[A], A]:
        return pair(a, a)
def gen(a: A) -> Iterator[tuple[Own[A], A]]:
    yield pair(a, a)
async def co(a: A) -> tuple[Own[A], A]:
    return pair(a, a)
def comp(a: A) -> list[tuple[A, A]]:
    return [pair(a, a) for _ in range(2)]
def fin(a: A) -> tuple[Own[A], A]:
    try:
        return pair(a, a)
    finally:
        print("f")
def arm(a: A, n: int32) -> tuple[Own[A], A]:
    match n:
        case 1:
            return pair(a, a)
        case _:
            return (A(0), a)
@error_return(E)
def er(a: A) -> tuple[Own[A], A]:
    return pair(a, a)
""")
    for line in (7, 9, 11, 13, 16, 22, 27):
        assert _perms(records[(line, "TpyCall")])[(0,)] is P.WARNED, line


def _copy_warnings(ctx: SemanticContext) -> list[tuple[int, str]]:
    """The copy warnings after the leaf prelude, by line, their remedy cut."""
    return [(d.loc.line - _LEAF_LINES, d.message.split(";")[0])
            for d in ctx.diagnostics
            if d.level.value == "warning" and d.loc is not None
            and d.loc.line > _LEAF_LINES and "cop" in d.message]


_SINKS_BODY = """\
from typing import Iterator
from tpy import Send, readonly
class H{G}:
    p: tuple[{L}, int32]
    def __init__(self, a: {P}) -> None:
        self.p = (a, 0)
def own_arg{G}(p: Own[tuple[{L}, int32]]) -> int32:
    return p[1]
def elem_arg{G}(p: tuple[Own[{L}], int32]) -> int32:
    return p[1]
def ret{G}(t: tuple[{L}, int32]) -> tuple[Own[{L}], int32]:
    return t
def gen{G}(a: {P}) -> Iterator[tuple[Own[{L}], int32]]:
    t: tuple[{L}, int32] = (a, 1)
    yield t
    yield (a, 2)
    print(t[1])
def f{G}(a: {P}) -> None:
    t: tuple[{L}, int32] = (a, 1)
    h = H(a)
    h.p = t
    h.p = (a, 2)
    xs: list[tuple[{L}, int32]] = []
    xs.append(t)
    xs.append((a, 3))
    print(own_arg(t))
    print(own_arg((a, 4)))
    print(elem_arg(t))
    print(elem_arg((a, 5)))
    print(t[1])
"""
_N, _L = "TpyName", "TpyTupleLiteral"
_W, _U, _H = P.WARNED, P.UNDECIDED, P.HEDGED


@pytest.mark.parametrize("leaf, records, warnings", [
    (dict(G="", L="A | B", P="A"),
     # The name's union leaf is copied unwarned at the field store (21,
     # UNDECIDED), the append (24) and the `Own[tuple]` argument (26), both
     # WARNED with no warning (BUGS.md#tuple-name-union-leaf-copy-silent).
     {(6, _L): _W, (12, _N): _W, (15, _N): _W, (16, _L): _W, (21, _N): _U,
      (22, _L): _W, (24, _N): _W, (25, _L): _W, (26, _N): _W, (27, _L): _W,
      (28, _N): _W, (29, _L): _W},
     [(6, "copies A | B into field (tuple element 0)"),
      (12, "copies A | B into owned storage (tuple element 0)"),
      (15, "copies tuple[A | B, int32] into owned storage"),
      (16, "copies A | B into owned storage (tuple element 0)"),
      (22, "copies A | B into field (tuple element 0)"),
      (25, "copies A into owned storage (argument 'value' tuple element 0)"),
      (27, "copies A into owned storage (argument 'p' tuple element 0)"),
      (28, "copies tuple[Own[A | B], int32] into owned storage"),
      (28, "copies tuple[A | B, int32] into owned storage"),
      (29, "copies A into owned storage (argument 'p' tuple element 0)")]),
    (dict(G="", L="readonly[A]", P="A"),
     {(6, _L): _W, (12, _N): _W, (15, _N): _W, (16, _L): _W, (21, _N): _W,
      (22, _L): _W, (24, _N): _W, (25, _L): _W, (26, _N): _W, (27, _L): _W,
      (28, _N): _W, (29, _L): _W},
     [(6, "copies A into field (tuple element 0)"),
      (12, "copies readonly[A] into owned storage (tuple element 0)"),
      (15, "copies tuple[readonly[A], int32] into owned storage"),
      (16, "copies A into owned storage (tuple element 0)"),
      (21, "copies readonly[A] into field (tuple element 0)"),
      (22, "copies A into field (tuple element 0)"),
      (24, "copies readonly[A] into owned storage (tuple element 0)"),
      (25, "copies A into owned storage (argument 'value' tuple element 0)"),
      (26, "copies readonly[A] into owned storage (tuple element 0)"),
      (27, "copies A into owned storage (argument 'p' tuple element 0)"),
      (28, "copies tuple[Own[readonly[A]], int32] into owned storage"),
      (28, "copies tuple[readonly[A], int32] into owned storage"),
      (29, "copies A into owned storage (argument 'p' tuple element 0)")]),
    # An owned `Send[T]` leaf is neither seen as owned nor as an open `T`
    # (BUGS.md#send-own-tuple-leaf-copy-unwarned): the return of the name
    # (12), the yielded literal (16) and the field store of the name (21)
    # are UNDECIDED, and the append (24) and the `Own[tuple]` argument (26)
    # of the name are WARNED, all with no warning or hedge.
    (dict(G="[T]", L="Send[T]", P="Send[T]"),
     {(6, _L): _W, (12, _N): _U, (15, _N): _H, (16, _L): _U, (21, _N): _U,
      (22, _L): _W, (24, _N): _W, (25, _L): _W, (26, _N): _W, (27, _L): _W},
     [(6, "may copy Send[T] into field (tuple element 0) if not a value "
       "type"),
      (15, "may copy tuple[Send[T], int32] into owned storage if not a value "
       "type"),
      (22, "may copy Send[T] into field (tuple element 0) if not a value "
       "type"),
      (25, "may copy Send[T] into owned storage (argument 'value' tuple "
       "element 0) if not a value type"),
      (27, "may copy Send[T] into owned storage (argument 'p' tuple element "
       "0) if not a value type"),
      (28, "may copy tuple[Send[T], int32] into owned storage if not a value "
       "type")]),
], ids=["union", "readonly", "send"])
def test_wrapped_and_union_leaves_at_every_owning_sink(
        leaf: dict[str, str], records: dict[tuple[int, str], LeafPermission],
        warnings: list[tuple[int, str]]) -> None:
    # A name and a literal of one leaf kind at a field, a container, an
    # `Own[tuple]` argument, an `Own`-element argument, a return and a
    # yield. The rule: a record leaf follows the copy rule the sink's
    # warning applies to it. The rows the parameters cite are the known
    # exceptions.
    _compiler, modules = _compile(LEAF_PRELUDE + _SINKS_BODY.format(**leaf))
    got, _w, ctx = _records_of(_entry(modules).analyzer.ctx)
    assert {key: _perms(got[key])[(0,)] for key in records} == records
    assert not set(got) - set(records)
    assert _copy_warnings(ctx) == warnings


def test_nested_members_and_walrus_args_follow_their_sites_rule() -> None:
    _compiler, modules = _compile(LEAF_PRELUDE + """\
from typing import Iterator
from tpy import Send
def take_mixed(p: tuple[Own[A], B]) -> int32:
    return p[1].w
def walrus_arg(a: A, b: B) -> None:
    t = (a, b)
    print(take_mixed((w := t)))
    print(w[0].v, a.v)
def take_whole(p: Own[tuple[A, tuple[B, int32]]]) -> int32:
    return p[1][1]
def nested_arg(b: B) -> None:
    print(take_whole((A(0), (b, 1))))
    print(b.w)
def nested_yield(a: A, b: B) -> Iterator[tuple[Own[tuple[A, B]], int32]]:
    yield ((a, b), 1)
class G[T]:
    p: tuple[Send[Own[T]], int32]
    def __init__(self, a: Send[T]) -> None:
        self.p = (a, 1)
def take(p: tuple[Own[A], tuple[A, int32]]) -> int32:
    return p[1][1]
def elem_nested(a: A) -> None:
    print(take((A(0), (a, 1))))
""")
    records, _w, ctx = _records_of(_entry(modules).analyzer.ctx)
    # The walrus at an `Own`-element parameter copies its owned leaf whole.
    assert _leaves(records[(7, "TpyName")]) == {
        (0,): (True, True, P.WARNED), (1,): (True, False, P.PASS)}
    # A nested value-tuple member of an argument is owned storage, as its
    # warning says.
    assert _perms(records[(12, "TpyTupleLiteral")]) == {
        (0,): P.MOVE, (1, 0): P.WARNED}
    # An `Own`-marked nested tuple member of a yield holds its members in
    # borrow form: no copy, undecided.
    assert _perms(records[(15, "TpyTupleLiteral")]) == {
        (0, 0): P.UNDECIDED, (0, 1): P.UNDECIDED}
    # The field stores a copy of the borrowed `Send[Own[T]]` member, yet the
    # field's check does not see it as owned and warns nothing: undecided
    # (BUGS.md#send-own-tuple-leaf-copy-unwarned).
    assert _perms(records[(19, "TpyTupleLiteral")]) == {(0,): P.UNDECIDED}
    # An unmarked nested member of an `Own`-element argument stays a borrow:
    # the record moves the fresh `A(0)` and passes the borrowed `a`, which
    # the slot does not own, while the site warns it copied
    # (BUGS.md#own-element-arg-nested-tuple-false-copy-warning).
    assert _leaves(records[(23, "TpyTupleLiteral")]) == {
        (0,): (False, True, P.MOVE), (1, 0): (True, False, P.PASS)}
    assert _copy_warnings(ctx) == [
        (7, "copies tuple[A, B] into owned storage"),
        (12, "copies B into owned storage (tuple element 1.0)"),
        (23, "copies A into owned storage (tuple element 1.0)")]


def _binding_facts(source: str, monkeypatch: pytest.MonkeyPatch
                   ) -> dict[tuple[str, str], tuple[NonOwningBind, frozenset[int]]]:
    """The ownership fact (`NonOwningBind`) a tuple binding leaves on each
    local, by (function, local), with `nonowning_elems` of it -- the
    top-level index set every reader asks."""
    out: dict[tuple[str, str], tuple[NonOwningBind, frozenset[int]]] = {}
    note = alias_rebind.note_nonowning_bind

    def recording(ctx: SemanticContext, name: str, at: object,
                  elems: object = None) -> None:
        note(ctx, name, at, elems)
        rec = ctx.func.nonowning_binds.get(name)
        bare = bare_tuple(ctx.func.current_scope.lookup(name))
        if rec is not None and bare is not None:
            out[(ctx.func.current_function.name, name)] = (
                rec, ctx.func.nonowning_elems(name, len(bare.element_types)))

    monkeypatch.setattr(alias_rebind, "note_nonowning_bind", recording)
    _compile_program(PRELUDE + source)
    return out


def test_binding_fact_is_path_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = _binding_facts("""\
def nested_param(p: tuple[tuple[A, int32], int32]) -> None:
    u = p
    print(u[1])
def nested_fresh() -> None:
    u = ((A(1), 3), 4)
    print(u[1])
def top_borrow(a: A) -> None:
    u = (a, 3)
    print(u[1])
def doubly(p: tuple[tuple[tuple[A, int32], int32], A]) -> None:
    u = p
    print(u[1].x)
def value_member(a: A) -> None:
    t = (a, 1)
    u = (t, 2)
    print(u[1])
def own_elem(p: tuple[Own[A], int32]) -> None:
    u = p
    print(u[1])
def own_marked_call(a: A) -> None:
    u = mixed(a)
    print(u[1].x)
def open_elem[T](p: tuple[T, int32]) -> None:
    u = p
    print(u[1])
def call_open(a: A) -> None:
    open_elem((a, 1))
def walrus(p: tuple[tuple[A, int32], int32]) -> None:
    print((u := p)[1])
""", monkeypatch)
    paths = {k: rec.elems for k, (rec, _i) in facts.items()}
    idxs = {k: i for k, (_rec, i) in facts.items()}
    # A name lends every reference path, a nested one included.
    assert paths[("nested_param", "u")] == {(0, 0)}
    assert ("nested_fresh", "u") not in facts
    assert paths[("top_borrow", "u")] == {(0,)}
    assert paths[("doubly", "u")] == {(0, 0, 0), (1,)}
    # The literal member `t` is captured by value: u's storage owns it.
    assert ("value_member", "u") not in facts
    assert paths[("value_member", "t")] == {(0,)}
    # A name owns nothing, an `Own` element included; a call keeps the
    # leaf its declared return marks `Own`.
    assert paths[("own_elem", "u")] == {(0,)}
    assert paths[("own_marked_call", "u")] == {(1,)}
    # An open type parameter may be a reference.
    assert paths[("open_elem", "u")] == {(0,)}
    assert paths[("walrus", "u")] == {(0, 0)}
    # The readers keep the top-level question: an element is borrowed only
    # when it is itself a borrowed path.
    assert idxs[("nested_param", "u")] == frozenset()
    assert idxs[("top_borrow", "u")] == {0}
    assert idxs[("doubly", "u")] == {1}
    assert idxs[("own_marked_call", "u")] == {1}
    assert idxs[("walrus", "u")] == frozenset()


def test_direct_elements_projection() -> None:
    assert direct_elements([]) == frozenset()
    assert direct_elements([(0,), (1, 0), (2, 1, 0)]) == {0}
    assert direct_elements([(0, 0), (0, 1)]) == frozenset()
    assert direct_elements([(0,), (2,)]) == {0, 2}
    assert NonOwningBind(3, None).direct_elems(2) == {0, 1}
    assert NonOwningBind(3, frozenset({(0, 1), (1,)})).direct_elems(2) == {1}
    assert not NonOwningBind(3, frozenset({(0, 1)})).borrows_direct()


def test_ownership_line_names_the_first_direct_borrow(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # A binding borrowing only a nested path makes no top-level element
    # non-owning, so the line every ownership note names is the first
    # binding that borrows directly, whichever comes first.
    source = PRELUDE + """\
def nested_first(p: tuple[tuple[A, int32], int32], q: tuple[A, int32]) -> None:
    u = p  # nested-only
    v = q  # direct
    print(u[1], v[1])
def direct_first(p: tuple[A, int32], q: tuple[tuple[A, int32], int32]) -> None:
    w = p  # direct
    print(w[1])
    z = q  # nested-only
    print(z[1])
"""
    lines: dict[tuple[str, str], int | None] = {}
    note = alias_rebind.note_nonowning_bind

    def recording(ctx: SemanticContext, name: str, at: object,
                  elems: object = None) -> None:
        note(ctx, name, at, elems)
        lines[(ctx.func.current_function.name, name)] = (
            ctx.func.nonowning_line(name))

    monkeypatch.setattr(alias_rebind, "note_nonowning_bind", recording)
    _compile_program(source)
    at = {text.split("#")[1].strip() + ":" + text.split("=")[0].strip(): i
          for i, text in enumerate(source.splitlines(), 1) if "  # " in text}
    assert lines[("nested_first", "u")] is None
    assert lines[("nested_first", "v")] == at["direct:v"]
    assert lines[("direct_first", "w")] == at["direct:w"]
    assert lines[("direct_first", "z")] is None

def test_value_captured_member_keeps_its_copy_hazard() -> None:
    # `t` captures the still-live `Own` member by value; its binding still
    # reports that element held by reference, so the explicit copy() at the
    # tuple's last use is not advised away.
    _compiler, modules = _compile(PRELUDE + """\
from tpy import copy
def own_str(s: Own[str]) -> Own[tuple[str, A]]:
    t = (s, A(1))
    print(s)
    return copy(t)
def own_int(n: Own[int32]) -> Own[tuple[int32, A]]:
    t = (n, A(1))
    print(n)
    return copy(t)
""")
    assert not [w for w in _warnings(_entry(modules).analyzer)
                if "unnecessary copy()" in w]
