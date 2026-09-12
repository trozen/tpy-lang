# tpy: ext_module
# Combined-CPython-slot families across an exposed hierarchy: a derived class
# overriding only PART of a slot family must keep the inherited other half --
# __eq__ overridden under a base's explicit custom __ne__ (MRO dispatches !=
# to the base body, not to !derived_eq), one half of __setitem__/__delitem__,
# one half of __add__/__radd__ -- and a comparison dunder must delegate across
# an intermediate ancestor with no comparisons of its own (multi-hop). All
# driver shapes are parity-clean: the .so matches the plain-Python MRO.
from tpy import int64
from tpy.extern import export


@export
class Gate:
    code: str
    _items: dict[str, int64]

    def __init__(self, code: str):
        self.code = code
        self._items = {}

    def __eq__(self, other: "Gate") -> bool:
        return self.code == other.code

    def __ne__(self, other: "Gate") -> bool:
        # deliberately NOT !__eq__ (compares lengths only), so a subclass
        # overriding __eq__ observably still dispatches != to this body
        return len(self.code) != len(other.code)

    def __setitem__(self, key: str, value: int64) -> None:
        self._items[key] = value

    def __delitem__(self, key: str) -> None:
        del self._items[key]

    def __getitem__(self, key: str) -> int64:
        return self._items[key]

    def __add__(self, other: int64) -> int64:
        return int64(len(self.code)) + other

    def __radd__(self, other: int64) -> int64:
        return other * 10 + int64(len(self.code))


@export
class SubGate(Gate):
    def __init__(self, code: str):
        super().__init__(code)

    def __eq__(self, other: "SubGate") -> bool:
        return self.code == other.code

    def __delitem__(self, key: str) -> None:
        del self._items[key]

    def __add__(self, other: int64) -> int64:
        return other + 1000


@export
class Node:
    v: int64

    def __init__(self, v: int64):
        self.v = v

    def __eq__(self, other: "Node") -> bool:
        return self.v == other.v

    def __hash__(self) -> int64:
        return self.v


@export
class MidNode(Node):
    def bump(self) -> None:
        self.v += 1


@export
class LeafNode(MidNode):
    def __lt__(self, other: "LeafNode") -> bool:
        return self.v < other.v


@export
class HashNode(MidNode):
    # own __hash__ with NO own comparisons: PyType_Ready inherits the
    # tp_hash/tp_richcompare pair only when both are unset, so the inherited
    # comparisons must be re-wired explicitly or they'd silently vanish
    def __hash__(self) -> int64:
        return self.v + 100
