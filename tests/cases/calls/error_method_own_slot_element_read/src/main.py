# A user record's `Own[T]` param is a real by-value slot (`own_param_t<T>`,
# i.e. `T&&`), which no LVALUE binds -- so the cells that admit an lvalue at
# an `Own[T]` slot are the builtin container INSERT's, whose const-ref
# overload copies. `_x_insert_own_slot` fences them to the stub receiver; the
# element read below is the class witness that would otherwise emit
# `push(::tpy::__getitem__(src, i))` into a `T&&` slot.
# The admitting direction is pinned by calls/method_own_slot_insert_lvalue.
from tpy import Int32, Own


class Item:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Bag[T]:
    held: list[T]

    def __init__(self) -> None:
        self.held = []

    def push(self, x: Own[T]) -> None:
        self.held.append(x)

    def echo(self, src: list[T], i: Int32) -> None:
        # An element read -- an lvalue -- at the record method's Own[T] slot.
        self.push(src[i])  # tpyc: error(/method\.arg_shape/)


def main() -> None:
    b = Bag[Item]()
    src = [Item(1)]
    b.echo(src, 0)
    print(len(b.held))


main()
