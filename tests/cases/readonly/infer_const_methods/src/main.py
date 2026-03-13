# Auto-infer const for methods that never mutate self (no explicit @readonly needed).
# Covers: pure readers, self-delegation chains, inherited protocols, dynamic vtable guard.
from tpy import Int32, dynamic
from typing import Protocol

class Counter:
    count: Int32

    def __init__(self) -> None:
        self.count = 0

    def increment(self) -> None:        # mutates self -- must NOT be const
        self.count += 1

    def increment_twice(self) -> None:  # delegates to self.increment() -- must NOT be const
        self.increment()
        self.increment()

    def get(self) -> Int32:             # only reads -- inferred const
        return self.count

    def is_zero(self) -> bool:          # only reads -- inferred const
        return self.count == 0


class Box:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = []

    def push(self, x: Int32) -> None:  # mutates self -- must NOT be const
        self.items.append(x)

    def push_default(self) -> None:    # calls self.push() -- must NOT be const
        self.push(0)

    def size(self) -> Int32:           # only reads -- inferred const
        return len(self.items)


# self.field.method() -- calling a non-readonly method on a field IS self-mutation.
# The receiver is self.items (a field access), not self directly, so mutation is
# recorded immediately rather than going through the call-edge propagation path.
class SortableBox:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = []

    def fill(self, a: Int32, b: Int32) -> None:   # mutates self.items -- must NOT be const
        self.items.append(a)
        self.items.append(b)

    def sort_items(self) -> None:                  # self.field.method() -- must NOT be const
        self.items.sort()

    def get_first(self) -> Int32:                  # only reads -- inferred const
        return self.items[0]


# @dynamic protocol with a non-dynamic parent: the concrete class that implements
# DynValued must keep value() non-const so it matches the C++ pure virtual signature.
# Without the recursive ancestor walk in _dynamic_proto_requires_nonconst, value()
# would be incorrectly inferred as const, making Valued abstract.
class HasValue(Protocol):
    def value(self) -> Int32: ...

@dynamic
class DynValued(HasValue, Protocol):
    pass

class Valued(DynValued):
    _n: Int32

    def __init__(self, n: Int32) -> None:
        self._n = n

    def value(self) -> Int32:           # must NOT be const (pure virtual override)
        return self._n


def show(v: DynValued) -> None:
    print(v.value())


def main() -> None:
    c = Counter()
    c.increment()
    c.increment_twice()
    print(c.get())
    print(c.is_zero())

    b = Box()
    b.push(1)
    b.push_default()
    print(b.size())

    sb = SortableBox()
    sb.fill(3, 1)
    sb.sort_items()
    print(sb.get_first())

    show(Valued(7))

main()
