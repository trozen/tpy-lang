"""Pins for the inherited-builtin-container receiver row: a user record that
INHERITS a container method (`class MyList(list[Int32])` -> `ml.append(10)`)
classifies into the container family, and its ctor routes past the non-F1
base -- plus the OVERRIDE boundary, which must keep taking the record path.
"""

from __future__ import annotations

from ..compilation_context import activate_compiler
from ..typesys import NominalType
from .lower.checks import _inherited_container_base, _method_recv_family
from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctor,
)

_PRELUDE = "from tpy import Int32, StrView\n"


def _classify(source: str, record: str, method: str):
    """(inherited container base | None, receiver family | None) for
    `record.method` -- the two facts the row is made of."""
    compiler, modules = _compile(source)
    entry = _entry(modules)
    analyzer = entry.analyzer
    t = _record_type(entry, record)
    with activate_compiler(compiler):
        return (_inherited_container_base(t, method, analyzer),
                _method_recv_family(t, analyzer, None, method))


def _record_type(entry, record: str) -> NominalType:
    assert any(r.name == record for r in entry.ast.records), record
    return NominalType(record, _module_qname=f"__main__.{record}")


_LIST_SRC = _PRELUDE + (
    "class MyList(list[Int32]):\n"
    "    tag: str\n"
    "    def __init__(self, tag: str) -> None:\n"
    "        self.tag = tag\n"
    "def main() -> None:\n"
    "    ml = MyList('t')\n"
    "    ml.append(1)\n"          # the inherited member-native render
    "    ml.extend([7, 8])\n"     # a container-literal arg at a threaded slot
    "    print(ml.pop())\n"       # the inherited free-function native form
    "    print(ml.pop(0))\n"      # ... and its arity-variant overload
    "    print(len(ml))\n"
    "main()\n"
)


class TestInheritedContainerRecv:
    def test_inherited_list_methods_route(self):
        _assert_routes_byte_identical(_LIST_SRC)

    def test_inherited_dict_methods_route(self):
        src = _PRELUDE + (
            "class MyDict(dict[StrView, Int32]):\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "def main() -> None:\n"
            "    md = MyDict()\n"
            "    md['a'] = 1\n"
            "    print(md.get('a', 0))\n"
            "    print(len(md))\n"
            "main()\n"
        )
        _assert_routes_byte_identical(src)

    def test_inherited_set_methods_route(self):
        src = _PRELUDE + (
            "class MySet(set[Int32]):\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "def main() -> None:\n"
            "    ms = MySet()\n"
            "    ms.add(3)\n"
            "    ms.add(4)\n"
            "    print(len(ms))\n"
            "main()\n"
        )
        _assert_routes_byte_identical(src)

    def test_inherited_method_classifies_container(self):
        base, fam = _classify(_LIST_SRC, "MyList", "append")
        assert base is not None
        assert fam is not None and fam.stub_recv


class TestOverrideBoundary:
    """A DECLARED method shadowing the inherited one takes the AST's
    user-record arm (`o.append(3)`, target-less literal args), not the
    container render (`p.push_back(3)`) -- so the row must key on the method
    name, not on the receiver type alone."""

    _SRC = _PRELUDE + (
        "class Overrider(list[Int32]):\n"
        "    def __init__(self) -> None:\n"
        "        pass\n"
        "    def append(self, value: Int32) -> None:\n"
        "        print(value)\n"
        "def main() -> None:\n"
        "    o = Overrider()\n"
        "    o.append(9)\n"
        "main()\n"
    )

    def test_override_not_classified_container(self):
        base, fam = _classify(self._SRC, "Overrider", "append")
        assert base is None
        assert fam is None

    def test_override_still_routes_via_record_path(self):
        _assert_routes_byte_identical(self._SRC)

    def test_sibling_inherited_method_on_overriding_record(self):
        """The override is per-METHOD: `Overrider` still inherits `pop`."""
        base, fam = _classify(self._SRC, "Overrider", "pop")
        assert base is not None
        assert fam is not None and fam.stub_recv


class TestContainerBaseCtor:
    def test_plain_container_base_ctor_routes(self):
        assert _lower_ctor(_LIST_SRC, "MyList") is not None

    def test_base_init_ctor_stays_ast(self):
        """`super().__init__([1, 2, 3])` seeds the container base's own
        elements -- it compiles and runs, so this reject is load-bearing:
        the MIL would have to spell the base initializer, which the ctor
        tail cannot render."""
        src = _PRELUDE + (
            "class Seeded(list[Int32]):\n"
            "    tag: str\n"
            "    def __init__(self, tag: str) -> None:\n"
            "        super().__init__([1, 2, 3])\n"
            "        self.tag = tag\n"
            "def main() -> None:\n"
            "    s = Seeded('x')\n"
            "    s.append(4)\n"
            "    print(len(s))\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src), "ctor:ctor.non_f1_base")


class TestAdjacentBasesStayOut:
    """Bases the row does NOT cover. Each routes correctly today; the pins
    exist so a future widening cannot admit one silently."""

    def test_bytearray_base_not_classified_container(self):
        src = _PRELUDE + (
            "class MyBA(bytearray):\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "def main() -> None:\n"
            "    b = MyBA()\n"
            "    print(len(b))\n"
            "main()\n"
        )
        base, _ = _classify(src, "MyBA", "append")
        _assert_rejects_at(_reject_tally(src), "ctor:ctor.non_f1_base")

    def test_generic_record_over_generic_container_routes(self):
        src = _PRELUDE + (
            "class Bag[T](list[T]):\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "def main() -> None:\n"
            "    b = Bag[Int32]()\n"
            "    b.append(5)\n"
            "    print(len(b))\n"
            "main()\n"
        )
        _assert_routes_byte_identical(src)


class TestNamelessClassificationStaysContainerBlind:
    """The chained-result callers ask `_container_method_recv` only "is this a
    container" and pass no method name; a record must never answer yes there,
    or a record-returning stub call would compose as a container receiver."""

    def test_no_method_name_rejects_record(self):
        base, _ = _classify(_LIST_SRC, "MyList", "append")
        assert base is not None
        compiler, modules = _compile(_LIST_SRC)
        entry = _entry(modules)
        t = _record_type(entry, "MyList")
        with activate_compiler(compiler):
            assert _inherited_container_base(t, None, entry.analyzer) is None
