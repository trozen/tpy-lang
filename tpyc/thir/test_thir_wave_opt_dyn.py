"""The Optional[@dynamic P] ptr-repr family (queue item 5 P-A).

Routing pins for the four rows -- the narrowed opt-dyn method receiver,
the structural-conformer Adapter/RefAdapter arg temps, the inheriting
ctor-rvalue temp at the Optional slot, and the OPT_PROTO_RVALUE local
decl -- plus the boundary pins from the dualgen probes: a polymorphic
CLASS Optional local keeps decl.opt_slot_source, and an Optional
[STRUCTURAL protocol] body keeps its fallback (the monomorphized
spelling is unwitnessed for both the receiver and the None-compare).
"""

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _lower_ctx_witnessed,
)

_DYN = (
    "from typing import Protocol, Optional\n"
    "from tpy import dynamic\n\n\n"
    "@dynamic\n"
    "class Pet(Protocol):\n"
    "    def name(self) -> str: ...\n\n\n"
)


class TestOptDynReceiver:
    def test_narrowed_param_method_call_routes(self):
        src = (_DYN
               + "class Dog(Pet):\n"
               + "    def name(self) -> str:\n"
               + "        return \"dog\"\n\n\n"
               + "def greet(p: Optional[Pet]) -> str:\n"
               + "    if p is None:\n"
               + "        return \"<none>\"\n"
               + "    return p.name()\n\n\n"
               + "def main() -> None:\n"
               + "    print(greet(Dog()))\n"
               + "    print(greet(None))\n\n\n"
               + "main()\n")
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return p->name();" in cpp
        assert "greet(nullptr)" in cpp
        _, wit = _lower_ctx_witnessed(src)
        assert wit.get("method.opt_dyn_recv", 0) >= 1


class TestOptDynAdapterArgs:
    SRC = (_DYN
           + "class Cat:\n"
           + "    def name(self) -> str:\n"
           + "        return \"cat\"\n\n\n"
           + "def greet(p: Optional[Pet]) -> str:\n"
           + "    if p is None:\n"
           + "        return \"<none>\"\n"
           + "    return p.name()\n\n\n"
           + "def main() -> None:\n"
           + "    print(greet(Cat()))\n"
           + "    felix = Cat()\n"
           + "    print(greet(felix))\n"
           + "    print(greet(None))\n\n\n"
           + "main()\n")

    def test_structural_conformer_temps_route(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # Owning Adapter for the ctor rvalue, RefAdapter for the lvalue.
        assert "::tpy::Adapter<Pet, Cat> __tmp_1{Cat()};" in cpp
        assert "greet(&(__tmp_1))" in cpp
        assert "::tpy::RefAdapter<Pet, Cat> __tmp_2{felix};" in cpp
        assert "greet(&(__tmp_2))" in cpp
        assert "greet(nullptr)" in cpp
        _, wit = _lower_ctx_witnessed(self.SRC)
        assert wit.get("optptr.adapter_temp", 0) >= 2


class TestOptDynLocalDecl:
    def test_inheriting_and_structural_slots_route(self):
        src = (_DYN
               + "class Dog(Pet):\n"
               + "    def name(self) -> str:\n"
               + "        return \"dog\"\n\n\n"
               + "class Cat:\n"
               + "    def name(self) -> str:\n"
               + "        return \"cat\"\n\n\n"
               + "def a() -> str:\n"
               + "    p: Optional[Pet] = Dog()\n"
               + "    if p is None:\n"
               + "        return \"none\"\n"
               + "    return p.name()\n\n\n"
               + "def b() -> str:\n"
               + "    p: Optional[Pet] = Cat()\n"
               + "    if p is None:\n"
               + "        return \"none\"\n"
               + "    return p.name()\n\n\n"
               + "def main() -> None:\n"
               + "    print(a())\n"
               + "    print(b())\n\n\n"
               + "main()\n")
        hpp, cpp = _assert_routes_byte_identical(src)
        # Inheriting conformer: class-typed slot, deduced pointer upcast.
        assert "Dog __slot_1 = Dog();" in cpp
        # Structural conformer: auto slot, monomorphized static dispatch.
        assert "auto __slot_1 = Cat();" in cpp
        assert cpp.count("auto* p = &__slot_1;") == 2
        _, wit = _lower_ctx_witnessed(src)
        assert wit.get("decl.opt_slot_proto_rvalue", 0) >= 2


class TestOptDynBoundaries:
    def test_polymorphic_class_local_still_defers(self):
        # BOUNDARY: a polymorphic CLASS Optional local (subclass rvalue)
        # keeps decl.opt_slot_source -- the AST types the slot at the
        # child but spells the POINTER at the base (`Base* b`), a render
        # the OPT_PROTO_RVALUE arm (auto*) does not mirror.
        src = ("from typing import Optional\n"
               "from tpy import Int32\n\n\n"
               "class Base:\n"
               "    n: Int32\n\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 1\n\n"
               "    def tag(self) -> Int32:\n"
               "        return self.n\n\n\n"
               "class Child(Base):\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 2\n\n\n"
               "def probe() -> Int32:\n"
               "    b: Optional[Base] = Child()\n"
               "    if b is None:\n"
               "        return -1\n"
               "    return b.tag()\n\n\n"
               "def main() -> None:\n"
               "    print(probe())\n\n\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.opt_slot_source")

    def test_structural_protocol_optional_still_defers(self):
        # BOUNDARY: an Optional[STRUCTURAL protocol] body keeps its
        # fallback -- both the None-compare and the receiver need the
        # monomorphized template spelling (the P-B constexpr family).
        src = ("from typing import Protocol, Optional\n\n\n"
               "class Named(Protocol):\n"
               "    def name(self) -> str: ...\n\n\n"
               "class Cat:\n"
               "    def name(self) -> str:\n"
               "        return \"cat\"\n\n\n"
               "def greet(p: Optional[Named]) -> str:\n"
               "    if p is None:\n"
               "        return \"<none>\"\n"
               "    return p.name()\n\n\n"
               "def main() -> None:\n"
               "    c = Cat()\n"
               "    print(greet(c))\n"
               "    print(greet(None))\n\n\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.if:if.cond_binop.is.optional_other_nonetype:binop.shape.is")
