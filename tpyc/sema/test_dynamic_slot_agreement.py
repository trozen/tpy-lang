"""In the @dynamic protocols a protocol or a class combines, a method name
is one slot, which every declaration of it must agree on; a method filling
it emits exactly the slot's receiver form."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..compilation_context import activate_compiler
from ..diagnostics import SemanticError
from ..parse import ParseError
from ..compiler import CompileError
from ..thir.testutil import _compile, _entry
from . import protocols as protocols_mod

HEADER = """\
from typing import Literal, Protocol, Self, overload
from dataclasses import dataclass
from tpy import dynamic, readonly, auto_readonly, auto_own, Own, pure, String, int32

class Ray:
    d: float
    def __init__(self, d: float) -> None:
        self.d = d

@dynamic
class Surface(Protocol):
    def bounce(self, ray: Ray) -> float: ...

@dynamic
class ReadSurface(Protocol):
    @readonly
    def bounce(self) -> float: ...

@dynamic
class Both(Protocol):
    def shine(self) -> float: ...

@dynamic
class ReadBoth(Protocol):
    @readonly
    def shine(self) -> float: ...
"""

_ONE_SLOT = (r" -- in the @dynamic protocols a class or protocol combines, a "
             r"method name is one slot$")

# The filler's receiver forms against the one slot of its name.
FORM_CASES = {
    # An @auto_readonly pair on a @readonly slot has a mutable form no slot
    # takes.
    "auto_readonly_on_readonly_slot": ("""
class Mat(ReadSurface):
    d: float
    def __init__(self) -> None:
        self.d = 1.0
    @auto_readonly
    def bounce(self) -> float:
        return self.d
""", r"'Mat\.bounce' has a mutable form, but the 'bounce' it fills in "
     r"@dynamic protocol 'ReadSurface' is @readonly -- drop auto_readonly$"),
    # A @readonly filler of a non-readonly slot.
    "readonly_on_mutable_slot": ("""
class Mat(Surface):
    def __init__(self) -> None:
        pass
    @readonly
    def bounce(self, ray: Ray) -> float:
        return ray.d
""", r"'Mat\.bounce' takes a readonly receiver, but the 'bounce' it fills "
     r"in @dynamic protocol 'Surface' is not readonly -- drop @readonly$"),
    # The same through @pure, which makes the method readonly.
    "pure_on_mutable_slot": ("""
class Mat(Surface):
    def __init__(self) -> None:
        pass
    @pure
    def bounce(self, ray: Ray) -> float:
        return ray.d
""", r"'Mat\.bounce' is readonly as @pure, .* is not readonly -- drop @pure$"),
    # @pure and @readonly both make it readonly: the remedy drops both.
    "pure_and_readonly_on_mutable_slot": ("""
class Mat(Surface):
    def __init__(self) -> None:
        pass
    @pure
    @readonly
    def bounce(self, ray: Ray) -> float:
        return ray.d
""", r"'Mat\.bounce' is readonly as @pure, .* is not readonly -- drop @pure "
     r"and @readonly$"),
    # A subclass override, not the base, takes the wrong form.
    "readonly_subclass_override": ("""
class Mat(Surface):
    def __init__(self) -> None:
        pass
    def bounce(self, ray: Ray) -> float:
        ray.d += 1.0
        return ray.d
class Chrome(Mat):
    @readonly
    def bounce(self, ray: Ray) -> float:
        return ray.d
""", r"'Chrome\.bounce' takes a readonly receiver, but the 'bounce' it "
     r"fills in @dynamic protocol 'Surface' is not readonly"),
    # A subclass override writing self on a @readonly slot.
    "writing_override_on_readonly_slot": ("""
class Mat(ReadSurface):
    k: float
    def __init__(self) -> None:
        self.k = 1.0
    @readonly
    def bounce(self) -> float:
        return self.k
class Chrome(Mat):
    def bounce(self) -> float:
        self.k += 1.0
        return self.k
""", r"'Chrome\.bounce' writes self, but it fills the @readonly 'bounce' "
     r"of @dynamic protocol 'ReadSurface'"),
    # Ref-qualified self forms are never a slot's form.
    "auto_own_self": ("""
class Mat(Surface):
    k: float
    def __init__(self) -> None:
        self.k = 1.0
    def bounce(self: auto_own[Self], ray: Ray) -> float:
        return self.k
""", r"'Mat\.bounce' takes self as auto_own\[Self\], but the 'bounce' it "
     r"fills in @dynamic protocol 'Surface' takes a plain self"),
    "own_self": ("""
class Mat(Surface):
    k: float
    def __init__(self) -> None:
        self.k = 1.0
    def bounce(self: Own[Self], ray: Ray) -> float:
        return self.k
""", r"'Mat\.bounce' takes self as Own\[Self\], but the 'bounce' it "
     r"fills in @dynamic protocol 'Surface' takes a plain self"),
    # A leaf pair on a mutable slot: its readonly twin dispatches nowhere.
    "leaf_auto_readonly": ("""
class Mat(Surface):
    def __init__(self) -> None:
        pass
    @auto_readonly
    def bounce(self, ray: Ray) -> float:
        return ray.d
""", r"'Mat\.bounce' has a readonly form, but the 'bounce' it fills in "
     r"@dynamic protocol 'Surface' is not readonly -- drop auto_readonly$"),
    # A per-parameter auto_readonly makes the same pair.
    "per_param_auto_readonly": ("""
class Mat(Surface):
    def __init__(self) -> None:
        pass
    def bounce(self, ray: auto_readonly[Ray]) -> float:
        return ray.d
""", r"'Mat\.bounce' has a readonly form, but the 'bounce' it fills in "
     r"@dynamic protocol 'Surface' is not readonly"),
    # A @readonly(False) override of a @readonly slot that does not write
    # self is still not readonly.
    "opt_out_override_on_readonly_slot": ("""
class Mat(ReadSurface):
    k: float
    def __init__(self) -> None:
        self.k = 1.0
    @readonly
    def bounce(self) -> float:
        return self.k
class Chrome(Mat):
    @readonly(False)
    def bounce(self) -> float:
        return 2.0
""", r"'Chrome\.bounce' is not readonly, but it fills the @readonly "
     r"'bounce' of @dynamic protocol 'ReadSurface'"),
    # `self: auto_readonly[Self]` spells the same pair as the decorator.
    "auto_readonly_self": ("""
