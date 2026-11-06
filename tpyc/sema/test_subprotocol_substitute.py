"""A protocol that names parent protocols is a substitute for each of them:
its effective members (Python's MRO) and fields are held to each parent's
at the arguments it names, by the member rule a class is held to."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..compilation_context import activate_compiler
from ..diagnostics import SemanticError
from ..thir.testutil import _compile, _entry

HEADER = """\
from typing import Protocol, Self
from tpy import dynamic, readonly, Own, ValueType, ReferenceType, Default, int32

class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Dog(Animal):
    def __init__(self, name: str) -> None:
        super().__init__(name)
"""

_NOT_A_SUBSTITUTE = r"Protocol '{child}' does not conform to protocol '{parent}': "

# A declaration that is no substitute for a parent, with the message.
REFUSED = {
    "drops_readonly": ("""
class Reads(Protocol):
    @readonly
    def width(self) -> int32: ...
class Writes(Reads, Protocol):
    def width(self) -> int32: ...
""", _NOT_A_SUBSTITUTE.format(child="Writes", parent="Reads")
     + r"method 'width' has wrong signature: expected '@readonly def "
       r"width\(self\) -> int32', got 'def width\(self\) -> int32'$"),
    # A `-> Self` requirement is met only by `-> Self`: another conformer of
    # the child is no `Self`.
    "self_return_narrowed": ("""
class Base(Protocol):
    def clone(self) -> Self: ...
class Child(Base, Protocol):
    def clone(self) -> "Child": ...
""", _NOT_A_SUBSTITUTE.format(child="Child", parent="Base")
     + r"method 'clone' has wrong signature: expected 'def clone\(self\) "
       r"-> Self', got 'def clone\(self\) -> Child' -- keep '-> Self'$"),
    "field_conflict": ("""
class Base(Protocol):
    v: int32
class Child(Base, Protocol):
    v: str
""", _NOT_A_SUBSTITUTE.format(child="Child", parent="Base")
     + r"field 'v: int32' missing or has wrong type$"),
    "narrowed_param": ("""
class Base(Protocol):
    def f(self, x: Animal) -> int32: ...
class Child(Base, Protocol):
    def f(self, x: Dog) -> int32: ...
""", _NOT_A_SUBSTITUTE.format(child="Child", parent="Base")
     + r"method 'f' has wrong signature: expected 'def f\(self, Animal\) "
       r"-> int32', got 'def f\(self, Dog\) -> int32'$"),
    # A generic parent is checked at the argument the child names.
    "generic_parent_at_its_arg": ("""
class Base[T](Protocol):
    def get(self) -> T: ...
class Child(Base[int32], Protocol):
    def get(self) -> str: ...
""", _NOT_A_SUBSTITUTE.format(child="Child", parent=r"Base\[int32\]")
     + r"method 'get' has wrong signature"),
    "dropped_default": ("""
class Base(Protocol):
    def f(self, x: int32 = 7) -> int32: ...
class Child(Base, Protocol):
    def f(self, x: int32) -> int32: ...
""", _NOT_A_SUBSTITUTE.format(child="Child", parent="Base")
     + r"method 'f' has wrong signature: expected 'def f\(self, int32 = \.\.\.\) "
       r"-> int32', got 'def f\(self, int32\) -> int32' -- the default of "
       r"parameter 'x' is required by 'Base'$"),
    # Keyword-only parameters reordered: a call through the parent passes
    # them in its order, by position.
    "keyword_only_reordered": ("""
class Base(Protocol):
    def f(self, *, x: int32, y: float) -> float: ...
class Child(Base, Protocol):
    def f(self, *, y: float, x: int32) -> float: ...
""", _NOT_A_SUBSTITUTE.format(child="Child", parent="Base")
     + r"method 'f' has wrong signature"),
    # A `__next__` needing an argument is no `__next__` a loop can call.
    "next_with_required_param": ("""
