"""Polymorphic-dispatch match tiers (poly_if_elif / poly_guarded):
dynamic_cast if-init chains, alias'd subject reads, field conditions,
and the excluded-face fallbacks."""
from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _compile, _entry, _fn, _lower_ctx

_PRELUDE = (
    "from typing import Protocol\n"
    "from tpy import dynamic\n"
    "@dynamic\n"
    "class Pet(Protocol):\n"
    "    def speak(self) -> str: ...\n"
    "class Dog(Pet):\n"
    "    tag: str\n"
    "    def __init__(self, tag: str) -> None:\n"
    "        self.tag = tag\n"
    "    def speak(self) -> str:\n        return \"woof\"\n"
    "class Cat(Pet):\n"
    "    def __init__(self) -> None:\n        pass\n"
    "    def speak(self) -> str:\n        return \"meow\"\n"
    "class Hamster(Pet):\n"
    "    def __init__(self) -> None:\n        pass\n"
    "    def speak(self) -> str:\n        return \"squeak\"\n"
)


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return hpp + cpp


class TestPolyMatchChain:
    SRC = (
        _PRELUDE
        + "def describe(p: Pet) -> str:\n"
        + "    match p:\n"
        + "        case Dog(tag=t):\n"
        + "            return \"dog:\" + t + \":\" + p.speak()\n"
        + "        case Cat() | Hamster():\n"
        + "            return \"small:\" + p.speak()\n"
        + "        case _:\n"
        + "            return \"?\"\n"
    )

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "describe") is not None

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        # The C++17 if-init cast + the per-arm ref alias.
        assert ("if (Dog* __mpoly_0 = "
                "dynamic_cast<Dog*>(&__match_subject_1)) {") in out
        assert "Dog& __case_0 = *__mpoly_0;" in out
        # The field binding and the narrowed subject read go via the alias.
        assert "auto& t = __case_0.tag;" in out
        assert "__case_0.speak()" in out
        # Or-arm: ||-joined null tests; the un-narrowed subject read stays.
        assert ("} else if ((dynamic_cast<Cat*>(&__match_subject_1) != "
                "nullptr) || (dynamic_cast<Hamster*>(&__match_subject_1) "
                "!= nullptr)) {") in out
        assert "p.speak()" in out


