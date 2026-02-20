# Box[T] proof-of-concept -- heap-allocated owning container.
# Simplified version of Rust's Box<T>, using UninitHeapStorage as backing.
from tpy import *
from tpy.mem import UninitHeapStorage

# TODO: implement all the feature to make it work

# TODO: other protocols? for bool and copy?
class Box[T](Deref[T]):
    _storage: UninitHeapStorage[T]

    def __init__(self, value: Own[T]):
        # TODO: by default construct with a single element?
        self._storage = UninitHeapStorage[T](1)
        self._storage.init0(value)

    def get(self) -> T:
        return self._storage.load0()

    def set(self, value: Own[T]) -> None:
        self._storage.drop0()
        self._storage.init0(value)

    # TODO: see safety issue next to take()
    def drop(self) -> None:
        self._storage.drop0()

    def has_value(self) -> bool:
        # TODO: check if self._storage is allocated
        return False

    def __deref__(self) -> T:
        return self.get()

    # TODO: readonly deref?

    # TODO: __del__() method
    # TODO: __bool__() method
    # TODO: __copy__() method?
    # TODO: __str__() method?
    # TODO: __eq__() method?
    # TODO: def take() -> Own[T]
    #   Safety after take() or drop() (use-after-move):
    #   1. Now: runtime panic in debug mode (storage tracks alive slots)
    #   2. Near-term: take(self: Own) consumes self, compiler rejects further use
    #   3. Long-term: full move/borrow tracking (Rust-style borrow checker)

# Int32
b = Box[Int32](42)
print(b.get())
b.set(100)
print(b.get())
# TODO: should deref here?
# print(b + 1)
b.drop()

# also Int32
# b2 = Box(666)
# TODO: should print something like "Box(666)" or maybe "Box[Int32](666)" or "Box(Int32(666))"
# print(b2)
# b2.drop()

# str
# TODO: should this use std::string? (what does owning std::string_view does)
s = Box[str]("hello")
print(s.get())
s.set("world")
print(s.get())
s.drop()

# TODO: test with real class
# Linked-list node
class Node:
    value: Int32
    # TODO: optimize like in rust -> single pointer instead of std::optional<>
    next: Box[Node] | None

    def __init__(self, value: Int32):
        self.value = value
        self.next = None

    # TODO: how to make it work?
    # TODO: proper nocopy propagation in Box[T]
    # TODO: Own[T]|None not recognized as moveable
    # def __init__(self, value: Int32, next: Own[Node] | None):
    #     self.value = value
    #     if next is not None:
    #         self.next = Box(next)
    #     else:
    #         self.next = None

# TODO: how to make it work
# n = Node(123, None)
