"""The borrow-alias decl wave: four source-shape rows for reference-type
slots initialised from lvalue/rvalue accesses.

Row 1 -- REF_ALIAS off a borrow-returning user-record __getitem__ subscript
(`r = e[k]` -> `Node& r = e[k];`), record-NAME and scalar keys.
Row 2 -- const REF_ALIAS off a borrow-form tuple PARAM's pointer-repr
element (`b = p[1]` -> `const Counter& b = (*std::get<1>(p));`).
Row 3 -- REF_ALIAS off a base-qualified field (`nums = A.buf` ->
`const std::vector<int32_t>& nums = this->A::buf;`), const via the
readonly-method projection, mutable in a plain method.
Row 4 -- the plain copy decl off a record field of an RVALUE call receiver
(`jar = get().cookies;` -- member of a dying temporary, so the copy is the
only legal emit and C++ selects the move ctor). A BORROW-returning receiver
keeps rejecting: copying a live object's field is the REF_ALIAS
value-position design stop.
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)


class TestUserGetitemRefAliasDecl:
    SRC = (
        "from tpy import Int32\n"
        "class Node:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32):\n        self.v = v\n"
        "class KeyEcho:\n"
        "    def __init__(self):\n        pass\n"
        "    def __getitem__(self, k: Node) -> Node:\n        return k\n"
        "class Holder:\n"
        "    a: Node\n"
        "    def __init__(self, a: Node):\n        self.a = a\n"
        "    def __getitem__(self, i: Int32) -> Node:\n        return self.a\n"
        "def pick(h: Holder) -> Int32:\n"
        "    n = h[0]\n"
        "    n.v += 1\n"
        "    return n.v\n"
        "def main():\n"
        "    e = KeyEcho()\n"
        "    k = Node(1)\n"
        "    r = e[k]\n"
        "    k.v = 5\n"
        "    r.v = 9\n"
        "    print(r.v, k.v, pick(Holder(Node(3))))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "Node& r = e[k];" in cpp
        assert "Node& n = h[0];" in cpp

    def test_witnesses_record_getitem(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert _fn(thir, "pick") is not None
        assert wit.get("subscript.record_getitem", 0) >= 2

    def test_own_returning_getitem_keeps_rejecting(self):
        # A by-VALUE (Own) __getitem__ result is an rvalue -- no alias to
        # bind; the decl stays on the AST path.
        src = (
            "from tpy import Int32, Own\n"
            "class Node:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32):\n        self.v = v\n"
            "class Maker:\n"
            "    def __init__(self):\n        pass\n"
            "    def __getitem__(self, i: Int32) -> Own[Node]:\n"
            "        return Node(i)\n"
            "def take(m: Maker) -> Int32:\n"
            "    n = m[0]\n"
            "    return n.v\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "take") is None

    def test_reassigned_alias_keeps_rejecting(self):
        # The POINTER sibling needs the `&(e[k])` reseat lift -- unwitnessed.
        src = (
            "from tpy import Int32\n"
            "class Node:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32):\n        self.v = v\n"
            "class KeyEcho:\n"
            "    def __init__(self):\n        pass\n"
            "    def __getitem__(self, k: Node) -> Node:\n        return k\n"
            "def use(e: KeyEcho, a: Node, b: Node) -> Int32:\n"
            "    r = e[a]\n"
            "    r = e[b]\n"
            "    return r.v\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestBorrowTupleParamElemRefAliasDecl:
    SRC = (
        "from tpy import Int32, readonly, nocopy\n"
        "@nocopy\n"
        "class Counter:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "def mixed(p: readonly[tuple[Int32, Counter]]) -> Int32:\n"
        "    a = p[0]\n"
        "    b = p[1]\n"
        "    return a + b.n\n"
        "def main() -> None:\n"
        "    c = Counter(5)\n"
        "    print(mixed((10, c)))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "const Counter& b = (*std::get<1>(p));" in cpp

    def test_witnesses_and_const(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        fn = _fn(thir, "mixed")
        assert fn is not None
        assert wit.get("decl.btuple_elem_alias", 0) >= 1
        decl = next(s for s in fn.body if getattr(s, "name", None) == "b")
        assert decl.is_const is True

    def test_mutable_param_routes_byte_identical(self):
        # The non-readonly disjunct: a plain borrow-form tuple param's
        # element binds the same deref render, non-const.
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "def bump(t: tuple[Int32, P]) -> Int32:\n"
            "    b = t[1]\n"
            "    b.x += 1\n"
            "    return b.x\n"
            "def main():\n"
            "    p = P(1)\n"
            "    print(bump((5, p)), p.x)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "P& b = (*std::get<1>(t));" in cpp

    def test_own_tuple_param_keeps_rejecting(self):
        # An `Own[tuple]` param is STORAGE form -- `std::get` yields the
        # element `T&`, so the deref-flagged render would be ill-formed;
        # the predicate's Own exclusion keeps the body on the AST path.
        src = (
            "from tpy import Int32, Own, nocopy\n"
            "@nocopy\n"
            "class Counter:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
            "def use(p: Own[tuple[Int32, Counter]]) -> Int32:\n"
            "    b = p[1]\n"
            "    return b.n\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestUnboundSelfFieldRefAliasDecl:
    SRC = (
        "from tpy import Int32, readonly\n"
        "class A:\n"
        "    buf: list[Int32]\n"
        "class B:\n"
        "    buf: list[str]\n"
        "class Combined(A, B):\n"
        "    def __init__(self) -> None:\n"
        "        A.buf = [Int32(1), Int32(2)]\n"
        "        B.buf = [\"x\", \"y\"]\n"
        "    @readonly\n"
        "    def total(self) -> Int32:\n"
        "        nums = A.buf\n"
        "        return Int32(len(nums))\n"
        "    def grow(self) -> None:\n"
        "        nums = A.buf\n"
        "        nums.append(7)\n"
        "def main() -> None:\n"
        "    c = Combined()\n"
        "    c.grow()\n"
        "    print(c.total())\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        joined = hpp + cpp
        # Const via the @readonly projection; mutable in the plain method.
        assert ("const std::vector<int32_t>& nums = this->A::buf;"
                in joined)
        assert "std::vector<int32_t>& nums = this->A::buf;" in joined

    def test_witnesses_unbound_self(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert wit.get("field.unbound_self", 0) >= 2

    def test_resumable_method_keeps_rejecting(self):
        # The AST hardcodes `this->`; a resumable method coro spells its
        # receiver `__self`, so the shape must stay on the AST path. The
        # FIRST gate is the resumable alias-bind admission (`_f2_reseat_ok`
        # has no unbound-self arm -> `res.alias_bind`); the shared
        # spelling's `self_cpp != "this"` fence is the backstop should
        # that admission ever widen. Full pipeline: only
        # generate_code_to_strings reaches the resumable leaf lowering,
        # and the assert names the exact gate so an unrelated earlier
        # reject cannot satisfy it.
        src = (
            "from tpy import Int32\n"
            "async def one() -> Int32:\n"
            "    return 1\n"
            "class A:\n"
            "    buf: list[Int32]\n"
            "class Sub(A):\n"
            "    def __init__(self) -> None:\n"
            "        A.buf = [Int32(1)]\n"
            "    async def total(self) -> Int32:\n"
            "        nums = A.buf\n"
            "        x = await one()\n"
            "        return Int32(len(nums)) + x\n"
        )
        _assert_rejects_at(_reject_tally(src), "resumable:res.alias_bind")


class TestFieldOfRvalueCallCopyDecl:
    SRC = (
        "from tpy import Int32, Own\n"
        "class Jar:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n        self.n = n\n"
        "class Resp:\n"
        "    jar: Jar\n"
        "    def __init__(self, n: Int32):\n        self.jar = Jar(n)\n"
        "def make(n: Int32) -> Own[Resp]:\n"
        "    return Resp(n)\n"
        "class Sess:\n"
        "    def __init__(self):\n        pass\n"
        "    def get(self, n: Int32) -> Own[Resp]:\n"
        "        return Resp(n)\n"
        "def main():\n"
        "    j = make(3).jar\n"
        "    j.n = 5\n"
        "    s = Sess()\n"
        "    k = s.get(7).jar\n"
        "    print(j.n, k.n)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "Jar j = make(3).jar;" in cpp
        assert "Jar k = s.get(7).jar;" in cpp

    def test_witnesses_copy_decl(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert wit.get("field.call_recv", 0) >= 2
        assert wit.get("decl.owned_record", 0) >= 2

    def test_borrow_returning_receiver_keeps_rejecting(self):
        # `h.peek()` returns a C++ `T&` -- its field is a LIVE object's
        # member, so the copy decl stays closed (the REF_ALIAS
        # value-position design stop).
        src = (
            "from tpy import Int32\n"
            "class Jar:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "class Resp:\n"
            "    jar: Jar\n"
            "    def __init__(self, n: Int32):\n        self.jar = Jar(n)\n"
            "class Hold:\n"
            "    r: Resp\n"
            "    def __init__(self):\n        self.r = Resp(1)\n"
            "    def peek(self) -> Resp:\n"
            "        return self.r\n"
            "def use(h: Hold) -> Int32:\n"
            "    j = h.peek().jar\n"
            "    return j.n\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
