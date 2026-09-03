"""Flat-tail wave 1 pins: the VALUE-Array call-result/arg rows and the
container-literal-at-Optional-slot temp+lift. Corpus witnesses:
generics/generic_int_param_array_subst, tplib/requests_cookies_send."""

from .testutil import (
    _assert_rejects_at, _compile, _entry, _lower_ctx, _fn, _lower_ctx_witnessed, _PRELUDE,
                      _reject_tally)
from ..codegen_cpp import CodeGenOptions


def _cpp(src: str):
    compiler, modules = _compile(src)
    _, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False))
    return compiler, cpp


class TestValueArrayCallRows:
    SRC = (_PRELUDE
           + "from tpy import Array\n"
           + "class Box:\n"
           + "    data: Array[Int32, 3]\n"
           + "    def __init__(self) -> None:\n"
           + "        self.data = [1, 2, 3]\n"
           + "    def get_data(self) -> Array[Int32, 3]:\n"
           + "        return self.data\n"
           + "def use(arr: Array[Int32, 3]) -> Int32:\n"
           + "    return arr[0]\n"
           + "def main() -> None:\n"
           + "    b = Box()\n"
           + "    print(use(b.get_data()))\n"
           + "main()\n")

    def test_routes_byte_identical(self):
        thir, witnesses = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert witnesses.get("method.array_value_ret", 0) >= 1
        compiler, out = _cpp(self.SRC)
        _, ast_out = _cpp(self.SRC)
        assert out == ast_out
        assert "use(b.get_data())" in out


class TestOptionalContainerLiteralArg:
    SRC = (_PRELUDE
           + "def send(cookies: dict[str, str] | None) -> Int32:\n"
           + "    if cookies is None:\n        return 0\n"
           + "    return len(cookies)\n"
           + "def main() -> None:\n"
           + "    print(send({\"sid\": \"abc\"}))\n"
           + "    print(send(None))\n"
           + "main()\n")

    def test_temp_lift_routes_byte_identical(self):
        thir, witnesses = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert witnesses.get("optptr.container_temp", 0) >= 1
        compiler, out = _cpp(self.SRC)
        _, ast_out = _cpp(self.SRC)
        assert out == ast_out
        assert "send(&(__tmp_1))" in out

    def test_list_literal_flavor_spells_temp(self):
        # A bare LIST literal's sema type is the Array-inferred flavor
        # (Array[str, 1]) -- the face kind-matches against the slot and
        # the temp init self-spells like the AST's.
        src = (_PRELUDE
               + "def send(names: list[str] | None) -> Int32:\n"
               + "    if names is None:\n        return 0\n"
               + "    return len(names)\n"
               + "def main() -> None:\n"
               + "    print(send([\"a\"]))\n"
               + "    print(send(None))\n"
               + "main()\n")
        thir, witnesses = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert witnesses.get("optptr.container_temp", 0) >= 1
        compiler, out = _cpp(src)
        _, ast_out = _cpp(src)
        assert out == ast_out
        assert ('std::vector<std::string> __tmp_1 = '
                'std::vector<std::string>{"a"};' in out)

    def test_set_literal_flavor_routes(self):
        # The set sibling of the kind-match leg.
        src = (_PRELUDE
               + "def send(nums: set[Int32] | None) -> Int32:\n"
               + "    if nums is None:\n        return 0\n"
               + "    return len(nums)\n"
               + "def main() -> None:\n"
               + "    print(send({1, 2}))\n"
               + "    print(send(None))\n"
               + "main()\n")
        thir, witnesses = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert witnesses.get("optptr.container_temp", 0) >= 1
        compiler, out = _cpp(src)
        _, ast_out = _cpp(src)
        assert out == ast_out