from typing import Iterator
class Child(Iterator[int32], Protocol):
    def __next__(self, x: int32) -> int32: ...
""", _NOT_A_SUBSTITUTE.format(child="Child", parent=r"Iterator\[int32\]")
     + r"method '__next__' has wrong signature"),
    # A positional parameter made keyword-only: `b.f(3)` would not bind.
    "positional_made_keyword_only": ("""
class Base(Protocol):
    def f(self, x: int32) -> int32: ...
class Child(Base, Protocol):
    def f(self, *, x: int32) -> int32: ...
""", _NOT_A_SUBSTITUTE.format(child="Child", parent="Base")
     + r"method 'f' has wrong signature: expected 'def f\(self, int32\) -> "
       r"int32', got 'def f\(self, \*, x: int32\) -> int32'$"),
    # A new required keyword-only parameter no call through the parent passes.
    "added_required_keyword_only": ("""
class Base(Protocol):
    def f(self, x: int32) -> int32: ...
class Child(Base, Protocol):
    def f(self, x: int32, *, y: int32) -> int32: ...
""", _NOT_A_SUBSTITUTE.format(child="Child", parent="Base")
     + r"method 'f' has wrong signature"),
    # A protocol-bound requirement `take(x: T)` with `T: HasN` is not met by
    # one conformer's type: another `HasN` would not bind.
    "protocol_bound_param_narrowed": ("""
class HasN(Protocol):
    def n(self) -> int32: ...
class A:
    def __init__(self) -> None:
        pass
    def n(self) -> int32:
        return 1
class Base[T: HasN](Protocol):
    def take(self, x: T) -> int32: ...
class Child[T: HasN](Base[T], Protocol):
    def take(self, x: A) -> int32: ...
""", _NOT_A_SUBSTITUTE.format(child="Child", parent=r"Base\[T\]")
     + r"method 'take' has wrong signature"),
    # A keyword-only parameter renamed: `b.f(x=3)` would not bind.
    "keyword_only_renamed": ("""
class Base(Protocol):
    def f(self, *, x: int32) -> int32: ...
class Child(Base, Protocol):
    def f(self, *, y: int32) -> int32: ...
""", _NOT_A_SUBSTITUTE.format(child="Child", parent="Base")
     + r"method 'f' has wrong signature"),
    # No C3 order exists: CPython refuses the class.
    "inconsistent_mro": ("""
class A(Protocol):
    def f(self) -> int32: ...
class B(Protocol):
    def f(self) -> int32: ...
class X(A, B, Protocol):
    pass
class Y(B, A, Protocol):
    pass
class Z(X, Y, Protocol):
    pass
""", r"Inconsistent base-class ordering in 'Z'"),
    # A plain child whose first, plain parent's method shadows a @dynamic
    # parent's slot of the same name.
    "plain_parent_ahead_of_dynamic_slot_under_plain_child": ("""
class Plain(Protocol):
    def f(self, x: Animal) -> int32: ...
@dynamic
class Dyn(Protocol):
    def f(self, x: Dog) -> int32: ...
class Leaf(Plain, Dyn, Protocol):
    pass
""", r"'Leaf' combines @dynamic declarations of 'f' that disagree in "
     r"parameter types"),
    # A plain child's redeclaration of an inherited @dynamic slot runs
    # through that slot's virtual, so the slot rule holds it.
    "readonly_over_dynamic_slot_under_plain_child": ("""
@dynamic
class Base(Protocol):
    def f(self) -> int32: ...
class Child(Base, Protocol):
    @readonly
    def f(self) -> int32: ...
""", r"'Child' combines @dynamic declarations of 'f' that disagree in "
     r"readonly-ness"),
    # A plain parent's method folded into a @dynamic child beside a
    # @dynamic parent's slot of the same name.
    "plain_parent_folded_beside_dynamic_slot": ("""
class PlainParent(Protocol):
    def f(self, x: Animal) -> int32: ...
