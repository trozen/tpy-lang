"""The @dynamic-protocol RETURNs bundle (designed-queue item 1).

Rows pinned here:
  * BORROW return slots (`-> P` -> `P&`, `-> readonly[P]` -> `const P&`):
    a borrow param returns bare, a pointer-global derefs.
  * `Own[P]` return slots (`std::unique_ptr<P>`): 'forward' names / calls /
    call-pair ternaries bare; conformer ctor rvalues and movable names take
    `std::make_unique<C>` (inheritance) / `::tpy::make_adapter<P>`
    (structural).
  * Receiver rows: an `Own[P]` name or call receiver arrows
    (`p->name()`, `make()->name()`); a borrow `P&`-call receiver dots;
    a structural protocol-typed method result chains
    (`c.half().value()`).
  * The erased-decl call rung (`Pet* r = &echo(dog);`).
  * Protocol GLOBALS: every module-init write is the DYN_PROTOCOL rebind
    with a `static std::optional<Slot> __global_slot_N;` hoist -- branch
    and loop writes included.

Boundaries that must keep rejecting: a ternary with a NAME arm at the
Own[P] return (only the call-pair row is admitted), and a STRUCTURAL
(non-@dynamic) protocol global.
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _top_level,
)

_PET = (
    "from tpy import dynamic, readonly, Own\n"
    "from typing import Protocol\n"
    "@dynamic\n"
    "class Pet(Protocol):\n"
    "    @readonly\n"
    "    def name(self) -> str: ...\n"
    "class Dog(Pet):\n"
    "    @readonly\n"
    "    def name(self) -> str:\n        return \"Rex\"\n"
    "class Cat:\n"
    "    @readonly\n"
    "    def name(self) -> str:\n        return \"Tom\"\n"
)


class TestDynBorrowReturn:
    SRC = _PET + (
        "global_pet: Pet = Dog()\n"
        "def echo(pet: Pet) -> Pet:\n"
        "    return pet\n"
        "def echo_ro(pet: readonly[Pet]) -> readonly[Pet]:\n"
        "    return pet\n"
        "def get_global() -> Pet:\n"
        "    return global_pet\n"
        "def main() -> None:\n"
        "    d = Dog()\n"
        "    r: Pet = echo(d)\n"
        "    print(r.name())\n"
        "    print(echo_ro(d).name())\n"
        "    print(get_global().name())\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "Pet& echo(Pet& pet)" in cpp
        assert "const Pet& echo_ro(const Pet& pet)" in cpp
        assert "return (*global_pet);" in cpp
        # The erased-decl call rung: the decl owns the `&` lift.
        assert "Pet* r = &echo(d);" in cpp
        # Borrow-call receiver keeps `.` access.
        assert "echo_ro(d).name()" in cpp
        assert "get_global().name()" in cpp


class TestDynOwnReturn:
    SRC = _PET + (
        "def make_inherit() -> Own[Pet]:\n"
        "    return Dog()\n"
        "def make_structural() -> Own[Pet]:\n"
        "    return Cat()\n"
        "def move_inherit() -> Own[Pet]:\n"
        "    d = Dog()\n"
        "    return d\n"
        "def move_structural() -> Own[Pet]:\n"
        "    c = Cat()\n"
        "    return c\n"
        "def fwd(p: Own[Pet]) -> Own[Pet]:\n"
        "    print(p.name())\n"
        "    return p\n"
        "def pick(flag: bool) -> Own[Pet]:\n"
        "    return make_inherit() if flag else make_structural()\n"
        "def main() -> None:\n"
        "    fwd(make_inherit())\n"
        "    print(make_structural().name())\n"
        "    print(pick(True).name())\n"
        "    print(move_inherit().name())\n"
        "    print(move_structural().name())\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # Conformer ctor rvalues: verdict-keyed wraps.
        assert "return std::make_unique<Dog>(Dog());" in cpp
        assert "return ::tpy::make_adapter<Pet>(Cat());" in cpp
        # Movable NAME sources move into the wrap.
        assert "return std::make_unique<Dog>(std::move(d));" in cpp
        assert "return ::tpy::make_adapter<Pet>(std::move(c));" in cpp
        # 'forward' name / ternary pass-through: bare, no spelled move.
        assert "return p;" in cpp
        assert ("return ((flag) ? (make_inherit()) : "
                "(make_structural()));") in cpp
        # Own[P] receivers arrow: name receiver and call receivers.
        assert "p->name()" in cpp
        assert "make_structural()->name()" in cpp
        assert "pick(true)->name()" in cpp
        # Forward CALL arg at the Own[P] slot renders bare.
        assert "fwd(make_inherit());" in cpp


class TestDynOwnMethodRecvArrow:
    SRC = _PET + (
        "class Farm:\n"
        "    def __init__(self) -> None:\n        pass\n"
        "    @readonly\n"
        "    def breed(self) -> Own[Pet]:\n"
        "        return Dog()\n"
        "def main() -> None:\n"
        "    f = Farm()\n"
        "    print(f.breed().name())\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # An Own[P]-returning METHOD-call receiver arrows.
        assert "f.breed()->name()" in cpp


class TestStructuralProtocolChain:
    SRC = (
        "from typing import Protocol\n"
        "from tpy import ValueType, Int32\n"
        "class Chainable(Protocol):\n"
        "    def half(self) -> Chainable: ...\n"
        "    def value(self) -> Int32: ...\n"
        "class Num(ValueType):\n"
        "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
        "    def half(self) -> Num:\n        return Num(self.v // 2)\n"
        "    def value(self) -> Int32:\n        return self.v\n"
        "def chain(c: Chainable) -> None:\n"
        "    print(c.half().value())\n"
        "def main() -> None:\n"
        "    chain(Num(8))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # The protocol-typed method result chains bare in the template body.
        assert "c.half().value()" in hpp


class TestDynProtocolGlobal:
    SRC = _PET + (
        "pet: Pet = Dog()\n"
        "print(pet.name())\n"
        "pet = Cat()\n"
        "if True:\n"
        "    pet = Dog()\n"
        "i = 0\n"
        "while i < 1:\n"
        "    pet = Cat()\n"
        "    i = i + 1\n"
        "print(pet.name())\n"
    )

    def test_top_level_routes(self):
        top, wit, fb = _top_level(self.SRC)
        assert top is not None, fb
        assert wit.get("top_level.global_dyn_rebind", 0) >= 4

    def test_byte_identical_render(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "static std::optional<Dog> __global_slot_1;" in cpp
        assert ("static std::optional<::tpy::Adapter<Pet, Cat>> "
                "__global_slot_2;") in cpp
        assert "__global_slot_1.emplace(Dog());" in cpp
        assert "pet = &*__global_slot_1;" in cpp


class TestDynOwnForwardMethodCallArg:
    SRC = _PET + (
        "class Farm:\n"
        "    def __init__(self) -> None:\n        pass\n"
        "    @readonly\n"
        "    def breed(self) -> Own[Pet]:\n"
        "        return Dog()\n"
        "def sink(p: Own[Pet]) -> None:\n"
        "    print(p.name())\n"
        "def main() -> None:\n"
        "    f = Farm()\n"
        "    sink(f.breed())\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        # An Own[P]-returning METHOD-call rvalue forwarded at the Own[P]
        # arg slot composes bare -- covered by the receiver/ret
        # admissions, not `_dyn_own_forward_call_arg` (free calls only);
        # this pin witnesses the composed route so a gate change cannot
        # silently drop it. (The erased-decl rung's method-call flavor is
        # sema-unconstructible: bare dyn-protocol METHOD returns are
        # rejected at registration.)
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "sink(f.breed());" in cpp


class TestDynRetBoundaries:
    def test_mixed_ternary_arm_keeps_rejecting(self):
        # A NAME arm at the Own[P] return: only the call-pair ternary row
        # is admitted; the body must fall back (byte-identical via AST).
        src = _PET + (
            "def make() -> Own[Pet]:\n"
            "    return Dog()\n"
            "def pick(flag: bool, q: Own[Pet]) -> Own[Pet]:\n"
            "    return make() if flag else q\n"
            "def main() -> None:\n"
            "    print(pick(True, Dog()).name())\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:expr.ifexpr")

    def test_erased_source_global_write_keeps_rejecting(self):
        # An already-ERASED source at the protocol-global write
        # (`pet = other` where other is itself a protocol global) is the
        # pointer-copy render, not the adapter-slot rebind -- unmirrored,
        # must stay on the AST path. (The structural-protocol global
        # boundary is unconstructible: sema rejects non-@dynamic protocol
        # variable types outright.)
        src = _PET + (
            "pet: Pet = Dog()\n"
            "other: Pet = Cat()\n"
            "pet = other\n"
            "print(pet.name())\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "top_level:stmt.var_decl:top_level.global_slot_protocol")