class Mat(Surface):
    def __init__(self) -> None:
        pass
    def bounce(self: auto_readonly[Self], ray: Ray) -> float:
        return ray.d
""", r"'Mat\.bounce' has a readonly form, but the 'bounce' it fills in "
     r"@dynamic protocol 'Surface' is not readonly -- drop auto_readonly$"),
    # An @overload stub emits its own receiver form: an @auto_readonly stub
    # adds a readonly one the mutable slot does not take.
    "auto_readonly_overload_stub": ("""
class Mat(Both):
    def __init__(self) -> None:
        pass
    @overload
    @auto_readonly
    def shine(self) -> float: ...
    def shine(self) -> float:
        return 1.0
""", r"'Mat\.shine' has a readonly form, but the 'shine' it fills in "
     r"@dynamic protocol 'Both' is not readonly -- drop auto_readonly$"),
    # A literal-only @overload group is emitted from its implementation,
    # so the implementation's @pure is what makes it readonly.
    "literal_only_overload_pure_impl": ("""
@dynamic
class Scale(Protocol):
    def f(self, x: int32) -> int32: ...
class Plain(Scale):
    def __init__(self) -> None:
        pass
    def f(self, x: int32) -> int32:
        return x
class Mat(Plain):
    @overload
    def f(self, x: Literal[1]) -> int32: ...
    @pure
    def f(self, x: int32) -> int32:
        return x
""", r"'Mat\.f' is readonly as @pure, but the 'f' it fills in @dynamic "
     r"protocol 'Scale' is not readonly -- drop @pure$"),
    # A reference-returning dunder gets the readonly/mutable pair whatever
    # is spelled: no decorator to drop.
    "implicit_pair_dunder": ("""
@dynamic
class Bag(Protocol):
    def __getitem__(self, i: int32) -> Ray: ...
class Box(Bag):
    r: Ray
    def __init__(self) -> None:
        self.r = Ray(1.0)
    def __getitem__(self, i: int32) -> Ray:
        return self.r
""", r"'Box\.__getitem__' has a mutable form, but the '__getitem__' it "
     r"fills in @dynamic protocol 'Bag' is @readonly$"),
    # Spelling @auto_readonly on it asks for the pair it gets anyway, so
    # dropping it is no remedy either.
    "auto_readonly_reference_dunder": ("""
@dynamic
class Bag(Protocol):
    def __getitem__(self, i: int32) -> Ray: ...
class Box(Bag):
    r: Ray
    def __init__(self) -> None:
        self.r = Ray(1.0)
    @auto_readonly
    def __getitem__(self, i: int32) -> Ray:
        return self.r
""", r"'Box\.__getitem__' has a mutable form, but the '__getitem__' it "
     r"fills in @dynamic protocol 'Bag' is @readonly$"),
    # A property getter is always a pair: overriding a slot's method, it
    # cannot fill the single slot, and has no decorator to drop.
    "property_getter": ("""
@dynamic
class Holder(Protocol):
    def ray(self) -> Ray: ...
class Mat(Holder):
    r: Ray
    def __init__(self) -> None:
        self.r = Ray(1.0)
    def ray(self) -> Ray:
        return self.r
class Box(Mat):
    @property
    def ray(self) -> Ray:
        return self.r
""", r"'Box\.ray' has a readonly form, but the 'ray' it fills in @dynamic "
     r"protocol 'Holder' is not readonly$"),
    # An implicitly readonly dunder on a slot the protocol opts out of
    # readonly: the filler must opt out too.
    "implicit_readonly_dunder": ("""
@dynamic
class Sized(Protocol):
    @readonly(False)
    def __len__(self) -> int32: ...
class Bag(Sized):
    def __init__(self) -> None:
        pass
    def __len__(self) -> int32:
        return 3
""", r"'Bag\.__len__' is implicitly readonly, but the '__len__' it fills "
     r"in @dynamic protocol 'Sized' is not readonly -- mark it "
     r"@readonly\(False\)$"),
    # Spelled @readonly too: dropping it would leave the dunder readonly,
    # so the remedy is the dunder's own.
    "readonly_spelled_implicit_dunder": ("""
@dynamic
class Sized(Protocol):
    @readonly(False)
    def __len__(self) -> int32: ...
class Bag(Sized):
    def __init__(self) -> None:
        pass
    @readonly
    def __len__(self) -> int32:
        return 3
""", r"'Bag\.__len__' takes a readonly receiver, .* -- mark it "
     r"@readonly\(False\)$"),
    # A frozen class's methods are readonly; no decorator to drop.
    "frozen_on_mutable_slot": ("""
@dataclass(frozen=True)
class Sq(Surface):
    s: float
    def bounce(self, ray: Ray) -> float:
        return self.s
""", r"'Sq\.bounce' is readonly in a frozen class, but the 'bounce' it "
     r"fills in @dynamic protocol 'Surface' is not readonly$"),
    # Frozen outranks a spelled @readonly or @pure: dropping either would
    # leave the method readonly.
    "frozen_readonly": ("""
@dynamic
class Get(Protocol):
    def get(self) -> int32: ...
@dataclass(frozen=True)
class F(Get):
    n: int32
    @readonly
    def get(self) -> int32:
        return self.n
""", r"'F\.get' is readonly in a frozen class, but the 'get' it fills in "
     r"@dynamic protocol 'Get' is not readonly$"),
    "frozen_pure": ("""
@dynamic
class Get(Protocol):
    def get(self) -> int32: ...
@dataclass(frozen=True)
class F(Get):
    n: int32
    @pure
    def get(self) -> int32:
        return self.n
""", r"'F\.get' is readonly in a frozen class, but the 'get' it fills in "
     r"@dynamic protocol 'Get' is not readonly$"),
    # @pure keeps the method readonly once auto_readonly is dropped: the
    # remedy drops both.
    "pure_pair": ("""
class Mat(Both):
    def __init__(self) -> None:
        pass
    @pure
    @auto_readonly
    def shine(self) -> float:
        return 1.0
""", r"'Mat\.shine' has a readonly form, but the 'shine' it fills in "
     r"@dynamic protocol 'Both' is not readonly -- drop @pure and "
     r"auto_readonly$"),
    # With auto_readonly dropped, @pure alone still keeps it readonly.
    "pure_pair_remedy": ("""
class Mat(Both):
    def __init__(self) -> None:
        pass
    @pure
    def shine(self) -> float:
        return 1.0
