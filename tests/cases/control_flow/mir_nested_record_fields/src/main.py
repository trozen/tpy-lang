# A field alias observes shared writes and keeps its original subobject when
# the parent holder is reseated; readonly aliases still observe other writers.
from tpy import int32, readonly


class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


class Outer:
    inner: Cell

    def __init__(self, value: int32):
        self.inner = Cell(value)

    def touch(self) -> int32:
        # Method aliases must borrow the same field that self subsequently writes.
        saved = self.inner  # tpyc: ok
        self.inner.value = 8
        return saved.value


class Deep:
    outer: Outer

    def __init__(self, value: int32):
        self.outer = Outer(value)


class Observer:
    value: int32

    def __init__(self, outer: Outer):
        # Constructor-local aliases have the same shared-mutation behavior.
        saved = outer.inner  # tpyc: ok
        outer.inner.value = 19
        self.value = saved.value


def capture(a: Outer, b: Outer, flag: bool) -> int32:
    current = a
    # Capture a's inline subobject before current may start pointing at b.
    saved = current.inner  # tpyc: ok
    if flag:
        current = b
    current.inner.value = 11
    return saved.value


def nested(root: Deep) -> int32:
    parent = root.outer
    saved = parent.inner
    # A write through a longer field path must reach the captured subobject.
    root.outer.inner.value = 17  # tpyc: ok
    return saved.value


def shared(outer: readonly[Outer], writer: Outer) -> int32:
    saved = outer.inner
    # Readonly access still sees changes made through a mutable alias.
    writer.inner.value = 13  # tpyc: ok
    return saved.value


def tuples(outer: Outer) -> int32:
    saved = outer.inner
    singleton = (saved,)
    pair = (saved, 1)
    # Both tuple shapes retain the field alias instead of copying the record.
    outer.inner.value = 23  # tpyc: ok
    return singleton[0].value + pair[0].value


def optional(outer: Outer | None) -> int32:
    if outer is None:
        return 0
    # The nested path is evaluated only after the presence guard succeeds.
    return outer.inner.value  # tpyc: ok


def union(outer: Outer | Cell) -> int32:
    if isinstance(outer, Outer):
        return outer.inner.value  # tpyc: ok
    return 0


def main():
    a = Outer(1)
    b = Outer(2)
    deep = Deep(3)
    print("free-function", capture(a, b, True), a.inner.value, b.inner.value)
    print("shared-owner", capture(a, a, True))
    print("nested", nested(deep))
    print("readonly", shared(a, a))
    print("method", a.touch())
    observer = Observer(a)
    print("constructor", observer.value, a.inner.value)
    print("tuple-shapes", tuples(a))
    print("optional", optional(a), optional(None))
    print("union", union(a), union(Cell(0)))


main()