class TestPolyMatchGuarded:
    SRC = (
        _PRELUDE
        + "def pick(p: Pet, flag: bool) -> str:\n"
        + "    match p:\n"
        + "        case Dog(tag=\"rex\"):\n"
        + "            return \"rex\"\n"
        + "        case Dog() if flag:\n"
        + "            return \"flagged dog\"\n"
        + "        case _:\n"
        + "            return \"other\"\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        # Standalone-if arms (const casts: no method is called on `p`, so
        # the const-borrow verdict applies); the field condition composes
        # around the ALIAS and gates the body + goto (fallthrough to the
        # next cast on miss).
        assert "if (const Dog* __mpoly_0 = dynamic_cast<const Dog*>" in out
        assert 'if (__case_0.tag == "rex") {' in out
        assert "goto __match_end_2;" in out
        # The guard gates the second Dog arm the same way.
        assert "if (flag) {" in out
        # The end label writes indented.
        assert "    __match_end_2:;" in out


class TestPolyStructuralAndConst:
    # Rect conforms STRUCTURALLY (no inheritance) -> dyn_adapter_cast; a
    # readonly borrow (only @readonly methods called) consts the casts.
    SRC = (
        "from typing import Protocol\n"
        "from tpy import dynamic, readonly\n"
        "@dynamic\n"
        "class Shape(Protocol):\n"
        "    @readonly\n"
        "    def area(self) -> int: ...\n"
        "class Rect:\n"
        "    w: int\n"
        "    def __init__(self, w: int) -> None:\n"
        "        self.w = w\n"
        "    @readonly\n"
        "    def area(self) -> int:\n        return self.w\n"
        "class Circle(Shape):\n"
        "    def __init__(self) -> None:\n        pass\n"
        "    @readonly\n"
        "    def area(self) -> int:\n        return 3\n"
        "def classify(s: Shape) -> str:\n"
        "    match s:\n"
        "        case Rect() as r:\n"
        "            return \"rect \" + str(r.area())\n"
        "        case Circle():\n"
        "            return \"circle \" + str(s.area())\n"
        "        case _:\n"
        "            return \"?\"\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        # Structural conformer routes through the adapter cast; the const
        # borrow consts the inheritance cast and the aliases.
        assert "::tpy::dyn_adapter_cast<Shape, Rect>(&__match_subject_1)" \
            in out
        assert "dynamic_cast<const Circle*>(&__match_subject_1)" in out
        assert "auto& r = __case_0;" in out


class TestPolyMatchFallbacks:
    # NB: or-alternatives with field sub-patterns need no fallback pin --
    # sema rejects them for poly matches outright; the lowering's
    # `alt.keywords` check is defensive.

    def test_generator_body_falls_back(self):
        # Poly dispatch inside a resumable frame region stays AST (the AST
        # refuses a suspending one; the non-suspending leaf is unmirrored).
        src = (
            _PRELUDE
            + "from typing import Iterator\n"
            + "def gen(p: Pet, n: int) -> Iterator[str]:\n"
            + "    i = 0\n"
            + "    while i < n:\n"
            + "        match p:\n"
            + "            case Dog():\n"
            + "                yield \"dog\"\n"
            + "            case _:\n"
            + "                yield \"other\"\n"
            + "        i += 1\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "gen") is None


class TestPolyMatchExcludedRungs:
    # NB: match.poly_ptr_source needs no fallback pin -- sema rejects class
    # patterns on an Optional[Protocol] subject outright ("class pattern
    # not valid for Optional inner type"), so the pointer-repr rung is a
    # defensive slice guard with no surface spelling today.

    def test_or_arm_as_binding_falls_back(self):
        # An `as` binding on an or-pattern arm binds the base subject in
        # the AST but types per-alternative in sema -- the
        # match.poly_or_bind rung.
        src = (
            _PRELUDE
            + "def f(p: Pet) -> str:\n"
            + "    match p:\n"
            + "        case Cat() | Hamster() as x:\n"
            + "            return x.speak()\n"
            + "        case _:\n"
            + "            return \"?\"\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_guarded_or_arm_and_guarded_wildcard(self):
        # The guarded tier's or-arm ANDs the guard into the block
        # condition; a guarded wildcard gates the body -- both witnessed.
        src = (
            _PRELUDE
            + "def f(p: Pet, flag: bool) -> str:\n"
            + "    match p:\n"
            + "        case Cat() | Hamster() if flag:\n"
            + "            return \"small\"\n"
            + "        case _ if not flag:\n"
            + "            return \"unflagged\"\n"
            + "        case _:\n"
            + "            return \"?\"\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        out = _cpp(src, thir=True)
        assert out == _cpp(src, thir=False)
        assert "!= nullptr)) && flag) {" in out
        assert "if ((!(flag))) {" in out


class TestPolyMatchFieldSubject:
    # A FIELD subject (`match o.pet:` on a deref-dispatch Box[Animal]
    # field): the subject lowers under BORROW_BIND, so the F1-record field
    # row admits the bare member read the lvalue bind borrows
    # (`auto& __match_subject_1 = o.pet;`). Name subjects keep the default
    # use; a SUBSCRIPT subject rides the same BORROW_BIND admission (the
    # poly_expr_subscript corpus flip); method-call subjects are
    # sema-rejected for the deref-dispatch family.
    SRC = (
        "from typing import Protocol\n"
        "from tpy import Int32, Own, dynamic\n"
        "from tplib.box import Box\n"
        "@dynamic\n"
        "class Tag(Protocol):\n"
        "    pass\n"
        "class Animal(Tag):\n"
        "    def __init__(self) -> None:\n"
        "        pass\n"
        "class Dog(Animal):\n"
        "    def __init__(self) -> None:\n"
        "        pass\n"
        "class Snake(Animal):\n"
        "    legs: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.legs = 0\n"
        "class Owner:\n"
        "    pet: Box[Animal]\n"
        "    def __init__(self, pet: Own[Box[Animal]]) -> None:\n"
        "        self.pet = pet\n"
        "def describe(o: Owner) -> str:\n"
        "    match o.pet:\n"
        "        case Dog():\n"
        "            return \"dog\"\n"
        "        case Snake() as s:\n"
        "            return \"snake \" + str(s.legs)\n"
        "        case _:\n"
        "            return \"?\"\n"
        "def main() -> None:\n"
        "    print(describe(Owner(Box(Dog()))))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert "auto& __match_subject_1 = o.pet;" in cpp
        assert ("dynamic_cast<const Dog*>"
                "(&(__match_subject_1.__deref__()))") in cpp

    def test_subscript_subject_routes(self):
        # The SUBSCRIPT-subject flavor of the BORROW_BIND widening, pinned
        # directly (the poly_expr_subscript corpus shape).
        src = self.SRC.replace(
            "def describe(o: Owner) -> str:\n"
            "    match o.pet:\n",
            "def describe_at(pets: list[Box[Animal]], i: int) -> str:\n"
            "    match pets[i]:\n"
        ).replace(
            "    print(describe(Owner(Box(Dog()))))\n",
            "    pets: list[Box[Animal]] = []\n"
            "    pets.append(Box(Dog()))\n"
            "    print(describe_at(pets, 0))\n"
        )
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert ("auto& __match_subject_1 = ::tpy::__getitem__"
                "(pets, i.to_fixed_check<int32_t>());") in cpp
