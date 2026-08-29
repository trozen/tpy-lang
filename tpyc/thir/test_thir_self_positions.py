"""Every consumer of a lowered `self`, in all three receiver spellings.

`THIRSelf.deref` is set where the node is BUILT, from whether the receiver
is a pointer -- a plain method's `this` is, a simple generator's `(*this)`
and a resumable frame's `__self` are not. So a VALUE position renders
`(*this)` without knowing anything about receivers, and the consumers that
need the bare pointer clear it instead. The units below hold both halves
down: one per value position (a position that dropped the deref emitted a
bare `Record*` into a value slot -- valid C++ often enough to be silent),
one per pointer position (`(*this)->x` and `&(this)` are ill-formed).

Only ONE of the two clearing classes has a structural rule behind it. The
arrow member access does: the render and the guard both read
`receiver_through_pointer`, so a consumer that picks the wrong one fails
loudly at the arrow site. The raw POINTER-SLOT class does NOT -- a
borrow-tuple element binding the receiver pointer itself (`{1, this}`)
clears through a separate helper, and those nodes carry only `addr_of`,
which cannot tell a pointer passthrough from a value slot: a value-typed
record legitimately sits deref'd at a bare value element, so a rule keyed
on that flag would reject legal code. A sound guard needs a per-element
pointer-slot channel the nodes do not have. Until then the slot rows are
held by the units below and by nothing else.

Neither class is unforgettable. Clearing the deref remains a
CONSTRUCTION-SITE obligation: most field-access and method-call
constructions never reach the positioning helper (they are safe by their
own receiver-shape guards, not by the rule), and the unbound-base field
access hardcodes its receiver's non-deref rather than deriving it.
"""

import pytest

from ..codegen_cpp.context import CodeGenOptions
from .dump import _function_lines
from .nodes import (
    Form, THIRFieldAccess, THIRFunction, THIRFunctionLayout, THIRMethodCall,
    THIRReturn, THIRSelf,
)
from ..typesys import INT32, VoidType
from .testutil import (
    _assert_routes_byte_identical, _compile, _entry, _lower_ctx_witnessed,
)
from .validate import THIRValidationError, validate_function


def _gen(source):
    compiler, modules = _compile(source)
    hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return hpp, cpp


_V = (
    "from tpy import Int32, Own\n"
    "class V:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
)

_MAIN = (
    "def main() -> None:\n"
    "    a = V(3)\n"
    "    b = V(5)\n"
    "    print(a.probe(b))\n"
    "main()\n"
)


def _plain(body: str, extra: str = "") -> str:
    """A `V` method named `probe` holding the position under test."""
    return _V + body + extra + _MAIN