""", r"'Mat\.shine' is readonly as @pure, but the 'shine' it fills in "
     r"@dynamic protocol 'Both' is not readonly -- drop @pure$"),
}

# Every @dynamic declaration of a name agrees, checked at the
# protocol or class that combines them.
CONFLICT_CASES = {
    # A class combining a mutable and a @readonly slot of one name: no
    # filler can give both, so the class is rejected whatever it spells.
    "dual_slot_class": ("""
class Mat(Both, ReadBoth):
    k: float
    def __init__(self) -> None:
        self.k = 1.0
    @auto_readonly
    def shine(self) -> float:
        return self.k
""", r"'Mat' combines @dynamic declarations of 'shine' that disagree in "
     r"readonly-ness: 'Both\.shine\(self\) -> float' and @readonly "
     r"'ReadBoth\.shine\(self\) -> float'" + _ONE_SLOT),
    # The same pair reached through a protocol parent.
    "dual_slot_protocol": ("""
@dynamic
class Surfaces(Both, ReadBoth, Protocol):
    pass
""", r"'Surfaces' combines @dynamic declarations of 'shine' that disagree "
     r"in readonly-ness"),
    # A child protocol redeclaring a parent slot with another parameter
    # type, in either direction.
    "redecl_param_widened": ("""
@dynamic
class Base(Protocol):
    def f(self, x: int32) -> float: ...
@dynamic
class Child(Base, Protocol):
    def f(self, x: float) -> float: ...
""", r"'Child' combines @dynamic declarations of 'f' that disagree in "
     r"parameter types: 'Child\.f\(self, x: float\) -> float' and "
     r"'Base\.f\(self, x: int32\) -> float'"),
    "redecl_param_narrowed": ("""
@dynamic
class Base(Protocol):
    def f(self, x: float) -> float: ...
@dynamic
class Child(Base, Protocol):
    def f(self, x: int32) -> float: ...
""", r"'Child' combines @dynamic declarations of 'f' that disagree in "
     r"parameter types: 'Child\.f\(self, x: int32\) -> float' and "
     r"'Base\.f\(self, x: float\) -> float'"),
    # A child protocol redeclaring a slot with the other receiver form, in
    # either direction, with no class implementing it.
    "redecl_readonly": ("""
@dynamic
class ReadMutable(Surface, Protocol):
    @readonly
    def bounce(self, ray: Ray) -> float: ...
""", r"'ReadMutable' combines @dynamic declarations of 'bounce' that "
     r"disagree in readonly-ness: @readonly 'ReadMutable\.bounce\(self, "
     r"ray: Ray\) -> float' and 'Surface\.bounce\(self, ray: Ray\) -> float'"),
    "redecl_mutable": ("""
@dynamic
class MutRead(ReadSurface, Protocol):
    def bounce(self) -> float: ...
""", r"'MutRead' combines @dynamic declarations of 'bounce' that disagree "
     r"in readonly-ness"),
    # A redeclaration agreeing with one parent still disagrees with the
    # other: the protocol's parents conflict.
    "redecl_both": ("""
@dynamic
class Both2(Both, ReadBoth, Protocol):
    @readonly
    def shine(self) -> float: ...
""", r"'Both2' combines @dynamic declarations of 'shine' that disagree in "
     r"readonly-ness"),
    # Two parents with same-named slots of different signatures.
    "dual_signature": ("""
@dynamic
class A(Protocol):
    def f(self, x: int32) -> int32: ...
@dynamic
class B(Protocol):
    def f(self, x: float) -> float: ...
@dynamic
class C(A, B, Protocol):
    pass
""", r"'C' combines @dynamic declarations of 'f' that disagree in "
     r"parameter types: 'A\.f\(self, x: int32\) -> int32' and "
     r"'B\.f\(self, x: float\) -> float'"),
    # A generic parent's slot is compared at the child's type argument.
    "generic_parent_redecl": ("""
@dynamic
class Base[T](Protocol):
    def f(self, x: T) -> int32: ...
@dynamic
class Child(Base[int32], Protocol):
    def f(self, x: float) -> int32: ...
""", r"'Child' combines @dynamic declarations of 'f' that disagree in "
     r"parameter types: 'Child\.f\(self, x: float\) -> int32' and "
     r"'Base\.f\(self, x: int32\) -> int32'"),
    # A plain protocol declares no slot, but the @dynamic parents it
    # combines still disagree.
    "nondynamic_combiner": ("""
class Surfaces(Both, ReadBoth, Protocol):
    pass
""", r"'Surfaces' combines @dynamic declarations of 'shine' that disagree "
     r"in readonly-ness"),
    # The @dynamic slots reach a plain protocol through plain parents too.
    "plain_bridge": ("""
class Left(Both, Protocol):
    pass
class Right(ReadBoth, Protocol):
    pass
class Surfaces(Left, Right, Protocol):
    pass
""", r"'Surfaces' combines @dynamic declarations of 'shine' that disagree "
     r"in readonly-ness: 'Both\.shine\(self\) -> float' and @readonly "
     r"'ReadBoth\.shine\(self\) -> float'"),
}


@pytest.mark.parametrize("name", list(FORM_CASES))
def test_slot_form_mismatch_is_rejected(name: str) -> None:
    body, message = FORM_CASES[name]
    with pytest.raises(SemanticError, match=message):
        _compile(HEADER + body)


@pytest.mark.parametrize("name", list(CONFLICT_CASES))
def test_slot_conflict_is_rejected(name: str) -> None:
    body, message = CONFLICT_CASES[name]
    with pytest.raises(SemanticError, match=message):
        _compile(HEADER + body)


def test_slot_conflict_is_reported_at_the_combiner() -> None:
    body = """
@dynamic
class Child(Surface, Protocol):
    @readonly
    def bounce(self, ray: Ray) -> float: ...
