# Property returning Optional[non-value-type] with getter and setter.
# The `truthy` section is the UNNARROWED boolean context over an Optional
# getter (`if h.num:` / `while` / `assert`). The snapshot shows what it costs:
# `std::optional<int32_t> num() const` returns BY VALUE, and each condition
# renders `::tpy::is_truthy(h.num())` -- one getter call per test, not a bare
# member read. It is not narrowing -- a getter is a call and is not narrowed --
# so the read under it stays Optional; and a truthiness test over an
# Optional[value] warns at the getter spelling exactly as at the field one.
from typing import Optional
from tpy import int32

class Node:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v

class Wrapper:
    _node: Optional[Node]

    def __init__(self) -> None:
        self._node = None

    @property
    def node(self) -> Optional[Node]:
        return self._node

    @node.setter
    def node(self, n: Optional[Node]) -> None:
        self._node = n

class Holder:
    _n: Optional[int32]

    def __init__(self, n: Optional[int32]) -> None:
        self._n = n

    @property
    def num(self) -> Optional[int32]:
        return self._n


# truthiness over an UNNARROWED Optional getter, at the three boolean contexts
def truthy(h: Holder, label: str) -> None:
    if h.num:  # tpyc: warning(/Truthiness check on optional value/)
        print("truthy:", label, "if")
    n = 0
    while h.num:  # tpyc: warning(/Truthiness check on optional value/)
        n += 1
        break
    print("truthy:", label, "while", n)
    if h.num:  # tpyc: warning(/Truthiness check on optional value/)
        assert h.num, "engaged"  # tpyc: warning(/Truthiness check on optional value/)
        print("truthy:", label, "assert")
    # the NEGATIVE leg: `is not None` is the None-only test the warning points
    # at, so it must stay silent -- and it does NOT narrow the read below it,
    # because a getter is a call (TODO.md, "Narrowing over a pure zero-arg
    # getter")
    if h.num is not None:  # tpyc: ok
        print("truthy:", label, "is-not-none")


def main() -> None:
    truthy(Holder(3), "three")
    truthy(Holder(0), "zero")
    truthy(Holder(None), "none")
    w = Wrapper()
    print(w.node is None)
    w.node = Node(42)
    n = w.node
    if n is not None:
        print(n.val)
    w.node = None
    print(w.node is None)

main()