@dynamic
class DynBase(Protocol):
    def f(self, x: Dog) -> int32: ...
@dynamic
class Leaf(PlainParent, DynBase, Protocol):
    pass
""", r"'Leaf' combines @dynamic declarations of 'f' that disagree in "
     r"parameter types"),
}


@pytest.mark.parametrize("case", REFUSED.values(), ids=REFUSED.keys())
def test_declaration_refused(case: tuple[str, str]) -> None:
    protocols, message = case
    with pytest.raises(SemanticError, match=message):
        _compile(HEADER + protocols + "print(1)\n")


# Declarations that substitute for each parent.
ACCEPTED = {
    "equal_redeclaration": """
class Base(Protocol):
    def get(self) -> int32: ...
class Child(Base, Protocol):
    def get(self) -> int32: ...
""",
    "widened_param": """
class Base(Protocol):
    def f(self, x: Dog) -> int32: ...
class Child(Base, Protocol):
    def f(self, x: Animal) -> int32: ...
""",
    "added_readonly": """
class Base(Protocol):
    def f(self) -> int32: ...
class Child(Base, Protocol):
    @readonly
    def f(self) -> int32: ...
""",
    "extra_param_with_default": """
class Base(Protocol):
    def f(self, x: int32) -> int32: ...
class Child(Base, Protocol):
    def f(self, x: int32, y: int32 = 5) -> int32: ...
""",
    "self_kept": """
class Base(Protocol):
    def clone(self) -> Self: ...
class Child(Base, Protocol):
    def clone(self) -> Self: ...
""",
    # Own[...] around a returned value type asks nothing of the member.
    "value_return_modulo_own": """
class Base(Protocol):
    def name(self) -> Own[str]: ...
class Child(Base, Protocol):
    def name(self) -> str: ...
""",
    # A plain parent and a @dynamic one agreeing on `f`.
    "plain_and_dynamic_parents_agree": """
class Plain(Protocol):
    def f(self, x: Dog) -> int32: ...
@dynamic
class Dyn(Protocol):
    def f(self, x: Dog) -> int32: ...
class Leaf(Plain, Dyn, Protocol):
    pass
""",
    # A parameter at a bounded type parameter may widen to the bound.
    "protocol_bound_param_widened": """
class HasN(Protocol):
    def n(self) -> int32: ...
class Base[T: HasN](Protocol):
    def take(self, x: T) -> int32: ...
class Child[T: HasN](Base[T], Protocol):
    def take(self, x: HasN) -> int32: ...
""",
    # A return at a bounded type parameter narrows to the parent's bound.
    "protocol_bound_return_narrowed": """
class HasN(Protocol):
    def n(self) -> int32: ...
class Base(Protocol):
    def get(self) -> HasN: ...
class Child[T: HasN](Base, Protocol):
    def get(self) -> T: ...
""",
    "keyword_only_kept": """
class Base(Protocol):
    def f(self, *, x: int32) -> int32: ...
class Child(Base, Protocol):
    def f(self, *, x: int32, y: int32 = 0) -> int32: ...
""",
    # A keyword-only requirement is met by a positional-or-keyword one.
    "keyword_only_met_positionally": """
class Base(Protocol):
    def f(self, *, x: int32) -> int32: ...
class Child(Base, Protocol):
    def f(self, x: int32) -> int32: ...
""",
    "generic_field_at_its_arg": """
class Base[T](Protocol):
    v: T
class Child(Base[int32], Protocol):
    v: int32
class Gen[U](Base[U], Protocol):
    v: U
""",
    # Marker and concrete-type predicates are inherited, not checked.
    "marker_parents": """
class Value(ValueType, Protocol):
    pass
class Reference(ReferenceType, Protocol):
    pass
class Construct(Default, Protocol):
    pass