class TestPlainMethodValuePositions:
    """`this` is a pointer, so each of these renders `(*this)`."""

    def test_unary_operand(self):
        # The reported wrong-code shape: `-(this)` has no operator- overload.
        src = _plain(
            "    def __neg__(self) -> Own['V']:\n"
            "        return V(-self.n)\n"
            "    def probe(self, o: 'V') -> Int32:\n"
            "        f = -self\n"
            "        return f.n\n")
        _assert_routes_byte_identical(src)
        hpp, _cpp = _gen(src)
        assert "-((*this))" in hpp

    def test_compare_operand(self):
        src = _plain(
            "    def __lt__(self, o: 'V') -> bool:\n"
            "        return self.n < o.n\n"
            "    def probe(self, o: 'V') -> Int32:\n"
            "        if self < o:\n"
            "            return 1\n"
            "        return 0\n")
        _assert_routes_byte_identical(src)
        assert "(((*this)) < (o))" in _gen(src)[0]

    def test_ternary_arm(self):
        src = _plain(
            "    def probe(self, o: 'V') -> Int32:\n"
            "        c = self if self.n > o.n else o\n"
            "        return c.n\n")
        _assert_routes_byte_identical(src)
        assert "((*this)) : (o)" in _gen(src)[0]

    def test_call_argument(self):
        src = _plain(
            "    def probe(self, o: 'V') -> Int32:\n"
            "        return take(self)\n"
            "def take(v: V) -> Int32:\n"
            "    return v.n\n")
        _assert_routes_byte_identical(src)
        assert "take((*this))" in _gen(src)[0]

    def test_union_argument_lifts_over_the_deref(self):
        # `&((*this))` -- the lift takes the address of the pointee, so it
        # must not add a deref of its own on top of the receiver read.
        src = _plain(
            "    def probe(self, o: 'V') -> Int32:\n"
            "        return take(self)\n"
            "class W:\n"
            "    m: Int32\n"
            "    def __init__(self, m: Int32) -> None:\n"
            "        self.m = m\n"
            "def take(u: V | W) -> Int32:\n"
            "    return 0\n")
        _assert_routes_byte_identical(src)
        hpp, _cpp = _gen(src)
        assert "&((*this))" in hpp
        assert "&((*(*this)))" not in hpp

    def test_print_raw(self):
        src = _plain(
            "    def probe(self, o: 'V') -> Int32:\n"
            "        print(self)\n"
            "        return 0\n")
        _assert_routes_byte_identical(src)
        assert "(*this)" in _gen(src)[0]

    def test_record_return(self):
        src = _plain(
            "    def me(self) -> 'V':\n"
            "        return self\n"
            "    def probe(self, o: 'V') -> Int32:\n"
            "        return self.me().n\n")
        _assert_routes_byte_identical(src)
        assert "return (*this);" in _gen(src)[0]

    def test_subscript_receiver(self):
        src = (
            "from tpy import Int32\n"
            "class V:\n"
            "    xs: list[Int32]\n"
            "    def __init__(self) -> None:\n"
            "        self.xs = [1, 2, 3]\n"
            "    def __getitem__(self, i: Int32) -> Int32:\n"
            "        return self.xs[i]\n"
            "    def __setitem__(self, i: Int32, v: Int32) -> None:\n"
            "        self.xs[i] = v\n"
            "    def probe(self) -> Int32:\n"
            "        self[1] = 9\n"
            "        return self[1]\n"
            "def main() -> None:\n"
            "    print(V().probe())\n"
            "main()\n")
        _assert_routes_byte_identical(src)
        hpp, _cpp = _gen(src)
        assert "(*this)[1]" in hpp

    def test_iterable(self):
        src = (
            "from tpy import Int32\n"
            "class V:\n"
            "    xs: list[Int32]\n"
            "    i: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.xs = [1, 2]\n"
            "        self.i = 0\n"
            "    def __iter__(self) -> 'V':\n"
            "        self.i = 0\n"
            "        return self\n"
            "    def __next__(self) -> Int32:\n"
            "        if self.i >= len(self.xs):\n"
            "            raise StopIteration()\n"
            "        v = self.xs[self.i]\n"
            "        self.i += 1\n"
            "        return v\n"
            "    def probe(self) -> Int32:\n"
            "        t = 0\n"
            "        for x in self:\n"
            "            t += x\n"
            "        return t\n"
            "def main() -> None:\n"
            "    print(V().probe())\n"
            "main()\n")
        _assert_routes_byte_identical(src)
        assert "= (*this);" in _gen(src)[0]

    def test_truthiness_bool(self):
        src = _plain(
            "    def __bool__(self) -> bool:\n"
            "        return self.n != 0\n"
            "    def probe(self, o: 'V') -> Int32:\n"
            "        if self:\n"
            "            return 1\n"
            "        return 0\n")
        _assert_routes_byte_identical(src)
        hpp, _cpp = _gen(src)
        assert "(*this)" in hpp
        assert "(*(*this))" not in hpp

    def test_truthiness_len(self):
        src = (
            "from tpy import Int32\n"
            "class V:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def __len__(self) -> Int32:\n"
            "        return self.n\n"
            "    def probe(self) -> Int32:\n"
            "        if self:\n"
            "            return 1\n"
            "        return 0\n"
            "def main() -> None:\n"
            "    print(V(0).probe())\n"
            "main()\n")
        _assert_routes_byte_identical(src)
        hpp, _cpp = _gen(src)
        assert "(*(*this))" not in hpp