"""
    with pytest.raises(SemanticError) as err:
        _compile(HEADER + body)
    assert err.value.loc is not None
    assert err.value.loc.line == HEADER.count("\n") + 3


def test_slot_conflict_with_import_cycle_parent(tmp_path: Path) -> None:
    # The parent protocol is a cycle peer, a skeleton while the child's
    # module registers: the check runs once the declarations settle.
    (tmp_path / "cyc_a.py").write_text(
        "from typing import Protocol\n"
        "from tpy import dynamic, readonly, int32\n"
        "from cyc_b import Base\n"
        "@dynamic\n"
        "class Child(Base, Protocol):\n"
        "    @readonly\n"
        "    def f(self) -> int32: ...\n"
        "def touch() -> int32: return 1\n")
    (tmp_path / "cyc_b.py").write_text(
        "from typing import Protocol\n"
        "from tpy import dynamic, int32\n"
        "from cyc_a import touch\n"
        "@dynamic\n"
        "class Base(Protocol):\n"
        "    def f(self) -> int32: ...\n")
    with pytest.raises(SemanticError,
                       match=r"'Child' combines @dynamic declarations of 'f' "
                             r"that disagree in readonly-ness"):
        _compile("from cyc_a import Child\nprint(1)\n",
                 extra_lib_dirs=[tmp_path])


def test_readonly_slot_takes_no_converting_mutable_method() -> None:
    # A @readonly slot needs a readonly method on the return-conversion
    # path too, as on the plain one.
    with pytest.raises(SemanticError,
                       match=r"Type C does not conform to protocol Read"):
        _compile(HEADER + """
@dynamic
class Read(Protocol):
    @readonly
    def f(self) -> str: ...
class C:
    def __init__(self) -> None:
        pass
    @readonly(False)
    def f(self) -> String:
        return String('ok')
def use(p: Read) -> str:
    return p.f()
def main() -> None:
    c = C()
    print(use(c))
""")


def test_matching_forms_compile() -> None:
    _compile(HEADER + """
class Mat(Surface):
    def __init__(self) -> None:
        pass
    def bounce(self, ray: Ray) -> float:
        return ray.d
class Glass(ReadSurface):
    def __init__(self) -> None:
        pass
    @readonly
    def bounce(self) -> float:
        return 1.0
class Pane(Glass):
    def bounce(self) -> float:
        return 2.0
""")


def test_matching_overload_stubs_compile() -> None:
    _compile(HEADER + """
class Mat(Surface):
    def __init__(self) -> None:
        pass
    @overload
    def bounce(self, ray: Ray) -> float: ...
    def bounce(self, ray: Ray) -> float:
        return ray.d
""")


def test_readonly_overload_copy_refused_at_mutable_slot() -> None:
    # An @overload stub's copy is an ordinary member, so its @readonly
    # is refused at a mutable slot as a plain @readonly method's is.
    with pytest.raises(SemanticError,
                       match=r"'Mat\.f' takes a readonly receiver"):
        _compile(HEADER + """
@dynamic
class Scale(Protocol):
    def f(self, x: int32) -> int32: ...
class Plain(Scale):
    def __init__(self) -> None:
        pass
    def f(self, x: int32) -> int32:
        return x
class Mat(Plain):
    @overload
    @readonly
    def f(self, x: Literal[1]) -> int32: ...
    def f(self, x: int32) -> int32:
        return x
""")


def test_generic_parent_redeclaration_at_its_argument_compiles() -> None:
    _compile(HEADER + """
@dynamic
class Base[T](Protocol):
    def f(self, x: T) -> int32: ...
@dynamic
class Child(Base[int32], Protocol):
    def f(self, x: int32) -> int32: ...
""")


def _plain_fold_visits(depth: int) -> int:
    """Method-tree walks to build the vtable of every protocol of a chain
    that alternates @dynamic and plain protocols."""
    src = HEADER + "@dynamic\nclass P0(Protocol):\n    def f(self) -> int32: ...\n"
    for i in range(1, depth):
        dec = "@dynamic\n" if i % 2 == 0 else ""
        src += f"{dec}class P{i}(P{i - 1}, Protocol):\n    def g{i}(self) -> int32: ...\n"
    compiler, modules = _compile(src)
    entry = next(m for m in modules if m.is_entry_point)
    checker = entry.analyzer.protocols
    checker._protocol_vtables.clear()
    compiler.protocol_caches.clear()
    count = [0]
    original = protocols_mod.protocol_methods

    def counted(*args: object, **kwargs: object) -> object:
        count[0] += 1
        return original(*args, **kwargs)

    protocols_mod.protocol_methods = counted
    try:
        with activate_compiler(compiler):
            for i in range(depth):
                checker.protocol_vtable(
                    entry.analyzer.registry.scan_by_short_name(f"P{i}"))
    finally:
        protocols_mod.protocol_methods = original
    return count[0]


def test_plain_parent_fold_is_linear() -> None:
    # Each protocol's methods are walked once, not once per descendant.
    assert _plain_fold_visits(40) <= 2 * _plain_fold_visits(20) + 2


def test_agreeing_declarations_compile() -> None:
    # A redeclaration with the parent's signature, two parents with the
    # same slot, and an `Own[int32]` return beside an `int32` one are each
    # one slot.
    _compile(HEADER + """
@dynamic
class LoudSurface(Surface, Protocol):
    def bounce(self, ray: Ray) -> float: ...
@dynamic
class Shiny(Protocol):
    def shine(self) -> float: ...
@dynamic
class Bright(Both, Shiny, Protocol):
    pass
@dynamic
class OwnShine(Protocol):
    def count(self) -> Own[int32]: ...
@dynamic
class PlainShine(Protocol):
    def count(self) -> int32: ...
class Mat(LoudSurface, Bright):
    def __init__(self) -> None:
        pass
    def bounce(self, ray: Ray) -> float:
        ray.d += 1.0
        return ray.d
    def shine(self) -> float:
        return 1.0
class Lamp(OwnShine, PlainShine):
    def __init__(self) -> None:
        pass
    def count(self) -> Own[int32]:
        return 1
""")


def test_own_return_of_value_type_does_not_count() -> None:
    # One `Own[...]` at a return is not part of the slot when it wraps a
    # value type: a scalar, `str`, or a class type parameter bounded
    # `ValueType`.
    _compile(HEADER + """
from tpy import ValueType
@dynamic
class Tick(Protocol):
    def get(self) -> int32: ...
@dynamic
class OwnTick(Protocol):
    def get(self) -> Own[int32]: ...
@dynamic
class Ticks(Tick, OwnTick, Protocol):
    pass
@dynamic
class Name(Protocol):
    def name(self) -> str: ...
@dynamic
class OwnName(Protocol):
    def name(self) -> Own[str]: ...
@dynamic
class Names(OwnName, Name, Protocol):
    pass
@dynamic
class Peek[T](Protocol):
    def peek(self) -> T: ...
@dynamic
class OwnPeek[T](Protocol):
    def peek(self) -> Own[T]: ...