""",
}


@pytest.mark.parametrize("protocols", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_declaration_accepted(protocols: str) -> None:
    _compile(HEADER + protocols + "print(1)\n")


def test_diamond_member_is_the_mro_ones() -> None:
    # Python resolves `Diamond.f` to `Right.f` (MRO Diamond, Left, Right,
    # Root), which is @readonly and so meets both parents.
    compiler, modules = _compile(HEADER + """
class Root(Protocol):
    def f(self) -> int32: ...
class Left(Root, Protocol):
    pass
class Right(Root, Protocol):
    @readonly
    def f(self) -> int32: ...
class Diamond(Left, Right, Protocol):
    pass
print(1)
""")
    entry = _entry(modules)
    with activate_compiler(compiler):
        info = entry.analyzer.registry.scan_by_short_name("Diamond")
        [f] = entry.analyzer.protocols.protocol_method_list(info)
    assert f.is_readonly


def test_two_hop_chain_is_its_ancestor_only_at_its_args() -> None:
    with pytest.raises(SemanticError,
                       match=r"Type Leaf\[int32\] does not conform to protocol "
                             r"Base\[str\]"):
        _compile(HEADER + """
class Base[T](Protocol):
    def get(self) -> T: ...
class Mid[U](Base[U], Protocol):
    pass
class Leaf[V](Mid[V], Protocol):
    pass
def use(b: Base[str]) -> str:
    return b.get()
def fwd(x: Leaf[int32]) -> str:
    return use(x)
print(1)
""")


def test_refused_with_import_cycle_parent(tmp_path: Path) -> None:
    # The parent protocol is a cycle peer, a skeleton while the child's
    # module registers: the check runs once the declarations settle.
    (tmp_path / "cyc_a.py").write_text(
        "from typing import Protocol\n"
        "from tpy import int32\n"
        "from cyc_b import Base\n"
        "class Child(Base, Protocol):\n"
        "    def f(self) -> str: ...\n"
        "def touch() -> int32: return 1\n")
    (tmp_path / "cyc_b.py").write_text(
        "from typing import Protocol\n"
        "from tpy import int32\n"
        "from cyc_a import touch\n"
        "class Base(Protocol):\n"
        "    def f(self) -> int32: ...\n")
    with pytest.raises(SemanticError,
                       match=_NOT_A_SUBSTITUTE.format(child="Child", parent="Base")
                       + r"method 'f' has wrong signature"):
        _compile("from cyc_a import Child\nprint(1)\n",
                 extra_lib_dirs=[tmp_path])


def test_class_dropping_a_default_refused() -> None:
    # The call-shape rule is the class's too: `Impl.f(x)` cannot take the
    # `b.f()` a `Base` admits.
    with pytest.raises(SemanticError,
                       match=r"Class 'Impl' does not conform to protocol 'Base': "
                             r"method 'f' has wrong signature: .* -- the default "
                             r"of parameter 'x' is required by 'Base'$"):
        _compile(HEADER + """
class Base(Protocol):
    def f(self, x: int32 = 7) -> int32: ...
class Impl(Base):
    def __init__(self) -> None:
        pass
    def f(self, x: int32) -> int32:
        return x
print(1)
""")


@pytest.mark.parametrize("source", [
    # `Own[Self]` at a value type's return is plain `Self`.
    """
@dataclass
class Meters(ValueType):
    n: int32
    def __add__(self, other: "Meters") -> "Meters":
        return Meters(self.n + other.n)
class Addable(Protocol):
    def __add__(self, other: Self) -> Own[Self]: ...
def twice(x: Addable) -> None:
    y = x + x
twice(Meters(2))
""",
    """
class OwnRise[U](Protocol):
    def rise(self) -> Own[U]: ...
class Tide(OwnRise[int32]):
    def __init__(self) -> None:
        pass
    def rise(self) -> int32:
        return 1
def via(r: OwnRise[int32]) -> int32:
    return r.rise()
