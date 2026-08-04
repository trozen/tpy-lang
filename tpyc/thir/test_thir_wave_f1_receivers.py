"""The receiver-row re-land wave (the F1 fence re-scope's promised yield):
four rows at the method-receiver / wrapper-union seams.

Row 1 -- field-CHAIN method receiver (`h.c.item.add(x)`), receiver-position
scope ONLY (the shared `_field_receiver_ok` widening was reverted once for
tripping read/write-sink pins), plus its arg-position twin at structural
protocol slots (`len(h.c.item)`).
Row 2 -- tuple-element F1-RECORD subscript receiver (`t[0].get()`): a
borrow-form tuple param's element is a bare `T*` (`std::get<0>(t)->get()`),
a storage tuple local's a value (`std::get<0>(pair).get()`).
Row 3 -- recursive-union WRAPPER borrow method return at BORROW_BIND sinks
(`show(v.inner.get())`).
Row 4 -- member-CTOR rvalue into a wrapper-union arg slot
(`eval_expr(Lit(42))` -> `Expr __tmp_N = Lit(...);`).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
    _fn,
)


def _thir_fallbacks(source, extra_lib_dirs=None):
    """Emit through THIR and return the fallback tally (reject pins)."""
    compiler, modules = _compile(source, extra_lib_dirs=extra_lib_dirs)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestChainFieldReceiver:
    SRC = (
        "from tpy import Int32, Own\n"
        "class Inner:\n"
        "    xs: list[Int32]\n"
        "    def __init__(self):\n        self.xs = []\n"
        "class Mid:\n"
        "    inner: Inner\n"
        "    def __init__(self, inner: Own[Inner]):\n        self.inner = inner\n"
        "def main():\n"
        "    m = Mid(Inner())\n"
        "    m.inner.xs.append(3)\n"
        "    print(len(m.inner.xs))\n"
        "main()\n"
    )

    def test_chain_receiver_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "m.inner.xs.push_back(3);" in cpp
        assert "::tpy::__len__(m.inner.xs)" in cpp

    def test_chain_receiver_witnessed(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert wit.get("method.recv.field_chain", 0) >= 1

    def test_nonf1_link_keeps_rejecting(self):
        # A chain whose middle link is NOT an F1 record (a generic record
        # over a LOCAL plain-alias union arg) stays on the AST path -- the
        # link walk is the fence.
        src = (
            "from tpy import Int32, StrView, Own\n"
            "type Num = Int32 | StrView\n"
            "class Holder2[T]:\n"
            "    tag: T\n"
            "    xs: list[Int32]\n"
            "    def __init__(self, tag: T):\n"
            "        self.tag = tag\n        self.xs = []\n"
            "class Outer3:\n"
            "    mid: Holder2[Num]\n"
            "    def __init__(self, mid: Own[Holder2[Num]]):\n"
            "        self.mid = mid\n"
            "def use(o: Outer3) -> None:\n"
            "    o.mid.xs.append(1)\n"
        )
        assert "body:expr.method_call" in _thir_fallbacks(src)


class TestTupleRecordElemReceiver:
    SRC = (
        "from tpy import Int32, readonly\n"
        "class Node:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32):\n        self.v = v\n"
        "    def get(self) -> Int32:\n        return self.v\n"
        "def both(t: tuple[Node, Node]) -> Int32:\n"
        "    return t[0].get() + t[1].get()\n"
        "def main():\n"
        "    pair: tuple[Node, Node] = (Node(1), Node(2))\n"
        "    print(both(pair))\n"
        "    print(pair[0].get())\n"
        "main()\n"
    )

    def test_borrow_and_storage_elem_receivers_route(self):
        # The one row serves both forms: the borrow-form param element is a
        # bare `T*` (`->`), the storage local element a value (`.`).
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::get<0>(t)->get()" in cpp
        assert "std::get<0>(pair).get()" in cpp

    def test_tuple_elem_receiver_witnessed(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "both") is not None
        assert wit.get("method.recv.tuple_record_elem", 0) >= 2

    def test_own_element_keeps_rejecting(self):
        # An Own element carries consuming semantics the row does not
        # mirror -- the body stays on the AST path.
        src = (
            "from tpy import Int32, Own\n"
            "class Node:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32):\n        self.v = v\n"
            "    def get(self) -> Int32:\n        return self.v\n"
            "def take(t: tuple[Own[Node], Int32]) -> Int32:\n"
            "    return t[0].get()\n"
        )
        assert "body:expr.method_call" in _thir_fallbacks(src)


_VALUE = (
    "from tplib import Box\n"
    "from tpy import Int32\n"
    "type Value = int | str | Neg\n"
    "class Neg:\n"
    "    inner: Box[Value]\n"
    "def show(v: Value) -> str:\n"
    "    if isinstance(v, Neg):\n"
    "        return \"-\" + show(v.inner.get())\n"
    "    elif isinstance(v, int):\n"
    "        return str(v)\n"
    "    else:\n"
    "        return v\n"
)


class TestWrapperUnionMethodReturn:
    def test_wrapper_ret_at_arg_sink_routes(self):
        # `show(v.inner.get())`: the accessor's `Value&` binds the
        # `const Value&` wrapper slot inline (method.ru_wrapper_ret +
        # arg.recursive_union_borrow_call together).
        src = _VALUE + (
            "def main() -> None:\n"
            "    print(show(42))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "show(__v.inner.get())" in cpp

    def test_wrapper_ret_at_storage_decl_routes(self):
        # The storage sibling: `w = n.inner.get()` lands the wrapper value
        # in its decl slot (the value-type storage escape).
        src = _VALUE + (
            "def peek(n: Neg) -> str:\n"
            "    w = n.inner.get()\n"
            "    return show(w)\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Value w = n.inner.get();" in cpp

    def test_wrapper_ctor_arg_temp_routes(self):
        # Row 4: the member-CTOR rvalue hoists the typed wrapper temp.
        src = (
            "from tplib import Box\n"
            "from tpy import Int32, Own\n"
            "type Expr = Lit | Pair\n"
            "class Lit:\n"
            "    value: int\n"
            "    def __init__(self, value: int) -> None:\n"
            "        self.value = value\n"
            "class Pair:\n"
            "    left: Box[Expr]\n"
            "    def __init__(self, left: Own[Box[Expr]]) -> None:\n"
            "        self.left = left\n"
            "def ev(e: Expr) -> int:\n"
            "    if isinstance(e, Lit):\n"
            "        return e.value\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(ev(Lit(42)))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Expr __tmp_1 = Lit(::tpy::BigInt(42));" in cpp
        assert "ev(__tmp_1)" in cpp

    def test_borrow_call_arg_composes_bare(self):
        # A BORROW-returning free call at the wrapper slot composes bare
        # (`show(pick(n))` -- the union-returns wave's
        # _ru_wrapper_borrow_call_arg widening; never the ctor-rvalue
        # temp). The CALLEE's own return still gates: a borrow-returning
        # METHOD-call source is outside the wrapper-borrow return slice
        # (name sources only), so `pick` keeps falling back.
        src = _VALUE + (
            "def pick(n: Neg) -> Value:\n"
            "    return n.inner.get()\n"
            "def use(n: Neg) -> str:\n"
            "    return show(pick(n))\n"
        )
        fell = _thir_fallbacks(src)
        assert any("return.wrapper_borrow_source" in k for k in fell), fell
        assert not any(k.startswith("body:expr.call") for k in fell), fell
