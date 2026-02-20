# Box[T] proof-of-concept -- heap-allocated owning container.
# Simplified version of Rust's Box<T>, using UninitHeapStorage as backing.
from tpy import *
from tpy.mem import UninitHeapStorage

# TODO: implement all the feature to make it work

# TODO: other protocols? for bool and copy?
# TODO: for production should use a Ptr[T] inside, with an annotation @nevernone, so compiler can optimize Box|None to a single pointer
class Box[T](Deref[T]):
    _storage: UninitHeapStorage[T]

    def __init__(self, value: Own[T]):
        # TODO: by default construct with a single element?
        # TODO: [T] should not be needed
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

    # TODO: __del__() method
    # TODO: __bool__() method
    # TODO: __copy__() method?
    # TODO: __str__() method?
    # TODO: __eq__() method?
    # TODO: def take() -> Own[T]
    #   Safety after take() or drop() (use-after-move):
    #   1. Now: runtime panic in debug mode (storage tracks alive slots)
    #   2. Near-term: take(self: Own) consumes self, compiler rejects further use: def take(self: Own[Self])?
    #   3. Long-term: full move/borrow tracking (Rust-style borrow checker)


# Int32
b = Box[Int32](42)
print("b.get():", b.get())
b.set(100)
print("b.get():", b.get())
# TODO: should deref here?
# print(b + 1)
b.drop()

# also Int32
b2 = Box(666)
# TODO: should print something like "Box(666)" or maybe "Box[Int32](666)" or "Box(Int32(666))"
print("b2:", b2)
b2c = b2.clone()
b2.drop()
print("b2c:", b2c)
b2c.drop()

# str
# TODO: should this use std::string? (what does owning std::string_view does)
s = Box[str]("hello")
print("s.get():", s.get())
s.set("world")
print("s.get():", s.get())
s.drop()

# TODO: test with real class
# Linked-list node
class Node:
    value: Int32
    # TODO: optimize like in rust -> single pointer instead of std::optional<>
    next: Box[Node] | None

    # def __init__(self, value: Int32):
    #     self.value = value
    #     self.next = None

    # TODO: proper nocopy propagation in Box[T]
    # TODO: Own[T]|None not recognized as moveable
    def __init__(self, value: Int32, next: Own[Node] | None):
        self.value = value
        self.next = Box.from_optional(next)

# TODO: how to make it work
print("Node:", Node(0, None))
# TODO: n = Node(123, Node(666, None))
# print("n:", n)
