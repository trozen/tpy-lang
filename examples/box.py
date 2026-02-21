# TODO: define proper `# tpy:` annotation
# tpy: default
# Box[T] proof-of-concept -- heap-allocated owning container.
# Simplified version of Rust's Box<T>, using UninitHeapStorage as backing.
from __future__ import annotations
from tpy import *
from tpy.mem import UninitHeapStorage

# TODO: for production should use a Ptr[T] inside, with an annotation @nevernone, so compiler can optimize Box|None to a single pointer
class Box[T](Deref[T]):
    _storage: UninitHeapStorage[T]

    def __init__(self, value: Own[T]):
        self._storage = UninitHeapStorage(1)
        self._storage.init0(value)

    def __del__(self):
        self._storage.drop0()

    def __deref__(self) -> T:
        return self.get()

    def get(self) -> T:
        return self._storage.load0()

    def set(self, value: Own[T]) -> None:
        self._storage.drop0()
        self._storage.init0(value)

    # TODO: def take() -> Own[T]
    #   Safety after take() or drop() (use-after-move):
    #   1. Now: runtime panic in debug mode (storage tracks alive slots)
    #   2. Near-term: take(self: Own) consumes self, compiler rejects further use: def take(self: Own[Self])?
    #   3. Long-term: full move/borrow tracking (Rust-style borrow checker)
    
    # TODO: same issue as take() (self: Own[Self]?)
    # def drop(self) -> None:
    #     self._storage.drop0()

    # not copyable, since it's allocating so we don't want accidental copies
    # TODO: add @nocopy to type to be explicit about it
    def clone(self) -> Own[Box[T]]:
        return Box[T](self.get())

    @staticmethod
    def from_optional(value: Own[T] | None) -> Own[Box[T]] | None:
        if value is not None:
            return Box(value)
        return None

    # TODO: readonly deref?
    # TODO: __str__() method?
    # TODO: __eq__() method?


# Int32
b = Box[Int32](42)
print("b.get():", b.get())
b.set(100)
print("b.get():", b.get())
# TODO: should deref here?
# print(b + 1)

# also Int32
b2 = Box(666)
# TODO: should print something like "Box(666)" or maybe "Box[Int32](666)" or "Box(Int32(666))"
print("b2:", b2)
b2c = b2.clone()
print("b2c:", b2c)

# str
# TODO: should this use std::string? (what does owning std::string_view does)
s = Box[str]("hello")
print("s.get():", s.get())
s.set("world")
print("s.get():", s.get())

class LinkedListNode:
    value: Int32
    # TODO: optimize like in rust -> single pointer instead of std::optional<>
    next: Box[LinkedListNode] | None

    # TODO: proper nocopy propagation in Box[T]
    # TODO: Own[T]|None not recognized as moveable
    # TODO: use Self as return
    def __init__(self, value: Int32, next: Own[LinkedListNode] | None):
        self.value = value
        self.next = Box.from_optional(next)

# TODO: next=None default
print("Node:", LinkedListNode(0, None))
n = LinkedListNode(123, LinkedListNode(666, None))
print("n:", n)