print(via(Tide()))
"""], ids=["meters_own_self", "tide_own_rise"])
def test_class_value_return_modulo_own(source: str) -> None:
    _compile("from dataclasses import dataclass\n" + HEADER + source)


_CLASS_SHAPE_BASE = """
from typing import TypedDict, Unpack
class Extras(TypedDict, total=False):
    z: int32
class Base(Protocol):
    def f(self, x: int32) -> int32: ...
class KwBase(Protocol):
    def g(self, *, x: int32, y: int32) -> int32: ...
class OneKw(Protocol):
    def g(self, *, x: int32) -> int32: ...
"""


@pytest.mark.parametrize("member", [
    "class Impl(Base):\n    def f(self, *, x: int32) -> int32:\n        return x\n",
    "class Impl(Base):\n    def f(self, x: int32, *, y: int32) -> int32:\n        return x\n",
    "class Impl(KwBase):\n    def g(self, *, x: int32, z: int32) -> int32:\n        return x\n",
    "class Impl(KwBase):\n    def g(self, *, y: int32, x: int32) -> int32:\n        return x\n",
    "class Impl(Base):\n    def f(self, *xs: int32) -> int32:\n        return 7\n",
    # A call through the protocol packs nothing into a trailing `*args` /
    # `**kwargs`, so a member taking one meets no requirement.
    "class Impl(Base):\n    def f(self, x: int32, *xs: int32) -> int32:\n        return x\n",
    "class Impl(Base):\n    def f(self, x: int32, **xs: Unpack[Extras]) -> int32:\n        return x\n",
    "class Impl(OneKw):\n    def g(self, **xs: Unpack[Extras]) -> int32:\n        return 1\n",
    # A positional-only member cannot take the keyword `x=`.
    "class Impl(OneKw):\n    def g(self, x: int32, /) -> int32:\n        return x\n",
], ids=["positional_made_keyword_only", "added_required_keyword_only",
        "keyword_only_renamed", "keyword_only_reordered", "varargs",
        "trailing_varargs", "trailing_kwargs", "kwargs_for_keyword_only",
        "positional_only_for_keyword_only"])
def test_class_call_shape_refused(member: str) -> None:
    with pytest.raises(SemanticError,
                       match=r"Class 'Impl' does not conform to protocol "
                             r"'(KwBase|Base|OneKw)': method '[fg]' has wrong signature"):
        _compile(HEADER + _CLASS_SHAPE_BASE + member + "print(1)\n")


def test_forward_through_import_cycle_parent(tmp_path: Path) -> None:
    # `Impl(Child)`'s declaration check asks `Child`'s ancestry while the
    # cycle peer's `Mid` is still a skeleton with no parents; the forward
    # to `Root`, known only once `Mid` completes, must not read that
    # answer.
    (tmp_path / "cyc_a.py").write_text(
        "from typing import Protocol\n"
        "from tpy import int32\n"
        "from cyc_b import Mid\n"
        "class Child(Mid, Protocol):\n"
        "    pass\n"
        "class Impl(Child):\n"
        "    def __init__(self) -> None:\n"
        "        pass\n"
        "    def f(self) -> int32:\n"
        "        return 3\n"
        "def touch() -> int32: return 1\n")
    (tmp_path / "cyc_b.py").write_text(
        "from typing import Protocol\n"
        "from tpy import int32\n"
        "from cyc_a import touch\n"
        "class Root(Protocol):\n"
        "    def f(self) -> int32: ...\n"
        "class Mid(Root, Protocol):\n"
        "    pass\n")
    _compile("from tpy import int32\n"
             "from cyc_a import Child, Impl\n"
             "from cyc_b import Root\n"
             "def use(r: Root) -> int32:\n"
             "    return r.f()\n"
             "def fwd(c: Child) -> int32:\n"
             "    return use(c)\n"
             "print(fwd(Impl()))\n",
             extra_lib_dirs=[tmp_path])
