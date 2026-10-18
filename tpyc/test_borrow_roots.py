"""BorrowTracker root resolution, the storage-walk key adapters and the
storage key type, over shapes no snapshot case can reach.

A capture re-seated through two aliases of ONE storage makes the borrow graph
a diamond. Resolving it needs the has-sources test to be unfiltered by the
visited set, or the second path reports the intermediate alias as a root; the
source-level face of that is masked today by the dotted-key gap filed as
BUGS.md#field-borrow-root-skips-alias-hop, which rejects the same programs
first, so the graph is the only level this is observable at.
"""
import copy
from contextlib import contextmanager
from collections.abc import Iterator

import pytest

from .compilation_context import activate_compiler
from .parse import (
    ResultForm, walk_body_stmts, TpyBinOp, TpyCall, TpyCoerce, TpyExpr, TpyFieldAccess, TpyIfExpr,
    TpyIntLiteral, TpyName, TpySubscript, TpyVarDecl,
)
from .sema.call_lends import (
    CalleeAccess, CalleeFact, ContractLend, MayLend, ReturnLend, TempLend,
    ViewLend, callee_lend_fact,
    frame_operand_keys, frame_operands, proven_lend_roots,
)
from .sema.context import (BorrowKind, BorrowTracker, SemanticContext,
                           frame_binding_fact)
from .sema.local_deduction import LocalTypeDeduction
from .sema.storage_walk import (
    Ask, KeyShape, LeafKind, LoanKey, Roots, StorageKey, Unkeyed, Use,
    borrow_key, call_loans, container_path_key, leaves, loan_key,
    reads_static_only, receiver_leaves, select_arm_keys, static_read_kind,
    storage_roots, view_leaves,
)
from .thir.testutil import _compile, _entry
from .typesys import INT32, FieldInfo, RecordInfo
from .value_category import peel_coerce

_k = StorageKey.of_name


def _diamond() -> BorrowTracker:
    # q re-seated over b1 and b2, which both alias h.
    bt = BorrowTracker()
    bt.add_borrow(_k("h"), "b1", BorrowKind.ALIAS)
    bt.add_borrow(_k("h"), "b2", BorrowKind.ALIAS)
    bt.add_borrow(_k("b1"), "q", BorrowKind.OPAQUE)
    bt.add_borrow(_k("b2"), "q", BorrowKind.OPAQUE)
    return bt


def test_converging_paths_both_resolve_to_the_shared_root() -> None:
    assert _diamond().all_storage_through_borrows("q") == [_k("h")]


def test_distinct_roots_are_all_reported_nearest_first() -> None:
    bt = BorrowTracker()
    bt.add_borrow(_k("h"), "q", BorrowKind.OPAQUE)
    bt.add_borrow(_k("g"), "q", BorrowKind.OPAQUE)
    assert bt.all_storage_through_borrows("q") == [_k("h"), _k("g")]


def test_unborrowed_name_has_no_root_and_falls_back_to_itself() -> None:
    bt = _diamond()
    assert bt.all_storage_through_borrows("z") == []
    assert bt.storage_roots_or_self("z") == [_k("z")]


def test_cycle_terminates() -> None:
    bt = BorrowTracker()
    bt.add_borrow(_k("a"), "b", BorrowKind.ALIAS)
    bt.add_borrow(_k("b"), "a", BorrowKind.ALIAS)
    # Every reachable node has a source, so none is storage; the caller's
    # fallback names the binding itself rather than an arbitrary cycle member.
    assert bt.all_storage_through_borrows("a") == []
    assert bt.storage_roots_or_self("a") == [_k("a")]


def test_alias_chain_ends_at_a_member_key() -> None:
    # b -> a -> o.f: a member path borrows nothing, so it is the storage.
    of = StorageKey("o", ("f",))
    bt = BorrowTracker()
    bt.add_borrow(of, "a", BorrowKind.ALIAS)
    bt.add_borrow(_k("a"), "b", BorrowKind.ALIAS)
    assert bt.effective_storage("b") == of
    assert bt.all_storage_through_borrows("b") == [of]


def test_retarget_moves_borrowers_onto_a_member_key() -> None:
    # Rebinding `a` (which aliases o.f) keeps its borrower on o.f and drops
    # the paths off a's old object.
    of = StorageKey("o", ("f",))
    bt = BorrowTracker()
    bt.add_borrow(of, "a", BorrowKind.ALIAS)
    bt.add_borrow(_k("a"), "b", BorrowKind.ALIAS)
    bt.add_borrow(StorageKey("a", ("g",)), "c", BorrowKind.ALIAS)
    bt.retarget_storage_borrows("a")
    assert "b" in bt.loans[of]
    assert _k("a") not in bt.loans
    assert StorageKey("a", ("g",)) not in bt.loans


# No corpus source stacks two coercions; the adapters peel every one of
# them, where the old speller peeled a single one.
def _coerced_twice(e: TpyExpr) -> TpyExpr:
    for _ in range(2):
        e = TpyCoerce(expr=e, actual_type=None, expected_type=None,
                      coercion=None, context_kind="", context_msg="")
    return e


