# Declared borrowing views: a readonly[T] template argument spells `const T`
# only on a borrowing view, and a view rooted in a param or `self` returns.
from tpy import int32, readonly, ValueType
from tpy.extern import native
from tpy.mem import UninitHeapStorage


@native("::Cursor", borrowing_view=True)
class Cursor(ValueType):
    @native("get")
    def get(self) -> int32: ...


@native("::Buffer")
class Buffer:
    def __init__(self) -> None: ...
    @native("cursor")
    def cursor(self) -> Cursor: ...


@native("::Bag")
class Bag[T]:
    def __init__(self) -> None: ...
    @native("empty")
    def empty(self) -> bool: ...


@native("::Window", borrowing_view=True)
class Window[T](ValueType):
    def __init__(self) -> None: ...
    @native("empty")
    def empty(self) -> bool: ...


class Node:
    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    # stdlib storage template at readonly[...]: spelled with a plain T
    buf: UninitHeapStorage[readonly[Node]]  # tpyc: ok

    def __init__(self) -> None:
        self.buf = UninitHeapStorage[readonly[Node]](4)


class Doc:
    buf: Buffer

    def __init__(self) -> None:
        self.buf = Buffer()

    # method: a view rooted in `self` is returned
    def cursor(self) -> Cursor:
        return self.buf.cursor()  # tpyc: ok


# free function: a view rooted in a parameter is returned
def cursor_of(b: Buffer) -> Cursor:
    return b.cursor()  # tpyc: ok


def main() -> None:
    Holder()
    print("storage", 4)
    bag = Bag[readonly[Node]]()  # tpyc: ok
    print("user storage", bag.empty())
    w = Window[readonly[Node]]()  # tpyc: ok
    print("user view", w.empty())
    b = Buffer()
    print("function", cursor_of(b).get())
    d = Doc()
    print("method", d.cursor().get())


main()