class TestValueOptArgRows:
    """The value-opt arg rows: a value-opt-returning CALL rvalue binds a
    SAME value-opt slot bare (call.optval_ret_pass), and `copy(record)`
    at an Own-OPTIONAL slot takes the copy-construct rvalue through the
    optional's converting ctor. Corpus witnesses:
    none_safety/optional_return_narrowed,
    auto_move/warn_unnecessary_copy_optional."""

    def test_optval_ret_pass_routes(self):
        src = (_PRELUDE
               + "from typing import Optional\n"
               + "def unwrap_or(x: Optional[Int32], fb: Int32) -> Int32:\n"
               + "    if x is not None:\n        return x\n"
               + "    return fb\n"
               + "def pick(n: Int32) -> Optional[Int32]:\n"
               + "    if n > 0:\n        return n\n"
               + "    return None\n"
               + "def main() -> None:\n"
               + "    print(unwrap_or(pick(3), 0))\n"
               + "    print(unwrap_or(None, 42))\n"
               + "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert w.get("call.optval_ret_pass", 0) >= 1
        compiler, out = _cpp(src)
        _, ast_out = _cpp(src)
        assert out == ast_out
        assert "unwrap_or(pick(3), 0)" in out

    def test_optview_ret_stays_ast(self):
        # BOUNDARY: the view flavor (Optional[str] return at an
        # Optional[str] slot) keeps its shim machinery -- _value_opt_scalar
        # excludes views.
        src = (_PRELUDE
               + "from typing import Optional\n"
               + "def unwrap_or(x: Optional[str], fb: str) -> str:\n"
               + "    if x is not None:\n        return x\n"
               + "    return fb\n"
               + "def pick(n: Int32) -> Optional[str]:\n"
               + "    if n > 0:\n        return \"y\"\n"
               + "    return None\n"
               + "def main() -> None:\n"
               + "    print(unwrap_or(pick(3), \"z\"))\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:call.arg_shape.optional")

    def test_copy_at_own_opt_slot_routes(self):
        src = (_PRELUDE
               + "from tpy import Own, copy\n"
               + "class Box:\n"
               + "    value: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.value = v\n"
               + "def consume(b: Own[Box] | None) -> Int32:\n"
               + "    if b is None:\n        return -1\n"
               + "    return b.value\n"
               + "def main() -> None:\n"
               + "    b = Box(42)\n"
               + "    print(consume(copy(b)))\n"
               + "    print(b.value)\n"
               + "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert w.get("own.record_copy", 0) >= 1
        compiler, out = _cpp(src)
        _, ast_out = _cpp(src)
        assert out == ast_out
        assert "consume(Box(b))" in out


class TestGetitemBorrowRecordReceiver:
    """The start_server receiver chain (sync twin): a user-__getitem__
    subscript returning a readonly BORROW record feeds a method call and a
    value-tuple index -- `srv.sockets[0].getsockname()[1]` renders the raw
    operator[] with `.` access. Corpus witnesses: the async start_server
    trio."""

    SRC = (_PRELUDE
           + "from tpy import Own, auto_readonly, nocopy\n"
           + "@nocopy\n"
           + "class Sock:\n"
           + "    port: Int32\n"
           + "    def __init__(self, port: Int32) -> None:\n"
           + "        self.port = port\n"
           + "    @auto_readonly\n"
           + "    def getsockname(self) -> tuple[str, Int32]:\n"
           + "        return (\"127.0.0.1\", self.port)\n"
           + "@nocopy\n"
           + "class Socks:\n"
           + "    _s: Sock\n"
           + "    def __init__(self, s: Own[Sock]) -> None:\n"
           + "        self._s = s\n"
           + "    @auto_readonly\n"
           + "    def __getitem__(self, i: Int32) -> auto_readonly[Sock]:\n"
           + "        return self._s\n"
           + "@nocopy\n"
           + "class Server:\n"
           + "    sockets: Socks\n"
           + "    def __init__(self, s: Own[Socks]) -> None:\n"
           + "        self.sockets = s\n"
           + "def main() -> None:\n"
           + "    srv = Server(Socks(Sock(8080)))\n"
           + "    port = srv.sockets[0].getsockname()[1]\n"
           + "    print(port)\n"
           + "main()\n")

    def test_chain_routes_byte_identical(self):
        thir, _witnesses = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        compiler, out = _cpp(self.SRC)
        _, ast_out = _cpp(self.SRC)
        assert out == ast_out
        assert "std::get<1>(srv.sockets[0].getsockname())" in out

    def test_reseated_field_receiver_also_routes(self):
        # The FIELD-receiver flavor tolerates a reseated (pointer-local)
        # base: `srv` rebinds to `Server*` and the chain still renders
        # byte-identically (the getitem recheck guards NAME receivers only).
        src = (self.SRC
               .replace("    srv = Server(Socks(Sock(8080)))\n",
                        "    srv = Server(Socks(Sock(8080)))\n"
                        "    srv = Server(Socks(Sock(9090)))\n"))
        thir, _w = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        compiler, out = _cpp(src)
        _, ast_out = _cpp(src)
        assert out == ast_out

    def test_pointer_local_name_obj_stays_ast(self):
        # BOUNDARY: the subscript's OBJ as a pointer-local NAME (`socks`
        # reseated -> `Socks* socks`; `socks[0]` would render `(*p)[...]`)
        # keeps rejecting at the getitem arm's idx/recv recheck.
        src = (self.SRC
               .replace("def main() -> None:\n"
                        "    srv = Server(Socks(Sock(8080)))\n"
                        "    port = srv.sockets[0].getsockname()[1]\n",
                        "def main() -> None:\n"
                        "    socks = Socks(Sock(8080))\n"
                        "    socks = Socks(Sock(9090))\n"
                        "    port = socks[0].getsockname()[1]\n"))
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.method_call:method.recv.subscript")


class TestPtrUnionDeclRows:
    """Flat-tail wave 3: member-typed literal inits, the container-member
    UNION_ADDR name bind, and the WIDE same-union bare-name arg pass.
    Corpus witnesses: match/match_union_primitive,
    union/isinstance_narrow_set."""

    SRC = (_PRELUDE
           + "class A:\n"
           + "    x: Int32\n"
           + "    def __init__(self, x: Int32) -> None:\n"
           + "        self.x = x\n"
           + "def f(v: Int32 | set[Int32]) -> None:\n"
           + "    if isinstance(v, set):\n        print(len(v))\n"
           + "def main() -> None:\n"
           + "    s: set[Int32] = {1, 2, 3}\n"
           + "    v: Int32 | set[Int32] = s\n"
           + "    f(v)\n"
           + "    w: Int32 | set[Int32] = 7\n"
           + "    f(w)\n"
           + "    u: str | A = \"hello\"\n"
           + "    if isinstance(u, str):\n        print(u)\n"
           + "main()\n")

    def test_rows_route_byte_identical(self):
        thir, _w = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        compiler, out = _cpp(self.SRC)
        _, ast_out = _cpp(self.SRC)
        assert out == ast_out
        assert "v{&(s)}" in out
        assert "__slot_1 = 7;" in out
        # The WIDE same-union bare-name arg pass renders the names bare.
        assert "f(v);" in out
        assert "f(w);" in out

    def test_own_union_slot_arg_stays_ast(self):
        # BOUNDARY: an `Own[union]` slot auto-moves on the AST path; the
        # pass-through gate is non-Own only.
        src = (_PRELUDE
               + "from tpy import Own\n"
               + "def h(v: Own[Int32 | set[Int32]]) -> None:\n"
               + "    if isinstance(v, set):\n        print(len(v))\n"
               + "def main() -> None:\n"
               + "    s: set[Int32] = {1, 2, 3}\n"
               + "    v: Int32 | set[Int32] = s\n"
               + "    h(v)\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.arg_shape.own_union")

    def test_ambiguous_literal_member_stays_ast(self):
        # BOUNDARY: two int-family members -- the converting ctor would be
        # ambiguous, so the single-member-of-family key rejects.
        src = (_PRELUDE
               + "from tpy import Int64\n"
               + "class A:\n"
               + "    x: Int32\n"
               + "    def __init__(self, x: Int32) -> None:\n"
               + "        self.x = x\n"
               + "def main() -> None:\n"
               + "    u: Int32 | Int64 | A = 5\n"
               + "    if isinstance(u, Int32):\n        print(u)\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.ptr_union_source")


class TestOwnRecordMethodRvalueConformer:
    """The reactor pair's row: an Own[record]-returning METHOD rvalue at an
    Own[@dynamic P] slot takes the make_adapter wrap (sync twin via the
    Cancellable-conforming record). Corpus witnesses:
    async/reactor_{accept_cancel_timeout,cancel_parked_io}."""

    def test_method_rvalue_conformer_routes(self):
        src = (_PRELUDE
               + "from tpy import Own\n"
               + "from tpy import dynamic\n"
               + "from typing import Protocol\n"
               + "@dynamic\n"
               + "class P(Protocol):\n"
               + "    def ping(self) -> Int32: ...\n"
               + "class Impl:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n"
               + "    def ping(self) -> Int32:\n        return self.n\n"
               + "class Factory:\n"
               + "    def __init__(self) -> None:\n        pass\n"
               + "    def make(self, n: Int32) -> Own[Impl]:\n"
               + "        return Impl(n)\n"
               + "def use(p: Own[P]) -> Int32:\n"
               + "    return p.ping()\n"
               + "def main() -> None:\n"
               + "    fac = Factory()\n"
               + "    print(use(fac.make(7)))\n"
               + "main()\n")
        thir, _w = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        compiler, out = _cpp(src)
        _, ast_out = _cpp(src)
        assert out == ast_out
        assert "::tpy::make_adapter<P>(fac.make(7))" in out

    def test_borrow_method_source_stays_ast(self):
        # BOUNDARY: a method returning a BORROW (field return, not an
        # rvalue source) at the Own[@dynamic P] slot must keep rejecting
        # -- only record prvalues take the conformer wrap.
        src = (_PRELUDE
               + "from tpy import Own\n"
               + "from tpy import dynamic\n"
               + "from typing import Protocol\n"
               + "@dynamic\n"
               + "class P(Protocol):\n"
               + "    def ping(self) -> Int32: ...\n"
               + "class Impl:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n"
               + "    def ping(self) -> Int32:\n        return self.n\n"
               + "class Factory:\n"
               + "    cached: Impl\n"
               + "    def __init__(self) -> None:\n"
               + "        self.cached = Impl(3)\n"
               + "    def get(self) -> Impl:\n"
               + "        return self.cached\n"
               + "def use(p: Own[P]) -> Int32:\n"
               + "    return p.ping()\n"
               + "def main() -> None:\n"
               + "    fac = Factory()\n"
               + "    print(use(fac.get()))\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:call.arg_shape.own_protocol.dyn")