def test_element_under_two_coercions_keys_its_container() -> None:
    sub = TpySubscript(TpyName("t"), TpyIntLiteral(0))
    lk = loan_key(_coerced_twice(sub))
    assert isinstance(lk, LoanKey)
    assert (lk.key, lk.shape) == (_k("t"), KeyShape.ELEMENT)
    assert lk.index is sub


def test_field_under_two_coercions_keys_the_field() -> None:
    lk = loan_key(_coerced_twice(TpyFieldAccess(TpyName("o"), "f")))
    assert isinstance(lk, LoanKey)
    assert (lk.key, lk.key.root, lk.shape, lk.index) == (
        StorageKey("o", ("f",)), "o", KeyShape.FIELD, None)


def test_ternary_under_two_coercions_keys_each_arm() -> None:
    xs0 = TpySubscript(TpyName("xs"), TpyIntLiteral(0))
    of = TpyFieldAccess(TpyName("o"), "f")
    inner = TpyIfExpr(TpyName("d"), _coerced_twice(TpyName("ys")), of)
    expr = _coerced_twice(TpyIfExpr(TpyName("c"), _coerced_twice(xs0),
                                    _coerced_twice(inner)))
    keys = select_arm_keys(expr)
    assert keys is not None
    assert [(str(k.key), k.shape) for k in keys] == [
        ("xs", KeyShape.ELEMENT), ("ys", KeyShape.WHOLE),
        ("o.f", KeyShape.FIELD)]
    assert receiver_leaves(expr) == [xs0, TpyName("ys"), of]
    # The ternary as a whole names no single key.
    assert loan_key(expr) is Unkeyed.UNPLACEABLE
    assert borrow_key(expr) is None


def test_call_under_two_coercions_is_not_a_place() -> None:
    expr = _coerced_twice(TpyCall(TpyName("f"), []))
    assert loan_key(expr) is Unkeyed.NOT_A_PLACE
    assert select_arm_keys(expr) is None


def test_store_container_under_coerced_subscripts() -> None:
    items = TpyFieldAccess(TpyName("o"), "items")
    place = _coerced_twice(TpySubscript(_coerced_twice(items),
                                        TpyIntLiteral(0)))
    assert container_path_key(place) == StorageKey("o", ("items",))


def test_and_or_arm_of_a_ternary_stays_one_unkeyed_operand() -> None:
    # The ternary splits its arms; an and/or arm is not split there and
    # loans nothing (BUGS.md#select-or-arm-unkeyed), while the receiver
    # leaves flatten it.
    xs0 = TpySubscript(TpyName("xs"), TpyIntLiteral(0))
    a_or_b = TpyBinOp(TpyName("a"), "||", TpyName("b"))
    expr = TpyIfExpr(TpyName("c"), xs0, a_or_b)
    keys = select_arm_keys(expr)
    assert keys is not None
    assert [(str(k.key), k.shape) for k in keys] == [("xs", KeyShape.ELEMENT)]
    assert receiver_leaves(expr) == [xs0, TpyName("a"), TpyName("b")]


def test_storage_key_and_name_meet_only_through_the_conversion() -> None:
    of = StorageKey("o", ("f",))
    assert str(of) == "o.f" and of.as_name() is None
    assert _k("o").as_name() == "o"
    # A missed crossing fails loudly instead of answering False.
    with pytest.raises(TypeError):
        _ = _k("o") == "o"
    with pytest.raises(TypeError):
        _ = "o" == _k("o")
    with pytest.raises(TypeError):
        _ = "o" in {_k("o"): 1}
    with pytest.raises(AssertionError):
        _k("o.f")


def test_storage_key_prefix_and_rebase() -> None:
    o, of, ofg = _k("o"), StorageKey("o", ("f",)), StorageKey("o", ("f", "g"))
    assert ofg.has_prefix(o) and ofg.has_prefix(of) and ofg.has_prefix(ofg)
    assert not of.has_prefix(ofg)
    assert not StorageKey("o", ("fg",)).has_prefix(of)
    # `m = o.mid` makes `m.inner` and `o.mid.inner` one place.
    assert StorageKey("m", ("inner",)).rebase(StorageKey("o", ("mid",))) \
        == StorageKey("o", ("mid", "inner"))


def test_storage_key_survives_a_state_copy_as_itself() -> None:
    of = StorageKey("o", ("f",))
    assert copy.deepcopy({of: [of]}) == {of: [of]}
    assert copy.deepcopy(of) is of
    # Sharing across snapshots is sound only because a key cannot change.
    with pytest.raises(AttributeError):
        of.root = "p"  # type: ignore[misc]


def test_unbound_base_field_path_is_rooted_at_self() -> None:
    # `Base.f` names this object's storage: the path and the loan key
    # spell it alike, so a mark through one meets a loan under the other.
    unbound = TpyFieldAccess(TpyName("Base"), "f")
    unbound.unbound_self_parent_type = INT32
    tag = TpyFieldAccess(unbound, "tag")
    assert container_path_key(tag) == StorageKey("self", ("f", "tag"))
    assert borrow_key(unbound) == StorageKey("self", ("f",))


# -- the call branch under each call policy --------------------------------