class TestPlainMethodPointerPositions:
    """The positions that consume `this` AS a pointer -- the deref comes off."""

    def test_field_read_arrows(self):
        src = _plain(
            "    def probe(self, o: 'V') -> Int32:\n"
            "        return self.n\n")
        _assert_routes_byte_identical(src)
        hpp, _cpp = _gen(src)
        assert "return this->n;" in hpp
        assert "(*this)->" not in hpp

    def test_method_call_arrows(self):
        src = _plain(
            "    def half(self) -> Int32:\n"
            "        return self.n\n"
            "    def probe(self, o: 'V') -> Int32:\n"
            "        return self.half()\n")
        _assert_routes_byte_identical(src)
        hpp, _cpp = _gen(src)
        assert "return this->half();" in hpp

    def test_user_deref_field_chain_arrows_the_first_hop(self):
        # `this->__deref__().x`: the chain's first hop is a member access
        # through the receiver, so it arrows like a plain field read.
        src = (
            "from tpy import Int32\n"
            "class Inner:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "    def double(self) -> Int32:\n"
            "        return self.v * 2\n"
            "class Wrap:\n"
            "    inner: Inner\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.inner = Inner(v)\n"
            "    def __deref__(self) -> Inner:\n"
            "        return self.inner\n"
            "    def probe(self) -> Int32:\n"
            "        return self.v + self.double()\n"
            "def main() -> None:\n"
            "    print(Wrap(2).probe())\n"
            "main()\n")
        _assert_routes_byte_identical(src)
        hpp, _cpp = _gen(src)
        assert "this->__deref__().v" in hpp
        assert "this->__deref__().double_()" in hpp
        assert "(*this).__deref__()" not in hpp

    def test_borrow_tuple_element_passes_the_bare_pointer(self):
        # A `V*` tuple element binds the receiver pointer itself: `{1, this}`,
        # never `{1, &(this)}` (a `V**`) nor `{1, (*this)}` (a `V`).
        src = _plain(
            "    def probe(self, o: 'V') -> Int32:\n"
            "        t = (1, self)\n"
            "        return t[1].n\n")
        _assert_routes_byte_identical(src)
        hpp, _cpp = _gen(src)
        assert "{1, this}" in hpp

    def test_optional_pointer_tuple_element_passes_the_bare_pointer(self):
        # The pointer-repr `V | None` element is a SECOND raw-`V*` slot row,
        # built as its own node with its own clear -- the sibling above does
        # not cover it. The receiver binds bare there too.
        src = _plain(
            "    def probe(self, o: 'V') -> Int32:\n"
            "        t: tuple[Int32, V | None] = (1, self)\n"
            "        return t[0]\n")
        _assert_routes_byte_identical(src)
        _thir, witnessed = _lower_ctx_witnessed(src)
        assert witnessed.get("btuple.elem_optptr", 0) >= 1
        hpp, _cpp = _gen(src)
        assert "{1, this}" in hpp
        assert "&(this)" not in hpp


class TestGeneratorReceiverStaysBare:
    """A simple generator's receiver is spelled `(*this)` already: every
    position reads it bare, and a second deref would be `(*(*this))`."""

    SRC = (
        "from typing import Iterator\n"
        "from tpy import Int32\n"
        "class V:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def half(self) -> Int32:\n"
        "        return self.n\n"
        "    def g_field(self) -> Iterator[Int32]:\n"
        "        i = 0\n"
        "        while i < 2:\n"
        "            yield self.n\n"
        "            i += 1\n"
        "    def g_method(self) -> Iterator[Int32]:\n"
        "        i = 0\n"
        "        while i < 2:\n"
        "            yield self.half()\n"
        "            i += 1\n"
        "    def g_arg(self) -> Iterator[Int32]:\n"
        "        i = 0\n"
        "        while i < 2:\n"
        "            yield take(self)\n"
        "            i += 1\n"
        "    def g_print(self) -> Iterator[Int32]:\n"
        "        i = 0\n"
        "        while i < 2:\n"
        "            print(self)\n"
        "            yield i\n"
        "            i += 1\n"
        "def take(v: V) -> Int32:\n"
        "    return v.n\n"
        "def main() -> None:\n"
        "    a = V(3)\n"
        "    for x in a.g_field():\n"
        "        print(x)\n"
        "    for x in a.g_method():\n"
        "        print(x)\n"
        "    for x in a.g_arg():\n"
        "        print(x)\n"
        "    for x in a.g_print():\n"
        "        print(x)\n"
        "main()\n")

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_no_second_deref(self):
        hpp, cpp = _gen(self.SRC)
        assert "(*(*this))" not in hpp and "(*(*this))" not in cpp
        assert "(*this).n" in hpp


