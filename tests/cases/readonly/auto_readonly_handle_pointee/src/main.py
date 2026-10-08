# A handle lent by an @auto_readonly accessor reaches the receiver's storage
# even when what it points at is a scalar: the call demotes the receiver, for
# a `Ptr[auto_readonly[int32]]` written with `unsafe_store` and for the
# `Span[auto_readonly[T]]` an ArrayList view or slice hands out.
from tpy import Ptr, Span, int32, uint32, auto_readonly, take_ptr
from tpy.unsafe import unsafe_store
from tplib.array_list import ArrayList


class Handle:
    p: Ptr[int32]

    def __init__(self, p: Ptr[int32]) -> None:
        self.p = p

    @auto_readonly
    def iptr(self) -> Ptr[auto_readonly[int32]]:
        return self.p


# scalar_alias: the scalar pointee written through a bound copy of the Ptr
def scalar_alias(h: Handle) -> None:
    q = h.iptr()  # tpyc: ok
    unsafe_store(q, uint32(0), 5)


# scalar_inline: the same through the call itself
def scalar_inline(h: Handle) -> None:
    unsafe_store(h.iptr(), uint32(0), 6)


# span_view: the ArrayList's view, written through a bound copy
def span_view(a: ArrayList[int32, 8]) -> None:
    s = a.__span__()  # tpyc: ok
    s[0] = 50


# span_slice: a slice of it, through the mutable subscript operator
def span_slice(a: ArrayList[int32, 8]) -> None:
    s = a[0:2]  # tpyc: ok
    s[1] = 60


def main() -> None:
    x: int32 = 1
    h = Handle(take_ptr(x))
    scalar_alias(h)
    print("scalar_alias", x)
    scalar_inline(h)
    print("scalar_inline", x)
    a = ArrayList[int32, 8]()
    a.append(1)
    a.append(2)
    a.append(3)
    span_view(a)
    print("span_view", a[0])
    span_slice(a)
    print("span_slice", a[1], a[2])


main()