class Cell[T: ValueType](Peek[T], OwnPeek[T]):
    x: T
    def __init__(self, x: T) -> None:
        self.x = x
    def peek(self) -> Own[T]:
        return self.x
def main() -> None:
    print(Cell[int32](7).peek())
""")


# `Own[...]` at a return counts when it wraps anything not known to be a
# value type: `Own` selects the by-value virtual over the reference one.
OWN_RETURN_CONFLICTS = {
    "unbounded_type_param": ("""
@dynamic
class Get[T](Protocol):
    def get(self) -> T: ...
@dynamic
class OwnGet[T](Protocol):
    def get(self) -> Own[T]: ...
@dynamic
class Gets[U](Get[U], OwnGet[U], Protocol):
    pass
""", r"'Gets' combines @dynamic declarations of 'get' that disagree in "
     r"return type: 'Get\.get\(self\) -> U' and "
     r"'OwnGet\.get\(self\) -> Own\[U\]'" + _ONE_SLOT),
    # The same pair through a plain combiner.
    "unbounded_type_param_plain_combiner": ("""
@dynamic
class Get[T](Protocol):
    def get(self) -> T: ...
@dynamic
class OwnGet[T](Protocol):
    def get(self) -> Own[T]: ...
class Gets[U](Get[U], OwnGet[U], Protocol):
    pass
""", r"'Gets' combines @dynamic declarations of 'get' that disagree in "
     r"return type"),
    # A reference type; the class filling the combined slot conforms, so the
    # combiner's own conflict is what is reported.
    "reference_type": ("""
@dynamic
class OwnRay(Protocol):
    def f(self) -> Own[Ray]: ...
@dynamic
class PlainRay(Protocol):
    def f(self) -> Ray: ...
@dynamic
class Rays(OwnRay, PlainRay, Protocol):
    pass
class Emitter(Rays):
    def __init__(self) -> None:
        pass
    def f(self) -> Own[Ray]:
        return Ray(5.0)
""", r"'Rays' combines @dynamic declarations of 'f' that disagree in "
     r"return type: 'OwnRay\.f\(self\) -> Own\[Ray\]' and "
     r"'PlainRay\.f\(self\) -> Ray'" + _ONE_SLOT),
    # An unbounded class type parameter is not known to be a value type, so
    # the class combining both declarations is the conflict.
    "unbounded_class_combiner": ("""
@dynamic
class Peek[T](Protocol):
    def peek(self) -> T: ...
@dynamic
class OwnPeek[T](Protocol):
    def peek(self) -> Own[T]: ...
class Cell[T](Peek[T], OwnPeek[T]):
    x: T
    def __init__(self, x: T) -> None:
        self.x = x
    def peek(self) -> Own[T]:
        return self.x
""", r"'Cell' combines @dynamic declarations of 'peek' that disagree in "
     r"return type: 'Peek\.peek\(self\) -> T' and "
     r"'OwnPeek\.peek\(self\) -> Own\[T\]'" + _ONE_SLOT),
}


@pytest.mark.parametrize("name", list(OWN_RETURN_CONFLICTS))
def test_own_return_of_non_value_type_counts(name: str) -> None:
    body, message = OWN_RETURN_CONFLICTS[name]
    with pytest.raises(SemanticError, match=message):
        _compile(HEADER + body)


def test_protocol_value_type_bound_makes_own_return_agree() -> None:
    # A protocol's `T: ValueType` bound rides on its T, so `-> T` and
    # `-> Own[T]` declare one slot.
    _compile(HEADER + """
from tpy import ValueType
@dynamic
class Get[T: ValueType](Protocol):
    def get(self) -> T: ...
@dynamic
class OwnGet[T: ValueType](Protocol):
    def get(self) -> Own[T]: ...
@dynamic
class Gets[U: ValueType](Get[U], OwnGet[U], Protocol):
    pass
""")


def test_own_parameter_still_counts() -> None:
    with pytest.raises(SemanticError,
                       match=r"'Takes' combines @dynamic declarations of "
                             r"'put' that disagree in parameter types"):
        _compile(HEADER + """
@dynamic
class Put(Protocol):
    def put(self, r: Ray) -> None: ...
@dynamic
class OwnPut(Protocol):
    def put(self, r: Own[Ray]) -> None: ...
@dynamic
class Takes(Put, OwnPut, Protocol):
    pass
""")


# A protocol type-parameter bound is resolved as a record's is: a protocol,
# which may name the parameter itself, and nothing else.
PROTOCOL_BOUNDS = {
    "constrained": ("class P[T: (int32, float)](Protocol):",
                    r"constrained type parameters \(`T: \(int32, float\)`\) "
                    r"are not supported"),
    "object": ("class P[T: object](Protocol):", r"Unknown type: object"),
    "union": ("class P[T: int32 | float](Protocol):",
              r"Type parameter bound must be a protocol, got float \| int32"),
    "optional": ("class P[T: int32 | None](Protocol):",
                 r"Type parameter bound must be a protocol, got int32 \| None"),
    "self_referencing": ("class P[T: Addable[T]](Protocol):", None),
}


@pytest.mark.parametrize("name", list(PROTOCOL_BOUNDS))
def test_protocol_type_param_bound(name: str) -> None:
    header, message = PROTOCOL_BOUNDS[name]
    source = HEADER + """
class Addable[T](Protocol):
    def plus(self, o: T) -> int32: ...
""" + header + """
    def count(self) -> int32: ...
class Bag:
    def plus(self, o: Bag) -> int32:
        return 1
    def count(self) -> int32:
        return 4
def use(p: P[Bag]) -> int32:
    return p.count()
"""
    if message is None:
        _compile(source)
        return
    with pytest.raises((SemanticError, ParseError, CompileError), match=message):
        _compile(source)


@pytest.mark.parametrize("generic", [False, True])
def test_relisted_generic_protocol_is_one_slot(generic: bool) -> None:
    # A class re-listing the protocol its generic base implements combines
    # one slot, at the class's type argument (the C++ build of this shape
    # still fails: BUGS.md#dynamic-protocol-reached-twice-ambiguous-base).
    derived = ("class Derived[U](Base[U], P[U]):\n    pass\n" if generic else
               "class Derived(Base[int32], P[int32]):\n"
               "    def get(self) -> int32:\n        return 1\n")
    compiler, modules = _compile(HEADER + """
@dynamic
class P[T](Protocol):
    def get(self) -> T: ...
class Base[T](P[T]):
    v: T
    def __init__(self, v: T) -> None:
        self.v = v
    def get(self) -> T:
        return self.v