class TestResumableReceiverStaysBare:
    """A resumable frame captures the receiver as `Record& __self`."""

    SRC = (
        "import asyncio\n"
        "from tpy import Int32\n"
        "class V:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def half(self) -> Int32:\n"
        "        return self.n\n"
        "    async def step(self) -> Int32:\n"
        "        print(self)\n"
        "        await asyncio.sleep(0.0)\n"
        "        t = self.n\n"
        "        t += self.half()\n"
        "        t += take(self)\n"
        "        return t\n"
        "def take(v: V) -> Int32:\n"
        "    return v.n\n"
        "async def run() -> None:\n"
        "    a = V(4)\n"
        "    print(await a.step())\n"
        "def main() -> None:\n"
        "    asyncio.run(run())\n"
        "main()\n")

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_frame_receiver_reads_bare(self):
        hpp, cpp = _gen(self.SRC)
        both = hpp + cpp
        assert "(*__self)" not in both
        assert "__self.n" in both
        assert "__self.half()" in both
        assert "take(__self)" in both


class TestSelfDerefStructuralRule:
    """The guard behind the ARROW consumer class -- the only one of the two
    that has one. It does not remove the obligation to clear the deref where
    a node is built; it turns forgetting at an arrow site into a loud failure
    instead of a silent one."""

    def _fn(self, node) -> THIRFunction:
        return THIRFunction(
            name="w", params=(), return_type=VoidType(),
            body=(THIRReturn(value=node),), layout=THIRFunctionLayout())

    def _self(self, deref: bool) -> THIRSelf:
        return THIRSelf(result_type=INT32, form=Form.BORROW,
                        deref=deref)

    def test_arrow_field_over_bare_receiver_passes(self):
        validate_function(self._fn(THIRFieldAccess(
            result_type=INT32, receiver=self._self(False),
            field_cpp="n", is_arrow=True)))

    def test_arrow_field_over_dereferenced_receiver_fails(self):
        with pytest.raises(THIRValidationError, match="arrow member access"):
            validate_function(self._fn(THIRFieldAccess(
                result_type=INT32, receiver=self._self(True),
                field_cpp="n", is_arrow=True)))

    def test_arrow_method_over_dereferenced_receiver_fails(self):
        with pytest.raises(THIRValidationError, match="arrow member access"):
            validate_function(self._fn(THIRMethodCall(
                result_type=INT32, receiver=self._self(True),
                method_cpp="m", args=(), is_arrow=True)))

    def test_value_slot_receiver_keeps_its_deref(self):
        # The cpp_template and native-free-function arms interpolate the
        # receiver into an ARGUMENT and never read `is_arrow`, so a
        # dereferenced receiver is the correct shape there.
        for kw in ({"cpp_template": "::tpy::f({0})"},
                   {"native_function_name": "tpy::f"}):
            node = THIRMethodCall(
                result_type=INT32, receiver=self._self(True),
                method_cpp="m", args=(), is_arrow=True, **kw)
            assert not node.receiver_through_pointer
            validate_function(self._fn(node))

    def test_dump_distinguishes_the_two_receiver_reads(self):
        bare = "\n".join(_function_lines(self._fn(self._self(False))))
        derefd = "\n".join(_function_lines(self._fn(self._self(True))))
        assert "%self" in bare and "*%self" not in bare
        assert "*%self" in derefd
