"""Pins for the decl storage-sink rows and the method receiver chains:
an F1-record / owned-container rvalue call (or record-returning dunder
binop) decls as the plain spelled copy, a generic value record and a
reference-element span spell their own slots, and a call / subscript
result composes as a method receiver. Boundary pins hold the shapes whose
AST render is NOT the plain copy (rebound targets, borrow returns)."""

from __future__ import annotations

from .testutil import _assert_byte_identical, _compile, _entry


def _gen_thir(source: str):
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return hpp + cpp, compiler._thir_face_witnesses, compiler._thir_fallback


_REC = (
    "from tpy import Int32, Own\n"
    "class Box:\n"
    "    val: Int32\n"
    "    def __init__(self, val: Int32):\n"
    "        self.val = val\n"
)


class TestRvalueStorageDecl:
    def test_record_container_call_decls_plain_copy(self):
        # A record-ELEMENT container result: `_storage_call_ret` leaves that
        # element family out, so only the generalised rvalue row admits it.
        src = _REC + (
            "def make() -> Own[list[Box]]:\n"
            "    return [Box(3)]\n"
            "def main() -> None:\n"
            "    bs = make()\n"
            "    print(len(bs))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert "std::vector<Box> bs = make();" in out
        assert faces.get("decl.rvalue_storage_call", 0) >= 1
        _assert_byte_identical(src)

    def test_record_element_pop_decls_plain_copy(self):
        src = _REC + (
            "def main() -> None:\n"
            "    heap: list[Box] = [Box(3), Box(1)]\n"
            "    top = heap.pop()\n"
            "    print(top.val)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "Box top = ::tpy::pop_back(heap);" in out
        _assert_byte_identical(src)

    def test_reassigned_container_call_target_stays_ast(self):
        # A REBOUND non-value local takes the AST's pointer-local form, which
        # the plain value decl does not render -- the guards must hold.
        src = _REC + (
            "def make(v: Int32) -> Own[list[Box]]:\n"
            "    return [Box(v)]\n"
            "def main() -> None:\n"
            "    bs = make(1)\n"
            "    print(len(bs))\n"
            "    bs = make(2)\n"
            "    print(len(bs))\n"
            "main()\n"
        )
        _out, _faces, fallback = _gen_thir(src)
        assert fallback
        _assert_byte_identical(src)


class TestValueRecordAndSpanSlots:
    def test_generic_value_record_decl_routes(self):
        src = (
            "from tpy import Int32, ValueType\n"
            "class Pair[T: ValueType](ValueType):\n"
            "    first: T\n"
            "    second: T\n"
            "    def __init__(self, first: T, second: T) -> None:\n"
            "        self.first = first\n"
            "        self.second = second\n"
            "def main() -> None:\n"
            "    p = Pair[Int32](1, 2)\n"
            "    q = p\n"
            "    print(q.first, q.second)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "Pair<int32_t> q = p;" in out
        _assert_byte_identical(src)

    def test_reference_element_span_decl_routes(self):
        src = (
            "from tpy import Int32, Span\n"
            "class Box:\n"
            "    val: Int32\n"
            "    def __init__(self, val: Int32):\n"
            "        self.val = val\n"
            "def main() -> None:\n"
            "    b: list[Box] = [Box(1)]\n"
            "    s: Span[Box] = b\n"
            "    print(len(s))\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "std::span<Box> s = ::tpy::as_mut_span(b);" in out
        _assert_byte_identical(src)


class TestReceiverChains:
    def test_container_returning_call_receiver_routes(self):
        src = (
            "from tpy import Int32\n"
            "def main() -> None:\n"
            "    groups: dict[str, list[Int32]] = {}\n"
            "    groups.setdefault(\"a\", []).append(1)\n"
            "    print(len(groups))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert ("::tpy::dict_setdefault(groups, \"a\", "
                "std::vector<int32_t>{}).push_back(1);") in out
        assert faces.get("method.recv.container_method", 0) >= 1
        _assert_byte_identical(src)

    def test_container_field_off_call_prints_through_wrap(self):
        src = (
            "from tpy import Int32\n"
            "class Holder:\n"
            "    log: list[Int32]\n"
            "    def __init__(self):\n"
            "        self.log = []\n"
            "class Outer:\n"
            "    h: Holder\n"
            "    def __init__(self):\n"
            "        self.h = Holder()\n"
            "    def get(self) -> Holder:\n"
            "        return self.h\n"
            "def main() -> None:\n"
            "    o = Outer()\n"
            "    o.get().log.append(4)\n"
            "    print(o.get().log)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "o.get().log.push_back(4);" in out
        assert "::tpy::ListPrinter(o.get().log)" in out
        _assert_byte_identical(src)

    def test_field_off_subscript_receiver_routes(self):
        src = (
            "from tpy import Int32\n"
            "class Node:\n"
            "    val: Int32\n"
            "    kids: list[Int32]\n"
            "    def __init__(self, val: Int32):\n"
            "        self.val = val\n"
            "        self.kids = []\n"
            "def main() -> None:\n"
            "    ns: list[Node] = [Node(1)]\n"
            "    ns[0].kids.append(7)\n"
            "    print(len(ns))\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "::tpy::__getitem__(ns, 0).kids.push_back(7);" in out
        _assert_byte_identical(src)


class TestNestedDefSelfCapture:
    def test_method_nested_def_captures_this(self):
        src = (
            "from tpy import Int32\n"
            "class Acc:\n"
            "    total: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.total = 0\n"
            "    def collect(self, k: Int32) -> None:\n"
            "        bonus = 1\n"
            "        def feed() -> None:\n"
            "            self.total += k + bonus\n"
            "        feed()\n"
            "def main() -> None:\n"
            "    a = Acc()\n"
            "    a.collect(5)\n"
            "    print(a.total)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "auto feed = [&bonus, &k, this]() {" in out
        # The captured receiver keeps rendering through `this` in the body.
        assert "this->total = ::tpy::add_check<int32_t>(this->total," in out
        _assert_byte_identical(src)

    def test_generator_method_nested_def_is_a_frame_member(self):
        # A nested def inside a generator method is not a lambda at all: it
        # becomes a frame member, so the capture row never applies -- pinned
        # so the self-capture admission cannot silently start emitting one.
        src = (
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "class Acc:\n"
            "    total: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.total = 3\n"
            "    def each(self) -> Iterator[Int32]:\n"
            "        def bump() -> Int32:\n"
            "            return self.total\n"
            "        yield bump()\n"
            "def main() -> None:\n"
            "    a = Acc()\n"
            "    for v in a.each():\n"
            "        print(v)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "int32_t __gen_Acc_each::bump() {" in out
        assert "[this]" not in out
        _assert_byte_identical(src)


_UNION_RECS = (
    "from tpy import Int32\n"
    "class Dog:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "class Cat:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
)


class TestPtrUnionReturns:
    def test_member_param_returns_its_address(self):
        src = _UNION_RECS + (
            "def wrap(d: Dog) -> Dog | Cat:\n"
            "    return d\n"
            "def main() -> None:\n"
            "    d = Dog(1)\n"
            "    u = wrap(d)\n"
            "    print(1)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert "    return &(d);" in out
        assert faces.get("ret.narrowed_union_addr", 0) >= 1
        _assert_byte_identical(src)

    def test_narrowed_subject_returns_alias_address(self):
        src = _UNION_RECS + (
            "def ensure(pet: Dog | Cat) -> Dog | Cat:\n"
            "    if isinstance(pet, Dog):\n"
            "        return pet\n"
            "    return pet\n"
            "def main() -> None:\n"
            "    d = Dog(1)\n"
            "    print(1)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "        return &(__pet);" in out
        _assert_byte_identical(src)

    def test_same_union_name_still_returns_bare(self):
        # An UN-narrowed same-union binding already IS the pointer variant --
        # it must not take an address-of.
        src = _UNION_RECS + (
            "def passthru(u: Dog | Cat) -> Dog | Cat:\n"
            "    return u\n"
            "def main() -> None:\n"
            "    print(1)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "    return u;" in out
        _assert_byte_identical(src)


class TestCallableReturn:
    def test_callable_binding_returns_bare(self):
        src = (
            "from typing import Callable\n"
            "from tpy import Int32\n"
            "def hold(f: Callable[[Int32], Int32]) -> Callable[[Int32], Int32]:\n"
            "    return f\n"
            "def double(x: Int32) -> Int32:\n"
            "    return x + x\n"
            "def main() -> None:\n"
            "    g = hold(double)\n"
            "    print(g(4))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert "    return f;\n" in out
        assert faces.get("ret.callable_name", 0) >= 1
        _assert_byte_identical(src)

    def test_lambda_valued_local_return_stays_ast(self):
        # A local bound to a LAMBDA is a closure name, not a std::function
        # binding: it keeps the closure-name arm, which spells the local
        # rather than the plain lowered read.
        src = (
            "from typing import Callable\n"
            "from tpy import Int32\n"
            "def pick(k: Int32) -> Callable[[Int32], Int32]:\n"
            "    def inner(x: Int32) -> Int32:\n"
            "        return x + k\n"
            "    return inner\n"
            "def main() -> None:\n"
            "    g = pick(2)\n"
            "    print(g(4))\n"
            "main()\n"
        )
        out, faces, _fallback = _gen_thir(src)
        assert faces.get("ret.callable_name", 0) == 0
        assert "    return inner;\n" in out
        _assert_byte_identical(src)


class TestRecordBinopDecl:
    def test_own_returning_dunder_binop_decls_plain_copy(self):
        src = (
            "from tpy import Int32, Own\n"
            "class Flags:\n"
            "    bits: Int32\n"
            "    def __init__(self, bits: Int32) -> None:\n"
            "        self.bits = bits\n"
            "    def __ror__(self, other: Int32) -> Own[Flags]:\n"
            "        return Flags(other | self.bits)\n"
            "def main() -> None:\n"
            "    f = Flags(1)\n"
            "    r = 4 | f\n"
            "    print(r.bits)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not fallback
        assert "Flags r = ((4) | (f));" in out
        assert faces.get("decl.rvalue_storage_call", 0) >= 1
        _assert_byte_identical(src)

    def test_borrow_returning_dunder_binop_stays_ast(self):
        # A dunder returning `Acc&` ALIASES an operand: the AST decls
        # `const Acc& c = ((a) + (b));`, not the plain value copy.
        src = (
            "from tpy import Int32\n"
            "class Acc:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def __add__(self, other: Acc) -> Acc:\n"
            "        return self\n"
            "def main() -> None:\n"
            "    a = Acc(3)\n"
            "    b = Acc(1)\n"
            "    c = a + b\n"
            "    print(c.n)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert fallback
        assert "const Acc& c = ((a) + (b));" in out
        _assert_byte_identical(src)


class TestUnionNarrowBindingFence:
    def test_for_each_element_union_narrow_stays_ast(self):
        # A ptr-variant-TYPED union reached through a for-each element binds
        # the VALUE variant, which codegen extracts with `std::get<T>`; THIR
        # tracks pointer-variant BINDINGS (params + ptr-variant local decls)
        # and rejects the ones it cannot classify rather than guessing.
        src = _UNION_RECS + (
            "def show(xs: list[Dog | Cat]) -> None:\n"
            "    for u in xs:\n"
            "        if isinstance(u, Dog):\n"
            "            print(\"dog\", u.n)\n"
            "        else:\n"
            "            print(\"cat\", u.n)\n"
            "def main() -> None:\n"
            "    print(1)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert fallback
        assert "std::holds_alternative<Dog>(u)" in out
        _assert_byte_identical(src)

    def test_union_param_narrow_still_routes_pointer_extraction(self):
        # The PARAM binding IS the pointer variant -- its extraction keeps
        # the `std::get<Dog*>` render the fence must not take away.
        src = _UNION_RECS + (
            "def show(u: Dog | Cat) -> None:\n"
            "    if isinstance(u, Dog):\n"
            "        print(\"dog\", u.n)\n"
            "    else:\n"
            "        print(\"cat\", u.n)\n"
            "def main() -> None:\n"
            "    print(1)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "std::holds_alternative<Dog*>(u)" in out
        _assert_byte_identical(src)

    def test_optional_returning_call_field_receiver_stays_ast(self):
        # `_indirect_field_receiver_ok` admits F1-record inner results only:
        # an Optional-returning call reads through the AST's own unwrap.
        src = (
            "from tpy import Int32\n"
            "class Holder:\n"
            "    log: list[Int32]\n"
            "    def __init__(self):\n"
            "        self.log = []\n"
            "class Outer:\n"
            "    h: Holder\n"
            "    def __init__(self):\n"
            "        self.h = Holder()\n"
            "    def maybe(self) -> Holder | None:\n"
            "        return self.h\n"
            "def main() -> None:\n"
            "    o = Outer()\n"
            "    m = o.maybe()\n"
            "    if m is not None:\n"
            "        m.log.append(1)\n"
            "    print(1)\n"
            "main()\n"
        )
        _out, _faces, fallback = _gen_thir(src)
        assert fallback
        _assert_byte_identical(src)

    def test_for_each_element_union_match_stays_ast(self):
        # The MATCH tiers share the isinstance arm's binding rule: a
        # ptr-variant-TYPED union bound as a for-each element extracts
        # WITHOUT the `*std::get<N*>` deref, so the subject must reject.
        src = _UNION_RECS + (
            "def show(xs: list[Dog | Cat]) -> None:\n"
            "    for u in xs:\n"
            "        match u:\n"
            "            case Dog():\n"
            "                print(\"dog\", u.n)\n"
            "            case Cat():\n"
            "                print(\"cat\", u.n)\n"
            "def main() -> None:\n"
            "    print(1)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert fallback
        assert "auto& __case_0 = std::get<1>(__match_subject_1);" in out
        _assert_byte_identical(src)

    def test_union_param_match_still_routes_pointer_extraction(self):
        src = _UNION_RECS + (
            "def show(u: Dog | Cat) -> None:\n"
            "    match u:\n"
            "        case Dog():\n"
            "            print(\"dog\", u.n)\n"
            "        case Cat():\n"
            "            print(\"cat\", u.n)\n"
            "def main() -> None:\n"
            "    print(1)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "*std::get<" in out
        _assert_byte_identical(src)
