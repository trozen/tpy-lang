"""THIR field-CHAIN read gate: a value scalar/Char/enum field read off a
receiver that is itself a value F1-record field chain (`o.mid.inner.v`,
`self.a.b`). The lowering already recurses `_lower_expr` through the receiver;
these tests pin that the gate admits the chain and the emit stays byte-
identical to the AST path, across return / local / arg / compare positions and
the self / param / narrowed-Optional bottom receivers."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (_compile, _entry, _fn, _lower_ctx,
                       _lower_ctx_witnessed, _assert_routes_byte_identical)

# Three-deep value F1-record chain. `f` returns a param chain, `via_self` a
# `this->` chain, `local` binds the chain then reads it, `narrowed` reaches the
# chain through a proven Optional-ptr receiver.
_CHAIN = (
    "from tpy import Int32, Char\n"
    "class Inner:\n"
    "    v: Int32\n"
    "    c: Char\n"
    "    def __init__(self, v: Int32, c: Char):\n        self.v = v\n        self.c = c\n"
    "class Mid:\n"
    "    inner: Inner\n"
    "    def __init__(self, v: Int32, c: Char):\n        self.inner = Inner(v, c)\n"
    "class Outer:\n"
    "    mid: Mid\n"
    "    def __init__(self, v: Int32, c: Char):\n        self.mid = Mid(v, c)\n"
    "    def via_self(self) -> Int32:\n        return self.mid.inner.v\n"
    "def f(o: Outer) -> Int32:\n    return o.mid.inner.v\n"
    "def get_char(o: Outer) -> Char:\n    return o.mid.inner.c\n"
    "def local(o: Outer) -> Int32:\n"
    "    x = o.mid.inner.v\n    return x + 1\n"
    "def as_compare(o: Outer) -> bool:\n    return o.mid.inner.v > 5\n"
    "def narrowed(o: Outer | None) -> Int32:\n"
    "    if o is not None:\n        return o.mid.inner.v\n    return 0\n"
)


class TestFieldChainRead:
    def _emit(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry,
            options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    def test_routed(self):
        thir = _lower_ctx(_CHAIN)
        for name in ("f", "get_char", "local", "as_compare", "narrowed"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert self._emit(_CHAIN, thir=True) == self._emit(_CHAIN, thir=False)

    def test_emit_arms(self):
        cpp = self._emit(_CHAIN, thir=True)
        assert "return o.mid.inner.v;" in cpp          # param chain, `.`
        assert "return this->mid.inner.v;" in cpp      # self chain, `this->`
        assert "int32_t x = o.mid.inner.v;" in cpp     # local bind
        assert "return o.mid.inner.c;" in cpp          # Char terminal

    def test_face_witnessed(self):
        _, witnessed = _lower_ctx_witnessed(_CHAIN)
        assert witnessed.get("field.chain_recv", 0) >= 1


class TestFieldChainRejects:
    """Chains that reach beyond the value-F1-record slice stay on the AST path
    -- the receiver widening never mis-routes an Optional / container / non-name
    intermediate that would need a deref / unwrap the mirror lacks."""

    def _rejects(self, extra_field: str, body: str) -> bool:
        src = (
            "from tpy import Int32\n"
            "class Inner:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32):\n        self.v = v\n"
            "class Mid:\n"
            f"    {extra_field}\n"
            "    def __init__(self, v: Int32):\n        self.inner = Inner(v)\n"
            "class Outer:\n"
            "    mid: Mid\n"
            "    def __init__(self, v: Int32):\n        self.mid = Mid(v)\n"
            f"{body}"
        )
        return _fn(_lower_ctx(src), "f") is None

    def test_optional_intermediate_rejects(self):
        # `o.mid.inner.v` where `inner` is `Inner | None`: the AST unwraps the
        # STORAGE optional (`deref_optional_check`), which is its own arm --
        # not the `Ptr`-valued intermediate the chain does admit.
        assert self._rejects(
            "inner: Inner | None",
            "def f(o: Outer) -> Int32:\n    return o.mid.inner.v\n")


_PTR_CHAIN = (
    "from tpy import Int32, Ptr\n"
    "class Q:\n"
    "    flag: Int32\n"
    "    def __init__(self, f: Int32):\n        self.flag = f\n"
    "class A:\n"
    "    q: Q\n"
    "    def __init__(self, f: Int32):\n        self.q = Q(f)\n"
    "class S:\n"
    "    a: A\n"
    "    def __init__(self, f: Int32):\n        self.a = A(f)\n"
    "class M:\n"
    "    s: Ptr[S]\n"
    "    def __init__(self, s: Ptr[S]):\n        self.s = s\n"
    "    def read(self) -> Int32:\n        return self.s.a.q.flag\n"
    "    def write(self, v: Int32) -> None:\n        self.s.a.q.flag = v\n"
    "def via_name(s: Ptr[S]) -> Int32:\n    return s.a.q.flag\n"
    "def proven(s0: S) -> Int32:\n"
    "    p: Ptr[S] = s0\n    return p.a.q.flag\n"
    "def main() -> None:\n"
    "    s = S(7)\n    m = M(s)\n    print(m.read())\n    m.write(9)\n"
    "    print(m.read())\n    print(via_name(s))\n    print(proven(s))\n"
    "main()\n"
)


class TestPtrValuedIntermediate:
    """A chain hop THROUGH a `Ptr`-valued field/param: the inner hop carries
    sema's auto-deref marker, so it is not marker-clean, but it renders
    through the pointer arm and the outer hop spells a bare `.` off it."""

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(_PTR_CHAIN)

    def test_both_renders_witnessed(self):
        # The unproven receiver keeps its NULL CHECK and the proven one spells
        # `->`: mirroring the arrow unconditionally would drop the check, so
        # both renders must appear.
        compiler, modules = _compile(_PTR_CHAIN)
        hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        out = hpp + cpp
        assert "return ::tpy::deref_check(this->s).a.q.flag;" in out
        assert "::tpy::deref_check(this->s).a.q.flag = v;" in out   # write
        assert "return ::tpy::deref_check(s).a.q.flag;" in out      # param
        assert "return p->a.q.flag;" in out                         # non-null

    def test_face_witnessed(self):
        _, witnessed = _lower_ctx_witnessed(_PTR_CHAIN)
        assert witnessed.get("field.chain_ptr_recv", 0) >= 1

    def test_second_ptr_hop_keeps_rejecting(self):
        # BOUNDARY: the row admits ONE marked inner hop, so a chain whose
        # intermediate is ALSO a Ptr field stays on the AST path.
        src = _PTR_CHAIN.replace("class S:\n    a: A\n"
                                 "    def __init__(self, f: Int32):\n"
                                 "        self.a = A(f)\n",
                                 "class S:\n    a: Ptr[A]\n"
                                 "    def __init__(self, a: Ptr[A]):\n"
                                 "        self.a = a\n")
        src = src.replace("    s = S(7)\n", "    inner = A(7)\n    s = S(inner)\n")
        assert _fn(_lower_ctx(src), "via_name") is None

    def test_user_deref_intermediate_keeps_rejecting(self):
        # BOUNDARY: a USER `__deref__` proxy mid-chain
        # (`this->r.__deref__().q.flag`) is its own family -- the row keys on
        # the Ptr-valued receiver predicate, which a proxy record cannot pass.
        src = (
            "from tpy import Int32, auto_readonly\n"
            "class Q:\n"
            "    flag: Int32\n"
            "    def __init__(self, f: Int32):\n        self.flag = f\n"
            "class A:\n"
            "    q: Q\n"
            "    def __init__(self, f: Int32):\n        self.q = Q(f)\n"
            "class Ref:\n"
            "    _target: A\n"
            "    def __init__(self, target: A):\n        self._target = target\n"
            "    @auto_readonly\n"
            "    def __deref__(self) -> A:\n        return self._target\n"
            "def read(r: Ref) -> Int32:\n    return r.q.flag\n"
        )
        assert _fn(_lower_ctx(src), "read") is None


class TestFieldOverCallRead:
    """Value field reads off an F1-record-returning call receiver
    (field.call_recv): the bare postfix member over the call render --
    `make(10, 20).x` (free call rvalue), `h.boxed.get().x` (method borrow
    return), ctor receivers. The inner call lowers through its own arms, so
    an unsupported call shape still falls the body back."""

    SRC = (
        "from tpy import Int32, Own\n"
        "class Point:\n"
        "    x: Int32\n"
        "    y: Int32\n"
        "    def __init__(self, x: Int32, y: Int32):\n"
        "        self.x = x\n        self.y = y\n"
        "class Holder:\n"
        "    p: Point\n"
        "    def __init__(self, p: Own[Point]):\n        self.p = p\n"
        "    def get(self) -> Point:\n        return self.p\n"
        "def make(x: Int32, y: Int32) -> Own[Point]:\n"
        "    return Point(x, y)\n"
        "def free_call_recv() -> Int32:\n"
        "    return make(10, 20).x\n"
        "def ctor_recv() -> Int32:\n"
        "    return Point(1, 2).y\n"
        "def method_recv(h: Holder) -> Int32:\n"
        "    return h.get().x\n"
        "def main() -> None:\n"
        "    print(free_call_recv(), ctor_recv())\n"
        "    print(method_recv(Holder(Point(3, 4))))\n"
        "main()\n"
    )

    def _emit(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry,
            options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    def test_routed_and_witnessed(self):
        thir, witnessed = _lower_ctx_witnessed(self.SRC)
        for name in ("free_call_recv", "ctor_recv", "method_recv"):
            assert _fn(thir, name) is not None, name
        assert witnessed.get("field.call_recv", 0) >= 3

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_emit_postfix_member(self):
        cpp = self._emit(self.SRC, thir=True)
        assert "return make(10, 20).x;" in cpp
        assert "return h.get().x;" in cpp

    def test_string_field_over_call_recv_routes(self):
        # A `String` field is inside the resolved str slice (it renders
        # `std::string` like an owned `str`), so the read off a call receiver
        # takes the same bare member render the family's other members do.
        src = (
            "from tpy import Own, String\n"
            "class P:\n"
            "    s: String\n"
            "    def __init__(self):\n        self.s = String(\"a\")\n"
            "def make() -> Own[P]:\n"
            "    return P()\n"
            "def f() -> None:\n"
            "    print(make().s)\n"
            "f()\n"
        )
        thir, witnessed = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert witnessed.get("field.call_recv", 0) >= 1
        assert "std::cout << make().s" in self._emit(src, thir=True)
        assert self._emit(src, thir=True) == self._emit(src, thir=False)


class TestFieldOverTemplateCallRead:
    """Field reads off TEMPLATE/builtin-rewrite record-rvalue call
    receivers: the @cpp_template expansion and the abs()->__abs__()
    builtin rewrite both render bare under the postfix member
    (`(t).__abs__().v` -- the RECEIVER admission on the template/native
    record-rvalue rows). A cpp-ref-returning (borrow) callee under a
    field keeps the whole-body fallback (the REF_ALIAS value-position
    design stop)."""

    _TEMP = (
        "from tpy import Int32, ValueType\n"
        "class Temp(ValueType):\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32):\n"
        "        self.v = v\n"
        "    def __abs__(self) -> Temp:\n"
        "        return Temp(-self.v if self.v < 0 else self.v)\n"
    )

    def test_abs_receiver_routes(self):
        src = (self._TEMP
               + "def probe() -> Int32:\n"
               + "    return abs(Temp(-7)).v\n"
               + "print(probe())\n")
        thir, witnessed = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is not None
        assert witnessed.get("call.template_record_rvalue", 0) >= 1
        compiler, modules = _compile(src)
        entry = _entry(modules)
        outs = [compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=t)) for t in (True,
                                                                     False)]
        assert outs[0] == outs[1]
        assert "(Temp(-7)).__abs__().v" in outs[0][1]

    def test_borrow_call_receiver_routes(self):
        # A borrow-returning free call under a field read composes bare via
        # the dedicated field-recv flag (`pick(a, b).x` -- transient,
        # nothing binds; decl binds keep the REF_ALIAS frontier).
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n"
            "        self.x = x\n"
            "def pick(a: P, b: P) -> P:\n"
            "    return a\n"
            "def probe(a: P, b: P) -> Int32:\n"
            "    return pick(a, b).x\n"
            "print(probe(P(1), P(2)))\n")
        thir, wit = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is not None
        assert wit.get("call.field_recv_borrow_ret", 0) >= 1


class TestPtrChainNoneSubject:
    """A raw `Ptr[T]` field at the END of a field chain as an `is [not]
    None` subject (`self.inner.node is not None` -> `this->inner.node !=
    nullptr`) and its checked read (`deref_check(this->inner.node).value`),
    via `_ptr_value_none_field` / `_ptr_value_field_recv_ok`'s
    `_chained_field_read_ok` legs."""

    _SRC = (
        "from tpy import Int32, Ptr\n"
        "class Node:\n"
        "    value: Int32\n"
        "    def __init__(self, value: Int32) -> None:\n"
        "        self.value = value\n"
        "class Container:\n"
        "    node: Ptr[Node]\n"
        "    def __init__(self, node: Ptr[Node]) -> None:\n"
        "        self.node = node\n"
        "    def mutate(self) -> None:\n"
        "        pass\n")

    def test_depth_two_chain_subject_and_read(self):
        # The corpus shape: narrowing invalidated by the mutate() call, so
        # the read keeps its deref_check.
        src = (self._SRC
               + "class Wrapper:\n"
               + "    inner: Container\n"
               + "    def __init__(self, inner: Container) -> None:\n"
               + "        self.inner = inner\n"
               + "    def read(self) -> Int32:\n"
               + "        if self.inner.node is not None:\n"
               + "            self.inner.mutate()\n"
               + "            return self.inner.node.value\n"
               + "        return -1\n"
               + "def go() -> None:\n"
               + "    n = Node(7)\n"
               + "    p: Ptr[Node] = n\n"
               + "    print(Wrapper(Container(p)).read())\n"
               + "go()\n")
        _assert_routes_byte_identical(src)

    def test_depth_three_chain_subject(self):
        src = (self._SRC
               + "class Wrapper:\n"
               + "    inner: Container\n"
               + "    def __init__(self, inner: Container) -> None:\n"
               + "        self.inner = inner\n"
               + "class Outer:\n"
               + "    w: Wrapper\n"
               + "    def __init__(self, w: Wrapper) -> None:\n"
               + "        self.w = w\n"
               + "    def read(self) -> Int32:\n"
               + "        if self.w.inner.node is not None:\n"
               + "            return self.w.inner.node.value\n"
               + "        return -1\n"
               + "def go() -> None:\n"
               + "    n = Node(7)\n"
               + "    p: Ptr[Node] = n\n"
               + "    print(Outer(Wrapper(Container(p))).read())\n"
               + "go()\n")
        _assert_routes_byte_identical(src)

    def test_valuetype_inner_chain_routes(self):
        # A chain whose INNER hop is a ValueType record admits through the
        # pre-existing value-field arms, not `_chained_field_read_ok`
        # (F1-keyed) -- a regression guard that the widening did not
        # disturb that route. (This class deliberately has no reject
        # boundary pin: the probed adjacent shapes all route correctly
        # via other arms, so there is no adjacent must-reject shape.)
        src = (
            "from tpy import Int32, Ptr, ValueType\n"
            "class Node:\n"
            "    value: Int32\n"
            "    def __init__(self, value: Int32) -> None:\n"
            "        self.value = value\n"
            "class VInner(ValueType):\n"
            "    node: Ptr[Node]\n"
            "    def __init__(self, node: Ptr[Node]) -> None:\n"
            "        self.node = node\n"
            "class Holder:\n"
            "    vinner: VInner\n"
            "    def __init__(self, vinner: VInner) -> None:\n"
            "        self.vinner = vinner\n"
            "    def read(self) -> Int32:\n"
            "        if self.vinner.node is not None:\n"
            "            return self.vinner.node.value\n"
            "        return -1\n"
            "def go() -> None:\n"
            "    n = Node(3)\n"
            "    p: Ptr[Node] = n\n"
            "    print(Holder(VInner(p)).read())\n"
            "go()\n")
        _assert_routes_byte_identical(src)
