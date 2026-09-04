"""The post-if / branch-entry narrowing extraction, read off the FACTS.

Sema stamps a branch's narrowings by a compositional recursion over the whole
boolean algebra, and the AST's extraction arms are driven by that map. A reader
that recovers the subject from the CONDITION's syntax instead covers strictly
less than the producer, and its non-recognition path emits nothing and rejects
nothing -- so the alias the AST declares just disappears. Lowering either
generates or raises; a drop does neither, and the body still counts as ROUTED,
so the ratchet reads the loss as progress.

The routing pins here are the shapes a finite condition recogniser misses (a
negated isinstance leaf inside an `or` chain, at either end, over two
subjects); the reject pins are the walks that have no mirrored arm and must now
say so rather than drop the alias.
"""
from .testutil import (
    _assert_byte_identical, _assert_rejects_at,
    _assert_routes_byte_identical, _lower_ctx_witnessed, _thir_ctx,
    _thir_ctx_witnessed,
)

_AB = (
    "from tpy import Int32, ValueType\n"
    "class A(ValueType):\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "class B(ValueType):\n"
    "    m: Int32\n"
    "    def __init__(self, m: Int32) -> None:\n"
    "        self.m = m\n"
)


class TestNegatedLeafOrChainGuard:
    def test_negated_leaf_first_routes_with_the_alias(self):
        # `not isinstance(...)` as an or-LEAF: the chain reader peels a `not`
        # around the whole chain but requires a bare call per leaf, so this
        # subject is unreachable from the condition -- the else fact names it
        # regardless.
        src = _AB + (
            "def take(u: A | B, flag: bool) -> Int32:\n"
            "    if not isinstance(u, A) or flag:\n"
            "        raise ValueError(\"nope\")\n"
            "    return u.n + 1\n"
            "def use() -> None:\n"
            "    print(take(A(3), False))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const auto& __u = std::get<A>(u);" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["narrow.union_post_if"] >= 1

    def test_negated_leaf_last_routes(self):
        # Leaf POSITION is irrelevant -- the fact does not move with it.
        src = _AB + (
            "def take(u: A | B, flag: bool) -> Int32:\n"
            "    if flag or not isinstance(u, A):\n"
            "        raise ValueError(\"nope\")\n"
            "    return u.n + 1\n"
            "def use() -> None:\n"
            "    print(take(A(3), False))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const auto& __u = std::get<A>(u);" in hpp + cpp

    def test_optional_three_member_union_guard_routes(self):
        # The stdlib flavor: a three-member nullable union whose second leaf
        # compares the same subject (`tz != self`).
        src = (
            "from tpy import Int32, ValueType\n"
            "class Z1(ValueType):\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def __eq__(self, other: \"Z1\") -> bool:\n"
            "        return self.n == other.n\n"
            "class Z2(ValueType):\n"
            "    m: Int32\n"
            "    def __init__(self, m: Int32) -> None:\n"
            "        self.m = m\n"
            "def check(tz: \"Z1 | Z2 | None\", me: Z1) -> Int32:\n"
            "    if not isinstance(tz, Z1) or tz != me:\n"
            "        raise ValueError(\"not self\")\n"
            "    return tz.n\n"
            "def use() -> None:\n"
            "    a = Z1(1)\n"
            "    b: Z1 | Z2 | None = a\n"
            "    print(check(b, a))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::get<Z1>(tz)" in hpp + cpp

    def test_two_subjects_emit_both_aliases(self):
        # The arm emits one alias per QUALIFYING FACT, not one per statement:
        # this chain leaves two independent subjects narrowed.
        src = _AB + (
            "def take(u: A | B, w: A | B) -> Int32:\n"
            "    if not isinstance(u, A) or not isinstance(w, A):\n"
            "        raise ValueError(\"nope\")\n"
            "    return u.n + w.n\n"
            "def use() -> None:\n"
            "    print(take(A(3), A(4)))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "const auto& __u = std::get<A>(u);" in out
        assert "const auto& __w = std::get<A>(w);" in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["narrow.union_post_if"] >= 2


class TestPostIfNonEmitters:
    """The neighbours that must keep emitting exactly what they emit today."""

    def test_plain_isinstance_leaf_chain_unchanged(self):
        # An UN-negated leaf: recognised by the condition reader before and
        # after, and the fall-through still leaves the complement narrowed.
        src = _AB + (
            "def take(u: A | B, flag: bool) -> Int32:\n"
            "    if isinstance(u, B) or flag:\n"
            "        raise ValueError(\"nope\")\n"
            "    return Int32(0)\n"
            "def use() -> None:\n"
            "    print(take(A(3), False))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "    const auto& __u = std::get<A>(u);" in hpp + cpp

    def test_bare_negated_guard_unchanged(self):
        src = _AB + (
            "def take(u: A | B) -> Int32:\n"
            "    if not isinstance(u, A):\n"
            "        raise ValueError(\"nope\")\n"
            "    return u.n + 1\n"
            "def use() -> None:\n"
            "    print(take(A(3)))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const auto& __u = std::get<A>(u);" in hpp + cpp

    def test_remaining_union_else_fact_emits_nothing(self):
        # The else fact is still a UNION, which has no single alternative to
        # extract -- the arm must stay silent, not extract a member.
        src = _AB + (
            "class C(ValueType):\n"
            "    k: Int32\n"
            "    def __init__(self, k: Int32) -> None:\n"
            "        self.k = k\n"
            "def take(v: A | B | C) -> Int32:\n"
            "    if isinstance(v, A):\n"
            "        return v.n\n"
            "    return Int32(0)\n"
            "def use() -> None:\n"
            "    print(take(A(3)))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        # Exactly one extraction: the then-branch entry. A second one at the
        # outer scope would be the post-if arm firing on a union fact.
        assert (hpp + cpp).count("std::get<A>(v)") == 1

    def test_literal_only_facts_emit_nothing(self):
        # A LiteralType fact tracks dead-branch folding; it binds no local, so
        # the post-if arm must not treat it as an extraction.
        src = (
            "from typing import Literal\n"
            "from tpy import Int32\n"
            "def take(mode: Literal[\"a\", \"b\"]) -> Int32:\n"
            "    if mode == \"a\":\n"
            "        return Int32(1)\n"
            "    return Int32(2)\n"
            "def use() -> None:\n"
            "    print(take(\"a\"))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::get<" not in hpp + cpp


class TestUnmirroredFactsReject:
    """A fact no CONDITION arm claims is carried by the BRANCH instead.

    Emitting nothing is the failure mode being fenced out: a dropped alias is
    a missing line, and the body still counts as routed. A single-var concrete
    fact now takes the branch-entry extraction; anything wider (a second
    subject, an already-narrowed one) must still reject rather than drop.
    """

    def test_else_body_branch_entry_facts_route(self):
        # The same chain with a genuine `else`: the fact lands on the ELSE
        # branch, which carries its extraction at branch entry even though no
        # narrowing arm claims the condition.
        src = _AB + (
            "def take(u: A | B, flag: bool) -> Int32:\n"
            "    if not isinstance(u, A) or flag:\n"
            "        return Int32(0)\n"
            "    else:\n"
            "        return u.n\n"
            "def use() -> None:\n"
            "    print(take(A(3), False))\n"
            "use()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert thir is not None
        assert w.get("if.cond_facts_alias", 0) >= 1

    def test_unread_subject_else_body_facts_route(self):
        # The same shape with the subject never read again: the extraction is
        # still emitted (an unused alias the build tolerates), so the branch
        # does not silently lose it.
        src = _AB + (
            "def take(u: A | B, flag: bool) -> Int32:\n"
            "    if not isinstance(u, A) or flag:\n"
            "        return Int32(0)\n"
            "    else:\n"
            "        return Int32(1)\n"
            "def use() -> None:\n"
            "    print(take(A(3), False))\n"
            "use()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert thir is not None
        assert w.get("if.cond_facts_alias", 0) >= 1

    def test_recursive_union_second_guard_rejects(self):
        # The already-narrowed skip in the AST-facts filter rides the plain
        # union leg only, so a recursive-union subject reaches the plan a
        # second time; the AST suffix-bumps a fresh alias there and the plan
        # has no maker for that.
        src = (
            "type V = None | int | str | list[V]\n"
            "def take(v: V) -> int:\n"
            "    if not isinstance(v, int):\n"
            "        return 0\n"
            "    if not isinstance(v, int):\n"
            "        return 1\n"
            "    return v\n"
            "def use() -> None:\n"
            "    a: V = 5\n"
            "    print(take(a))\n"
            "use()\n"
        )
        _ctx, fb = _thir_ctx(src)
        _assert_rejects_at(fb, "body:stmt.if", "if.post_narrow_unmirrored")

    def test_while_head_facts_route_with_the_alias(self):
        # No narrowing arm reads an `and` chain, but sema still stamps the
        # complement of its negated isinstance leaf: the body carries that
        # extraction at loop entry, so reads of the subject see the member.
        src = _AB + (
            "def take(u: A | B, flag: bool) -> Int32:\n"
            "    t = Int32(0)\n"
            "    while not isinstance(u, B) and flag:\n"
            "        t += u.n\n"
            "        flag = False\n"
            "    return t\n"
            "def use() -> None:\n"
            "    print(take(A(3), True))\n"
            "use()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("while.cond_facts_alias", 0) >= 1
        assert thir is not None

    def test_generator_frame_guard_rejects(self):
        # A resumable leaf takes the alias only in a block whose control
        # leaves the frame; anything else has no BB-local mirror.
        src = _AB + (
            "from typing import Iterator\n"
            "def g(u: A | B, flag: bool) -> Iterator[Int32]:\n"
            "    if not isinstance(u, A) or flag:\n"
            "        raise ValueError(\"nope\")\n"
            "    yield Int32(7)\n"
            "def use() -> None:\n"
            "    for v in g(A(1), False):\n"
            "        print(v)\n"
            "use()\n"
        )
        # The landmark is shared by every narrow reject in the resumable walk,
        # so it cannot name WHICH fence held; the alias assertion pins the
        # obligation itself -- the lowering owes it here, unread or not.
        _ctx, fb = _thir_ctx(src)
        _assert_rejects_at(fb, "resumable:res.narrowed_resume")

    def test_finally_helper_post_if_routes(self):
        # The helper walk drives the post-if arm off the FACTS, not the
        # condition's shape -- this subject is unreachable from the condition,
        # so a shape reader would drop the extraction owed here.
        # The alias is unread (`print` does not touch `u`) and still owed.
        src = _AB + (
            "from typing import Iterator\n"
            "def g(u: A | B, flag: bool) -> Iterator[Int32]:\n"
            "    try:\n"
            "        yield Int32(1)\n"
            "    finally:\n"
            "        if not isinstance(u, A) or flag:\n"
            "            raise ValueError(\"nope\")\n"
            "        print(\"done\")\n"
            "def use() -> None:\n"
            "    for v in g(A(1), False):\n"
            "        print(v)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const auto& __u = std::get<A>(u);" in hpp + cpp
        _ctx, faces, fb = _thir_ctx_witnessed(src)
        assert not fb
        assert faces["res.postif_narrow"] >= 1