_LEND_SRC = """\
from tpy import int32, Own
from typing import Callable
class Inner:
    n: int32
    def __init__(self) -> None:
        self.n = 0
    def me(self) -> "Inner":
        return self
class Holder:
    inner: Inner
    def __init__(self) -> None:
        self.inner = Inner()
    def get(self) -> Inner:
        return self.inner
    def pick(self, x: Inner, y: Inner, c: bool) -> Inner:
        return x if c else y
    def make(self) -> Own[Inner]:
        return Inner()
    @property
    def prop(self) -> Inner:
        return self.inner
G = Inner()
def ident(x: Inner) -> Inner:
    return x
def ident_h(x: Holder) -> Holder:
    return x
def ident_l(x: list[Inner]) -> list[Inner]:
    return x
def fresh(x: Inner) -> Own[Inner]:
    return Inner()
def glob() -> Inner:
    return G
def subject(h: Holder, a: Inner, b: Inner, c: bool,
            fn: Callable[[Inner], Inner], h2: Holder, xs: list[Inner],
            ys: list[Inner]) -> None:
    v_recv = h.get()
    v_arg = ident(h.inner)
    v_multi = h.pick(a, b, c)
    v_nested = ident(ident(h.inner).me())
    v_select = ident(a if c else b)
    v_walrus = (w := h.get())
    v_getter = h.prop
    v_value = fn(a)
    v_glob = glob()
    v_fresh = fresh(a)
    v_chain = h.get().me()
    v_off = ident_h(h).inner
    v_wsel = (w2 := h.get() if c else ident(a))
    v_sfield = (ident_h(h) if c else ident_h(h2)).inner
    v_sindex = (ident_l(xs) if c else ident_l(ys))[0]
    v_sarg = ident((h if c else h2).inner)
"""


LendCase = tuple[SemanticContext, dict[str, TpyExpr]]


@pytest.fixture
def lend_case() -> Iterator[LendCase]:
    # The callee facts the policies read resolve modules through the active
    # compiler, as they do during a compile.
    compiler, modules = _compile(_LEND_SRC)
    entry = _entry(modules)
    ctx = entry.analyzer.ctx
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    inits = {s.name: s.init for s in fn.body if isinstance(s, TpyVarDecl)}
    with activate_compiler(compiler):
        yield ctx, inits


@contextmanager
def _pending(ctx: SemanticContext, names: set[str]) -> Iterator[None]:
    """The callee facts as a body sees them before `names`' bodies are
    analyzed: no `return_borrows_from` recorded yet."""
    roots = [fi.root for name in names
             for fi in ctx.registry.get_function(name) or ()]
    saved = [root.return_borrows_from for root in roots]
    for root in roots:
        root.return_borrows_from = None
    try:
        yield
    finally:
        for root, fact in zip(roots, saved):
            root.return_borrows_from = fact


def _holder(ctx: SemanticContext, e: TpyExpr) -> Roots:
    return storage_roots(e, Use.HOLDER, None, MayLend(ctx))


