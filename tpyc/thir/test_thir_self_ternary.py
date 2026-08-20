"""The bare `self` arm of an F1-record ternary.

`self` is a POINTER in a plain method, and a ternary arm is a VALUE
position, so the arm owes gen_expr_deref's receiver deref (`((c) ?
((*this)) : (o))`) -- without it the C++ operands are `Acc*` and `Acc&`
and the program does not build. The shape ROUTED before the deref retag
landed and the corpus had no witness (the three `operators/op_*` cases
carrying it are `return`-position and reject upstream), so the byte-diff
was structurally blind to it.

Boundary units below hold the positions the retag must NOT reach: the
pointer-repr Optional / value-repr Optional / ptr-union ternary faces (all
resolved before the record branch), and a receiver spelled by a non-pointer
`self` (a generator's `(*this)`, a poly-narrowed `__self_ptr` alias) where
a second deref would be wrong.
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)


def _gen(source):
    """(compiler, hpp, cpp) from a THIR run -- for render + witness reads.
    The ROUTING claim always rides `_assert_routes_byte_identical`."""
    compiler, modules = _compile(source)
    hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return compiler, hpp, cpp


def _fallback(source):
    compiler, modules = _compile(source)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


_ACC = (
    "from tpy import Int32\n"
    "class Acc:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
)


class TestSelfThenArmDerefs:
    # The reported wrong-code shape: a `self` THEN arm at a record-ternary
    # decl. `Acc*` bound to `Acc&` does not compile.
    SRC = (
        _ACC +
        "    def pick(self, o: Acc) -> Acc:\n"
        "        c = self if self.n >= o.n else o\n"
        "        return c\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_deref_render_and_face(self):
        compiler, hpp, _cpp = _gen(self.SRC)
        assert "Acc& c = (((this->n >= o.n)) ? ((*this)) : (o));" in hpp
        assert "? (this) :" not in hpp
        assert compiler._thir_face_witnesses.get("ifexpr.record", 0) >= 1


class TestSelfElseArmDerefs:
    # The mirror position: `self` in the ELSE arm takes the same deref.
    SRC = (
        _ACC +
        "    def pick(self, o: Acc) -> Acc:\n"
        "        c = o if o.n >= self.n else self\n"
        "        return c\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_deref_render(self):
        _compiler, hpp, _cpp = _gen(self.SRC)
        assert "Acc& c = (((o.n >= this->n)) ? (o) : ((*this)));" in hpp
        assert ": (this))" not in hpp


class TestSelfBothArmsDeref:
    # Both arms `self`: neither is special-cased, so both deref.
    SRC = (
        _ACC +
        "    def pick(self, c: bool) -> Acc:\n"
        "        r = self if c else self\n"
        "        return r\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_deref_render(self):
        _compiler, hpp, _cpp = _gen(self.SRC)
        assert "Acc& r = ((c) ? ((*this)) : ((*this)));" in hpp


class TestSelfArmWithBorrowCallArm:
    # The self arm beside the OTHER admitted arm shape (a `T&`-returning
    # call): only the self arm retags, the call arm renders bare.
    SRC = (
        _ACC +
        "    def pick(self, o: Acc, c: bool) -> Int32:\n"
        "        t = self if c else trusted(o)\n"
        "        return t.n\n"
        "def trusted(b: Acc) -> Acc:\n"
        "    return b\n"
        "def main() -> None:\n"
        "    a = Acc(3)\n"
        "    b = Acc(4)\n"
        "    print(a.pick(b, True))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_deref_render(self):
        _compiler, hpp, _cpp = _gen(self.SRC)
        assert "Acc& t = ((c) ? ((*this)) : (trusted(o)));" in hpp


class TestSelfArmOwnSlotCopyTemp:
    # The prvalue consumer: an `Own[T]` call slot copies the ternary into a
    # temp, so the deref feeds a real record COPY (`auto __tmp_1 = ...`).
    # A bare `this` here would deduce a pointer and move the wrong thing.
    SRC = (
        "from tpy import Int32, Own\n"
        "class Acc:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def pick(self, o: Acc, c: bool) -> Int32:\n"
        "        r = take(self if c else o)\n"
        "        return r.n\n"
        "def take(b: Own[Acc]) -> Own[Acc]:\n"
        "    b.n += 1\n"
        "    return b\n"
        "def main() -> None:\n"
        "    print(Acc(3).pick(Acc(4), True))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_copy_temp_derefs(self):
        _compiler, hpp, _cpp = _gen(self.SRC)
        assert "auto __tmp_1 = ((c) ? ((*this)) : (o));" in hpp
        assert "take(std::move(__tmp_1));" in hpp


class TestReadonlyMethodSelfArmDerefs:
    # A `@readonly` method's receiver is `const Acc*`; the deref yields the
    # `const Acc&` the const-borrow binding needs.
    SRC = (
        "from tpy import Int32, readonly\n"
        "class Acc:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    @readonly\n"
        "    def pick(self, o: readonly[Acc]) -> readonly[Acc]:\n"
        "        c = self if self.n >= o.n else o\n"
        "        return c\n"
        "def main() -> None:\n"
        "    a = Acc(3)\n"
        "    b = Acc(4)\n"
        "    print(a.pick(b).n)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_const_deref_render(self):
        _compiler, hpp, _cpp = _gen(self.SRC)
        assert "const Acc& c = (((this->n >= o.n)) ? ((*this)) : (o));" in hpp


class TestGeneratorSelfArmStaysUndereferenced:
    # BOUNDARY (dualgen-probed): a generator body's receiver is already
    # spelled `(*this)` (`_LowerCtx.self_is_pointer=False`), so the retag
    # must be a no-op -- a second deref would emit `(*(*this))`.
    SRC = (
        "from typing import Iterator\n"
        "from tpy import Int32\n"
        "class Acc:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def picks(self, o: Acc) -> Iterator[Int32]:\n"
        "        i = 0\n"
        "        while i < 2:\n"
        "            c = self if self.n >= o.n else o\n"
        "            yield c.n\n"
        "            i += 1\n"
        "def main() -> None:\n"
        "    a = Acc(3)\n"
        "    b = Acc(4)\n"
        "    for v in a.picks(b):\n"
        "        print(v)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_single_deref_render(self):
        _compiler, hpp, _cpp = _gen(self.SRC)
        assert "Acc& c = ((((*this).n >= o.n)) ? ((*this)) : (o));" in hpp
        assert "(*(*this))" not in hpp


class TestPolyNarrowedSelfArmUsesAlias:
    # BOUNDARY (dualgen-probed): inside `isinstance(self, Dog)` the read
    # routes through the pre-bound cast pointer, so lowering yields a
    # THIRName -- never a THIRSelf -- and the retag cannot fire. This is
    # the AST's `narrowed_vars` carve-out in gen_expr_deref, reached
    # structurally rather than by a second predicate.
    SRC = (
        "from typing import Protocol\n"
        "from tpy import dynamic\n"
        "@dynamic\n"
        "class Tagged(Protocol):\n"
        "    pass\n"
        "class Pet(Tagged):\n"
        "    name: str\n"
        "    def __init__(self, n: str) -> None:\n"
        "        self.name = n\n"
        "    def pick(self, d: 'Dog') -> str:\n"
        "        if isinstance(self, Dog):\n"
        "            c = self if len(self.name) >= len(d.name) else d\n"
        "            return c.bark()\n"
        "        return 'pet: ' + self.name\n"
        "class Dog(Pet):\n"
        "    def __init__(self, n: str) -> None:\n"
        "        super().__init__(n)\n"
        "    def bark(self) -> str:\n"
        "        return 'woof from ' + self.name\n"
        "def main() -> None:\n"
        "    a = Dog('rex')\n"
        "    b = Dog('bo')\n"
        "    print(a.pick(b))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_alias_not_this(self):
        _compiler, hpp, _cpp = _gen(self.SRC)
        assert "? ((*__self_ptr)) : (d)" in hpp
        assert "? ((*this))" not in hpp


class TestPtrOptTernarySelfArmStillRejects:
    # BOUNDARY (dualgen-probed): the pointer-repr Optional face resolves
    # BEFORE the record branch and its record-name arm fences `self` by
    # name (an address-of the receiver is its own render rung). The retag
    # must not make this shape newly admissible.
    SRC = (
        _ACC +
        "    def pick(self) -> 'Acc | None':\n"
        "        c: Acc | None = self if self.n >= 0 else None\n"
        "        return c\n"
        "def main() -> None:\n"
        "    r = Acc(3).pick()\n"
        "    if r is not None:\n"
        "        print(r.n)\n"
        "main()\n"
    )

    def test_stays_ast_byte_identical(self):
        assert _fallback(self.SRC) == {"body:expr.ifexpr": 1}
        _assert_byte_identical(self.SRC)


class TestPtrOptTernaryInMethodUnchanged:
    # BOUNDARY: the ptr-opt ternary face itself, exercised inside a METHOD
    # (so `self_is_pointer` is live) -- arms keep their own normalization,
    # untouched by the record-branch retag.
    SRC = (
        "from tpy import Int32\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.val = v\n"
        "class Holder:\n"
        "    opt: Box | None\n"
        "    tag: Int32\n"
        "    def __init__(self, b: Box | None) -> None:\n"
        "        self.opt = b\n"
        "        self.tag = 1\n"
        "    def bump(self, p: Box | None, c: bool) -> None:\n"
        "        t = p if c else self.opt\n"
        "        if t is not None:\n"
        "            t.val += self.tag\n"
        "def main() -> None:\n"
        "    h = Holder(Box(7))\n"
        "    b = Box(3)\n"
        "    h.bump(b, True)\n"
        "    print(b.val)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_face_and_render(self):
        compiler, hpp, _cpp = _gen(self.SRC)
        assert ("Box* t = ((c) ? (p) : (::tpy::optional_to_ptr(this->opt)));"
                in hpp)
        assert compiler._thir_face_witnesses.get("ifexpr.ptr_opt", 0) >= 1


class TestValueOptTernaryInMethodUnchanged:
    # BOUNDARY: the value-repr Optional face in a method -- both arms wrap
    # in the spelled optional, no receiver deref anywhere near it.
    SRC = (
        _ACC +
        "    def pick(self, v: Int32, flag: bool) -> Int32 | None:\n"
        "        self.n += v\n"
        "        return v if flag else None\n"
        "def main() -> None:\n"
        "    print(Acc(3).pick(4, True))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_face_witnessed(self):
        compiler, _hpp, _cpp = _gen(self.SRC)
        assert compiler._thir_face_witnesses.get("ifexpr.value_opt", 0) >= 1


class TestPtrUnionTernaryInMethodUnchanged:
    # BOUNDARY: the WIDE ptr-union face in a method. A `self` arm cannot
    # reach it at all (sema rejects a record-vs-union ternary), so the pin
    # holds the face's own arms unchanged.
    SRC = (
        "from tpy import Int32\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "class B:\n"
        "    y: Int32\n"
        "    def __init__(self, y: Int32) -> None:\n"
        "        self.y = y\n"
        "class H:\n"
        "    u: A | B\n"
        "    step: Int32\n"
        "    def __init__(self, u: A | B) -> None:\n"
        "        self.u = u\n"
        "        self.step = 100\n"
        "    def bump(self, p: A | B, c: bool) -> None:\n"
        "        t = p if c else self.u\n"
        "        if isinstance(t, A):\n"
        "            t.x += self.step\n"
        "def main() -> None:\n"
        "    h = H(B(7))\n"
        "    a = A(3)\n"
        "    p: A | B = a\n"
        "    h.bump(p, True)\n"
        "    print(a.x)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_face_witnessed(self):
        compiler, hpp, _cpp = _gen(self.SRC)
        assert ("((c) ? (p) : (::tpy::to_ptr_variant(this->u)))" in hpp)
        assert compiler._thir_face_witnesses.get("ifexpr.ptr_union", 0) >= 1