""" + derived)
    entry = next(m for m in modules if m.is_entry_point)
    checker = entry.analyzer.protocols
    with activate_compiler(compiler):
        derived_info = entry.analyzer.registry.get_record("Derived")
        assert checker.record_slot_conflict(derived_info) is None
        vtable = checker.record_vtable(derived_info)
    assert list(vtable) == ["get"]
    assert str(vtable["get"].sig.return_type) == ("U" if generic else "int32")


def test_combined_slot_does_not_depend_on_parent_order() -> None:
    # Slots agreeing modulo `Own` around a value type at the return merge
    # into the plain side whichever parent comes first: `Own` changes
    # nothing there.
    compiler, modules = _compile(HEADER + """
@dynamic
class A(Protocol):
    def f(self) -> Own[str]: ...
@dynamic
class B(Protocol):
    def f(self) -> str: ...
@dynamic
class AB(A, B, Protocol):
    pass
@dynamic
class BA(B, A, Protocol):
    pass
""")
    entry = next(m for m in modules if m.is_entry_point)
    checker = entry.analyzer.protocols
    with activate_compiler(compiler):
        slots = [checker.protocol_vtable(
                     entry.analyzer.registry.scan_by_short_name(name))["f"]
                 for name in ("AB", "BA")]
    assert all(slot.combined for slot in slots)
    assert slots[0].readonly == slots[1].readonly
    assert [str(slot.sig.return_type) for slot in slots] == ["str", "str"]


def test_record_combining_value_slots_does_not_depend_on_parent_order() -> None:
    # A class combining `-> T` and `-> Own[T]` directly, `T: ValueType`,
    # merges them into the same plain slot in either parent order.
    compiler, modules = _compile(HEADER + """
from tpy import ValueType
@dynamic
class Peek[T](Protocol):
    def peek(self) -> T: ...
@dynamic
class OwnPeek[T](Protocol):
    def peek(self) -> Own[T]: ...
class PlainFirst[T: ValueType](Peek[T], OwnPeek[T]):
    x: T
    def __init__(self, x: T) -> None:
        self.x = x
    def peek(self) -> Own[T]:
        return self.x
class OwnFirst[T: ValueType](OwnPeek[T], Peek[T]):
    x: T
    def __init__(self, x: T) -> None:
        self.x = x
    def peek(self) -> Own[T]:
        return self.x
""")
    entry = next(m for m in modules if m.is_entry_point)
    checker = entry.analyzer.protocols
    with activate_compiler(compiler):
        slots = []
        for name in ("PlainFirst", "OwnFirst"):
            info = entry.analyzer.registry.get_record(name)
            assert checker.record_slot_conflict(info) is None
            slots.append(checker.record_vtable(info)["peek"])
    assert [str(slot.sig.return_type) for slot in slots] == ["T", "T"]
    assert slots[0].readonly == slots[1].readonly


_OWN_STR_PARENTS = """
@dynamic
class A(Protocol):
    def f(self) -> Own[str]: ...
@dynamic
class B(Protocol):
    def f(self) -> str: ...
"""


@pytest.mark.parametrize("order", ["A, B", "B, A"])
def test_plain_filler_of_combined_value_slot_accepted(order: str) -> None:
    # A combiner with no members of its own conforms as its parents do;
    # `A.f -> Own[str]` takes a `-> str` method, a returned value type
    # being the caller's either way.
    _compile(HEADER + _OWN_STR_PARENTS + f"""
@dynamic
class Comb({order}, Protocol):
    pass
class Z:
    def __init__(self) -> None:
        pass
    def f(self) -> str:
        return "z"
def use(x: Comb) -> str:
    return x.f()
def main() -> None:
    print(use(Z()))
""")


def test_view_of_plain_first_combined_slot_rejected() -> None:
    # Through a combiner whose first parent declares `-> str`, a StrView of
    # the call result is a temporary's.
    with pytest.raises(SemanticError,
                       match=r"Cannot return StrView referencing a local or "
                             r"temporary"):
        _compile(HEADER + _OWN_STR_PARENTS + """
from tpy import StrView
@dynamic
class BA(B, A, Protocol):
    pass
class X(BA):
    def __init__(self) -> None:
        pass
    def f(self) -> Own[str]:
        return "x" * 40
def vba(b: BA) -> StrView:
    return b.f()
def main() -> None:
    print(vba(X()))
""")


def test_view_of_own_first_combined_slot_rejected() -> None:
    # The `-> Own[str]`-first order: the call's `Own[str]` result is a
    # temporary too.
    with pytest.raises(SemanticError,
                       match=r"Cannot return StrView referencing a local or "
                             r"temporary"):
        _compile(HEADER + _OWN_STR_PARENTS + """
from tpy import StrView
@dynamic
class AB(A, B, Protocol):
    pass
class X(AB):
    def __init__(self) -> None:
        pass
    def f(self) -> Own[str]:
        return "x" * 40
def vab(b: AB) -> StrView:
    return b.f()
def main() -> None:
    print(vab(X()))
""")


_OWN_STR_TWINS = """
from tpy import StrView
@dynamic
class A(Protocol):
    def f(self) -> Own[str]: ...
@dynamic
class B(Protocol):
    def f(self) -> Own[str]: ...
class C:
    def f(self) -> Own[str]:
        return "x" * 40
"""


@pytest.mark.parametrize("protocols", [
    # A plain combiner redeclaring the combined slot.
    """
class AB(A, B, Protocol):
    def f(self) -> str: ...
""",
    # A plain protocol inheriting a plain middle protocol's redeclaration.
    """
class M(A, B, Protocol):
    def f(self) -> str: ...
class AB(M, Protocol):
    pass
"""], ids=["own", "inherited"])
def test_view_of_redeclared_combined_slot_rejected(protocols: str) -> None:
    # A redeclaration over a combined slot keeps its own return type, so
    # its `-> str` result is a temporary's, not the merged `Own[str]`.
    with pytest.raises(SemanticError,
                       match=r"Cannot return StrView referencing a local or "
                             r"temporary"):
        _compile(HEADER + _OWN_STR_TWINS + protocols + """
def view(p: AB) -> StrView:
    return p.f()
def main() -> None:
    print(view(C()))