def test_may_follows_an_inferred_receiver_and_argument(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    for name, root in (("v_recv", "h"), ("v_arg", "h"), ("v_nested", "h"),
                       ("v_walrus", "h"), ("v_getter", "h")):
        roots = _holder(ctx, inits[name])
        assert roots.names == [root] and not roots.unknown, name
    assert all(p.through_call for p in _holder(ctx, inits["v_recv"]).places)
    # A getter is a path hop, not a spelled call.
    assert not any(p.through_call
                   for p in _holder(ctx, inits["v_getter"]).places)


def test_may_names_every_lent_operand(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    assert _holder(ctx, inits["v_multi"]).names == ["a", "b"]
    assert _holder(ctx, inits["v_select"]).names == ["a", "b"]


def test_may_reads_a_callable_value_as_lending_nothing(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    assert _holder(ctx, inits["v_value"]) == Roots()


def test_may_reference_result_lending_nothing_is_unknown(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    roots = _holder(ctx, inits["v_glob"])
    assert not roots.places and len(roots.unknown) == 1
    # An owned result is fresh: nothing, and nothing unknown.
    assert _holder(ctx, inits["v_fresh"]) == Roots()


def test_may_pending_callee_is_its_contract_assumed(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    with _pending(ctx, {"ident", "fresh"}):
        roots = _holder(ctx, inits["v_arg"])
        assert roots.names == ["h"] and all(p.assumed for p in roots.places)
        assert roots.proven().single() is None
        # A pending callee whose result is owned lends nothing in any order.
        assert _holder(ctx, inits["v_fresh"]) == Roots()


def test_call_loans_key_each_select_arm_and_chain_root(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    may = MayLend(ctx)
    keys = [str(lo.key) for lo in call_loans(inits["v_select"], may)]
    assert keys == ["a", "b"]
    # A chain of lending calls loans its first receiver, not a temporary.
    loans = call_loans(inits["v_chain"], may)
    assert [str(lo.key) for lo in loans] == ["h"]
    # A lent argument loans the key its own path spells.
    assert [str(lo.key) for lo in call_loans(inits["v_arg"], may)] == [
        "h.inner"]


def test_call_loans_field_off_a_lending_call_loans_its_operand(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    loans = call_loans(inits["v_off"], MayLend(ctx))
    assert [str(lo.key) for lo in loans] == ["h"]
    assert loans[0].source is not None and loans[0].source.idx == 0


def test_call_loans_select_under_a_walrus_keys_each_arm(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    keys = [str(lo.key) for lo in call_loans(inits["v_wsel"], MayLend(ctx))]
    assert keys == ["h", "a"]


def test_call_loans_select_under_a_projection_keys_each_arm(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    may = MayLend(ctx)
    for name, keys in (("v_sfield", ["h", "h2"]), ("v_sindex", ["xs", "ys"]),
                       ("v_sarg", ["h.inner", "h2.inner"])):
        assert [str(lo.key) for lo in call_loans(inits[name], may)] == keys, name


def test_call_loans_name_a_nested_call_s_tuple_temporary() -> None:
    # The tuple temporary `first` binds lives to the end of the statement,
    # so the result `relay` hands on points into it.
    compiler, modules = _compile(
        "from tpy import StrView, int32\n"
        "def first(p: tuple[str, int32]) -> StrView:\n    return p[0]\n"
        "def relay(v: StrView) -> StrView:\n    return v\n"
        "def subject(s: str) -> None:\n"
        "    v = relay(first((s, 1)))\n    print(v)\n")
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    init = fn.body[0].init
    with activate_compiler(compiler):
        loans = call_loans(init, MayLend(entry.analyzer.ctx))
    assert len(loans) == 1 and loans[0].temp_backed
    call = loans[0].call
    assert isinstance(call, TpyCall) and isinstance(call.func, TpyName)
    assert call.func.name == "first"
    assert loans[0].source is not None and loans[0].source.idx == 0


def test_pending_callee_files_no_temp_backed_loan() -> None:
    # A pending callee's sources are guesses: the tuple temporary its
    # argument builds backs the result only once its body says so.
    compiler, modules = _compile(
        "from tpy import StrView, int32\n"
        "def first(p: tuple[str, int32]) -> StrView:\n    return p[0]\n"
        "def subject(s: str) -> None:\n"
        "    v = first((s, 1))\n    print(v)\n")
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    init = fn.body[0].init
    ctx = entry.analyzer.ctx
    with activate_compiler(compiler):
        analyzed = call_loans(init, MayLend(ctx))
        with _pending(ctx, {"first"}):
            pending = call_loans(init, MayLend(ctx))
    assert len(analyzed) == 1 and analyzed[0].temp_backed
    assert not any(lo.temp_backed for lo in pending)


def test_call_loans_file_nothing_for_a_call_the_policy_cannot_answer(lend_case: LendCase) -> None:
    # No operand names what a global-returning call points into.
    ctx, inits = lend_case
    assert call_loans(inits["v_glob"], MayLend(ctx)) == []


def test_call_loans_follow_a_borrow_returning_await() -> None:
    compiler, modules = _compile(
        "class Inner:\n    def __init__(self) -> None:\n        pass\n"
        "class Holder:\n    inner: Inner\n"
        "    def __init__(self) -> None:\n        self.inner = Inner()\n"
        "async def aget(h: Holder) -> Inner:\n    return h.inner\n"
        "async def subject(h: Holder) -> None:\n"
        "    v = await aget(h)\n    print(v)\n")
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    init = fn.body[0].init
    ctx = entry.analyzer.ctx
    assert init.await_result_is_borrow
    with activate_compiler(compiler):
        assert _holder(ctx, init).names == ["h"]
        assert [str(lo.key) for lo in call_loans(init, MayLend(ctx))] == ["h"]


def test_must_refuses_what_it_cannot_prove(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    assert proven_lend_roots(ctx, inits["v_recv"]) == ["h"]
    assert proven_lend_roots(ctx, inits["v_nested"]) == ["h"]
    for name in ("v_select", "v_walrus", "v_getter", "v_value"):
        assert proven_lend_roots(ctx, inits[name]) is None, name


def test_contract_keeps_every_operand_but_an_owned_result(lend_case: LendCase) -> None:
    ctx, inits = lend_case
    contract = ContractLend(ctx)
    roots = storage_roots(inits["v_multi"], Use.HOLDER, None, contract)
    assert roots.names == ["h", "a", "b"]
    assert storage_roots(inits["v_fresh"], Use.HOLDER, None,
                         contract) == Roots()


def test_may_body_less_stub_answers_its_convention() -> None:
    # A protocol method records no borrow fact; a reference it returns is
    # into its receiver.
    compiler, modules = _compile(
        "from typing import Protocol\n"
        "class Inner:\n    def __init__(self) -> None:\n        pass\n"
        "class Getter(Protocol):\n    def get(self) -> Inner: ...\n"
        "def subject(p: Getter) -> None:\n    v = p.get()\n    print(v)\n")
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    init = fn.body[0].init
    assert init.resolved_function_info.root.return_borrows_from is None
    with activate_compiler(compiler):
        assert _holder(entry.analyzer.ctx, init).names == ["p"]


# -- the FRAME and VIEW policies ---------------------------------------------

_FRAME_VIEW_SRC = """\
from enum import Enum
from typing import Callable, Iterator, Literal, Protocol
from tpy import StrView, int32
class Color(Enum):
    RED = 1
class C:
    xs: list[int32]
    def __init__(self) -> None:
        self.xs = [1]
    def items(self) -> Iterator[int32]:
        for x in self.xs:
            yield x
class Viewer(Protocol):
    def view(self, s: str, n: int32) -> StrView: ...
def walk(c: C) -> Iterator[int32]:
    for x in c.xs:
        yield x
def head(s: str, n: int32) -> StrView:
    return s
def up(s: str) -> str:
    return s.upper()
def mode(s: str) -> Literal["r"]:
    return "r"
def head2(s: str, t: str) -> StrView:
    return s
def empty_view(s: str, n: int32) -> StrView:
    return "x"
def gen2(xs: list[int32], k: int32) -> Iterator[int32]:
    for x in xs:
        yield x + k
class Util:
    @staticmethod
    def lit(s: str) -> StrView:
        return "x"
def subject(c: C, s: str, n: int32, p: Viewer, col: Color,
            fn: Callable[[str, int32], StrView],
            fn2: Callable[[int32], StrView],
            xs: list[int32], s2: str) -> None:
    g_method = c.items()
    g_free = walk(c)
    g_two = gen2(xs, n)
    v_empty = empty_view(s, n)
    v_rec2 = head2(s, s2)
    v_kw = p.view(s=s, n=n)
    v_qual = Util.lit(s)
    v_rec = head(s, n)
    v_up = up(s)
    v_lit = mode(s)
    v_stub = p.view(s, n)
    v_val = fn(s, n)
    v_none = fn2(n)
    v_enum = col.name
    v_bslice = b"abc"[1:2]
    v_sslice = "abc"[1:2]
    print(next(g_method, -1), next(g_free, -1), v_rec, v_up, v_lit, v_stub,
          v_val, v_none, v_enum, v_bslice, v_sslice, next(g_two, -1),
          v_empty, v_rec2, v_kw, v_qual)
"""


FrameViewCase = tuple[SemanticContext, LocalTypeDeduction, dict[str, TpyExpr]]


@pytest.fixture
def frame_view_case() -> Iterator[FrameViewCase]:
    compiler, modules = _compile(_FRAME_VIEW_SRC)
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    inits = {s.name: peel_coerce(s.init) for s in fn.body
             if isinstance(s, TpyVarDecl)}
    with activate_compiler(compiler):
        yield entry.analyzer.ctx, entry.analyzer.deduction, inits


def _view_operands(ctx: SemanticContext, e: TpyExpr) -> list[str] | None:
    srcs = ViewLend(ctx).at_call(e, (), Ask.LIFETIME)
    if srcs is None:
        return None
    ops = [peel_coerce(s.expr) for s in srcs]
    return [o.name if isinstance(o, TpyName) else type(o).__name__
            for o in ops]


def test_frame_names_a_method_frame_s_receiver_where_may_does_not(
        frame_view_case: FrameViewCase) -> None:
    ctx, _, inits = frame_view_case
    method, free = inits["g_method"], inits["g_free"]
    # MAY lends nothing at a method frame call: the frame's receiver loan
    # is BUGS.md#generator-method-receiver-borrow-unrepresented.
    assert call_loans(method, MayLend(ctx)) == []
    assert [str(k) for k in frame_operand_keys(method)] == ["c"]
    # The free twin's argument is named by both.
    assert [str(lo.key) for lo in call_loans(free, MayLend(ctx))] == ["c"]
    assert [str(k) for k in frame_operand_keys(free)] == ["c"]


def test_view_answers_recorded_sources_else_every_buffer_operand(
        frame_view_case: FrameViewCase) -> None:
    ctx, _, inits = frame_view_case
    # A recorded fact: its sources only.
    assert _view_operands(ctx, inits["v_rec"]) == ["s"]
    # No fact (a protocol stub) or a callable value: every operand that can
    # own a buffer -- arguments first, then a typed receiver; a number is
    # bufferless.
    assert _view_operands(ctx, inits["v_stub"]) == ["s", "p"]
    assert _view_operands(ctx, inits["v_val"]) == ["s"]
    # A view with nothing it may read is unknown.
    assert _view_operands(ctx, inits["v_none"]) is None
    # A Literal[str] result is static and a non-view result fresh.
    assert _view_operands(ctx, inits["v_lit"]) == []
    assert _view_operands(ctx, inits["v_up"]) == []


def test_view_leaves_classify_each_leaf_on_its_own_node(
        frame_view_case: FrameViewCase) -> None:
    ctx, _, inits = frame_view_case

    def leaves(name: str) -> list[tuple[LeafKind, str | None, int]]:
        return [(lf.kind, lf.name, len(lf.path))
                for lf in view_leaves(inits[name], ViewLend(ctx))]

    # An enum member's `.name` reads a static table, not the member.
    assert leaves("v_enum") == [(LeafKind.STATIC, None, 0)]
    # A `bytes` literal's slice builds owned bytes; a `str` one views the
    # literal.
    assert leaves("v_bslice") == [(LeafKind.TEMPORARY, None, 1)]
    assert leaves("v_sslice") == [(LeafKind.STATIC, None, 1)]
    assert leaves("v_rec") == [(LeafKind.PLACE, "s", 1)]
    assert leaves("v_lit") == [(LeafKind.STATIC, None, 0)]
    assert leaves("v_up") == [(LeafKind.TEMPORARY, None, 0)]
    assert leaves("v_none") == [(LeafKind.UNNAMED, None, 0)]


def test_view_leaves_of_qualifier_reads(tmp_path) -> None:
    (tmp_path / "consts.py").write_text(
        "from typing import Final\nfrom tpy import StrView\n"
        "TAG: Final[StrView] = \"tag\"\nNAME: str = \"name\"\n")
    compiler, modules = _compile(
        "import consts\n"
        "class Base:\n    s: str\n"
        "    def __init__(self) -> None:\n        self.s = \"base\"\n"
        "class Sub(Base):\n"
        "    def run(self) -> None:\n"
        "        v_unbound = Base.s[1:]\n        print(v_unbound)\n"
        "def subject() -> None:\n"
        "    v_final = consts.TAG\n    v_module = consts.NAME[1:]\n"
        "    print(v_final, v_module)\n",
        extra_lib_dirs=[tmp_path])
    entry = _entry(modules)
    inits: dict[str, TpyExpr] = {}
    walk_body_stmts(
        [s for f in (*entry.ast.functions,
                     *(m for r in entry.ast.all_records() for m in r.methods))
         for s in f.body],
        lambda e: None,
        lambda s: inits.__setitem__(s.name, s.init)
        if isinstance(s, TpyVarDecl) else None)
    with activate_compiler(compiler):
        policy = ViewLend(entry.analyzer.ctx)

        def leaves(name: str) -> list[tuple[LeafKind, int]]:
            return [(lf.kind, len(lf.path))
                    for lf in view_leaves(peel_coerce(inits[name]), policy)]

        # A `Final` view read through its module is a constant.
        assert leaves("v_final") == [(LeafKind.CONSTANT, 0)]
        # A plain module variable can be rebound through `global`, and an
        # unbound `Base.f` path reads as a qualifier too: a global, which a
        # view's source reader answers like any unplaceable leaf.
        assert leaves("v_module") == [(LeafKind.GLOBAL, 1)]
        assert leaves("v_unbound") == [(LeafKind.GLOBAL, 1)]


def test_static_read_needs_a_final_referent(tmp_path) -> None:
    # Durability rides on the referent's finality, never on the leaf kind:
    # a mutable qualifier read is a CONSTANT / GLOBAL leaf too.
    (tmp_path / "consts.py").write_text(
        "from typing import Final\nfrom tpy import StrView\n"
        "TAG: Final[StrView] = \"tag\"\nNAME: StrView = \"name\"\n")
    compiler, modules = _compile(
        "import consts\nfrom typing import Final\nfrom tpy import StrView\n"
        "class C:\n    K: Final[StrView] = \"k\"\n"
        "    def __init__(self) -> None:\n        pass\n"
        "def subject(c: C) -> None:\n"
        "    v_final = consts.TAG\n    v_mut = consts.NAME\n"
        "    v_cls = c.K\n    print(v_final, v_mut, v_cls)\n",
        extra_lib_dirs=[tmp_path])
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    inits = {s.name: peel_coerce(s.init) for s in fn.body
             if isinstance(s, TpyVarDecl)}
    # An unstamped module read is classified by its qualifier shape; the
    # stamp is what a filled default carries.
    assert inits["v_final"].module_var_access is None
    inits["v_final"].module_var_access = ("consts", "TAG")
    inits["v_mut_stamped"] = copy.copy(inits["v_mut"])
    inits["v_mut_stamped"].module_var_access = ("consts", "NAME")
    with activate_compiler(compiler):
        ctx = entry.analyzer.ctx
        for policy in (ViewLend(ctx), TempLend(ctx)):
            def static(name: str) -> list[bool]:
                return [lf.static_read for lf in leaves(inits[name], policy)]

            for mutable in ("v_mut", "v_mut_stamped"):
                assert static(mutable) == [False]
                assert not reads_static_only(inits[mutable], policy)
            assert static("v_final") == [True]
            assert reads_static_only(inits["v_final"], policy)
            # A class constant read through an instance reads the class.
            assert static("v_cls") == [True]
            assert reads_static_only(inits["v_cls"], policy)


def test_class_constant_without_a_finality_record_is_not_static() -> None:
    rec = RecordInfo(name="R", fields=[],
                     class_constants={"K": FieldInfo("K", INT32)})
    read = TpyFieldAccess(obj=TpyName("r"), field="K",
                          class_constant_owner=rec)
    assert static_read_kind(read, None) is None
    rec.class_constants_finality["K"] = False
    assert static_read_kind(read, None) is None
    rec.class_constants_finality["K"] = True
    assert static_read_kind(read, None) is LeafKind.GLOBAL


def test_view_falls_back_to_every_buffer_operand_without_a_usable_fact(
        frame_view_case: FrameViewCase) -> None:
    ctx, _, inits = frame_view_case
    # A known-empty fact: every operand that can own a buffer.
    assert _view_operands(ctx, inits["v_empty"]) == ["s"]
    # A keyword operand is an operand; the typed receiver comes last.
    assert _view_operands(ctx, inits["v_kw"]) == ["s", "p"]
    # A class qualifier is no receiver: it lends nothing.
    assert _view_operands(ctx, inits["v_qual"]) == ["s"]
    # A recorded fact names its sources only; a bodied callee read before
    # its body is analyzed has no fact yet, and a result sema stamped fresh
    # (a declared borrow over a temporary: set by hand, no binding here
    # declares one) reads none.
    call = inits["v_rec2"]
    assert _view_operands(ctx, call) == ["s"]
    root = call.resolved_function_info.root
    fact = root.return_borrows_from
    root.return_borrows_from = None
    try:
        assert _view_operands(ctx, call) == ["s", "s2"]
    finally:
        root.return_borrows_from = fact
    form = call.result_form
    call.result_form = ResultForm.COPY
    try:
        assert _view_operands(ctx, call) == ["s", "s2"]
    finally:
        call.result_form = form


def test_view_hoist_roots_of_a_literal_slice(
        frame_view_case: FrameViewCase) -> None:
    _, ded, inits = frame_view_case
    # A `bytes` literal's slice builds owned bytes the hoist rule cannot
    # place; a `str` literal's slice views static storage.
    assert ded.view_hoist_roots(inits["v_bslice"]) is None
    assert ded.view_hoist_roots(inits["v_sslice"]) == set()


def test_frame_selects_the_receiver_only_of_a_method_on_an_object(
        frame_view_case: FrameViewCase) -> None:
    ctx, _, inits = frame_view_case

    def names(name: str) -> list[str] | None:
        ops = frame_operands(inits[name])
        return None if ops is None else [peel_coerce(o).name for o in ops]

    assert names("g_method") == ["c"]
    # A static method called through its class names no receiver (no
    # static generator or coroutine parses today: the call is marked by
    # hand).
    method = inits["g_method"]
    method.is_static_call = True
    try:
        assert names("g_method") == []
    finally:
        method.is_static_call = False
    # A generator keeps the argument it borrows, not a number.
    assert names("g_two") == ["xs"]
    # A call that builds no frame selects nothing.
    assert names("v_rec") is None


def test_frame_names_no_module_qualifier(tmp_path) -> None:
    (tmp_path / "gens.py").write_text(
        "from typing import Iterator\nfrom tpy import int32\n"
        "def gen(xs: list[int32]) -> Iterator[int32]:\n"
        "    for x in xs:\n        yield x\n")
    compiler, modules = _compile(
        "import gens\nfrom tpy import int32\n"
        "def subject(xs: list[int32]) -> None:\n"
        "    g = gens.gen(xs)\n    print(next(g, -1))\n",
        extra_lib_dirs=[tmp_path])
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    init = peel_coerce(fn.body[0].init)
    assert init.user_module_call
    with activate_compiler(compiler):
        ops = frame_operands(init)
    assert ops is not None and [peel_coerce(o).name for o in ops] == ["xs"]


_FRAME_FACT_SRC = """\
from typing import Iterator
from tpy import int32
class H:
    p: list[int32]
    def __init__(self) -> None:
        self.p = [7]
class B:
    h: H
    def __init__(self) -> None:
        self.h = H()
def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x
def packs(*xss: list[int32]) -> Iterator[int32]:
    for xs in xss:
        for x in xs:
            yield x
def pick2(a: list[int32], b: list[int32], c: bool) -> list[int32]:
    return a if c else b
def first_of(t: tuple[list[int32], list[int32]]) -> list[int32]:
    return t[0]
class D(B):
    def run(self) -> None:
        g_unbound = items(B.h.p)
        print(next(g_unbound, -1))
def subject(a: list[int32], b: list[int32], c: bool) -> None:
    for i in range(2):
        g_pack = packs(a, b)
        g_pack1 = packs(a)
        g_pack_rep = packs(a, a)
        print(next(g_pack, -1), next(g_pack1, -1), next(g_pack_rep, -1))
    g_call = items(pick2(a, b, c))
    g_tuple = items(first_of((a, b)))
    g_andor = items(a) or items(b)
    print(next(g_call, -1), next(g_tuple, -1), next(g_andor, -1))
"""


def test_frame_binding_fact_per_operand_rule() -> None:
    # Each operand the frame keeps is a single proven root or a temporary,
    # else the binding is untraced.
    compiler, modules = _compile(_FRAME_FACT_SRC)
    entry = _entry(modules)
    inits: dict[str, TpyExpr] = {}
    walk_body_stmts(
        [s for f in (*entry.ast.functions,
                     *(m for r in entry.ast.all_records() for m in r.methods))
         for s in f.body],
        lambda e: None,
        lambda s: inits.__setitem__(s.name, s.init)
        if isinstance(s, TpyVarDecl) else None)
    ctx = entry.analyzer.ctx
    with activate_compiler(compiler):
        # A `*args` pack is a temporary array of its elements when they
        # name several roots; one naming a single root roots there, though
        # the array is still a temporary of the pass
        # (BUGS.md#frame-pack-temp-rooted-at-element).
        assert frame_binding_fact(inits["g_pack"], ctx) == (
            frozenset({None}), True)
        assert frame_binding_fact(inits["g_pack1"], ctx) == (
            frozenset({"a"}), True)
        assert frame_binding_fact(inits["g_pack_rep"], ctx) == (
            frozenset({"a"}), True)
        # An operand with several roots is untraced: a call returning
        # either argument, a call over a tuple literal of two.
        assert frame_binding_fact(inits["g_call"], ctx) is None
        assert frame_binding_fact(inits["g_tuple"], ctx) is None
        # An and/or of two frames is no frame binding.
        assert frame_binding_fact(inits["g_andor"], ctx) is None
        # An unbound `Base.f` path is this object's storage.
        assert frame_binding_fact(inits["g_unbound"], ctx) == (
            frozenset({"self"}), True)


_DURABILITY_SRC = """\
from enum import Enum
from typing import Callable
from tpy import Own, take_ptr
class Node:
    v: int
    name: str
    def __init__(self, v: int) -> None:
        self.v = v
        self.name = "node"
class Color(Enum):
    RED = 1
def pick(a: list[Node], b: list[Node]) -> list[Node]:
    return a if len(a) > len(b) else b
def make(n: int) -> Own[Node]:
    return Node(n)
def subject(ps: list[Node], n: Node, f: Callable[[Node], Node],
            flag: bool) -> None:
    local = [Node(1)]
    r_pick = pick(ps, local)
    r_fresh = make(1)
    r_ptr = take_ptr(n)
    r_sel = n if flag else ps[0]
    r_enum = Color.RED.name
    r_callable = f(n)
    t_field = make(2).name
    print(len(r_pick), r_fresh.v, r_ptr.v, r_sel.v, r_enum, r_callable.v,
          t_field)
"""


def test_durability_leaves_classify_each_operand_and_call() -> None:
    compiler, modules = _compile(_DURABILITY_SRC)
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    inits = {s.name: s.init for s in fn.body if isinstance(s, TpyVarDecl)}
    with activate_compiler(compiler):
        ctx = entry.analyzer.ctx

        def ret(name: str) -> list[tuple[LeafKind, str | None, list[str]]]:
            return [(lf.kind, lf.name, [c.how.value for c in lf.calls])
                    for lf in leaves(inits[name], ReturnLend(ctx),
                                     split=False)]

        # A call lending a parameter AND a local: both are leaves, so no
        # durability reader takes the result for the parameter's alone.
        assert ret("r_pick") == [(LeafKind.PLACE, "ps", ["recorded"]),
                                 (LeafKind.PLACE, "local", ["recorded"])]
        # An `Own` result is a fresh object: a temporary.
        assert ret("r_fresh") == [(LeafKind.TEMPORARY, None, [])]
        # `take_ptr` lends its argument as a borrowing constructor.
        assert ret("r_ptr") == [(LeafKind.PLACE, "n", ["constructor"])]
        # A ternary is each arm.
        assert ret("r_sel") == [(LeafKind.PLACE, "n", []),
                                (LeafKind.PLACE, "ps", [])]
        # An enum member's `.name` is static storage.
        assert ret("r_enum") == [(LeafKind.STATIC, None, [])]
        # A `Callable` returns a class instance by value: a fresh object.
        assert ret("r_callable") == [(LeafKind.TEMPORARY, None, [])]
        # A view of a temporary's field reads storage dying with the
        # statement.
        assert [lf.kind for lf in leaves(inits["t_field"], TempLend(ctx),
                                         split=False)
                ] == [LeafKind.TEMPORARY]


_CALLABLE_SHAPES_SRC = """\
from typing import Callable
from tpy import StrView
def ident(s: str) -> StrView:
    return s
def make() -> Callable[[str], StrView]:
    return ident
class Holder:
    def __init__(self, f: Callable[[str], StrView]) -> None:
        self.f = f
def subject(h: Holder, f: Callable[[str], StrView], s: str) -> None:
    named: StrView = f(s)
    field: StrView = h.f(s)
    expression: StrView = make()(s)
    print(named, field, expression)
"""


def test_callable_value_shapes_classify_by_callee_access() -> None:
    # A callable value has no body to read a fact off; how the call reaches
    # it is what RETURN and TEMPORARY tell apart.
    compiler, modules = _compile(_CALLABLE_SHAPES_SRC)
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    inits = {s.name: peel_coerce(s.init) for s in fn.body
             if isinstance(s, TpyVarDecl)}
    with activate_compiler(compiler):
        ctx = entry.analyzer.ctx

        def kinds(name: str, policy: object) -> list[tuple[LeafKind,
                                                           str | None]]:
            return [(lf.kind, lf.name)
                    for lf in leaves(inits[name], policy, split=False)]

        for name, access in (("named", CalleeAccess.NAME),
                             ("field", CalleeAccess.MEMBER),
                             ("expression", CalleeAccess.EXPRESSION)):
            lend = callee_lend_fact(inits[name])
            assert (lend.kind, lend.access, lend.fresh, lend.declares_view,
                    lend.sources) == (CalleeFact.CALLABLE, access, False,
                                      True, frozenset()), name
        # RETURN reads no callable's contract: through a name or a receiver
        # the storage is unnamed, through a callee expression a temporary.
        assert kinds("named", ReturnLend(ctx)) == [(LeafKind.UNNAMED, None)]
        assert kinds("field", ReturnLend(ctx)) == [(LeafKind.UNNAMED, None)]
        assert kinds("expression", ReturnLend(ctx)) == [
            (LeafKind.TEMPORARY, None)]
        # TEMPORARY reads a named callable's contract (the view reads its
        # argument), trusts a field-held one as a view method of its
        # receiver, and a callee expression's view as stable.
        assert kinds("named", TempLend(ctx)) == [(LeafKind.PLACE, "s")]
        assert kinds("field", TempLend(ctx)) == [(LeafKind.PLACE, "h")]
        assert kinds("expression", TempLend(ctx)) == [
            (LeafKind.UNNAMED, None)]