""")


def test_redeclared_combined_slot_return_refused() -> None:
    # A plain combiner's `-> float` over two `-> int32` slots would run
    # through their one `int32` virtual, so the redeclaration is held to
    # the slot.
    with pytest.raises(SemanticError,
                       match=r"'AB' combines @dynamic declarations of 'f' that "
                             r"disagree in return type: .*" + _ONE_SLOT):
        _compile(HEADER + """
@dynamic
class A(Protocol):
    def f(self) -> int32: ...
@dynamic
class B(Protocol):
    def f(self) -> int32: ...
class AB(A, B, Protocol):
    def f(self) -> float: ...
class C:
    def f(self) -> float:
        return 1.5
def use(p: AB) -> float:
    return p.f()
def main() -> None:
    print(use(C()))
""")


def test_keywords_through_combined_slot_bind_the_first_names() -> None:
    # The parents name the parameters in opposite orders; the first
    # parent's declaration is the one a call sees, whole: a call through
    # the combiner binds keywords by its names, as CPython binds them by
    # the first base's method.
    compiler, modules = _compile(HEADER + """
@dynamic
class A(Protocol):
    def f(self, y: int32, x: int32) -> Own[int32]: ...
@dynamic
class B(Protocol):
    def f(self, x: int32, y: int32) -> int32: ...
@dynamic
class AB(A, B, Protocol):
    pass
class X(AB):
    def __init__(self) -> None:
        pass
    def f(self, y: int32, x: int32) -> Own[int32]:
        return 10 * y + x
def use(p: AB) -> int32:
    return p.f(x=1, y=2)
def main() -> None:
    print(use(X()))
""")
    entry = _entry(modules)
    with activate_compiler(compiler):
        [f] = entry.analyzer.protocols.collect_protocol_methods(
            entry.analyzer.registry.scan_by_short_name("AB"))
    assert [n for n, _ in f.params if n != "self"] == ["y", "x"]
    assert str(f.return_type) == "Own[int32]"
    assert "return p.f(2, 1);" in compiler.generate_module_sources(entry).cpp


def test_keywords_through_differently_named_own_str_parents() -> None:
    # As above with `-> Own[str]` / `-> str`: the first parent's names bind
    # through the @dynamic combiner and through a plain one as a bound (21
    # 21 under CPython).
    compiler, modules = _compile(HEADER + """
@dynamic
class A(Protocol):
    def f(self, y: int32, x: int32) -> Own[str]: ...
@dynamic
class B(Protocol):
    def f(self, x: int32, y: int32) -> str: ...
@dynamic
class AB(A, B, Protocol):
    pass
class PB(A, B, Protocol):
    pass
class X(AB):
    def __init__(self) -> None:
        pass
    def f(self, y: int32, x: int32) -> Own[str]:
        return str(10 * y + x)
class S:
    def f(self, y: int32, x: int32) -> Own[str]:
        return str(10 * y + x)
def use(p: AB) -> str:
    return p.f(x=1, y=2)
def use_plain[T: PB](p: T) -> str:
    return p.f(x=1, y=2)
def main() -> None:
    print(use(X()), use_plain(S()))
""")
    entry = _entry(modules)
    registry = entry.analyzer.registry
    checker = entry.analyzer.protocols
    with activate_compiler(compiler):
        for name in ("AB", "PB"):
            [f] = checker.collect_protocol_methods(
                registry.scan_by_short_name(name))
            assert [n for n, _ in f.params if n != "self"] == ["y", "x"]
            assert str(f.return_type) == "Own[str]"
    sources = compiler.generate_module_sources(entry)
    # The generic `use_plain` renders in the header.
    assert (sources.cpp + sources.hpp).count("return p.f(2, 1);") == 2


def test_differing_defaults_keep_the_first_declaration() -> None:
    # The parents differ in a default besides `Own`: the first one is the
    # one a call sees, so `p.f(y=1)` fills A's `x=11` (12 under CPython).
    compiler, modules = _compile(HEADER + """
@dynamic
class A(Protocol):
    def f(self, x: int32 = 11, y: int32 = 0) -> Own[int32]: ...
@dynamic
class B(Protocol):
    def f(self, x: int32 = 22, y: int32 = 0) -> int32: ...
@dynamic
class AB(A, B, Protocol):
    pass
class C(AB):
    def f(self, x: int32 = 11, y: int32 = 0) -> Own[int32]:
        return x + y
def use(p: AB) -> int32:
    return p.f(y=1)
def main() -> None:
    print(use(C()))
""")
    entry = _entry(modules)
    with activate_compiler(compiler):
        [f] = entry.analyzer.protocols.collect_protocol_methods(
            entry.analyzer.registry.scan_by_short_name("AB"))
    assert str(f.return_type) == "Own[int32]"
    assert "return p.f(11, 1);" in compiler.generate_module_sources(entry).cpp


def test_keyword_only_difference_keeps_the_positional_first() -> None:
    # A positional `A.f(x)` first and a keyword-only `B.f(*, x)`: the first
    # is the one a call sees, so the positional call compiles.
    _compile(HEADER + """
@dynamic
class A(Protocol):
    def f(self, x: int32) -> Own[int32]: ...
@dynamic
class B(Protocol):
    def f(self, *, x: int32) -> int32: ...
@dynamic
class AB(A, B, Protocol):
    pass
class C(AB):
    def f(self, x: int32) -> Own[int32]:
        return x
def use(p: AB) -> int32:
    return p.f(11)
def main() -> None:
    print(use(C()))
""")


_VIEW_REJECTED = r"Cannot return StrView referencing a local or temporary"

# A StrView of an `Own[str]` result called through a protocol or a type
# parameter views a temporary, whichever declaration the call sees.
OWN_STR_VIEW_CASES = {
    # A lone @dynamic slot.
    "lone_dynamic": """
@dynamic
class A(Protocol):
    def f(self) -> Own[str]: ...
class X(A):
    def __init__(self) -> None:
        pass
    def f(self) -> Own[str]:
        return "x" * 40
def view(b: A) -> StrView:
    return b.f()
def main() -> None:
    print(view(X()))
""",
    # A lone plain protocol as a type-parameter bound.
    "lone_structural": """
class Q1(Protocol):
    def f(self) -> Own[str]: ...
class X:
    def __init__(self) -> None:
        pass
    def f(self) -> Own[str]:
        return "x" * 40
def view[T: Q1](t: T) -> StrView:
    return t.f()
def main() -> None:
    print(view(X()))
""",
    # An inherited redeclaration with another default, first of the
    # combined parents.
    "inherited_redeclaration": """
@dynamic
class A(Protocol):
    def f(self, n: int32 = 3) -> Own[str]: ...
@dynamic
class A2(A, Protocol):
    def f(self, n: int32 = 4) -> Own[str]: ...
@dynamic
class B(Protocol):
    def f(self, n: int32 = 3) -> str: ...
@dynamic
class AB(A2, B, Protocol):
    pass
class X(AB):
    def __init__(self) -> None:
        pass
    def f(self, n: int32 = 3) -> Own[str]:
        return "x" * 40
def view(p: AB) -> StrView:
    return p.f(3)
def main() -> None:
    print(view(X()))
""",
    # Parents differing in a default besides `Own`, the `Own[str]` first.
    "differing_defaults": """
@dynamic
class A(Protocol):
    def f(self, n: int32 = 3) -> Own[str]: ...
@dynamic
class B(Protocol):
    def f(self, n: int32 = 4) -> str: ...
@dynamic
class AB(A, B, Protocol):
    pass
class X(AB):
    def __init__(self) -> None:
        pass
    def f(self, n: int32 = 3) -> Own[str]:
        return "x" * 40
def view(p: AB) -> StrView:
    return p.f()
def main() -> None:
    print(view(X()))
""",
    # Through a `Ptr` receiver, which reaches the pointee's method.
    "ptr_receiver": """
from tpy import Ptr
@dynamic
class A(Protocol):
    def f(self) -> Own[str]: ...
class X(A):
    def __init__(self) -> None:
        pass
    def f(self) -> Own[str]:
        return "x" * 40
def mk(a: A) -> Ptr[A]:
    return a
def view(a: A) -> StrView:
    q = mk(a)
    return q.f()
def main() -> None:
    print(view(X()))
""",
    # Through a `Box`, which reaches the pointee's method by `__deref__`.
    "box_receiver": """
from tplib.box import Box
@dynamic
class A(Protocol):
    @readonly
    def f(self) -> Own[str]: ...
class X(A):
    def __init__(self) -> None:
        pass
    @readonly
    def f(self) -> Own[str]:
        return "x" * 40
def view(p: readonly[Box[A]]) -> StrView:
    return p.f()
def main() -> None:
    b: Box[A] = Box(X())
    print(view(b))
""",
}


@pytest.mark.parametrize("name", list(OWN_STR_VIEW_CASES))
def test_view_of_protocol_own_result_rejected(name: str) -> None:
    with pytest.raises(SemanticError, match=_VIEW_REJECTED):
        _compile(HEADER + "from tpy import StrView\n" + OWN_STR_VIEW_CASES[name])


def test_owning_use_of_protocol_own_result_compiles() -> None:
    # The inverse: returned as `str`, or bound and then used, the result
    # is the caller's.
    _compile(HEADER + """
@dynamic
class A(Protocol):
    def f(self) -> Own[str]: ...
class Q(Protocol):
    def f(self) -> Own[str]: ...
class X(A):
    def __init__(self) -> None:
        pass
    def f(self) -> Own[str]:
        return "x" * 4
def owned(a: A) -> str:
    return a.f()
def owned_bound[T: Q](t: T) -> str:
    return t.f()
def bound(a: A) -> int32:
    s = a.f()
    return len(s)
def main() -> None:
    x = X()
    print(owned(x), owned_bound(x), bound(x))
""")


@pytest.mark.parametrize("order", ["Q1, Q2", "Q2, Q1"])
def test_view_of_plain_only_combined_method_rejected(order: str) -> None:
    # A plain combiner of two plain protocols, in either order: the call's
    # `-> str` or `-> Own[str]` result is a temporary's.
    with pytest.raises(SemanticError,
                       match=r"Cannot return StrView referencing a local or "
                             r"temporary"):
        _compile(HEADER + f"""
from tpy import StrView
class Q1(Protocol):
    def f(self) -> Own[str]: ...
class Q2(Protocol):
    def f(self) -> str: ...
class P({order}, Protocol):
    pass
class X:
    def __init__(self) -> None:
        pass
    def f(self) -> Own[str]:
        return "x" * 40
def g[T: P](t: T) -> StrView:
    return t.f()
def main() -> None:
    print(g(X()))
""")


def test_readonly_requirement_rejects_inferred_converting_method() -> None:
    # A @readonly requirement takes no undecorated method on the
    # return-conversion path, as on the plain one
    # (BUGS.md#protocol-readonly-method-inferred-conformer-rejected).
    with pytest.raises(SemanticError,
                       match=r"Cannot infer type arguments for 'show'"):
        _compile(HEADER + """
class Named(Protocol):
    @readonly
    def name(self) -> str: ...
class P:
    n: str
    def __init__(self) -> None:
        self.n = "bob"
    def name(self) -> String:
        return String(self.n)
def show[T: Named](x: T) -> None:
    print(x.name())
def main() -> None:
    show(P())
""")


_READONLY_STR_CASE = """
class AB(A, B, Protocol):
    def g(self) -> int32: ...
class C:
    x: int32
    def __init__(self) -> None:
        self.x = 0
    def f(self) -> Own[str]:
        self.x += 1
        return str(self.x)
    def g(self) -> int32:
        return 0
def use[T: AB](p: T) -> str:
    return p.f()
def main() -> None:
    print(use(C()))
"""


def test_first_parents_readonly_requirement_is_kept() -> None:
    # `A.f` @readonly first: the plain combiner keeps its readonly
    # requirement, which the mutating `C.f` does not meet.
    with pytest.raises(SemanticError,
                       match=r"Cannot infer type arguments for 'use'"):
        _compile(HEADER + """
from tpy import readonly
class A(Protocol):
    @readonly
    def f(self) -> Own[str]: ...
class B(Protocol):
    def f(self) -> str: ...
""" + _READONLY_STR_CASE)


def test_later_parents_readonly_requirement_is_refused() -> None:
    # `B.f` @readonly second: the first, mutable `A.f` is the combiner's
    # member, so the combiner is no substitute for `B`.
    with pytest.raises(SemanticError,
                       match=r"Protocol 'AB' does not conform to protocol 'B': "
                             r"method 'f' has wrong signature: expected "
                             r"'@readonly def f\(self\) -> str'"):
        _compile(HEADER + """
from tpy import readonly
class A(Protocol):
    def f(self) -> Own[str]: ...
class B(Protocol):
    @readonly
    def f(self) -> str: ...
""" + _READONLY_STR_CASE)
